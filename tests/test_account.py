"""The account menu: who you are, what you're called, and switching accounts.

Three things are pinned here, and the first is the one that matters:
**recognising the admin is a label, never a permission.** It comes from
Firebase's own ``admin`` custom claim on the session's account record — the
same fact the category watch and the monitor ceiling already ask — and
nothing on a page, no email string and no settings field can produce it.
The tests below therefore check both halves: the admin *is* recognisable,
and an ordinary member neither sees nor can reach anything the admin has.

The display name is a TicketRadar field on ``users/{uid}``, the same
document the app already reads as its settings. It is authoritative for
everything on screen — which is what lets a Google user change the name
TicketRadar calls them without anything of theirs at Google being touched.

Switching accounts is a sign-out and a normal sign-in. The only thing that
crosses the boundary is an email address, which authenticates nobody.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from auth import session as auth_session
from monitor import state as state_store
from tests.test_app import run, text
from ui import account

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

UI_DIR = Path(account.__file__).resolve().parent


@pytest.fixture(autouse=True)
def clear_direct_session_state():
    """Direct auth tests share Streamlit's bare-mode session mapping.

    AppTest creates its own session, but the focused auth checks below also
    exercise the session helpers directly. Clear those keys after each test
    so a direct Google/admin/switching check cannot become the next test's
    authenticated account or Firestore scope.
    """
    yield
    import streamlit as st

    st.session_state.clear()


def code_of(path: Path) -> str:
    """The file's source with its docstrings and comments removed.

    A test that bans a word has to ban it in the *code*: the prose that
    explains why a credential is never stored says the word "password", and
    that is exactly as it should be.
    """
    src = path.read_text(encoding="utf-8")
    prose: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        first = node.body[0] if getattr(node, "body", None) else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            prose.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = [line for n, line in enumerate(src.splitlines(), 1)
            if n not in prose and not line.strip().startswith("#")]
    return "\n".join(kept)


def keys(app) -> list[str]:
    return [b.key for b in app.button]


def body(app) -> str:
    return " ".join(m.value for m in app.markdown)


@pytest.fixture
def admin(signed_in):
    """The signed-in account carries Firebase's ``admin`` claim.

    Set on the very :class:`auth.session.AuthUser` the restore hands back —
    which is where the real one lands too, from ``accounts:lookup``. There is
    no other way in, here or in production.
    """
    signed_in.admin = True
    yield signed_in
    signed_in.admin = False


# ──────────────────────────────────────────────────────────────────────────
# 1 · The admin is recognisable — and it is still only a label
# ──────────────────────────────────────────────────────────────────────────
def test_the_admin_account_is_named_admin_in_the_chip_and_the_menu(admin):
    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    page = body(app)
    assert 'class="tr-acct-chip"' in page
    assert ">Admin<" in page, "the account menu does not say Admin"
    assert 'class="tr-admin-tag"' in page, "no ADMIN mark on the account"
    assert admin.email in page, "the address is still shown"


def test_admin_chip_keeps_the_display_name_out_of_the_closed_chip(admin):
    app = run()
    chip = next(m.value for m in app.markdown if 'class="tr-acct-chip"' in m.value)
    assert ">Admin<" in chip
    assert "Test Person" not in chip


def test_an_ordinary_member_is_their_own_name_and_carries_no_admin_mark(signed_in):
    app = run()
    page = body(app)
    assert "Test" in page                      # the fixture's display name
    # The class exists in the stylesheet for the admin; no *element* carries it.
    assert 'class="tr-admin-tag"' not in page
    assert ">Admin<" not in page


def test_the_admin_label_comes_from_the_claim_not_the_address(signed_in):
    """The same account, same email, no claim: nothing says Admin. The label
    tracks Firebase's record and nothing else."""
    signed_in.admin = False
    assert account.chip_label(signed_in, {}) != account.ADMIN_LABEL
    signed_in.admin = True
    assert account.chip_label(signed_in, {}) == account.ADMIN_LABEL
    signed_in.admin = False


def test_naming_the_admin_never_widens_what_the_admin_may_do():
    """``ui.account`` decides what the menu *says*. It must not be the thing
    any gate asks: every one of those still reads the claim itself."""
    src = code_of(UI_DIR / "account.py")
    for banned in ("owner_uid", "purge", "load_monitors", "admin = True", "admin=True"):
        assert banned not in src, f"{banned!r} has no business in the account menu"
    app_src = (UI_DIR.parent / "app.py").read_text(encoding="utf-8")
    # The two authorization gates still go through auth.session's own claim.
    assert "auth_session.is_admin()" in app_src
    assert "admin=user.admin" in app_src


# ──────────────────────────────────────────────────────────────────────────
# 2 · Switch account is the admin's alone
# ──────────────────────────────────────────────────────────────────────────
def test_only_the_admin_is_offered_switch_account(admin):
    assert "acct_switch" in keys(run())


def test_a_member_never_sees_switch_account(signed_in):
    ks = keys(run())
    assert "acct_switch" not in ks
    # …and still has everything they always had.
    for key in ("acct_avatar", "acct_settings", "auth_signout", "acct_delete"):
        assert key in ks, key


def test_a_member_cannot_open_the_switcher_even_by_asking(signed_in):
    """The dialog is gated on the claim when it opens *and* when it draws, so
    a session key set some other way opens nothing."""
    app = AppTest.from_file(str(UI_DIR.parent / "app.py"), default_timeout=60)
    app.session_state[account.OPEN_KEY] = True
    app.run()
    assert not app.exception
    assert not any((b.key or "").startswith("switch_") for b in app.button)
    assert app.session_state.get(account.OPEN_KEY) is not True


def test_open_switcher_does_nothing_for_a_member(signed_in):
    import streamlit as st

    st.session_state.pop(account.OPEN_KEY, None)
    account.open_switcher(signed_in, {}, mirror=False)
    assert not account.is_open()


# ──────────────────────────────────────────────────────────────────────────
# 3 · Switching is a sign-out, not an impersonation
# ──────────────────────────────────────────────────────────────────────────
def test_switching_signs_the_current_user_out_and_keeps_only_an_address(admin):
    import streamlit as st

    st.session_state.clear()
    st.session_state[auth_session.USER_KEY] = admin
    auth_session.request_switch("other@example.com")

    assert st.session_state.get(auth_session.USER_KEY) is None, "still signed in"
    assert st.session_state.get("auth_cookie_clear") is True, "the cookie was not dropped"
    assert st.session_state[auth_session.SWITCH_EMAIL_KEY] == "other@example.com"
    # Nothing that could authenticate anybody came along for the ride.
    leftovers = {k: v for k, v in st.session_state.items() if isinstance(v, str)}
    assert admin.refresh_token not in leftovers.values()
    assert admin.id_token not in leftovers.values()


def test_the_address_survives_the_session_wipe_and_nothing_else_does(admin):
    import streamlit as st

    st.session_state.clear()
    st.session_state[auth_session.USER_KEY] = admin
    st.session_state["step"] = 4
    st.session_state["movie_id"] = "bookmyshow:ET1"
    auth_session.request_switch("other@example.com")
    auth_session.apply_pending_reset()

    assert auth_session.take_switch_email() == "other@example.com"
    assert "movie_id" not in st.session_state
    assert st.session_state.get("page") == "Home"


def test_the_switcher_stores_addresses_and_never_a_credential():
    src = code_of(UI_DIR / "account.py")
    for banned in ("password", "refresh_token", "id_token =", "custom_token", "signInWith"):
        assert banned not in src, f"the switcher must not hold {banned!r}"
    assert "sign_out" in src and "request_switch" in src


def test_the_login_page_prefills_the_address_but_never_a_password(admin):
    """After the switch the sign-in form opens with the address in the box —
    and the person still has to sign in."""
    import streamlit as st

    from ui import login

    st.session_state.clear()
    st.session_state[auth_session.SWITCH_EMAIL_KEY] = "other@example.com"
    login.apply_switch_prefill()
    assert st.session_state["auth_email"] == "other@example.com"
    assert st.session_state[login.MODE_KEY] == "signin"
    assert "auth_password" not in st.session_state
    assert auth_session.current_user() is None


def test_remembered_addresses_are_validated_and_bounded():
    settings = {account.ACCOUNTS_FIELD: ["a@example.com", "a@example.com", "nonsense",
                                         "", None, "B@Example.com"]}
    assert account.known_emails(settings) == ["a@example.com", "b@example.com"]
    assert account.known_emails({account.ACCOUNTS_FIELD: "not a list"}) == []
    many = {account.ACCOUNTS_FIELD: [f"a{i}@example.com" for i in range(40)]}
    assert len(account.known_emails(many)) == account.MAX_ACCOUNTS


# ──────────────────────────────────────────────────────────────────────────
# 4 · The display name
# ──────────────────────────────────────────────────────────────────────────
def test_the_profile_document_is_what_the_app_shows(signed_in):
    assert account.display_name(signed_in, {}) == "Test Person"          # Firebase's
    assert account.display_name(signed_in, {account.NAME_FIELD: "Manoj"}) == "Manoj"
    assert account.first_name(signed_in, {account.NAME_FIELD: "Manoj K"}) == "Manoj"


def test_a_name_is_trimmed_collapsed_and_capped():
    assert account.clean_name("   Manoj   Anumolu  ") == "Manoj Anumolu"
    assert account.clean_name("x" * 200) == "x" * account.MAX_NAME
    assert account.clean_name(None) == ""


def test_a_blank_name_is_refused_and_nothing_is_written(signed_in, monkeypatch):
    writes: list[dict] = []
    monkeypatch.setattr(account, "save_settings", lambda s, **k: writes.append(s))
    for raw in ("", "   ", "\t\n"):
        assert account.save_display_name(signed_in, {}, raw, mirror=False)
    assert writes == [], "a blank name reached the store"


def test_saving_a_name_writes_the_profile_and_updates_the_session(signed_in):
    import streamlit as st

    st.session_state[auth_session.USER_KEY] = signed_in
    settings = state_store.load_settings()
    problem = account.save_display_name(signed_in, settings, "  Movie  Man ", mirror=False)
    assert problem == ""
    assert state_store.load_settings()[account.NAME_FIELD] == "Movie Man"
    # The chip changes on this very run — no sign-out, no reload.
    assert auth_session.current_user().display_name == "Movie Man"


def test_a_google_user_renames_in_ticketradar_and_nothing_of_theirs_at_google(signed_in):
    """Their Google profile name is only a default. The TicketRadar value
    wins, and no part of this touches Google — there is no such call to make.
    """
    import streamlit as st

    google_user = auth_session.AuthUser(
        uid="uid-google-1", email="manoj@gmail.com", display_name="Manoj Anumolu",
        id_token="id.token", refresh_token="refresh.token", expires_at=4102444800.0,
        photo_url="https://lh3.googleusercontent.com/a/x")
    st.session_state[auth_session.USER_KEY] = google_user
    settings = state_store.load_settings()
    assert account.display_name(google_user, settings) == "Manoj Anumolu"

    assert account.save_display_name(google_user, settings, "MovieMan", mirror=False) == ""
    assert state_store.load_settings()[account.NAME_FIELD] == "MovieMan"
    assert account.display_name(google_user, state_store.load_settings()) == "MovieMan"
    # Their Google identity is untouched: the photo, and the name Google gave.
    assert google_user.photo_url == "https://lh3.googleusercontent.com/a/x"
    src = code_of(UI_DIR / "account.py")
    assert "googleapis" not in src and "people/me" not in src


def test_the_name_field_is_one_more_field_on_the_settings_document(signed_in):
    """Reusing ``users/{uid}`` is the point: showing the name costs no read of
    its own, and the notification address and avatar are still there."""
    settings = {**state_store.load_settings(), "notify_email": "a@b.com",
                "avatar_key": "batman"}
    state_store.save_settings(settings, mirror=False)
    account.save_display_name(signed_in, state_store.load_settings(), "Renamed", mirror=False)
    stored = state_store.load_settings()
    assert stored[account.NAME_FIELD] == "Renamed"
    assert stored["notify_email"] == "a@b.com"
    assert stored["avatar_key"] == "batman"


def test_account_settings_offers_the_name_box_and_saving_shows_it_everywhere(signed_in):
    app = run("Settings")
    assert not app.exception, [str(e) for e in app.exception]
    box = next(w for w in app.text_input if w.key == account.NAME_INPUT)
    assert box.value == "Test Person"
    assert "save_display_name" in keys(app)

    app.text_input(key=account.NAME_INPUT).set_value("Movie Man").run()
    app.button(key="save_display_name").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state[auth_session.USER_KEY].display_name == "Movie Man"
    assert "Movie Man" in body(app)


def test_saving_a_name_never_resets_the_wizard(signed_in):
    """The save is a callback, so one run draws the result — and the picks the
    person had made are still there afterwards."""
    app = run("Settings", step=3, location="hyderabad", movie_id="bookmyshow:ET1")
    app.text_input(key=account.NAME_INPUT).set_value("Renamed").run()
    app.button(key="save_display_name").click().run()
    assert app.session_state["step"] == 3
    assert app.session_state["movie_id"] == "bookmyshow:ET1"
    assert app.session_state["location"] == "hyderabad"


def test_the_admin_keeps_a_name_and_is_still_called_admin(admin):
    settings = {account.NAME_FIELD: "Manoj"}
    assert account.display_name(admin, settings) == "Manoj"
    assert account.chip_label(admin, settings) == "Admin"


def test_signup_still_asks_for_a_name_and_keeps_it():
    """The sign-up form's name field and the Firebase call behind it are
    exactly as they were; the profile field is an addition, not a swap."""
    src = (UI_DIR / "login.py").read_text(encoding="utf-8")
    assert 'key="auth_su_name"' in src and '_fail("Enter your full name.")' in src
    fb = (UI_DIR.parent / "auth" / "firebase.py").read_text(encoding="utf-8")
    assert '"displayName": name' in fb
