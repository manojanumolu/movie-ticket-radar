"""Authentication: the Firebase client, the session, and the gate around the app.

Firebase is replaced by :class:`FakeFirebase`, an in-memory Identity Toolkit
that answers the same four endpoints with the same shapes (and the same
error codes) the real one does — so the tests are deterministic and never
touch Google, while every message a person could see is the one the real
service would have produced.

The ``signed_in`` fixture in ``conftest.py`` restores a session for every
other ``AppTest`` in the suite; the tests here switch it off to look at the
gate from the outside.
"""

from __future__ import annotations

import re
import time

import pytest

from auth import firebase, session
from auth.firebase import AuthError

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
#: Captured before any fixture swaps it out (module import precedes fixtures).
REAL_RESTORE = session.restore


# ──────────────────────────────────────────────────────────────────────────
# A fake Identity Toolkit
# ──────────────────────────────────────────────────────────────────────────
class FakeFirebase:
    """Accounts in a dict; responses shaped like Google's."""

    def __init__(self):
        self.accounts: dict[str, dict] = {}     # email -> {password, uid, name, verified}
        self.calls: list[tuple[str, dict]] = []
        self.reset_requests: list[str] = []
        self.verification_sent: list[str] = []   # emails a VERIFY_EMAIL went to
        self.fail_with: str | None = None        # force one Firebase error code
        self.fail_verify_with: str | None = None  # …or only for VERIFY_EMAIL

    def add(self, email: str, password: str, *, uid: str = "uid-1", name: str = "", verified: bool = True) -> None:
        self.accounts[email] = {"password": password, "uid": uid, "name": name, "verified": verified}

    def verify(self, email: str) -> None:
        """What clicking Firebase's link does."""
        self.accounts[email]["verified"] = True

    def _by_token(self, id_token: str):
        for email, acct in self.accounts.items():
            if f"id.{acct['uid']}" == id_token:
                return email, acct
        return None, None

    @staticmethod
    def _error(code: str, status: int = 400):
        return status, {"error": {"code": status, "message": code, "errors": [{"message": code}]}}

    def _creds(self, email: str) -> dict:
        acct = self.accounts[email]
        return {"localId": acct["uid"], "email": email, "displayName": acct["name"],
                "idToken": f"id.{acct['uid']}", "refreshToken": f"refresh.{acct['uid']}",
                "expiresIn": "3600", "registered": True}

    def __call__(self, url: str, params: dict, payload: dict):
        action = url.rsplit(":", 1)[-1] if "accounts:" in url else "token"
        self.calls.append((action, dict(payload)))
        assert params.get("key"), "every call carries the Web API key"
        if self.fail_with:
            return self._error(self.fail_with)
        if action == "signInWithPassword":
            acct = self.accounts.get(payload.get("email", ""))
            if acct is None or acct["password"] != payload.get("password"):
                return self._error("INVALID_LOGIN_CREDENTIALS")
            return 200, self._creds(payload["email"])
        if action == "signUp":
            email = payload.get("email", "")
            if email in self.accounts:
                return self._error("EMAIL_EXISTS")
            if len(payload.get("password", "")) < 6:
                return self._error("WEAK_PASSWORD : Password should be at least 6 characters")
            self.add(email, payload["password"], uid=f"uid-{len(self.accounts) + 1}", verified=False)
            return 200, self._creds(email)
        if action == "update":
            for acct in self.accounts.values():
                if f"id.{acct['uid']}" == payload.get("idToken"):
                    acct["name"] = payload.get("displayName", "")
                    return 200, {"localId": acct["uid"], "displayName": acct["name"]}
            return self._error("INVALID_ID_TOKEN")
        if action == "sendOobCode":
            kind = payload.get("requestType")
            if kind == "VERIFY_EMAIL":
                if self.fail_verify_with:
                    return self._error(self.fail_verify_with)
                email, acct = self._by_token(payload.get("idToken", ""))
                if acct is None:
                    return self._error("INVALID_ID_TOKEN")
                self.verification_sent.append(email)
                return 200, {"kind": "identitytoolkit#GetOobConfirmationCodeResponse", "email": email}
            assert kind == "PASSWORD_RESET"
            email = payload.get("email", "")
            if email not in self.accounts:
                return self._error("EMAIL_NOT_FOUND")
            self.reset_requests.append(email)
            return 200, {"kind": "identitytoolkit#GetOobConfirmationCodeResponse", "email": email}
        if action == "lookup":
            email, acct = self._by_token(payload.get("idToken", ""))
            if acct is None:
                return self._error("INVALID_ID_TOKEN")
            return 200, {"users": [{"localId": acct["uid"], "email": email, "displayName": acct["name"],
                                    "emailVerified": acct["verified"]}]}
        if action == "token":
            for acct in self.accounts.values():
                if f"refresh.{acct['uid']}" == payload.get("refresh_token"):
                    return 200, {"user_id": acct["uid"], "id_token": f"id.{acct['uid']}",
                                 "refresh_token": f"refresh.{acct['uid']}", "expires_in": "3600"}
            return self._error("INVALID_REFRESH_TOKEN")
        raise AssertionError(f"unexpected endpoint {url}")


@pytest.fixture
def fake(monkeypatch):
    """Firebase configured, and answered by the fake."""
    fb = FakeFirebase()
    fb.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fb.add("sita@example.com", "Interval99", uid="uid-sita", name="Sita Devi")
    fb.add("newbie@example.com", "Trailer2026", uid="uid-newbie", name="New Person", verified=False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setattr(firebase, "_post", fb)
    return fb


@pytest.fixture
def visitor(monkeypatch):
    """Nobody signed in, nothing to restore — a fresh browser."""
    monkeypatch.setattr(session, "restore", lambda: None)


def run(**state):
    app = AppTest.from_file("app.py", default_timeout=60)
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def settle(app):
    """One plain rerun. A submit that ends in ``st.rerun()`` leaves AppTest's
    element tree holding both runs' widgets (the browser would have dropped
    the first run's); a clean run shows only what is on screen."""
    return app.run()


def body(app) -> str:
    """Everything rendered as markdown, minus the stylesheets."""
    import re

    from html import unescape

    text = " ".join(m.value for m in app.markdown)
    return unescape(re.sub(r"<style>.*?</style>", "", text, flags=re.S))


def on_login_page(app) -> bool:
    return "Welcome back." in body(app) or "Create your account." in body(app) or "Reset your password." in body(app)


def in_the_app(app) -> bool:
    return '<div class="tr-hero">' in body(app)


# ──────────────────────────────────────────────────────────────────────────
# The client
# ──────────────────────────────────────────────────────────────────────────
def test_every_firebase_code_becomes_one_plain_sentence():
    assert "don't match" in firebase.explain("INVALID_LOGIN_CREDENTIALS")
    assert firebase.explain("EMAIL_NOT_FOUND") == firebase.explain("INVALID_PASSWORD")   # no enumeration
    assert "stronger password" in firebase.explain("WEAK_PASSWORD : Password should be at least 6 characters")
    assert "Too many attempts" in firebase.explain("TOO_MANY_ATTEMPTS_TRY_LATER : Access to this account …")
    assert "configured" in firebase.explain("API key not valid. Please pass a valid API key.")
    assert firebase.explain("SOMETHING_NEW_FROM_GOOGLE") == firebase.FALLBACK
    import re

    for message in firebase.MESSAGES.values():
        assert not re.search(r"[A-Z]+_[A-Z]+", message)   # never a raw code


def test_sign_in_returns_the_account_google_vouched_for(fake):
    creds = firebase.FirebaseAuth().sign_in("ravi@example.com", "Popcorn2026")
    assert creds.uid == "uid-ravi"
    assert creds.email == "ravi@example.com"
    assert creds.display_name == "Ravi Teja"
    assert creds.id_token and creds.refresh_token
    assert creds.email_verified is True                       # from accounts:lookup
    assert [a for a, _ in fake.calls] == ["signInWithPassword", "lookup"]
    assert firebase.FirebaseAuth().sign_in("newbie@example.com", "Trailer2026").email_verified is False


def test_a_wrong_password_is_a_clean_error(fake):
    with pytest.raises(AuthError) as exc:
        firebase.FirebaseAuth().sign_in("ravi@example.com", "nope")
    assert exc.value.code == "INVALID_LOGIN_CREDENTIALS"
    assert str(exc.value) == firebase.MESSAGES["INVALID_LOGIN_CREDENTIALS"]


def test_sign_up_creates_the_account_unverified_and_the_verification_call_is_separate(fake):
    client = firebase.FirebaseAuth()
    creds = client.sign_up("Arjun Rao", "arjun@example.com", "Interval99")
    assert creds.display_name == "Arjun Rao"
    assert fake.accounts["arjun@example.com"]["name"] == "Arjun Rao"
    assert creds.email_verified is False
    assert [a for a, _ in fake.calls] == ["signUp", "update"]     # nothing hidden in here
    # The verification request is its own call — exactly Firebase's documented shape.
    client.send_email_verification(creds.id_token)
    action, payload = fake.calls[-1]
    assert action == "sendOobCode"
    assert payload == {"requestType": "VERIFY_EMAIL", "idToken": creds.id_token}
    assert fake.verification_sent == ["arjun@example.com"]       # Firebase's email, not Gmail SMTP
    # …and a refusal is an AuthError carrying the status and code, not a silent print.
    fake.fail_with = "TOO_MANY_ATTEMPTS_TRY_LATER"
    with pytest.raises(AuthError) as exc:
        client.send_email_verification(creds.id_token)
    assert (exc.value.status, exc.value.code) == (400, "TOO_MANY_ATTEMPTS_TRY_LATER")
    assert exc.value.diagnostic == "HTTP 400, TOO_MANY_ATTEMPTS_TRY_LATER"
    with pytest.raises(AuthError) as exc:
        client.send_email_verification("")
    assert exc.value.code == "INVALID_ID_TOKEN"


def test_sign_in_user_refuses_an_unverified_account(fake, monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {})
    creds = firebase.FirebaseAuth().sign_in("newbie@example.com", "Trailer2026")
    with pytest.raises(AuthError) as exc:
        session.sign_in_user(creds)
    assert exc.value.code == "EMAIL_NOT_VERIFIED"
    assert session.current_user() is None


def test_a_duplicate_account_is_named_as_such(fake):
    with pytest.raises(AuthError) as exc:
        firebase.FirebaseAuth().sign_up("Ravi", "ravi@example.com", "Popcorn2026")
    assert exc.value.code == "EMAIL_EXISTS"
    assert "already" in str(exc.value)


def test_password_reset_never_reveals_whether_an_account_exists(fake):
    client = firebase.FirebaseAuth()
    client.send_password_reset("ravi@example.com")
    client.send_password_reset("nobody@example.com")        # EMAIL_NOT_FOUND → silently ok
    assert fake.reset_requests == ["ravi@example.com"]


def test_refresh_and_lookup_restore_an_account(fake):
    client = firebase.FirebaseAuth()
    creds = client.refresh("refresh.uid-ravi")
    assert creds.uid == "uid-ravi" and creds.id_token == "id.uid-ravi"
    assert client.lookup(creds.id_token) == {"uid": "uid-ravi", "email": "ravi@example.com",
                                             "display_name": "Ravi Teja", "email_verified": True}
    with pytest.raises(AuthError) as exc:
        client.refresh("refresh.stale")
    assert "sign in again" in str(exc.value)


def test_no_network_is_a_sentence_not_a_traceback(monkeypatch):
    import requests

    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "k")

    def down(*a, **k):
        raise requests.ConnectionError("dns")

    monkeypatch.setattr(firebase, "_post", down)
    with pytest.raises(AuthError) as exc:
        firebase.FirebaseAuth().sign_in("a@b.co", "x")
    assert exc.value.code == "network"


def test_without_a_web_api_key_nothing_is_attempted(monkeypatch):
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)
    monkeypatch.setattr(firebase, "_secret", lambda table, key: "")
    assert firebase.is_configured() is False
    with pytest.raises(AuthError) as exc:
        firebase.FirebaseAuth().sign_in("a@b.co", "x")
    assert exc.value.code == "not_configured"


def test_form_validation_rules():
    assert firebase.valid_email("me@example.com")
    assert not firebase.valid_email("me@example")
    assert not firebase.valid_email("not an email")
    assert firebase.password_problem("short1") == "Use at least 8 characters."
    assert firebase.password_problem("lettersonly") == "Mix letters and numbers."
    assert firebase.password_problem("Popcorn2026") == ""


# ──────────────────────────────────────────────────────────────────────────
# The session
# ──────────────────────────────────────────────────────────────────────────
def test_only_an_authuser_this_module_stored_counts(visitor):
    app = run(auth_user={"uid": "forged", "email": "x@y.z"})
    assert on_login_page(app) and not in_the_app(app)
    app = run(auth_user="uid-forged")
    assert on_login_page(app) and not in_the_app(app)


def test_cookie_script_writes_a_safe_literal():
    script = session._cookie_script('tok"en</script>', 60)
    assert "</script>" not in script.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert "Max-Age=60" in script and "SameSite=Lax" in script and "Path=/" in script
    assert "Max-Age=0" in session._cookie_script("", 0)


def test_the_id_token_refreshes_itself_before_it_expires(fake, monkeypatch):
    """What the next step (Firestore, per user) will call."""
    import streamlit as st

    state = {session.USER_KEY: session.AuthUser(uid="uid-ravi", email="ravi@example.com", id_token="old",
                                                refresh_token="refresh.uid-ravi", expires_at=time.time() + 10)}
    monkeypatch.setattr(st, "session_state", state)
    assert session.id_token() == "id.uid-ravi"
    assert state[session.USER_KEY].expires_at > time.time() + 3000
    assert session.current_uid() == "uid-ravi"
    # Nothing to refresh yet: the same token comes back without a call.
    fake.calls.clear()
    state[session.USER_KEY].expires_at = time.time() + 3600
    assert session.id_token() == "id.uid-ravi" and fake.calls == []


# ──────────────────────────────────────────────────────────────────────────
# The gate, through the real app
# ──────────────────────────────────────────────────────────────────────────
def test_login_page_renders_while_unauthenticated(visitor):
    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    assert on_login_page(app)
    assert not in_the_app(app)
    assert not app.sidebar.radio                      # no navigation for a visitor
    assert {t.key for t in app.text_input} == {"auth_email", "auth_password"}
    assert {b.key for b in app.button} >= {"auth_signin", "auth_forgot", "auth_google", "auth_to_signup"}
    text = body(app)
    for piece in ("Never Miss", "TICKETS", "DETECTED", "Don't have an account?", "Monitoring 24 / 7",
                  "Secure & Private", "GOOD<br>MOVIES<br>FIND<br>YOU"):
        assert piece in text, piece
    labels = {b.label for b in app.button}
    assert {"SIGN IN", "Continue with Google", "Forgot password?", "Create account"} <= labels


def test_a_fresh_browser_lands_on_the_login_page_not_home(monkeypatch):
    """The deployment regression: a brand-new session (Incognito — no cookie,
    nothing in session state) with *no* Firebase configuration on the host.
    This runs the real ``restore()``, not the suite's stand-in, and the
    answer must be the login page with sign-in failing safely — never Home."""
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "")
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)
    monkeypatch.setattr(firebase, "_secret", lambda table, key: "")
    assert not firebase.is_configured()

    app = run(page="Home")
    assert not app.exception, [str(e) for e in app.exception]
    assert on_login_page(app) and not in_the_app(app)
    assert "Movie Ticket Monitor" not in body(app) and "Where are you watching?" not in body(app)
    assert not app.sidebar.radio and not any(b.key == "loc_hyderabad" for b in app.button)
    assert "auth_user" not in app.session_state
    # Missing configuration is shown, not treated as permission to pass.
    assert firebase.MESSAGES["not_configured"] in body(app)
    app.text_input(key="auth_email").set_value("anyone@example.com")
    app.text_input(key="auth_password").set_value("anything1")
    app.button(key="auth_signin").click().run()
    app = settle(app)
    assert on_login_page(app) and not in_the_app(app)
    assert "auth_user" not in app.session_state


def test_the_gate_is_the_first_thing_main_does():
    """``app.py`` runs ``set_page_config`` → ``inject()`` (CSS only) →
    ``main()``, and ``main()``'s first statement is ``require_user()``. Pinned
    so nothing authenticated-only can ever be drawn ahead of the gate."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    calls = [ast.unparse(n.value.func) for n in tree.body
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert calls == ["st.set_page_config", "inject", "main"]
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    first = main.body[0]
    assert isinstance(first, ast.Assign) and ast.unparse(first.value) == "require_user()"


@pytest.mark.parametrize("page", ["Home", "My Monitors", "History", "Settings"])
def test_no_page_can_be_reached_around_the_gate(visitor, page):
    app = run(page=page, step=5, location="hyderabad")
    assert on_login_page(app)
    for private in ("Notification email", "My Monitors", "Where are you watching?", "What the radar has picked up"):
        assert private not in body(app)
    assert not any(b.key in ("start", "loc_hyderabad", "save_settings") for b in app.button)


def test_invalid_email_or_password_shows_a_clean_error(visitor, fake):
    app = run()
    app.text_input(key="auth_email").set_value("ravi@example.com")
    app.text_input(key="auth_password").set_value("wrong-password-1")
    app.button(key="auth_signin").click().run()
    assert not app.exception
    assert on_login_page(app)
    assert firebase.MESSAGES["INVALID_LOGIN_CREDENTIALS"] in body(app)
    assert "INVALID_LOGIN_CREDENTIALS" not in body(app)
    assert "auth_user" not in app.session_state or app.session_state["auth_user"] is None


def test_a_malformed_email_never_reaches_firebase(visitor, fake):
    app = run()
    app.text_input(key="auth_email").set_value("ravi")
    app.text_input(key="auth_password").set_value("whatever1")
    app.button(key="auth_signin").click().run()
    assert firebase.MESSAGES["INVALID_EMAIL"] in body(app)
    assert fake.calls == []


def test_valid_login_reaches_the_existing_app(visitor, fake):
    app = run()
    app.text_input(key="auth_email").set_value("ravi@example.com")
    app.text_input(key="auth_password").set_value("Popcorn2026")
    app.button(key="auth_signin").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert in_the_app(app) and not on_login_page(app)
    # The existing product, not a second dashboard.
    text = body(app)
    assert "Movie Ticket Monitor" in text and "Where are you watching?" in text
    assert [r.key for r in app.sidebar.radio] == ["page"]
    # Firebase's UID is what the session carries — and it is shown as the account.
    user = app.session_state["auth_user"]
    assert isinstance(user, session.AuthUser)
    assert user.uid == "uid-ravi" and user.email == "ravi@example.com"
    assert "Ravi Teja" in text and "ravi@example.com" in text
    assert any(b.key == "auth_signout" for b in app.button)
    # The password never lands in session state.
    assert "Popcorn2026" not in repr(app.session_state)
    # The cookie write is queued for the browser, and the API key stays out of the page.
    assert "test-web-api-key" not in text


def test_the_session_survives_reruns(visitor, fake):
    app = run()
    app.text_input(key="auth_email").set_value("ravi@example.com")
    app.text_input(key="auth_password").set_value("Popcorn2026")
    app.button(key="auth_signin").click().run()
    assert in_the_app(app)
    app.button(key="loc_hyderabad").click().run()      # an ordinary interaction
    assert in_the_app(app)
    assert app.session_state["auth_user"].uid == "uid-ravi"
    assert app.session_state["step"] == 2


def test_signup_creates_an_account_and_signs_in(visitor, fake):
    app = run()
    app.button(key="auth_to_signup").click().run()
    assert "Create your account." in body(app)
    assert {t.key for t in app.text_input} == {"auth_su_name", "auth_su_email", "auth_su_password", "auth_su_confirm"}
    app.text_input(key="auth_su_name").set_value("  Arjun   Rao ")
    app.text_input(key="auth_su_email").set_value("arjun@example.com")
    app.text_input(key="auth_su_password").set_value("Interval99")
    app.text_input(key="auth_su_confirm").set_value("Interval99")
    app.button(key="auth_signup").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    app = settle(app)
    # The account exists, Firebase emailed the link — and nobody is signed in.
    assert fake.accounts["arjun@example.com"]["name"] == "Arjun Rao"
    assert fake.verification_sent == ["arjun@example.com"]
    assert not in_the_app(app)
    assert "auth_user" not in app.session_state
    text = body(app)
    assert "You're almost in." in text and "Verify your email address to activate" in text
    assert "arjun@example.com" in text
    assert {b.key for b in app.button} >= {"auth_resend", "auth_verify_back"}
    pend = app.session_state["auth_pending"]
    assert pend["email"] == "arjun@example.com" and pend["sends"] == 1
    assert pend["cooldown_until"] > time.time() + 50                 # Firebase said 2xx → cooldown
    assert "Interval99" not in repr(pend) and "auth_user" not in app.session_state


def test_password_mismatch_is_caught_before_firebase(visitor, fake):
    app = run(auth_mode="signup")
    app.text_input(key="auth_su_name").set_value("Arjun")
    app.text_input(key="auth_su_email").set_value("arjun@example.com")
    app.text_input(key="auth_su_password").set_value("Interval99")
    app.text_input(key="auth_su_confirm").set_value("Interval98")
    app.button(key="auth_signup").click().run()
    assert "don't match" in body(app)
    assert on_login_page(app) and fake.calls == []


def test_weak_password_and_missing_fields_are_explained(visitor, fake):
    app = run(auth_mode="signup")
    app.button(key="auth_signup").click().run()
    assert "Enter your full name." in body(app)
    app.text_input(key="auth_su_name").set_value("Arjun")
    app.text_input(key="auth_su_email").set_value("arjun@example.com")
    app.text_input(key="auth_su_password").set_value("short")
    app.text_input(key="auth_su_confirm").set_value("short")
    app.button(key="auth_signup").click().run()
    assert "at least 8 characters" in body(app)
    assert fake.calls == []


def test_duplicate_account_is_handled(visitor, fake):
    app = run(auth_mode="signup")
    app.text_input(key="auth_su_name").set_value("Ravi")
    app.text_input(key="auth_su_email").set_value("ravi@example.com")
    app.text_input(key="auth_su_password").set_value("Popcorn2026")
    app.text_input(key="auth_su_confirm").set_value("Popcorn2026")
    app.button(key="auth_signup").click().run()
    assert firebase.MESSAGES["EMAIL_EXISTS"] in body(app)
    assert "EMAIL_EXISTS" not in body(app)
    assert on_login_page(app)


def test_forgot_password_flow(visitor, fake):
    app = run()
    app.button(key="auth_forgot").click().run()
    text = body(app)
    assert "Reset your password." in text
    assert "Enter your email and we&#x27;ll send you a secure reset link." in text or \
        "Enter your email and we'll send you a secure reset link." in text
    assert {t.key for t in app.text_input} == {"auth_reset_email"}
    app.text_input(key="auth_reset_email").set_value("ravi@example.com")
    app.button(key="auth_send_reset").click().run()
    assert not app.exception
    app = settle(app)
    assert "Check your inbox." in body(app) and "ravi@example.com" in body(app)
    assert fake.reset_requests == ["ravi@example.com"]
    assert {t.key for t in app.text_input} == set()          # nothing left to type
    # Back to sign in.
    app.button(key="auth_to_signin").click().run()
    assert "Welcome back." in body(app)


def test_forgot_password_for_an_unknown_email_looks_the_same(visitor, fake):
    app = run(auth_mode="reset")
    app.text_input(key="auth_reset_email").set_value("nobody@example.com")
    app.button(key="auth_send_reset").click().run()
    assert "Check your inbox." in body(app)
    assert "EMAIL_NOT_FOUND" not in body(app) and "don't match" not in body(app)


def test_google_button_is_honest_about_not_being_enabled(visitor, fake):
    app = run()
    app.button(key="auth_google").click().run()
    assert "isn't switched on" in body(app)
    assert on_login_page(app) and fake.calls == []


def test_logout_returns_to_the_login_page(fake):
    app = run()                                  # signed in by the conftest fixture
    assert in_the_app(app)
    app.button(key="auth_signout").click().run()
    assert not app.exception
    app = settle(app)
    assert on_login_page(app) and not in_the_app(app)
    assert "auth_user" not in app.session_state
    # …and the cookie's old refresh token is not used to sign straight back in.
    assert app.session_state["auth_restore_tried"] is True
    app = app.run()
    assert on_login_page(app)


def test_a_restored_session_comes_from_google_not_the_cookie(monkeypatch, fake):
    """A reload carries only the refresh token; who it belongs to is what
    Google says, not what the cookie says."""
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.uid-ravi")
    app = run()
    assert in_the_app(app)
    assert app.session_state["auth_user"].uid == "uid-ravi"
    assert app.session_state["auth_user"].email == "ravi@example.com"
    assert [a for a, _ in fake.calls] == ["token", "lookup"]

    # A stale or forged cookie is refused, and the login page is what shows.
    fake.calls.clear()
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.forged")
    app = run()
    assert on_login_page(app)
    assert [a for a, _ in fake.calls] == ["token"]           # asked Google; Google said no


def test_the_uid_is_available_to_the_application():
    app = run()
    user = app.session_state["auth_user"]
    assert user.uid == "uid-test-1"
    assert session.AuthUser(uid="u", email="a@b.co", display_name="Ravi Teja").first_name == "Ravi"
    assert session.AuthUser(uid="u", email="a@b.co").first_name == "a"
    assert session.AuthUser(uid="u", email="a@b.co").label == "a@b.co"


# ──────────────────────────────────────────────────────────────────────────
# Step 6B — email verification, account switching, the sidebar
# ──────────────────────────────────────────────────────────────────────────
def sign_in_as(app, email: str, password: str):
    app.text_input(key="auth_email").set_value(email)
    app.text_input(key="auth_password").set_value(password)
    app.button(key="auth_signin").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    return settle(app)


def test_an_unverified_account_cannot_sign_in_and_lands_on_verify(visitor, fake):
    app = run()
    app.text_input(key="auth_email").set_value("newbie@example.com")
    app.text_input(key="auth_password").set_value("Trailer2026")
    app.button(key="auth_signin").click().run()
    assert not app.exception
    assert "isn't verified yet" in body(app)          # the one-shot notice on that rerun
    app = settle(app)
    assert not in_the_app(app)
    assert "auth_user" not in app.session_state
    assert "You're almost in." in body(app)
    pend = app.session_state["auth_pending"]
    assert pend["email"] == "newbie@example.com"
    # No email was requested at sign-in, so nothing claims one was sent and
    # there is no cooldown: the button is live straight away.
    assert fake.verification_sent == [] and pend["sends"] == 0 and pend["cooldown_until"] == 0
    assert "Sent" not in body(app) and "AVAILABLE IN" not in body(app)
    assert next(b for b in app.button if b.key == "auth_resend").disabled is False
    assert "Trailer2026" not in repr(pend)


def resend_button(app):
    return next(b for b in app.button if b.key == "auth_resend")


def test_A_firebase_accepts_the_request_then_success_and_cooldown(visitor, fake):
    """HTTP 200 from sendOobCode → "sent" is shown and the 60s cooldown starts."""
    app = run()
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    assert resend_button(app).disabled is False
    resend_button(app).click().run()
    assert fake.calls[-1] == ("sendOobCode", {"requestType": "VERIFY_EMAIL", "idToken": "id.uid-newbie"})
    assert fake.verification_sent == ["newbie@example.com"]
    text = body(app)
    assert "Verification email sent to newbie@example.com" in text
    assert re.search(r"RESEND AVAILABLE IN (59|60)s", text)
    pend = app.session_state["auth_pending"]
    assert pend["sends"] == 1 and 55 < pend["cooldown_until"] - time.time() <= 60
    assert resend_button(app).disabled is True
    # Never more than the limit, however long you wait.
    app.session_state["auth_pending"] = {**pend, "cooldown_until": 0, "sends": 3}
    app = app.run()
    assert resend_button(app).disabled is True and "limit" in body(app)


def test_B_firebase_refuses_the_request_then_error_and_no_cooldown(visitor, fake):
    """A 4xx from sendOobCode → the safe error (status + code) is shown, no
    "sent", no cooldown, the button stays live. Both at sign-up and on resend."""
    fake.fail_with = None
    app = run(auth_mode="signup")
    app.text_input(key="auth_su_name").set_value("Arjun")
    app.text_input(key="auth_su_email").set_value("arjun@example.com")
    app.text_input(key="auth_su_password").set_value("Interval99")
    app.text_input(key="auth_su_confirm").set_value("Interval99")

    fake.fail_verify_with = "OPERATION_NOT_ALLOWED"       # only VERIFY_EMAIL is refused
    app.button(key="auth_signup").click().run()
    text = body(app)                                                # the rerun that shows the error
    assert "Firebase didn't accept the verification email request (HTTP 400, OPERATION_NOT_ALLOWED)" in text
    assert "Your account was created, but" in text
    app = settle(app)
    text = body(app)
    assert "You're almost in." in text                              # the account does exist
    assert "Sent" not in text and "AVAILABLE IN" not in text
    pend = app.session_state["auth_pending"]
    assert pend["sends"] == 0 and pend["cooldown_until"] == 0
    assert fake.verification_sent == []
    assert resend_button(app).disabled is False
    # Resend refused too: same honesty.
    resend_button(app).click().run()
    text = body(app)
    assert "(HTTP 400, OPERATION_NOT_ALLOWED)" in text and "Verification email sent" not in text
    assert app.session_state["auth_pending"]["sends"] == 0
    assert resend_button(app).disabled is False


def test_C_the_countdown_is_computed_from_the_absolute_deadline():
    from ui import login

    pend = {"email": "x@y.z", "cooldown_until": 1000.0, "sends": 1}
    assert [login._cooldown_remaining(pend, now=1000.0 - n) for n in (60, 59, 58, 2, 1, 0.4, 0)] == [60, 59, 58, 2, 1, 1, 0]
    assert login._cooldown_remaining(pend, now=1005.0) == 0            # a tab that slept past it
    assert login._cooldown_remaining(None) == 0


def test_C_the_countdown_on_screen_tracks_the_deadline(visitor, fake, monkeypatch):
    from ui import login

    app = run()
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    deadline = time.time() + 60
    app.session_state["auth_pending"] = {**app.session_state["auth_pending"], "cooldown_until": deadline, "sends": 1}
    seen = []
    for skew in (0.5, 1.5, 2.5):                                          # 59 → 58 → 57
        monkeypatch.setattr(login.time, "time", lambda skew=skew: deadline - 60 + skew)
        app = app.run()
        seen.append(re.search(r"RESEND AVAILABLE IN (\d+)s", body(app)).group(1))
        assert resend_button(app).disabled is True
    assert seen == ["60", "59", "58"]


def test_D_when_the_deadline_passes_resend_is_available(visitor, fake):
    app = run()
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    app.session_state["auth_pending"] = {**app.session_state["auth_pending"], "cooldown_until": time.time() - 1, "sends": 1}
    app = app.run()
    assert "RESEND AVAILABLE" in body(app) and "AVAILABLE IN" not in body(app)
    assert resend_button(app).disabled is False
    resend_button(app).click().run()
    assert fake.verification_sent == ["newbie@example.com"]
    assert app.session_state["auth_pending"]["sends"] == 2


def test_E_no_key_token_or_password_reaches_the_page_or_the_log(visitor, fake, capsys):
    app = run()
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    resend_button(app).click().run()                                      # a 200
    fake.fail_with = "TOO_MANY_ATTEMPTS_TRY_LATER"
    app.session_state["auth_pending"] = {**app.session_state["auth_pending"], "cooldown_until": 0}
    app = app.run()
    resend_button(app).click().run()                                      # a 400
    fake.fail_with = None
    log = capsys.readouterr().out
    page_text = " ".join(m.value for m in app.markdown)
    for secret in ("test-web-api-key", "id.uid-newbie", "refresh.uid-newbie", "Trailer2026"):
        assert secret not in log, secret
        assert secret not in page_text, secret
    # …while the safe diagnostic is in both.
    assert "[auth] sendOobCode VERIFY_EMAIL: HTTP 200 ok" in log
    assert "[auth] sendOobCode VERIFY_EMAIL: HTTP 400 TOO_MANY_ATTEMPTS_TRY_LATER" in log
    assert "(HTTP 400, TOO_MANY_ATTEMPTS_TRY_LATER)" in page_text


def test_once_verified_the_same_account_signs_in_normally(visitor, fake):
    app = run()
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    assert not in_the_app(app)
    app.button(key="auth_verify_back").click().run()
    assert "Welcome back." in body(app)
    fake.verify("newbie@example.com")                # the person clicked Firebase's link
    app = sign_in_as(app, "newbie@example.com", "Trailer2026")
    assert in_the_app(app)
    assert app.session_state["auth_user"].uid == "uid-newbie"
    assert "auth_pending" not in app.session_state    # the reset cleared it


def test_a_cookie_for_an_unverified_account_does_not_restore(monkeypatch, fake):
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.uid-newbie")
    app = run()
    assert on_login_page(app) and "auth_user" not in app.session_state
    assert [a for a, _ in fake.calls] == ["token", "lookup"]


def test_switching_accounts_starts_the_second_person_from_a_clean_session(visitor, fake):
    """Bug 1: Account A signs out, Account B signs in, in the same tab.
    Nothing of A — identity, wizard progress, page, flash — survives."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    assert app.session_state["auth_user"].uid == "uid-ravi"
    # Ravi gets some way into the wizard and leaves a flash behind.
    app.button(key="loc_hyderabad").click().run()
    assert app.session_state["step"] == 2 and app.session_state["location"] == "hyderabad"
    app.session_state["flash"] = ("success", "Ravi's own message")
    app.session_state["furthest"] = 4
    app.session_state["_synced_once"] = True
    app.session_state["page"] = "Settings"
    app = app.run()

    app.button(key="auth_signout").click().run()
    app = settle(app)
    assert on_login_page(app)
    for key in ("auth_user", "location", "movie_id", "theatres", "flash", "_synced_once", "furthest"):
        assert key not in app.session_state, key

    app = sign_in_as(app, "sita@example.com", "Interval99")
    assert in_the_app(app)
    user = app.session_state["auth_user"]
    assert user.uid == "uid-sita" and user.email == "sita@example.com"
    assert app.session_state["step"] == 1 and app.session_state["location"] == ""
    assert app.session_state["page"] == "Home"
    assert "Ravi's own message" not in body(app)
    assert "Ravi Teja" not in body(app) and "Sita Devi" in body(app)


def test_signing_in_twice_replaces_the_identity_completely(visitor, fake):
    """No sign-out in between: a second sign-in still starts clean."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    app.button(key="loc_hyderabad").click().run()
    app.session_state["auth_restore_tried"] = True
    # Force the login page without signing out (the way a forged or stale
    # session would look) and sign in as somebody else.
    del app.session_state["auth_user"]
    app = app.run()
    assert on_login_page(app)
    app = sign_in_as(app, "sita@example.com", "Interval99")
    assert app.session_state["auth_user"].uid == "uid-sita"
    assert app.session_state["step"] == 1 and app.session_state["location"] == ""


def test_legacy_json_monitors_are_global_until_firestore(visitor, fake, make_monitor):
    """Bug 2, documented honestly. Two different things are asserted here:

    A. authentication/session leakage — fixed: Account B's session carries
       B's identity only, and nothing of A's UI state;
    B. the legacy store — *not* per user: ``data/monitors.json`` and
       ``data/history.json`` are one global file each, read by every
       signed-in person. Permanent per-user data isolation requires the
       upcoming Firestore migration (step 7). This test pins that so nobody
       mistakes today's behaviour for isolation.
    """
    from monitor.state import record_history, upsert_monitor

    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    monitor = make_monitor(email="ravi@example.com")
    upsert_monitor(monitor, mirror=False)
    record_history(monitor, "CREATED", "Monitor created.", mirror=False)

    app.button(key="auth_signout").click().run()
    app = settle(app)
    app = sign_in_as(app, "sita@example.com", "Interval99")

    # A — the session is Sita's and only Sita's.
    assert app.session_state["auth_user"].uid == "uid-sita"
    assert "ravi@example.com" not in body(app).replace("watcher@example.com", "")  # identity, not monitor data
    # B — the legacy global store is still what My Monitors and History read.
    app.session_state["page"] = "My Monitors"
    app = app.run()
    assert "Avengers: Endgame Encore" in body(app)                 # Ravi's monitor, visible to Sita
    app.session_state["page"] = "History"
    app = app.run()
    assert "Avengers: Endgame Encore" in body(app)                 # …and so is his history


# ──────────────────────────────────────────────────────────────────────────
# The sidebar and the pages behind the gate
# ──────────────────────────────────────────────────────────────────────────
def test_the_sidebar_is_the_original_with_the_account_row_at_the_foot():
    app = run()
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert '<div class="tr-logo">' in side and "Ticket<em>Radar</em>" in side
    [nav] = app.sidebar.radio
    assert nav.key == "page" and list(nav.options) == ["Home", "My Monitors", "History", "Settings"]
    assert nav.value == "Home"
    assert "tr-quote" in side and "tr-version" in side
    # The account row: initial, name, address — then the menu.
    assert '<div class="tr-acct">' in side
    assert '<div class="av">T</div>' in side and "Test Person" in side and "tester@example.com" in side
    assert "Account" in side
    # Order: brand, nav, foot, account — the account row is last.
    assert side.index("tr-logo") < side.index("tr-quote") < side.index("tr-acct")
    # No standalone Sign out in the nav: it lives in the menu, with Account settings.
    assert {b.key for b in app.sidebar.button} == {"acct_settings", "auth_signout"}


def test_account_settings_opens_the_existing_settings_page():
    app = run()
    app.button(key="acct_settings").click().run()
    assert not app.exception
    assert app.session_state["page"] == "Settings"
    assert "Where alerts go" in body(app)


def test_the_entrance_never_touches_the_sidebar():
    """The broken sidebar after sign-in: an animated ``transform`` on the
    sidebar overrode the translateX Streamlit uses to slide it away."""
    from ui import login

    assert "stSidebar" not in login.ENTRANCE_CSS
    assert "transform" not in login.ENTRANCE_CSS


@pytest.mark.parametrize("page, marker", [
    ("Home", "Where are you watching?"),
    ("My Monitors", "Everything you've asked us to watch."),
    ("History", "What the radar has picked up."),
    ("Settings", "Where alerts go, and what's wired up."),
])
def test_every_page_is_reachable_once_signed_in(visitor, fake, page, marker):
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    app.session_state["page"] = page
    app = app.run()
    assert not app.exception, [str(e) for e in app.exception]
    assert marker in body(app)
    assert app.session_state["auth_user"].uid == "uid-ravi"


def test_the_identity_is_never_read_from_a_form_url_or_monitor(visitor, fake, make_monitor):
    """Bug 7: nothing but Firebase's answer decides who is signed in."""
    from monitor.state import upsert_monitor

    upsert_monitor(make_monitor(email="someone-else@example.com"), mirror=False)
    app = run(auth_email="forged@example.com", notify_email="forged@example.com",
              settings_email="forged@example.com")
    assert on_login_page(app)
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    user = app.session_state["auth_user"]
    assert (user.uid, user.email) == ("uid-ravi", "ravi@example.com")
    assert session.current_uid.__module__ == "auth.session"
    # The sidebar shows Firebase's account, not any of the addresses above.
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert "ravi@example.com" in side
    assert "forged@example.com" not in side and "someone-else@example.com" not in side


def test_refresh_tokens_never_reach_the_page(visitor, fake):
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    page_text = " ".join(m.value for m in app.markdown)
    assert "refresh.uid-ravi" not in page_text and "id.uid-ravi" not in page_text
    assert app.session_state["auth_user"].refresh_token == "refresh.uid-ravi"   # server side only
    assert "refresh.uid-ravi" not in repr(app.session_state["auth_user"])       # …and out of logs


def test_no_secret_lives_in_a_tracked_file():
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    assert ".streamlit/secrets.toml" not in tracked and ".env" not in tracked
    import re

    for path in tracked:
        if path.endswith((".py", ".toml", ".md", ".yml", ".example")):
            text = open(path, encoding="utf-8", errors="ignore").read()
            assert not re.search(r"AIza[0-9A-Za-z_\-]{30,}", text), path       # a real Web API key
            assert not re.search(r"github_pat_[0-9A-Za-z_]{20,}", text), path   # a real PAT


# ──────────────────────────────────────────────────────────────────────────
# The boundary the worker keeps
# ──────────────────────────────────────────────────────────────────────────
def test_the_worker_never_imports_auth():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, run_monitor, monitor.worker, monitor.checker, monitor.discovery, monitor.changes;"
         "assert not [m for m in sys.modules if m == 'auth' or m.startswith('auth.')], 'auth leaked into the worker';"
         "print('OK')"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_login_page_never_prints_a_secret(visitor, monkeypatch, fake):
    monkeypatch.setenv("GH_TOKEN", "github_pat_secret_value")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "gmail-secret-value")
    app = run()
    text = body(app)
    for secret in ("test-web-api-key", "github_pat_secret_value", "gmail-secret-value"):
        assert secret not in text
