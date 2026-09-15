"""Firebase Identity Toolkit, over REST.

Email/password sign-in for a Python app is four HTTPS calls to Google's
Identity Toolkit — the same endpoints the Firebase JS SDK uses underneath.
Going straight to them keeps the dependency list as it is (``requests`` is
already here) and keeps every credential exchange server-side: the browser
only ever sees the form. Because the answers come from Google to *this*
process, the UID in them is trusted; nothing the browser sends is.

Configuration: the project's **Web API key** (``FIREBASE_WEB_API_KEY`` in
the environment, or ``[firebase] api_key`` in Streamlit secrets). Firebase
designs that key to ship inside public web apps — it identifies the project,
it does not grant access — but it still lives in secrets, never in source.
No service account, no private key, nothing an Admin SDK would want.

Every failure surfaces as :class:`AuthError` carrying one human sentence.
Firebase's own codes (``INVALID_LOGIN_CREDENTIALS``, ``EMAIL_EXISTS`` …) are
translated here and never shown to a user.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any

import requests

IDENTITY = "https://identitytoolkit.googleapis.com/v1/accounts:{action}"
SECURE_TOKEN = "https://securetoken.googleapis.com/v1/token"
TIMEOUT = 12.0

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PASSWORD_MIN = 8


class AuthError(Exception):
    """A failure the user can be told about, in plain words.

    ``code`` is Firebase's identifier (or one of ours, in lower case) so the
    UI and the tests can branch on it; ``str(error)`` is the sentence shown;
    ``status`` is the HTTP status Firebase answered with (0 when it never
    answered). Together they are the safe diagnostic — no key, no token.
    """

    def __init__(self, message: str, code: str = "error", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def diagnostic(self) -> str:
        """``HTTP 400, INVALID_ID_TOKEN`` — what to show next to a message
        when the person needs to tell somebody what Firebase said."""
        return f"HTTP {self.status}, {self.code}" if self.status else self.code


#: Firebase code → what to tell the person. Wrong-password and no-such-user
#: read the same on purpose: naming which one it was tells an attacker which
#: addresses have accounts.
MESSAGES: dict[str, str] = {
    "INVALID_LOGIN_CREDENTIALS": "That email and password don't match. Check both and try again.",
    "INVALID_PASSWORD": "That email and password don't match. Check both and try again.",
    "EMAIL_NOT_FOUND": "That email and password don't match. Check both and try again.",
    "USER_NOT_FOUND": "That email and password don't match. Check both and try again.",
    "INVALID_EMAIL": "That doesn't look like an email address.",
    "MISSING_EMAIL": "Enter your email address.",
    "MISSING_PASSWORD": "Enter your password.",
    "EMAIL_EXISTS": "There's already a TicketRadar account for that email. Sign in instead.",
    "WEAK_PASSWORD": f"Choose a stronger password — at least {PASSWORD_MIN} characters, with a letter and a number.",
    "USER_DISABLED": "This account has been disabled. Get in touch if you think that's a mistake.",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many attempts. Wait a few minutes, then try again.",
    "OPERATION_NOT_ALLOWED": "Email sign-in isn't switched on for this TicketRadar yet.",
    "PASSWORD_LOGIN_DISABLED": "Email sign-in isn't switched on for this TicketRadar yet.",
    "TOKEN_EXPIRED": "Your session has expired. Please sign in again.",
    "INVALID_REFRESH_TOKEN": "Your session has expired. Please sign in again.",
    "INVALID_ID_TOKEN": "Your session has expired. Please sign in again.",
    "INVALID_GRANT_TYPE": "Your session has expired. Please sign in again.",
    "MISSING_REFRESH_TOKEN": "Your session has expired. Please sign in again.",
    "RESET_PASSWORD_EXCEED_LIMIT": "Too many reset requests. Wait a little, then try again.",
    "API_KEY_INVALID": "TicketRadar's sign-in isn't configured correctly on this host.",
    "CONFIGURATION_NOT_FOUND": "TicketRadar's sign-in isn't configured correctly on this host.",
    "EMAIL_NOT_VERIFIED": "Verify your email address first — the link is in your inbox.",
    "network": "Couldn't reach the sign-in service. Check your connection and try again.",
    "not_configured": "Sign-in isn't configured on this host yet — the Firebase Web API key is missing.",
}
FALLBACK = "Something went wrong signing you in. Please try again."


def explain(code: str) -> str:
    """The sentence for a Firebase code such as ``WEAK_PASSWORD : Password should …``."""
    head = (code or "").split(":")[0].strip().upper()
    if head in MESSAGES:
        return MESSAGES[head]
    if code in MESSAGES:
        return MESSAGES[code]
    # A few codes arrive with a suffix ("TOO_MANY_ATTEMPTS_TRY_LATER : …").
    for known, message in MESSAGES.items():
        if head.startswith(known):
            return message
    if "API KEY" in head:
        return MESSAGES["API_KEY_INVALID"]
    return FALLBACK


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FirebaseConfig:
    api_key: str
    project_id: str = ""
    auth_domain: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _secret(table: str, key: str) -> str:
    """One value from ``st.secrets[table][key]``, or "" outside Streamlit."""
    try:
        import streamlit as st

        section = st.secrets.get(table, {})
        value = section.get(key, "") if hasattr(section, "get") else ""
        return str(value or "").strip()
    except Exception:  # noqa: BLE001 - no secrets file, or not in Streamlit
        return ""


def config() -> FirebaseConfig:
    """Environment first (the way every other credential here works), then
    the ``[firebase]`` table of Streamlit secrets."""
    return FirebaseConfig(
        api_key=(os.environ.get("FIREBASE_WEB_API_KEY") or _secret("firebase", "api_key")).strip(),
        project_id=(os.environ.get("FIREBASE_PROJECT_ID") or _secret("firebase", "project_id")).strip(),
        auth_domain=(os.environ.get("FIREBASE_AUTH_DOMAIN") or _secret("firebase", "auth_domain")).strip(),
    )


def is_configured() -> bool:
    return config().configured


_config_logged = False


def log_config_once() -> None:
    """One line per process saying whether the host handed the app its
    Firebase configuration — the project id (public: it is in every Firebase
    web app's config) and *whether* the key and auth domain are present.
    Never the key itself."""
    global _config_logged
    if _config_logged:
        return
    _config_logged = True
    cfg = config()
    print(f"[auth] Firebase config: project_id={cfg.project_id or '(missing)'}, "
          f"api_key_present={bool(cfg.api_key)}, auth_domain_present={bool(cfg.auth_domain)}", flush=True)


# ──────────────────────────────────────────────────────────────────────────
# Transport — one function, so the tests can replace the network
# ──────────────────────────────────────────────────────────────────────────
def _post(url: str, params: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST JSON, return (status, body). Never raises for an HTTP error —
    the body carries Firebase's code — but does for no network at all."""
    response = requests.post(url, params=params, json=payload, timeout=TIMEOUT)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


# ──────────────────────────────────────────────────────────────────────────
# Results
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Credentials:
    """What Firebase hands back for a signed-in account."""

    uid: str
    email: str
    display_name: str
    id_token: str
    refresh_token: str
    expires_in: int  # seconds the id_token is good for (Firebase: 3600)
    #: Only ``accounts:lookup`` says this; sign-in and sign-up fill it from
    #: there. False until the person has clicked Firebase's verification link.
    email_verified: bool = False


# ──────────────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────────────
class FirebaseAuth:
    """The four calls the app makes. Construct with the Web API key."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or config().api_key
        #: (HTTP status, "OK" or Firebase's code) of the most recent exchange —
        #: the safe diagnostic, for the UI to show next to a result.
        self.last_status: int = 0
        self.last_code: str = ""

    # -- plumbing ---------------------------------------------------------
    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AuthError(MESSAGES["not_configured"], "not_configured")
        url = IDENTITY.format(action=action)
        label = action + (f" {payload['requestType']}" if action == "sendOobCode" else "")
        return self._exchange(url, payload, label)

    def _exchange(self, url: str, payload: dict[str, Any], label: str = "token") -> dict[str, Any]:
        """One request. The log line is the safe diagnostic — the endpoint,
        the HTTP status and Firebase's code — never the payload, the key, a
        token or an address."""
        try:
            status, body = _post(url, {"key": self.api_key}, payload)
        except (requests.RequestException, OSError) as exc:  # DNS, TLS, timeout …
            self.last_status, self.last_code = 0, "network"
            print(f"[auth] {label}: HTTP 0 network ({type(exc).__name__})", flush=True)
            raise AuthError(MESSAGES["network"], "network") from None
        if status >= 400 or "error" in body:
            error = body.get("error") or {}
            code = str(error.get("message") or error.get("status") or f"HTTP_{status}")
            short = code.split(":")[0].strip()
            self.last_status, self.last_code = status, short
            print(f"[auth] {label}: HTTP {status} {short}", flush=True)
            raise AuthError(explain(code), short, status)
        self.last_status, self.last_code = status, "OK"
        print(f"[auth] {label}: HTTP {status} OK", flush=True)
        return body

    # -- the calls --------------------------------------------------------
    def sign_in(self, email: str, password: str) -> Credentials:
        """Password exchange, then the account record — the only place
        Firebase reports whether the email address has been verified."""
        body = self._call("signInWithPassword",
                          {"email": email, "password": password, "returnSecureToken": True})
        creds = _credentials(body)
        info = self.lookup(creds.id_token)
        return replace(creds, email=info["email"] or creds.email,
                       display_name=info["display_name"] or creds.display_name,
                       email_verified=info["email_verified"])

    def sign_up(self, name: str, email: str, password: str) -> Credentials:
        """Create the account. The credentials come back *unverified*: they
        are enough to request the verification email (the caller does that,
        and shows exactly what Firebase answered), never enough to enter
        the app."""
        body = self._call("signUp", {"email": email, "password": password, "returnSecureToken": True})
        creds = _credentials(body)
        if name:
            # The display name is a second call; a failure there must not
            # leave a freshly created account looking like a failed sign-up.
            try:
                updated = self._call("update", {"idToken": creds.id_token, "displayName": name,
                                                "returnSecureToken": False})
                creds = replace(creds, display_name=str(updated.get("displayName") or name))
            except AuthError as exc:
                print(f"[auth] display name not saved for new account: {exc.code}", flush=True)
                creds = replace(creds, display_name=name)
        return replace(creds, email_verified=False)

    def send_email_verification(self, id_token: str, continue_url: str = "") -> None:
        """``POST accounts:sendOobCode {requestType: VERIFY_EMAIL, idToken}``:
        Firebase sends its own verification email (Authentication →
        Templates) to the account behind ``id_token``. Returns only when
        Firebase answered 2xx — "accepted the request", which is not the
        same as "delivered"; anything else raises with the status and code.

        ``continue_url`` is where Firebase's verification page offers to
        send the person afterwards — the app, which then shows "email
        verified, sign in". It carries no credential. If the host isn't in
        the project's authorized domains Firebase refuses the URL, and the
        request is repeated without it rather than lost.
        """
        if not id_token:
            raise AuthError(MESSAGES["INVALID_ID_TOKEN"], "INVALID_ID_TOKEN")
        payload: dict[str, Any] = {"requestType": "VERIFY_EMAIL", "idToken": id_token}
        if continue_url:
            try:
                self._call("sendOobCode", {**payload, "continueUrl": continue_url})
                return
            except AuthError as exc:
                if exc.code not in ("UNAUTHORIZED_DOMAIN", "INVALID_CONTINUE_URI", "MISSING_CONTINUE_URI"):
                    raise
                print(f"[auth] continueUrl refused ({exc.code}); sending without it", flush=True)
        self._call("sendOobCode", payload)

    def account_verified(self, refresh_token: str) -> Credentials | None:
        """Has the person clicked the link yet? Refreshes the parked
        account's token and reads its record; returns verified credentials
        (enough to open a session) or None while it is still unverified."""
        creds = self.refresh(refresh_token)
        info = self.lookup(creds.id_token)
        if not info["email_verified"]:
            return None
        return replace(creds, email=info["email"], display_name=info["display_name"],
                       email_verified=True)

    def send_password_reset(self, email: str) -> None:
        """Ask Firebase to email a reset link. Says nothing about whether the
        address has an account — that is Firebase's own default behaviour
        (email-enumeration protection) and we keep to it when it isn't."""
        try:
            self._call("sendOobCode", {"requestType": "PASSWORD_RESET", "email": email})
        except AuthError as exc:
            if exc.code == "EMAIL_NOT_FOUND":
                return
            raise

    def refresh(self, refresh_token: str) -> Credentials:
        """Exchange a refresh token for a fresh ID token. Google validates the
        token; a revoked or expired one raises with a sign-in-again message."""
        if not self.api_key:
            raise AuthError(MESSAGES["not_configured"], "not_configured")
        body = self._exchange(SECURE_TOKEN, {"grant_type": "refresh_token", "refresh_token": refresh_token})
        return Credentials(
            uid=str(body.get("user_id", "")),
            email="",
            display_name="",
            id_token=str(body.get("id_token", "")),
            refresh_token=str(body.get("refresh_token") or refresh_token),
            expires_in=int(body.get("expires_in") or 3600),
        )

    def lookup(self, id_token: str) -> dict[str, Any]:
        """The account behind an ID token:
        ``{uid, email, display_name, email_verified}``."""
        body = self._call("lookup", {"idToken": id_token})
        users = body.get("users") or []
        if not users:
            raise AuthError(MESSAGES["INVALID_ID_TOKEN"], "INVALID_ID_TOKEN")
        user = users[0]
        return {
            "uid": str(user.get("localId", "")),
            "email": str(user.get("email", "")),
            "display_name": str(user.get("displayName", "") or ""),
            "email_verified": bool(user.get("emailVerified", False)),
        }


def _credentials(body: dict[str, Any]) -> Credentials:
    uid = str(body.get("localId", ""))
    if not uid or not body.get("idToken"):
        raise AuthError(FALLBACK, "malformed")
    return Credentials(
        uid=uid,
        email=str(body.get("email", "")),
        display_name=str(body.get("displayName", "") or ""),
        id_token=str(body["idToken"]),
        refresh_token=str(body.get("refreshToken", "")),
        expires_in=int(body.get("expiresIn") or 3600),
    )


# ──────────────────────────────────────────────────────────────────────────
# Validation the form does before Firebase is asked
# ──────────────────────────────────────────────────────────────────────────
def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def password_problem(password: str) -> str:
    """"" when the password is acceptable, else the reason it isn't."""
    if len(password or "") < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Mix letters and numbers."
    return ""


__all__ = [
    "AuthError",
    "Credentials",
    "FirebaseAuth",
    "FirebaseConfig",
    "MESSAGES",
    "PASSWORD_MIN",
    "config",
    "explain",
    "is_configured",
    "log_config_once",
    "password_problem",
    "valid_email",
]
