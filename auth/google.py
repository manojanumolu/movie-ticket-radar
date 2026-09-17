"""Google sign-in: the OAuth half, before Firebase ever hears about it.

The app is a Streamlit process: every sign-in call it makes is a server-side
REST call (``auth.firebase``), and the only code that runs in the browser is
the cookie bridge, whose iframe Streamlit sandboxes without permission to
navigate the page. So Google sign-in is the plain **authorization-code
redirect**, the flow built for exactly this shape of application:

1. "Continue with Google" is a real link to ``accounts.google.com`` — a
   top-level navigation, which needs no permission at all. It carries a
   random ``state`` the bridge has just written to a short-lived cookie.
2. Google sends the browser back to the app with ``?code=…&state=…``. That
   is a brand-new Streamlit session, so the only memory of step 1 is the
   cookie; ``state`` must match it or nothing is exchanged.
3. This module trades the code for Google's ID token, server to server,
   with the OAuth client secret that lives in Streamlit's secrets.
4. ``auth.firebase.FirebaseAuth.sign_in_with_google`` hands that ID token to
   Firebase's own ``accounts:signInWithIdp``. Firebase checks it with
   Google, finds or creates the account, and answers with the same
   ``idToken``/``refreshToken``/``localId`` a password sign-in produces —
   from there on nothing is Google-specific: the same ``Credentials``, the
   same ``sign_in_user``, the same cookie, the same UID keying Firestore.

Configuration (never in the repository): ``[google] client_id`` and
``client_secret`` in Streamlit secrets — the *Web client* Firebase created
when the provider was enabled — and, optionally, ``redirect_uri`` when the
app's public URL cannot be read from the request. Nothing here ever reaches
the browser except the client id, which is public by design.
"""

from __future__ import annotations

import hashlib
import os
import secrets as _secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PROVIDER_ID = "google.com"
SCOPES = "openid email profile"
TIMEOUT = 12.0
#: How long a sign-in attempt may take between clicking the button and
#: coming back from Google. Generous; a stale one simply asks again.
STATE_SECONDS = 600


class GoogleError(Exception):
    """A failure with a message safe to show, and a code safe to log."""

    def __init__(self, message: str, code: str = "google"):
        super().__init__(message)
        self.code = code


MESSAGES: dict[str, str] = {
    "cancelled": "Google sign-in was cancelled.",
    "state": "Sign-in didn't complete — please try again.",
    "invalid_grant": "That Google sign-in has expired — please try again.",
    "redirect_uri_mismatch": "Google sign-in isn't configured correctly on this host.",
    "invalid_client": "Google sign-in isn't configured correctly on this host.",
    "unauthorized_client": "Google sign-in isn't configured correctly on this host.",
    "not_configured": "Google sign-in isn't switched on for this TicketRadar yet — "
                      "use your email and password for now.",
    "no_token": "Google didn't return a valid sign-in. Please try again.",
    "network": "Couldn't reach Google. Check your connection and try again.",
    "failed": "Google sign-in didn't complete. Please try again.",
}


def explain(code: str) -> str:
    return MESSAGES.get(code, MESSAGES["failed"])


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _secret(key: str) -> str:
    from auth.firebase import _secret as read

    return read("google", key)


def config() -> GoogleConfig:
    return GoogleConfig(
        client_id=(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or _secret("client_id")).strip(),
        client_secret=(os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or _secret("client_secret")).strip(),
        redirect_uri=(os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or _secret("redirect_uri")).strip(),
    )


def is_configured() -> bool:
    return config().configured


def redirect_uri(page_url: str = "") -> str:
    """Where Google sends the browser back: the app's own root.

    The configured value wins. Otherwise it is derived from the URL the
    browser is on (``st.context.url``) — scheme and host, path ``/`` — so
    the deployed app and a laptop each name themselves, and neither is
    written into the code. Whatever this returns must be registered as an
    authorized redirect URI on the OAuth client, exactly.
    """
    configured = config().redirect_uri
    if configured:
        return configured
    parts = urlsplit(page_url or "")
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}/"
    return ""


# ──────────────────────────────────────────────────────────────────────────
# Step 1: the link
# ──────────────────────────────────────────────────────────────────────────
def new_state() -> str:
    return _secrets.token_urlsafe(24)


def nonce_for(state: str) -> str:
    """The OpenID nonce, derived from the state so one cookie covers both."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def authorization_url(state: str, redirect: str, *, login_hint: str = "") -> str:
    params = {
        "client_id": config().client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce_for(state),
        "access_type": "online",
        "include_granted_scopes": "false",
        # Always let the person pick the account: a household laptop with
        # two Google accounts must not silently use the last one.
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# ──────────────────────────────────────────────────────────────────────────
# Step 3: the code → Google's ID token
# ──────────────────────────────────────────────────────────────────────────
def _post_form(url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """POST form-encoded (what Google's token endpoint accepts), return
    (status, body). Never raises for an HTTP error; does for no network."""
    response = requests.post(url, data=data, timeout=TIMEOUT,
                             headers={"Accept": "application/json"})
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def exchange_code(code: str, redirect: str) -> str:
    """Trade the authorization code for Google's ID token. The log line is
    the safe diagnostic — status and Google's error code — never the code,
    the secret or a token."""
    cfg = config()
    if not cfg.configured:
        raise GoogleError(MESSAGES["not_configured"], "not_configured")
    try:
        status, body = _post_form(TOKEN_URL, {
            "code": code,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        })
    except (requests.RequestException, OSError) as exc:
        print(f"[auth] google token: HTTP 0 network ({type(exc).__name__})", flush=True)
        raise GoogleError(MESSAGES["network"], "network") from None
    if status >= 400 or "error" in body:
        error = str(body.get("error") or f"HTTP_{status}")
        print(f"[auth] google token: HTTP {status} {error}", flush=True)
        raise GoogleError(explain(error), error)
    id_token = str(body.get("id_token") or "")
    if not id_token:
        print(f"[auth] google token: HTTP {status} no id_token", flush=True)
        raise GoogleError(MESSAGES["no_token"], "no_token")
    print(f"[auth] google token: HTTP {status} OK", flush=True)
    return id_token


__all__ = [
    "GoogleConfig",
    "GoogleError",
    "MESSAGES",
    "PROVIDER_ID",
    "STATE_SECONDS",
    "authorization_url",
    "config",
    "exchange_code",
    "explain",
    "is_configured",
    "new_state",
    "nonce_for",
    "redirect_uri",
]
