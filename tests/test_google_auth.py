"""Continue with Google — the OAuth redirect, and Firebase's signInWithIdp.

Everything a Google sign-in produces is what a password sign-in produces:
Firebase ``idToken``/``refreshToken``/``localId`` from ``signInWithIdp``, the
account record from ``lookup`` (verified, admin), the same ``Credentials``,
the same ``sign_in_user``, the same cookie, the same UID keying Firestore.
These tests pin the Google-specific part — the link, the ``state`` cookie
across the redirect, the code exchange, Firebase's collision answers — and
then that nothing downstream can tell the difference.

Google's token endpoint is a fake (``auth.google._post_form``), Firebase is
``tests.test_auth.FakeFirebase`` with ``signInWithIdp`` modelled on the
documented behaviour, and the browser is the bare-mode session used by the
restore tests.
"""

from __future__ import annotations

import types
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from auth import firebase, google, session
from auth.firebase import AuthError
from config.timezone import now_ist
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget
from monitor.state import ACTIVE_MONITOR_LIMIT, MonitorLimitError, Scope
from tests.test_auth import FakeFirebase
from tests.test_isolation import MemoryFirestore, PROJECT

APP_URL = "https://movie-ticket-radar.streamlit.app/"
GOOGLE_TOKEN = "google-id-token.priya"


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────
class FakeGoogleTokens:
    """``oauth2.googleapis.com/token``: an authorization code for an ID token."""

    def __init__(self):
        self.codes: dict[str, str] = {}           # code -> google id_token
        self.registered = {APP_URL, "http://localhost:8501/"}
        self.calls: list[dict] = []
        self.fail_with: str | None = None
        self.network_down = False

    def __call__(self, url: str, data: dict[str, str]):
        import requests

        assert url == google.TOKEN_URL
        self.calls.append(dict(data))
        if self.network_down:
            raise requests.ConnectionError("no route")
        if self.fail_with:
            return 400, {"error": self.fail_with}
        if data.get("client_id") != "web-client-id" or data.get("client_secret") != "web-client-secret":
            return 401, {"error": "invalid_client"}
        if data.get("redirect_uri") not in self.registered:
            return 400, {"error": "redirect_uri_mismatch"}
        token = self.codes.pop(data.get("code", ""), None)      # single use
        if token is None:
            return 400, {"error": "invalid_grant"}
        return 200, {"id_token": token, "access_token": "ya29.x", "expires_in": 3599}


@pytest.fixture
def stack(monkeypatch):
    """Google configured, Firebase answered by the fake, one Google identity
    ready to sign in, and a bare-mode browser."""
    fb = FakeFirebase()
    fb.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fb.add_google_identity(GOOGLE_TOKEN, email="priya@gmail.com", name="Priya Rao",
                           photo="https://lh3.googleusercontent.com/p", sub="1001")
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setattr(firebase, "_post", fb)

    google_tokens = FakeGoogleTokens()
    google_tokens.codes["code-1"] = GOOGLE_TOKEN
    monkeypatch.setattr(google, "_post_form", google_tokens)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)

    bare: dict = {}
    query_params: dict = {}
    query = types.SimpleNamespace(to_dict=lambda: dict(query_params), clear=lambda: query_params.clear())
    stub = types.SimpleNamespace(
        session_state=bare,
        context=types.SimpleNamespace(cookies={}, headers={}, url=APP_URL + "?code=x"),
        query_params=query,
    )
    monkeypatch.setattr(session, "st", stub)
    from ui import login
    monkeypatch.setattr(login, "st", types.SimpleNamespace(
        session_state=bare, context=stub.context, query_params=query,
        rerun=lambda: (_ for _ in ()).throw(_Rerun())))
    monkeypatch.setattr(session, "_cookie_header_from_runtime", lambda: "")

    class Stack:
        firebase = fb
        tokens = google_tokens
        session = bare
        params = query_params

        @staticmethod
        def browser_holds(**cookies: str) -> None:
            """What the bridge reports on the return: the jar the browser sent."""
            bare[session.BRIDGE_JAR_KEY] = {**cookies, "seen": len(cookies)}

        @staticmethod
        def returns_from_google(code: str = "code-1", state_value: str | None = None,
                                error: str = "") -> None:
            query_params.clear()
            if error:
                query_params["error"] = error
                return
            query_params["code"] = code
            query_params["state"] = (state_value if state_value is not None
                                     else bare.get(session.OAUTH_STATE_KEY, ""))

    return Stack


class _Rerun(Exception):
    """``st.rerun()`` in bare mode: the handler asked for the next run."""


def _complete(stack):
    """Run the return handler; True means it signed in and asked to rerun."""
    from ui import login

    try:
        login.handle_google_return()
    except _Rerun:
        return True
    return False


def _link_params(stack) -> dict[str, str]:
    from ui import login

    login.prepare_google()
    ready, redirect = login.google_ready()
    assert ready
    url = google.authorization_url(session.oauth_state(), redirect)
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ──────────────────────────────────────────────────────────────────────────
# The link
# ──────────────────────────────────────────────────────────────────────────
def test_the_link_is_a_real_google_authorization_request(stack):
    params = _link_params(stack)
    assert params["client_id"] == "web-client-id"
    assert params["redirect_uri"] == APP_URL                    # derived from the page, root only
    assert params["response_type"] == "code"
    assert set(params["scope"].split()) == {"openid", "email", "profile"}
    assert params["prompt"] == "select_account"
    assert params["state"] == stack.session[session.OAUTH_STATE_KEY]
    assert params["nonce"] == google.nonce_for(params["state"])
    assert "client_secret" not in params                         # never leaves the server


def test_the_state_is_queued_for_the_bridge_once_per_session(stack):
    from ui import login

    login.prepare_google()
    first = session.oauth_state()
    login.prepare_google()
    assert session.oauth_state() == first
    ops = stack.session[session.BRIDGE_OPS_KEY]
    assert [(o["name"], o["value"], o["max_age"]) for o in ops] == [
        (session.OAUTH_STATE_COOKIE, first, google.STATE_SECONDS)]


def test_a_configured_redirect_uri_wins_over_the_page(stack, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8501/")
    assert _link_params(stack)["redirect_uri"] == "http://localhost:8501/"


def test_without_configuration_the_button_stays_honest(stack, monkeypatch):
    from ui import login

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    assert login.google_ready() == (False, "")
    login.prepare_google()
    assert session.OAUTH_STATE_KEY not in stack.session            # no cookie for nothing


# ──────────────────────────────────────────────────────────────────────────
# The return: success
# ──────────────────────────────────────────────────────────────────────────
def test_a_successful_return_signs_in_with_the_firebase_uid(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()

    assert _complete(stack) is True

    user = stack.session[session.USER_KEY]
    assert isinstance(user, session.AuthUser)
    assert user.uid == "uid-google-1001"                          # Firebase's localId, nothing else
    assert user.email == "priya@gmail.com"
    assert user.display_name == "Priya Rao"
    assert user.photo_url == "https://lh3.googleusercontent.com/p"
    assert user.id_token == "id.uid-google-1001"
    assert user.refresh_token == stack.firebase.refresh_token_for("uid-google-1001")
    assert user.admin is False
    # The exchange used the code once, with the secret, server to server.
    assert [c["code"] for c in stack.tokens.calls] == ["code-1"]
    assert stack.tokens.codes == {}
    # Firebase did the identity work: signInWithIdp then the account record.
    assert [a for a, _ in stack.firebase.calls] == ["signInWithIdp", "lookup"]
    idp = stack.firebase.calls[0][1]
    assert idp["postBody"] == f"id_token={GOOGLE_TOKEN}&providerId=google.com"
    assert idp["requestUri"] == APP_URL
    # …and the session is the ordinary one: cookie queued, restore guard set.
    assert stack.session["auth_cookie_set"] == user.refresh_token
    assert stack.session["auth_restore_tried"] is True
    assert stack.session["auth_just_signed_in"] is True
    assert stack.params == {}                                     # the code never survives the run


def test_google_accounts_are_verified_without_the_email_flow(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    assert _complete(stack) is True
    assert stack.firebase.verification_sent == []
    assert session.PENDING_KEY not in stack.session
    assert stack.session.get("auth_mode") == "signin"


def test_the_state_cookie_is_spent_on_return(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    _complete(stack)
    assert session.OAUTH_STATE_KEY not in stack.session
    cleared = [o for o in stack.session[session.BRIDGE_OPS_KEY] if o["name"] == session.OAUTH_STATE_COOKIE]
    assert cleared[-1]["value"] == "" and cleared[-1]["max_age"] == 0


# ──────────────────────────────────────────────────────────────────────────
# The return: every way it can fail, each one a clean line
# ──────────────────────────────────────────────────────────────────────────
def test_cancelling_at_google_is_a_notice_not_an_error(stack):
    session.oauth_state()
    stack.returns_from_google(error="access_denied")
    assert _complete(stack) is False
    assert stack.session["auth_notice"] == ("info", "Google sign-in was cancelled.")
    assert stack.tokens.calls == [] and stack.firebase.calls == []
    assert session.USER_KEY not in stack.session


def test_another_provider_error_is_reported_plainly(stack):
    stack.returns_from_google(error="temporarily_unavailable")
    assert _complete(stack) is False
    assert stack.session["auth_notice"] == ("info", google.MESSAGES["failed"])


@pytest.mark.parametrize("returned", ["", "not-the-state", None])
def test_a_state_that_does_not_match_the_cookie_exchanges_nothing(stack, returned):
    """The CSRF check: a code arriving with the wrong (or no) state is never
    exchanged, so a crafted link cannot sign somebody into an attacker's
    Google account."""
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(state_value=returned if returned is not None else "x" * 32)
    assert _complete(stack) is False
    assert stack.session["auth_error"] == google.MESSAGES["state"]
    assert stack.tokens.calls == []
    assert stack.firebase.calls == []


def test_a_missing_state_cookie_exchanges_nothing(stack):
    """A different browser than the one that clicked (or ten minutes later)."""
    state = session.oauth_state()
    stack.browser_holds()                                          # no cookie at all
    stack.returns_from_google(state_value=state)
    assert _complete(stack) is False
    assert stack.session["auth_error"] == google.MESSAGES["state"]
    assert stack.tokens.calls == []


@pytest.mark.parametrize("code, message", [
    ("invalid_grant", google.MESSAGES["invalid_grant"]),           # expired / already used
    ("redirect_uri_mismatch", google.MESSAGES["redirect_uri_mismatch"]),
    ("invalid_client", google.MESSAGES["invalid_client"]),
])
def test_google_token_endpoint_errors_are_clean_messages(stack, code, message):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.tokens.fail_with = code
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == message
    assert stack.firebase.calls == []                              # Firebase never asked
    assert session.USER_KEY not in stack.session


def test_a_reused_code_is_expired(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    assert _complete(stack) is True
    stack.session.pop(session.USER_KEY)
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-1")                       # the same code again
    assert _complete(stack) is False
    assert stack.session["auth_error"] == google.MESSAGES["invalid_grant"]


def test_no_network_to_google_is_said_so(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.tokens.network_down = True
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == google.MESSAGES["network"]


def test_a_token_google_did_not_issue_is_refused_by_firebase(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.tokens.codes["code-2"] = "forged-token"
    stack.returns_from_google(code="code-2")
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["INVALID_IDP_RESPONSE"]
    assert session.USER_KEY not in stack.session


def test_google_provider_disabled_in_firebase(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.firebase.google_enabled = False
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["GOOGLE_NOT_ENABLED"]


def test_a_google_identity_without_an_email_is_refused(stack):
    stack.firebase.add_google_identity("no-email-token", email="", name="Nobody", sub="2")
    stack.tokens.codes["code-3"] = "no-email-token"
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-3")
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["GOOGLE_NO_EMAIL"]


def test_a_firebase_outage_is_the_existing_network_message(stack, monkeypatch):
    import requests

    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})

    def down(url, params, payload):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(firebase, "_post", down)
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["network"]


# ──────────────────────────────────────────────────────────────────────────
# Collisions: the same email as a password account
# ──────────────────────────────────────────────────────────────────────────
def test_the_same_email_resolves_to_the_existing_uid_never_a_second_one(stack):
    """Ravi has a password account. Signing in with Google for the same
    address, under Firebase's default one-account-per-email, gives the
    *same* UID with Google linked — his monitors and history are keyed on
    that UID and stay his."""
    stack.firebase.add_google_identity("ravi-google-token", email="ravi@example.com",
                                       name="Ravi Teja", sub="7")
    stack.tokens.codes["code-r"] = "ravi-google-token"
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-r")

    assert _complete(stack) is True
    user = stack.session[session.USER_KEY]
    assert user.uid == "uid-ravi"
    assert stack.firebase.accounts["ravi@example.com"]["providers"] == ["password", "google.com"]
    assert len([a for a in stack.firebase.accounts.values() if a["uid"] == "uid-ravi"]) == 1


def test_when_firebase_asks_for_confirmation_nobody_is_signed_in(stack):
    """``needConfirmation`` is Firebase declining to link on its own. The
    app does not guess: no sign-in, no account, one clear instruction."""
    stack.firebase.add_google_identity("ravi-google-token", email="ravi@example.com", sub="7")
    stack.firebase.need_confirmation = True
    stack.tokens.codes["code-r"] = "ravi-google-token"
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-r")

    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["NEED_CONFIRMATION"]
    assert session.USER_KEY not in stack.session
    assert [a for a in stack.firebase.accounts.values() if a["uid"] != "uid-ravi"] == []


def test_email_exists_is_the_same_instruction(stack):
    stack.firebase.fail_with = "EMAIL_EXISTS"
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["NEED_CONFIRMATION"]


def test_what_the_wrong_project_setting_would_do(stack):
    """Documented, not tolerated: with one-account-per-email *off* Firebase
    itself mints a second UID for the same email — the duplicate identity
    that would hide a person's monitors. This is why the setting must stay
    at its default; the app cannot tell the two UIDs apart from here."""
    stack.firebase.one_account_per_email = False
    stack.firebase.add_google_identity("ravi-google-token", email="ravi@example.com", sub="7")
    stack.tokens.codes["code-r"] = "ravi-google-token"
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-r")
    assert _complete(stack) is True
    assert stack.session[session.USER_KEY].uid != "uid-ravi"          # the hazard, made visible


# ──────────────────────────────────────────────────────────────────────────
# Downstream: nothing can tell a Google account from a password one
# ──────────────────────────────────────────────────────────────────────────
def _google_creds(stack, token=GOOGLE_TOKEN) -> firebase.Credentials:
    return firebase.FirebaseAuth("k").sign_in_with_google(token, APP_URL)


def test_the_admin_claim_comes_through_the_same_lookup(stack):
    creds = _google_creds(stack)
    assert creds.admin is False and creds.provider == "google.com" and creds.email_verified is True
    stack.firebase.set_claims("priya@gmail.com", {"admin": True})
    assert _google_creds(stack).admin is True
    assert session._from_credentials(_google_creds(stack)).admin is True


def test_a_google_session_restores_from_the_cookie_like_any_other(stack, monkeypatch):
    import time

    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    assert _complete(stack) is True
    user = stack.session.pop(session.USER_KEY)
    stack.session.pop("auth_restore_tried", None)

    from tests.test_auth import REAL_RESTORE

    stack.browser_holds(**{session.COOKIE: user.refresh_token,
                           session.SESSION_STARTED_COOKIE: str(time.time())})
    restored = REAL_RESTORE()
    assert restored is not None and restored.uid == user.uid
    assert restored.admin is False


def test_sign_out_forgets_a_google_session(stack):
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google()
    assert _complete(stack) is True
    session.sign_out()
    assert session.current_user() is None
    assert stack.session["auth_cookie_clear"] is True


def _monitor(title: str, owner: str) -> Monitor:
    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=10, monitor_until=now_ist() + timedelta(days=2),
        notify_email="w@example.com", owner_uid=owner)


@pytest.fixture
def firestore(monkeypatch):
    store = MemoryFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setattr(state_mod, "_owner_of", {})
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    yield store
    state_mod.set_scope_provider(None)


def _scope_for(creds: firebase.Credentials) -> None:
    """What app._scope builds from the session's AuthUser."""
    user = session._from_credentials(creds)
    state_mod.set_scope_provider(lambda: Scope(PROJECT, user.uid, lambda: f"id.{user.uid}", admin=user.admin))


def test_a_google_user_is_limited_and_isolated_like_everyone(stack, firestore):
    stack.firebase.add_google_identity("other-token", email="arjun@gmail.com", name="Arjun", sub="55")
    priya, arjun = _google_creds(stack), _google_creds(stack, "other-token")

    _scope_for(priya)
    for i in range(ACTIVE_MONITOR_LIMIT):
        state_mod.upsert_monitor(_monitor(f"Priya {i}", priya.uid), mirror=False)
    with pytest.raises(MonitorLimitError):
        state_mod.upsert_monitor(_monitor("Priya too many", priya.uid), mirror=False)

    _scope_for(arjun)
    assert state_mod.load_monitors() == []                        # none of Priya's
    state_mod.upsert_monitor(_monitor("Arjun 0", arjun.uid), mirror=False)
    assert {m.owner_uid for m in state_mod.load_monitors()} == {arjun.uid}
    with pytest.raises(PermissionError):
        state_mod.upsert_monitor(_monitor("Forged", priya.uid), mirror=False)

    # …and the documents carry the Firebase UID, nothing Google-specific.
    owners = {d["owner_uid"] for d in firestore.docs["monitors"].values()}
    assert owners == {priya.uid, arjun.uid}


def test_a_google_admin_has_no_limit(stack, firestore):
    _google_creds(stack)                                          # the account exists first…
    stack.firebase.set_claims("priya@gmail.com", {"admin": True})  # …then the claim is granted
    priya = _google_creds(stack)
    _scope_for(priya)
    for i in range(ACTIVE_MONITOR_LIMIT + 2):
        state_mod.upsert_monitor(_monitor(f"Priya {i}", priya.uid), mirror=False)
    assert state_mod.active_monitor_count(state_mod.load_monitors()) == ACTIVE_MONITOR_LIMIT + 2


# ──────────────────────────────────────────────────────────────────────────
# The password path is untouched
# ──────────────────────────────────────────────────────────────────────────
def test_password_sign_in_is_exactly_as_before(stack):
    creds = firebase.FirebaseAuth("k").sign_in("ravi@example.com", "Popcorn2026")
    assert creds.uid == "uid-ravi" and creds.provider == "password" and creds.photo_url == ""
    assert [a for a, _ in stack.firebase.calls] == ["signInWithPassword", "lookup"]
    with pytest.raises(AuthError):
        firebase.FirebaseAuth("k").sign_in("ravi@example.com", "wrong")


def test_the_return_handler_does_nothing_on_an_ordinary_visit(stack):
    assert _complete(stack) is False
    assert stack.tokens.calls == [] and stack.firebase.calls == []
    assert "auth_error" not in stack.session and "auth_notice" not in stack.session


# ──────────────────────────────────────────────────────────────────────────
# The page itself
# ──────────────────────────────────────────────────────────────────────────
def test_the_login_page_draws_the_link_when_google_is_configured(monkeypatch):
    """Same place, same words, same styling hooks as the button it replaces —
    but a real link to Google carrying this session's state."""
    from tests.test_auth import body, on_login_page, run

    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", APP_URL)

    app = run()
    assert not app.exception and on_login_page(app)
    html = " ".join(m.value for m in app.markdown)
    assert 'class="tr-auth-google"' in html
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in html
    assert "Continue with Google" in html
    assert "client_secret" not in html
    state = app.session_state[session.OAUTH_STATE_KEY]
    assert f"state={state}" in html
    assert "auth_google" not in {b.key for b in app.button}      # the notice button is gone
    # …and the cookie carrying that state was queued for the bridge.
    ops = app.session_state.get(session.BRIDGE_OPS_KEY) or []
    assert not ops or any(o["name"] == session.OAUTH_STATE_COOKIE for o in ops)


def test_the_login_page_keeps_the_honest_button_when_not_configured(monkeypatch):
    from tests.test_auth import body, on_login_page, run

    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)

    app = run()
    assert on_login_page(app)
    assert "auth_google" in {b.key for b in app.button}
    assert 'class="tr-auth-google"' not in " ".join(m.value for m in app.markdown)
    app.button(key="auth_google").click().run()
    assert "isn't switched on" in body(app)


# ──────────────────────────────────────────────────────────────────────────
# Verified means Firebase's record says so — not "it came from Google"
# ──────────────────────────────────────────────────────────────────────────
def test_verified_is_the_account_records_word_not_the_providers(stack):
    """Firebase does mark Google-asserted addresses verified. If it ever did
    not, the app must not enter on the strength of the provider's name, and
    must not start the password flow's verification email either."""
    stack.firebase.google_marks_verified = False
    creds = _google_creds(stack)
    assert creds.provider == "google.com" and creds.email_verified is False

    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.tokens.codes["code-1"] = GOOGLE_TOKEN
    stack.returns_from_google()
    assert _complete(stack) is False
    assert stack.session["auth_error"] == firebase.MESSAGES["EMAIL_NOT_VERIFIED"]
    assert session.USER_KEY not in stack.session
    assert session.PENDING_KEY not in stack.session
    assert stack.firebase.verification_sent == []


def test_the_existing_uid_is_kept_for_a_verified_password_account_with_both_methods(stack):
    """The production case exactly: Ravi verified his password account (the
    app requires it), then signs in with Google for the same Gmail address.
    Same UID, both sign-in methods, nothing created, nothing moved."""
    stack.firebase.accounts["ravi@example.com"]["verified"] = True
    stack.firebase.add_google_identity("ravi-google-token", email="ravi@example.com", sub="7")
    stack.tokens.codes["code-r"] = "ravi-google-token"
    before = {k: dict(v) for k, v in stack.firebase.accounts.items()}
    state = session.oauth_state()
    stack.browser_holds(**{session.OAUTH_STATE_COOKIE: state})
    stack.returns_from_google(code="code-r")

    assert _complete(stack) is True
    assert stack.session[session.USER_KEY].uid == "uid-ravi"
    after = stack.firebase.accounts
    assert set(after) == set(before)                                # no new account
    assert after["ravi@example.com"]["password"] == "Popcorn2026"   # password still works
    assert after["ravi@example.com"]["providers"] == ["password", "google.com"]
    # …and the password sign-in still lands on the same UID afterwards.
    assert firebase.FirebaseAuth("k").sign_in("ravi@example.com", "Popcorn2026").uid == "uid-ravi"


def test_the_google_control_opens_in_a_new_tab_never_the_top_frame(monkeypatch):
    """Community Cloud runs the app inside the host's iframe, sandboxed with
    allow-popups + allow-popups-to-escape-sandbox but no allow-top-navigation
    (read off the deployed host's own iframe chunk). ``_top`` is refused
    silently there — the status bar previews the URL, the click does nothing.
    ``_blank`` on the person's own click is a plain, script-free, unsandboxed
    new tab: exactly what the sandbox permits, and immune to popup blockers.
    """
    import re

    from tests.test_auth import on_login_page, run

    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", APP_URL)

    app = run()
    assert on_login_page(app)
    html = " ".join(m.value for m in app.markdown)
    anchor = re.search(r'<a class="tr-auth-google"[^>]*>', html)
    assert anchor, html
    tag = anchor.group(0)
    assert 'target="_blank"' in tag
    assert 'rel="noopener"' in tag                                  # the new tab gets no opener
    assert 'target="_top"' not in tag and "window.open" not in html and "onclick" not in tag.lower()
    # The URL itself is untouched: still the server-minted authorization request.
    href = re.search(r'href="([^"]+)"', tag).group(1).replace("&amp;", "&")
    assert href.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert f"state={app.session_state[session.OAUTH_STATE_KEY]}" in href
    assert "client_secret" not in href
