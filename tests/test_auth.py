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

import json
import re
import time

import pytest

from auth import firebase, session
from auth.firebase import AuthError
from tests.conftest import APP_SCRIPT

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
#: Captured before any fixture swaps it out (module import precedes fixtures).
REAL_RESTORE = session.restore


# ──────────────────────────────────────────────────────────────────────────
# A fake Identity Toolkit
# ──────────────────────────────────────────────────────────────────────────
class FakeFirebase:
    """Accounts in a dict; responses shaped like Google's."""

    #: Appended to every refresh token. Real Firebase refresh tokens contain
    #: characters ``encodeURIComponent`` escapes (``/``, ``+``, ``=``); the
    #: browser harness sets this so a cookie round trip is exercised for real.
    #: Left empty here so the suite's exact-token assertions stay readable.
    token_suffix: str = ""

    def __init__(self):
        self.accounts: dict[str, dict] = {}     # email -> {password, uid, name, verified}
        self.calls: list[tuple[str, dict]] = []
        self.reset_requests: list[str] = []
        self.verification_sent: list[str] = []   # emails a VERIFY_EMAIL went to
        self.fail_with: str | None = None        # force one Firebase error code
        self.fail_verify_with: str | None = None  # …or only for VERIFY_EMAIL
        self.fail_delete_with: str | None = None  # …or only for accounts:delete
        self.deleted: list[str] = []             # emails whose account was deleted
        #: Google identities Firebase would accept: google_id_token -> profile.
        self.google: dict[str, dict] = {}
        self.google_enabled: bool = True
        #: The project's "one account per email address" setting (the default).
        #: Off, Firebase would mint a second UID for a Google sign-in whose
        #: email already has a password account — the duplicate the app must
        #: never let happen. Kept so a test can show what that would look like.
        self.one_account_per_email: bool = True
        #: Firebase asks for confirmation instead of linking (a non-trusted
        #: provider's collision). Set by a test to exercise that answer.
        self.need_confirmation: bool = False
        #: Whether the account record ends up ``emailVerified`` after a Google
        #: sign-in. Firebase does mark it so; a test turns this off to prove
        #: the app takes Firebase's word for it, not the provider's name.
        self.google_marks_verified: bool = True

    def refresh_token_for(self, uid: str) -> str:
        return f"refresh.{uid}{self.token_suffix}"

    def add(self, email: str, password: str, *, uid: str = "uid-1", name: str = "", verified: bool = True) -> None:
        self.accounts[email] = {"password": password, "uid": uid, "name": name, "verified": verified}

    def add_google_identity(self, id_token: str, *, email: str, name: str = "",
                            photo: str = "", sub: str = "") -> None:
        """A Google account Firebase would verify an ID token for."""
        self.google[id_token] = {"email": email, "name": name, "photo": photo,
                                 "sub": sub or f"g-{email}"}

    def set_claims(self, email: str, claims: dict | None) -> None:
        """What an administrator's ``setCustomUserClaims`` does to the account
        record. There is no client call that can — only this, on the fake, as
        only the Admin SDK can on the real thing."""
        self.accounts[email]["claims"] = claims

    def verify(self, email: str) -> None:
        """What clicking Firebase's link does."""
        self.accounts[email]["verified"] = True

    def _by_token(self, id_token: str):
        for email, acct in self.accounts.items():
            if f"id.{acct['uid']}" == id_token:
                return acct.get("email", email), acct
        return None, None

    @staticmethod
    def _error(code: str, status: int = 400):
        return status, {"error": {"code": status, "message": code, "errors": [{"message": code}]}}

    def _sign_in_with_idp(self, payload: dict) -> tuple[int, dict]:
        """``accounts:signInWithIdp`` as Firebase answers it for Google.

        Firebase verifies the ID token with Google and then, under *one
        account per email address*, treats Google as a trusted provider: an
        email that already has a password account resolves to **that
        account's UID** with google.com linked. It never mints a second UID
        in that mode. With the setting off it would — modelled so the test
        can show why the setting matters.
        """
        if not self.google_enabled:
            return self._error("OPERATION_NOT_ALLOWED")
        if not payload.get("requestUri", "").startswith("http"):
            return self._error("INVALID_IDP_RESPONSE")
        body = dict(part.split("=", 1) for part in payload.get("postBody", "").split("&") if "=" in part)
        if body.get("providerId") != "google.com":
            return self._error("INVALID_CREDENTIAL_OR_PROVIDER_ID")
        profile = self.google.get(body.get("id_token", ""))
        if profile is None:
            return self._error("INVALID_IDP_RESPONSE")
        email = profile["email"]
        acct = self.accounts.get(email)
        if self.need_confirmation and acct is not None:
            return 200, {"needConfirmation": True, "email": email, "verifiedProvider": ["password"]}
        new_user = False
        if acct is None or (not self.one_account_per_email and "google.com" not in acct.get("providers", [])):
            new_user = True
            uid = f"uid-google-{profile['sub']}"
            key = email if acct is None else f"{email}#google"
            self.accounts[key] = {"password": None, "uid": uid, "name": profile["name"],
                                  "verified": self.google_marks_verified,
                                  "providers": ["google.com"], "email": email}
            acct = self.accounts[key]
        else:
            acct["verified"] = acct["verified"] or self.google_marks_verified
            acct.setdefault("providers", ["password"])
            if "google.com" not in acct["providers"]:
                acct["providers"].append("google.com")
        return 200, {
            "localId": acct["uid"], "email": email, "displayName": profile["name"] or acct["name"],
            "photoUrl": profile["photo"], "emailVerified": acct["verified"], "providerId": "google.com",
            "idToken": f"id.{acct['uid']}", "refreshToken": self.refresh_token_for(acct["uid"]),
            "expiresIn": "3600", "isNewUser": new_user, "federatedId": f"https://accounts.google.com/{profile['sub']}",
        }

    def _creds(self, email: str) -> dict:
        acct = self.accounts[email]
        return {"localId": acct["uid"], "email": email, "displayName": acct["name"],
                "idToken": f"id.{acct['uid']}", "refreshToken": self.refresh_token_for(acct["uid"]),
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
        if action == "signInWithIdp":
            return self._sign_in_with_idp(payload)
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
        if action == "delete":
            email, acct = self._by_token(payload.get("idToken", ""))
            if acct is None:
                return self._error("INVALID_ID_TOKEN")
            if self.fail_delete_with:
                return self._error(self.fail_delete_with)
            del self.accounts[email]
            self.deleted.append(email)
            return 200, {"kind": "identitytoolkit#DeleteAccountResponse"}
        if action == "lookup":
            email, acct = self._by_token(payload.get("idToken", ""))
            if acct is None:
                return self._error("INVALID_ID_TOKEN")
            user = {"localId": acct["uid"], "email": email, "displayName": acct["name"],
                    "emailVerified": acct["verified"]}
            if acct.get("claims") is not None:
                # Google reports custom claims as a JSON *string*.
                user["customAttributes"] = json.dumps(acct["claims"])
            return 200, {"users": [user]}
        if action == "token":
            for acct in self.accounts.values():
                if self.refresh_token_for(acct["uid"]) == payload.get("refresh_token"):
                    return 200, {"user_id": acct["uid"], "id_token": f"id.{acct['uid']}",
                                 "refresh_token": self.refresh_token_for(acct["uid"]), "expires_in": "3600"}
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
    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)   # no browser in AppTest


@pytest.fixture
def bare_session(monkeypatch):
    """Drive ``auth.session`` outside a Streamlit script run.

    Its module-level ``st`` is replaced by a stand-in whose ``session_state``
    is a plain dict and whose ``context`` carries no cookies, so the cookie
    readers and the restore guard can be exercised directly.
    """
    import types

    state: dict = {}
    stub = types.SimpleNamespace(
        session_state=state,
        context=types.SimpleNamespace(cookies={}, headers={}),
    )
    monkeypatch.setattr(session, "st", stub)
    return state


def run(**state):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
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
    assert "Unable to sign in with those credentials." in firebase.explain("INVALID_LOGIN_CREDENTIALS")
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
                                             "display_name": "Ravi Teja", "email_verified": True,
                                             "admin": False}
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


def test_the_bridge_component_is_served_and_speaks_the_protocol():
    """The bridge is a real Streamlit component, not injected HTML: it answers
    the render message, posts its value back and keeps itself at zero height."""
    from pathlib import Path as _Path

    html = (_Path("auth") / "bridge" / "index.html").read_text(encoding="utf-8")
    assert "streamlit:componentReady" in html
    assert "streamlit:render" in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:setFrameHeight" in html and "height: 0" in html
    # It must not re-post an unchanged value — that is how a loop starts.
    assert "lastSent" in html and "encoded !== lastSent" in html
    # Both SameSite branches, and Secure whenever https.
    assert "SameSite=None" in html and "SameSite=Lax" in html and "Secure" in html


def test_the_auth_path_no_longer_injects_raw_html():
    """The deprecated ``st.components.v1.html`` is gone from the auth path —
    the bridge is the only thing that touches the browser now."""
    from pathlib import Path as _Path

    for name in ("auth/session.py", "auth/gate.py"):
        src = _Path(name).read_text(encoding="utf-8")
        assert "components.v1.html" not in src, name
        assert "components.html(" not in src, name


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
    assert {"SIGN IN", "Continue with Google", "Forgot password?", "CREATE ACCOUNT"} <= labels


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
    # set_page_config → the CSS → the store learns how to find the signed-in
    # user (no rendering) → main(), whose first statement is the gate.
    assert calls == ["st.set_page_config", "inject", "state_store.set_scope_provider", "main"]
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
    assert "You're almost in." in text and "VERIFY YOUR EMAIL" in text
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
    assert body(app).count("Enter your email address first.") == 1
    assert {t.key for t in app.text_input} == {"auth_email", "auth_password"}
    app.text_input(key="auth_email").set_value("ravi@example.com")
    app.button(key="auth_forgot").click().run()
    text = body(app)
    assert "Reset your password." in text
    assert "Enter your email and we&#x27;ll send you a secure reset link." in text or \
        "Enter your email and we'll send you a secure reset link." in text
    assert {t.key for t in app.text_input} == {"auth_reset_email"}
    app.text_input(key="auth_reset_email").set_value("ravi@example.com")
    app.button(key="auth_send_reset").click().run()
    assert not app.exception
    assert "Check your inbox." in body(app)
    assert "If an account exists for this email" in body(app)
    assert fake.reset_requests == ["ravi@example.com"]
    assert {t.key for t in app.text_input} == set()          # nothing left to type


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
# Step 6B — email verification and the sidebar
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
    assert "Inbox, Spam, or Promotions" in text                 # never a claim of Inbox delivery
    assert re.search(r"RESEND AVAILABLE IN (59|60)s", text)
    assert "LAST REQUEST HTTP 200 OK" not in text              # diagnostics stay in logs
    assert app.session_state["auth_pending"]["last_answer"]["status"] == 200
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
    assert "the verification email couldn't be sent: OPERATION_NOT_ALLOWED (HTTP 400)" in text
    assert "Your account was created, but" in text
    assert "accepted" not in text.lower()
    app = settle(app)
    text = body(app)
    assert "You're almost in." in text                                           # the account does exist
    assert "Sent" not in text and "AVAILABLE IN" not in text
    pend = app.session_state["auth_pending"]
    assert pend["sends"] == 0 and pend["cooldown_until"] == 0
    assert fake.verification_sent == []
    assert resend_button(app).disabled is False
    # Resend refused too: same honesty.
    resend_button(app).click().run()
    text = body(app)
    assert "verification email couldn't be sent: OPERATION_NOT_ALLOWED (HTTP 400)" in text
    assert "accepted" not in text.lower()
    assert app.session_state["auth_pending"]["sends"] == 0
    assert "LAST REQUEST HTTP 400 OPERATION_NOT_ALLOWED" not in body(app)
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


def test_E_no_key_token_or_password_reaches_the_page_or_the_log(visitor, fake, capsys, monkeypatch):
    monkeypatch.setattr(firebase, "_config_logged", False)      # the once-per-process line, again
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
    assert "[auth] sendOobCode VERIFY_EMAIL: HTTP 200 OK" in log
    assert "[auth] sendOobCode VERIFY_EMAIL: HTTP 400 TOO_MANY_ATTEMPTS_TRY_LATER" in log
    assert "TOO_MANY_ATTEMPTS_TRY_LATER (HTTP 400)" in page_text
    # The config line names the project and only the *presence* of the key.
    assert "[auth] Firebase config: project_id=" in log and "api_key_present=True" in log
    assert "newbie@example.com" not in log                          # no address in the log either


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


def test_a_cookie_survives_the_percent_encoding_round_trip(monkeypatch):
    """The refresh-token cookie is written with ``encodeURIComponent`` and read
    back by Streamlit *undecoded*. A real Firebase refresh token contains ``/``,
    ``+`` and ``=``, so without a decode on the way in the token handed back to
    Google was ``AMf-vBx%2F…`` — INVALID_REFRESH_TOKEN, and every browser
    refresh signed the person out. This is that bug, pinned.
    """
    from urllib.parse import quote

    import streamlit as st

    raw = "AMf-vBx/abc+def=ghi_jkl-mno"
    stored = {session.COOKIE: quote(raw, safe=""),
              session.SESSION_STARTED_COOKIE: quote("1789561923.5", safe="")}

    class Ctx:
        cookies = stored

    monkeypatch.setattr(st, "context", Ctx())
    assert stored[session.COOKIE] != raw                      # the browser really did escape it
    assert session._cookie() == raw                           # …and we hand Google the real token
    assert session._cookie_started() == 1789561923.5


def test_a_refresh_does_not_sign_the_user_out_and_keeps_the_deadline(visitor, fake, monkeypatch):
    """Issue #3: a browser refresh restores the session from the cookie and
    never signs the user out, and it never *extends* the seven-day deadline."""
    import time as _time

    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    assert in_the_app(app)
    started = app.session_state[session.SESSION_STARTED_KEY]
    expires = app.session_state[session.SESSION_EXPIRES_KEY]
    assert expires == started + session.SESSION_SECONDS

    # A refresh two days later restores from the cookie (Google re-validates
    # the refresh token) and lands back in the app — not on the login page.
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.uid-ravi")
    monkeypatch.setattr(session, "_cookie_started", lambda: started)
    monkeypatch.setattr(_time, "time", lambda: started + 2 * 86400)
    fresh = run()                                    # a brand-new browser session
    assert in_the_app(fresh)
    assert fresh.session_state["auth_user"].uid == "uid-ravi"
    # The absolute deadline is unchanged: a refresh does not slide it forward.
    assert fresh.session_state[session.SESSION_EXPIRES_KEY] == expires


def test_the_seven_day_session_deadline_is_absolute(visitor, fake, monkeypatch):
    """Issue #3: once the seven-day deadline passes, the next run signs out —
    even though the Firebase refresh token would still be accepted."""
    import time as _time

    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    started = app.session_state[session.SESSION_STARTED_KEY]

    # A cookie restore attempted after the deadline is refused before Google
    # is ever asked.
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.uid-ravi")
    monkeypatch.setattr(session, "_cookie_started", lambda: started)
    monkeypatch.setattr(_time, "time", lambda: started + session.SESSION_SECONDS + 1)
    expired = run()
    assert on_login_page(expired)
    assert "auth_user" not in expired.session_state
    # The token exchange never happened: the deadline is enforced locally.
    assert not any(action == "token" for action, _ in fake.calls)


def test_the_verify_screen_has_no_i_have_verified_button(visitor, fake):
    """Issue #5: the manual "I've verified" button is gone — the tab continues
    on its own — and so is the BACK-only requirement to report verification."""
    app = run()
    app.button(key="auth_to_signup").click().run()               # switch to Create account
    app.text_input(key="auth_su_name").set_value("New Person")
    app.text_input(key="auth_su_email").set_value("fresh@example.com")
    app.text_input(key="auth_su_password").set_value("Popcorn2026")
    app.text_input(key="auth_su_confirm").set_value("Popcorn2026")
    app.button(key="auth_signup").click().run()
    app = settle(app)
    assert "VERIFY YOUR EMAIL" in body(app)
    keys = {b.key for b in app.button}
    assert "auth_continue" not in keys                            # no "I'VE VERIFIED" button
    assert {"auth_resend", "auth_verify_back"} <= keys            # resend + back only


def test_a_verified_return_continues_into_the_app_automatically(visitor, fake):
    """Issue #5: coming back from Firebase's verification link, with the
    browser still holding the refresh credential, drops straight into the app
    — no password re-entry, no "I've verified" click."""
    # An account that has just verified, whose tab still has the pending token.
    fake.add("back@example.com", "Popcorn2026", uid="uid-back", name="Back Person", verified=True)

    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state[session.PENDING_KEY] = {
        "email": "back@example.com", "display_name": "Back Person",
        "id_token": "id.uid-back", "refresh_token": "refresh.uid-back",
        "cooldown_until": 0.0, "sends": 1, "checked_at": 0.0,
    }
    app.query_params["verified"] = "back@example.com"
    app.run()
    # handle_verified_return() → continue_if_verified() signed the account in
    # from its refresh token, and the app is what renders.
    assert "auth_user" in app.session_state
    assert app.session_state["auth_user"].uid == "uid-back"
    assert in_the_app(app)


def test_login_and_app_are_never_rendered_together(visitor, fake):
    """Issue #2: the login shell and the app never coexist in one render, and
    the whole page lives in the gate's fixed ``tr_page`` slot so a keyed
    container from the previous role can't linger."""
    # Unauthenticated: the login shell, no app.
    app = run()
    assert on_login_page(app) and not in_the_app(app)
    assert "tr-acct-chip" not in body(app)                       # no account chip on the login page

    # Signed in: the app, no login shell, exactly one account chip.
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    text = body(app)
    assert in_the_app(text_app := app) is not None               # keep the app handle
    assert "Welcome back." not in text and "Create your account." not in text
    assert text.count('<div class="tr-acct-chip">') == 1


def test_the_gate_reserves_the_fixed_page_slot():
    """Issue #2: both roles render inside the same fixed ``tr_page`` container,
    which is what keeps a login⇄app transition a clean DOM swap."""
    import ast
    from pathlib import Path

    src = Path("auth/gate.py").read_text(encoding="utf-8")
    assert 'st.container(key="tr_chrome")' in src
    assert 'st.container(key="tr_page")' in src
    # app.py renders the app inside that reserved slot.
    app_src = Path("app.py").read_text(encoding="utf-8")
    assert "with app_container():" in app_src
    ast.parse(src)


def test_json_store_isolates_monitors_and_history_per_account(visitor, fake, make_monitor):
    """Per-user isolation in the JSON compatibility store (issue #6).

    Even without Firestore enabled, ``data/monitors.json`` and
    ``data/history.json`` are scoped by the Firebase UID that owns each record.
    Two things are asserted:

    A. authentication/session leakage — Account B's session carries B's
       identity only, and nothing of A's UI state;
    B. data isolation — a monitor and its history created for Ravi are *not*
       visible to Sita on My Monitors or History.
    """
    from monitor.state import record_history, upsert_monitor

    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    # Ravi's own monitor, stamped with his UID (as the app's own start flow does).
    monitor = make_monitor(email="ravi@example.com", owner_uid="uid-ravi")
    upsert_monitor(monitor, mirror=False)
    record_history(monitor, "CREATED", "Monitor created.", mirror=False)

    app.button(key="auth_signout").click().run()
    app = settle(app)
    app = sign_in_as(app, "sita@example.com", "Interval99")

    # A — the session is Sita's and only Sita's.
    assert app.session_state["auth_user"].uid == "uid-sita"
    assert "ravi@example.com" not in body(app).replace("watcher@example.com", "")  # identity, not monitor data
    # B — Ravi's monitor and history are invisible to Sita.
    app.session_state["page"] = "My Monitors"
    app = app.run()
    assert "Avengers: Endgame Encore" not in body(app)             # Ravi's monitor, hidden from Sita
    app.session_state["page"] = "History"
    app = app.run()
    assert "Avengers: Endgame Encore" not in body(app)             # …and so is his history


# ──────────────────────────────────────────────────────────────────────────
# The sidebar and the pages behind the gate
# ──────────────────────────────────────────────────────────────────────────
def test_the_sidebar_is_navigation_only_and_the_account_control_is_top_right():
    app = run()
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert '<div class="tr-logo">' in side and "Ticket<em>Radar</em>" in side
    [nav] = app.sidebar.radio
    assert nav.key == "page" and list(nav.options) == ["Home", "My Monitors", "History", "Settings"]
    assert nav.value == "Home"
    assert "tr-quote" in side and "tr-version" in side
    # Nothing about the account in the sidebar: no chip, no menu, no buttons.
    assert "tr-acct" not in side and "tester@example.com" not in side and "Test Person" not in side
    assert not app.sidebar.button
    # The account control is in the main content: a chip with the initial and
    # first name, and a menu holding Account settings and Sign out.
    main = " ".join(m.value for m in app.main.markdown)
    assert '<div class="tr-acct-chip">' in main
    assert '<div class="av">T</div>' in main and '<div class="n">Test</div>' in main
    assert "tester@example.com" in main                         # inside the menu
    assert {b.key for b in app.main.button} >= {"acct_settings", "auth_signout"}
    # …and it is drawn before the page, i.e. at the top.
    assert main.index("tr-acct-chip") < main.index('<div class="tr-hero">')


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
    # The account menu shows Firebase's account, not any of the addresses above.
    main = " ".join(m.value for m in app.main.markdown)
    assert "ravi@example.com" in main and '<div class="n">Ravi</div>' in main
    assert "forged@example.com" not in main and "someone-else@example.com" not in main


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


# ──────────────────────────────────────────────────────────────────────────
# Delete account
# ──────────────────────────────────────────────────────────────────────────
def _seed_for(uid: str, make_monitor, email: str):
    """One monitor, its state, history and settings, all owned by ``uid``."""
    from monitor import state as state_mod

    monitor = make_monitor(email=email, owner_uid=uid)
    _as(uid)
    state_mod.upsert_monitor(monitor, mirror=False)
    state_mod.save_state({monitor.id: state_mod.MonitorState(check_count=3)}, mirror=False)
    state_mod.record_history(monitor, "CREATED", "Monitor created.", mirror=False)
    state_mod.save_settings({"notify_email": email, "default_interval": 15}, mirror=False)
    return monitor


def _as(uid: str):
    from monitor import state as state_mod

    state_mod.set_scope_provider(
        lambda: state_mod.Scope("", uid, lambda: "", firestore_enabled=False))


def test_delete_account_removes_only_that_owners_data(make_monitor):
    """Monitors, state, history and settings for the signed-in UID go; another
    account's records are untouched."""
    from monitor import state as state_mod

    try:
        a = _seed_for("uid-a", make_monitor, "a@example.com")
        b = _seed_for("uid-b", make_monitor, "b@example.com")

        _as("uid-a")
        removed = state_mod.purge_user_data(mirror=False)
        assert removed["monitors"] == 1 and removed["history"] == 1 and removed["settings"] == 1
        assert state_mod.load_monitors() == []
        assert state_mod.load_state() == {}
        assert state_mod.load_history() == []
        assert state_mod.load_settings().get("notify_email") != "a@example.com"

        # B is entirely unaffected.
        _as("uid-b")
        assert [m.id for m in state_mod.load_monitors()] == [b.id]
        assert state_mod.load_state()[b.id].check_count == 3
        assert [h["message"] for h in state_mod.load_history()] == ["Monitor created."]
        assert state_mod.load_settings()["notify_email"] == "b@example.com"
        assert a.id != b.id
    finally:
        state_mod.set_scope_provider(None)


def test_delete_account_leaves_the_shared_catalogue_alone(make_monitor, provider_factory, listing_url):
    """User-owned data goes; the catalogue every account browses does not."""
    from monitor import catalogue, state as state_mod

    try:
        from tests.conftest import build_payload

        catalogue.store_snapshot(provider_factory([build_payload([])]).resolve(listing_url),
                                 mirror=False)
        before = len(catalogue.load_catalogue().get("movies", []))
        assert before >= 1

        _seed_for("uid-a", make_monitor, "a@example.com")
        _as("uid-a")
        state_mod.purge_user_data(mirror=False)

        assert len(catalogue.load_catalogue().get("movies", [])) == before
    finally:
        state_mod.set_scope_provider(None)


def test_delete_account_uses_the_authenticated_uid_not_an_argument():
    """``purge_user_data`` and ``delete_account`` take no account identifier:
    who gets deleted comes from the scope/session only, never from the page."""
    import inspect

    from monitor import state as state_mod

    for fn in (state_mod.purge_user_data, session.delete_account):
        positional = [p for p in inspect.signature(fn).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        assert positional == [], f"{fn.__name__} must not accept an account identifier"
    # The Firebase call is authorised by the ID token, never by a supplied UID.
    src = inspect.getsource(firebase.FirebaseAuth.delete_account)
    assert '"idToken": id_token' in src


def test_delete_account_signs_out_and_clears_the_cookie(visitor, fake):
    """The whole flow through the app: confirm, delete, land on the login page
    with the session gone and the cookie told to clear."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    assert in_the_app(app)

    app.button(key="acct_delete").click().run()                  # opens the panel
    assert "Delete your account?" in body(app)
    assert app.session_state["acct_delete_open"] is True
    assert "auth_user" in app.session_state                      # nothing deleted yet

    # The button is inert until DELETE is typed.
    assert next(b for b in app.button if b.key == "acct_delete_confirm").disabled is True
    app.text_input(key="acct_delete_word").set_value("DELETE").run()
    assert next(b for b in app.button if b.key == "acct_delete_confirm").disabled is False

    app.button(key="acct_delete_confirm").click().run()
    assert fake.deleted == ["ravi@example.com"]                  # Firebase account gone
    assert "ravi@example.com" not in fake.accounts
    assert "auth_user" not in app.session_state                  # session cleared
    # sign_out() queued the cookie clear and the gate has already emitted it,
    # so the flag is consumed rather than left behind.
    assert "auth_cookie_clear" not in app.session_state
    assert "auth_restore_tried" in app.session_state             # no silent restore next paint
    app = settle(app)
    assert on_login_page(app) and not in_the_app(app)


def test_cancelling_delete_keeps_the_account(visitor, fake):
    """Cancel closes the panel and deletes nothing."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    app.button(key="acct_delete").click().run()
    assert "Delete your account?" in body(app)
    app.button(key="acct_delete_cancel").click().run()
    assert app.session_state["acct_delete_open"] is False
    assert "Delete your account?" not in body(app)
    assert fake.deleted == [] and "ravi@example.com" in fake.accounts
    assert in_the_app(app) and app.session_state["auth_user"].uid == "uid-ravi"


def test_a_deleted_account_does_not_come_back_on_refresh(visitor, fake, monkeypatch):
    """After deletion the cookie's refresh token is refused by Firebase, so a
    reload lands on the login page rather than restoring the dead account."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    app.button(key="acct_delete").click().run()
    app.text_input(key="acct_delete_word").set_value("DELETE").run()
    app.button(key="acct_delete_confirm").click().run()
    settle(app)
    assert fake.deleted == ["ravi@example.com"]

    # A fresh browser session still holding the old cookie.
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: fake.refresh_token_for("uid-ravi"))
    monkeypatch.setattr(session, "_cookie_started", lambda: time.time())
    reloaded = run()
    assert on_login_page(reloaded) and "auth_user" not in reloaded.session_state


def test_firebase_asking_for_a_recent_sign_in_is_shown_not_bypassed(visitor, fake):
    """Firebase can demand a fresh sign-in before a destructive change. That is
    surfaced as a message; the flow never works around it."""
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    fake.fail_delete_with = "CREDENTIAL_TOO_OLD_LOGIN_AGAIN"
    app.button(key="acct_delete").click().run()
    app.text_input(key="acct_delete_word").set_value("DELETE").run()
    app.button(key="acct_delete_confirm").click().run()
    assert fake.deleted == []
    assert "ravi@example.com" in fake.accounts                   # still there
    # The reason is shown as an error banner (st.error), not swallowed. The
    # click's own run already drained it, so it is read here.
    shown = body(app) + " ".join(e.value for e in app.error)
    assert "Please sign in again to confirm account deletion." in shown
    assert app.session_state["acct_delete_open"] is False         # panel closed, nothing retried
    assert in_the_app(app)                                        # still signed in


def test_the_account_menu_holds_settings_delete_and_sign_out():
    """Issue #10: the top-right menu — and nothing account-related in the sidebar."""
    app = run()
    assert {b.key for b in app.main.button} >= {"acct_settings", "acct_delete", "auth_signout"}
    side = " ".join(m.value for m in app.sidebar.markdown)
    assert "tr-acct" not in side and not app.sidebar.button


def test_isolation_holds_in_both_directions_across_a_switch(visitor, fake, make_monitor):
    """Issue #9, pinned in both directions and without a reload in between.

        A signs in  → sees only A's monitor and history
        A signs out → B signs in → sees only B's
        B signs out → A signs in → sees only A's, and none of B's
    """
    from dataclasses import replace

    from monitor.state import record_history, upsert_monitor

    def seed(uid: str, email: str, title: str):
        monitor = make_monitor(email=email, owner_uid=uid)
        monitor.movie = replace(monitor.movie, title=title)
        upsert_monitor(monitor, mirror=False)
        record_history(monitor, "CREATED", f"{title} created.", mirror=False)
        return monitor

    seed("uid-ravi", "ravi@example.com", "Ravi's Film")
    seed("uid-sita", "sita@example.com", "Sita's Film")

    def sees(app) -> tuple[bool, bool]:
        """(sees Ravi's, sees Sita's) across My Monitors and History."""
        seen = ""
        for page in ("My Monitors", "History"):
            app.session_state["page"] = page
            seen += body(app.run())
        return "Ravi's Film" in seen, "Sita's Film" in seen

    # A
    app = run()
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    assert sees(app) == (True, False)

    # A → B, in the same tab, no reload
    app.session_state["page"] = "Home"
    app = app.run()
    app.button(key="auth_signout").click().run()
    app = settle(app)
    app = sign_in_as(app, "sita@example.com", "Interval99")
    assert app.session_state["auth_user"].uid == "uid-sita"
    assert sees(app) == (False, True)

    # B → A again
    app.session_state["page"] = "Home"
    app = app.run()
    app.button(key="auth_signout").click().run()
    app = settle(app)
    app = sign_in_as(app, "ravi@example.com", "Popcorn2026")
    assert app.session_state["auth_user"].uid == "uid-ravi"
    assert sees(app) == (True, False)


# ──────────────────────────────────────────────────────────────────────────
# Persistent session across a browser refresh
# ──────────────────────────────────────────────────────────────────────────
def test_a_blind_run_does_not_spend_the_restore_attempt(monkeypatch, bare_session):
    """The refresh-logout bug, pinned.

    ``st.context.cookies`` answers with an *empty mapping and no error* for a
    run whose client context Streamlit has not resolved. Spending the
    once-per-session restore attempt on such a run is what made one blind run
    a permanent sign-out: the cookie was still in the browser, still being
    sent, and nothing ever looked again.
    """
    blind = {"on": True}
    jar = {session.COOKIE: "refresh.uid-ravi", session.SESSION_STARTED_COOKIE: "1789561923.5"}
    monkeypatch.setattr(session, "_cookie_jar", lambda: ({} if blind["on"] else dict(jar)))

    assert REAL_RESTORE() is None
    # Nothing was attempted, so nothing was spent.
    assert not bare_session.get("auth_restore_tried")

    blind["on"] = False
    assert session._cookie() == "refresh.uid-ravi"
    assert not bare_session.get("auth_restore_tried")   # a later run still may


def test_the_cookie_jar_falls_back_to_the_request_header(monkeypatch):
    """When ``st.context.cookies`` is empty or missing (Streamlit < 1.42, or an
    unresolved client context) the same Cookie header is read from further
    down rather than reporting "no session"."""
    import streamlit as st

    class Ctx:
        cookies: dict = {}                       # empty, exactly as Streamlit answers

        class headers:                           # noqa: N801 - a stand-in
            @staticmethod
            def get(name, default=None):
                return "tr_session=abc%2Fdef; other=1" if name == "Cookie" else default

    monkeypatch.setattr(st, "context", Ctx())
    assert session._cookie_jar()["tr_session"] == "abc%2Fdef"
    assert session._cookie() == "abc/def"        # …and decoded on the way out


def test_parse_cookie_header_is_tolerant():
    assert session._parse_cookie_header("a=1; b=2") == {"a": "1", "b": "2"}
    assert session._parse_cookie_header("") == {}
    assert session._parse_cookie_header("junk; a=1") == {"a": "1"}
    assert session._parse_cookie_header("a=1; a=2")["a"] == "1"     # first wins


def test_signing_in_queues_the_persistent_session_cookie(visitor, fake):
    """A successful sign-in leaves the refresh token and the session start
    queued for the browser, which is what a later reload restores from."""
    app = run()
    app.text_input(key="auth_email").set_value("ravi@example.com")
    app.text_input(key="auth_password").set_value("Popcorn2026")
    app.button(key="auth_signin").click().run()
    # The gate flushes the queue as it paints, so the values are consumed —
    # what must be true afterwards is that the deadline was recorded.
    started = app.session_state[session.SESSION_STARTED_KEY]
    assert app.session_state[session.SESSION_EXPIRES_KEY] == started + session.SESSION_SECONDS
    assert app.session_state["auth_user"].refresh_token == fake.refresh_token_for("uid-ravi")


def test_an_expired_id_token_refreshes_instead_of_signing_out(fake, bare_session):
    """An ID token is short-lived. Its expiry must refresh the token, never end
    the session, while the refresh token is still good."""
    bare_session[session.USER_KEY] = session.AuthUser(
        uid="uid-ravi", email="ravi@example.com", display_name="Ravi Teja",
        id_token="id.stale", refresh_token=fake.refresh_token_for("uid-ravi"),
        expires_at=time.time() - 1,                      # already past
    )
    bare_session[session.SESSION_EXPIRES_KEY] = time.time() + session.SESSION_SECONDS
    fake.calls.clear()

    token = session.id_token()
    assert token == "id.uid-ravi"                        # a fresh one
    assert [a for a, _ in fake.calls] == ["token"]       # via the refresh flow
    assert session.current_user() is not None            # still signed in
    assert bare_session[session.USER_KEY].expires_at > time.time()


def test_a_refused_refresh_token_signs_out_and_drops_the_cookie(monkeypatch, fake):
    """Firebase actually rejecting the stored credential is one of the three
    reasons to end a session — and the dead cookie is cleared."""
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "_cookie", lambda: "refresh.not-a-real-token")
    monkeypatch.setattr(session, "_cookie_started", lambda: time.time())
    app = run()
    assert on_login_page(app) and "auth_user" not in app.session_state
    # The dead credential was thrown away rather than retried for ever.
    assert "auth_restore_tried" in app.session_state


def test_flush_cookie_never_queues_a_cookie_that_deletes_itself(monkeypatch, bare_session):
    """``Max-Age=0`` deletes a cookie. A missing or passed deadline must queue
    nothing rather than silently throw the session away."""
    bare_session.update({"auth_cookie_set": "refresh.uid-ravi",
                         session.SESSION_EXPIRES_KEY: time.time() - 10})   # already past
    session.flush_cookie()
    assert bare_session.get(session.BRIDGE_OPS_KEY) is None                # nothing queued

    bare_session.clear()
    started = time.time()
    bare_session.update({"auth_cookie_set": "refresh.uid-ravi",
                         "auth_session_cookie_set": str(started),
                         session.SESSION_EXPIRES_KEY: started + session.SESSION_SECONDS})
    session.flush_cookie()
    ops = bare_session[session.BRIDGE_OPS_KEY]
    assert {op["name"] for op in ops} == {session.COOKIE, session.SESSION_STARTED_COOKIE}
    assert all(op["max_age"] > 0 for op in ops)
    # The deadline is what sets the lifetime — never a fresh seven days.
    assert max(op["max_age"] for op in ops) <= session.SESSION_SECONDS


def test_signing_out_queues_a_clear_for_the_bridge(bare_session):
    bare_session["auth_cookie_clear"] = True
    session.flush_cookie()
    ops = bare_session[session.BRIDGE_OPS_KEY]
    assert {op["name"] for op in ops} == {session.COOKIE, session.SESSION_STARTED_COOKIE}
    assert all(op["max_age"] == 0 for op in ops)                           # a clear


def test_restore_uses_the_token_the_bridge_reported(fake, bare_session, monkeypatch):
    """The whole point: with no server-side cookie at all, the value the
    browser reported is what restores the session."""
    monkeypatch.setattr(session, "_cookie_header_from_runtime", lambda: "")
    bare_session[session.BRIDGE_JAR_KEY] = {
        session.COOKIE: fake.refresh_token_for("uid-ravi"),
        session.SESSION_STARTED_COOKIE: str(time.time()),
        "seen": 2,
    }
    assert session.bridge_answered() is True
    assert session.bridge_jar()[session.COOKIE] == fake.refresh_token_for("uid-ravi")

    user = REAL_RESTORE()
    assert user is not None and user.uid == "uid-ravi"
    assert [a for a, _ in fake.calls] == ["token", "lookup"]


def test_a_bridge_without_a_token_shows_login(fake, bare_session):
    """An answer with no session cookie is a real answer: show login, and do
    not sit waiting for the browser again."""
    bare_session[session.BRIDGE_JAR_KEY] = {session.COOKIE: "", "seen": 1}
    assert session.bridge_answered() is True
    assert session.bridge_jar() == {}
    assert REAL_RESTORE() is None
    assert fake.calls == []                                                # Firebase never asked


def test_an_invalid_bridge_token_shows_login(fake, bare_session, monkeypatch):
    monkeypatch.setattr(session, "_cookie_header_from_runtime", lambda: "")
    bare_session[session.BRIDGE_JAR_KEY] = {
        session.COOKIE: "refresh.not-a-real-token",
        session.SESSION_STARTED_COOKIE: str(time.time()), "seen": 2,
    }
    assert REAL_RESTORE() is None
    assert bare_session.get("auth_cookie_clear") is True                   # the dead cookie goes


def test_the_bridge_value_never_reaches_a_log(fake, bare_session, monkeypatch, capsys):
    """Diagnostics carry counts and booleans; the token never appears."""
    monkeypatch.setattr(session, "_cookie_header_from_runtime", lambda: "")
    token = fake.refresh_token_for("uid-ravi")
    bare_session[session.BRIDGE_JAR_KEY] = {
        session.COOKIE: token, session.SESSION_STARTED_COOKIE: str(time.time()), "seen": 2}
    REAL_RESTORE()
    session.log_cookie_report()
    out = capsys.readouterr().out
    assert "SUCCESS" in out                       # it did restore
    assert token not in out
    assert "uid-ravi" not in out
    assert "ravi@example.com" not in out


def test_both_accounts_restore_as_themselves_after_a_refresh(visitor, fake, monkeypatch):
    """A refresh restores whoever the cookie belongs to — A as A, B as B."""
    for email, password, uid in (("ravi@example.com", "Popcorn2026", "uid-ravi"),
                                 ("sita@example.com", "Interval99", "uid-sita")):
        app = run()
        app = sign_in_as(app, email, password)
        started = app.session_state[session.SESSION_STARTED_KEY]

        # A brand-new Streamlit session, as a browser refresh makes.
        monkeypatch.setattr(session, "restore", REAL_RESTORE)
        monkeypatch.setattr(session, "_cookie", lambda uid=uid: fake.refresh_token_for(uid))
        monkeypatch.setattr(session, "_cookie_started", lambda started=started: started)
        fresh = run()
        assert in_the_app(fresh), email
        assert fresh.session_state["auth_user"].uid == uid
        assert fresh.session_state["auth_user"].email == email
        # …and the deadline is the original one, not a new seven days.
        assert fresh.session_state[session.SESSION_EXPIRES_KEY] == started + session.SESSION_SECONDS
        monkeypatch.setattr(session, "restore", lambda: None)


def test_the_restoring_beat_renders_no_markup_at_all():
    """The restoring screen printed its own ``<div class="tr-restoring">`` as
    text: ``st.markdown`` parses Markdown first, and the indented lines of the
    triple-quoted fragment became a code block. It now renders nothing, so
    there is no markup left to leak — and no layout shift on every reload."""
    import inspect

    from auth import gate

    src = inspect.getsource(gate._restoring)
    body = src.split('"""')[-1]                       # past the docstring
    assert "st.markdown" not in body and "unsafe_allow_html" not in body
    assert "<div" not in body and "<style" not in body
    assert gate._restoring() is None                  # and it draws nothing


def test_the_gate_never_hands_indented_markup_to_markdown():
    """Any HTML the gate does render must go through ``clean_html`` (or carry
    no indented lines), which is the guard against the same code-block trap."""
    from pathlib import Path as _Path

    src = _Path("auth/gate.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("<div", "<style", "<span")) and line.startswith("    "):
            raise AssertionError(f"indented raw markup would render as a code block: {stripped[:60]}")
