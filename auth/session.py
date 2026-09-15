"""Who is signed in, for the length of a Streamlit session — and across a reload.

The signed-in user lives in ``st.session_state``, which is server-side memory
keyed by the websocket: every rerun (each click is one) sees it, and the
browser has no way to write to it. That is the authenticated boundary.

A browser reload opens a new session, so the *refresh token* Firebase issued
at sign-in is also kept in a cookie. On a fresh session it is handed back to
Google, which either answers with a new ID token and the account's UID — or
refuses, in which case the person sees the login page. The cookie is never
trusted on its own; only Google's answer to it is.

``id_token()`` refreshes itself shortly before it expires, so the next step
(Firestore, per user) can call it and always get a live token.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace

import streamlit as st

from auth import firebase
from auth.firebase import AuthError, Credentials

USER_KEY = "auth_user"
COOKIE = "tr_session"
COOKIE_DAYS = 30
#: Refresh the ID token this many seconds before Firebase says it expires.
REFRESH_MARGIN = 120


@dataclass
class AuthUser:
    """The Firebase account behind this session. ``uid`` is what the next
    step keys Firestore documents on."""

    uid: str
    email: str
    display_name: str = ""
    id_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    expires_at: float = 0.0
    signed_in_at: float = field(default_factory=time.time)

    @property
    def first_name(self) -> str:
        name = (self.display_name or "").strip()
        return name.split()[0] if name else (self.email.split("@")[0] if self.email else "there")

    @property
    def label(self) -> str:
        return (self.display_name or "").strip() or self.email


# ──────────────────────────────────────────────────────────────────────────
# Reading
# ──────────────────────────────────────────────────────────────────────────
def current_user() -> AuthUser | None:
    """The signed-in user, or None. Only an :class:`AuthUser` this module
    stored counts — anything else in the slot is treated as nobody."""
    user = st.session_state.get(USER_KEY)
    if isinstance(user, AuthUser) and user.uid:
        return user
    return None


def current_uid() -> str:
    user = current_user()
    return user.uid if user else ""


# ──────────────────────────────────────────────────────────────────────────
# Writing
# ──────────────────────────────────────────────────────────────────────────
def _from_credentials(creds: Credentials, *, email: str = "", display_name: str = "") -> AuthUser:
    return AuthUser(
        uid=creds.uid,
        email=email or creds.email,
        display_name=display_name or creds.display_name,
        id_token=creds.id_token,
        refresh_token=creds.refresh_token,
        expires_at=time.time() + max(60, creds.expires_in),
    )


def sign_in_user(creds: Credentials) -> AuthUser:
    """Record a successful Firebase exchange as this session's user and
    queue the cookie that lets a reload pick the session up again."""
    user = _from_credentials(creds)
    st.session_state[USER_KEY] = user
    st.session_state["auth_restore_tried"] = True
    st.session_state["auth_cookie_set"] = user.refresh_token
    st.session_state.pop("auth_cookie_clear", None)
    st.session_state["auth_just_signed_in"] = True
    return user


def sign_out() -> None:
    """Forget the user here and tell the browser to drop the cookie. The
    Firebase tokens are simply discarded — there is nothing to keep."""
    st.session_state.pop(USER_KEY, None)
    st.session_state.pop("auth_cookie_set", None)
    st.session_state.pop("auth_just_signed_in", None)
    st.session_state["auth_cookie_clear"] = True
    # A reload after signing out must not quietly sign back in from the
    # cookie this session was opened with.
    st.session_state["auth_restore_tried"] = True


# ──────────────────────────────────────────────────────────────────────────
# Restoring after a reload
# ──────────────────────────────────────────────────────────────────────────
def _cookie() -> str:
    """The session cookie the browser sent when this session opened."""
    try:
        return str(st.context.cookies.get(COOKIE) or "")
    except Exception:  # noqa: BLE001 - no browser (tests), or an old runtime
        return ""


def restore() -> AuthUser | None:
    """Once per session: turn the cookie's refresh token into a signed-in
    user by asking Google. Returns None when there is nothing to restore or
    Google refuses; either way the login page is what comes next."""
    if st.session_state.get("auth_restore_tried"):
        return None
    st.session_state["auth_restore_tried"] = True
    token = _cookie()
    if not token or not firebase.is_configured():
        return None
    client = firebase.FirebaseAuth()
    try:
        creds = client.refresh(token)
        info = client.lookup(creds.id_token)
    except AuthError as exc:
        print(f"[auth] stored session not restored: {exc.code}")
        st.session_state["auth_cookie_clear"] = True
        return None
    user = _from_credentials(creds, email=info["email"], display_name=info["display_name"])
    if info["uid"]:
        user.uid = info["uid"]
    if not user.uid:
        st.session_state["auth_cookie_clear"] = True
        return None
    st.session_state[USER_KEY] = user
    return user


# ──────────────────────────────────────────────────────────────────────────
# Tokens for the next step
# ──────────────────────────────────────────────────────────────────────────
def id_token() -> str:
    """A live Firebase ID token for the signed-in user ("" when nobody is).
    Refreshed through Google when it is about to expire."""
    user = current_user()
    if user is None:
        return ""
    if user.expires_at - time.time() > REFRESH_MARGIN or not user.refresh_token:
        return user.id_token
    try:
        creds = firebase.FirebaseAuth().refresh(user.refresh_token)
    except AuthError as exc:
        print(f"[auth] token refresh failed: {exc.code}")
        return user.id_token
    fresh = replace(user, id_token=creds.id_token, refresh_token=creds.refresh_token,
                    expires_at=time.time() + max(60, creds.expires_in))
    st.session_state[USER_KEY] = fresh
    if creds.refresh_token != user.refresh_token:
        st.session_state["auth_cookie_set"] = creds.refresh_token
    return fresh.id_token


# ──────────────────────────────────────────────────────────────────────────
# The cookie itself — written by the browser, from a zero-height component
# ──────────────────────────────────────────────────────────────────────────
def flush_cookie() -> None:
    """Emit the pending cookie write or clear, if there is one.

    Called from a page that is actually being painted (the app after a
    sign-in, the login page after a sign-out): a script queued right before
    ``st.rerun()`` might never reach the browser, so the intent is kept in
    session state and acted on during the next full render.
    """
    import streamlit.components.v1 as components

    if st.session_state.pop("auth_cookie_clear", False):
        components.html(_cookie_script("", 0), height=0)
    token = st.session_state.pop("auth_cookie_set", "")
    if token:
        components.html(_cookie_script(token, COOKIE_DAYS * 86400), height=0)


def _cookie_script(value: str, max_age: int) -> str:
    # json.dumps makes the value a JS string literal, and "</" is escaped so
    # the literal can never close the script element; the value is only ever
    # a token Google issued, but the rule costs nothing.
    literal = json.dumps(value).replace("</", r"<\/")
    return (
        "<script>(function(){try{"
        "var d=window.parent.document;"
        "var s=(window.parent.location.protocol==='https:')?'; Secure':'';"
        f"d.cookie={json.dumps(COOKIE)}+'='+encodeURIComponent({literal})"
        f"+'; Max-Age={int(max_age)}; Path=/; SameSite=Lax'+s;"
        "}catch(e){}})();</script>"
    )


__all__ = [
    "COOKIE",
    "AuthUser",
    "current_uid",
    "current_user",
    "flush_cookie",
    "id_token",
    "restore",
    "sign_in_user",
    "sign_out",
]
