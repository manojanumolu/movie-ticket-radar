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
#: An account that exists but whose email is not yet verified: enough to
#: resend the verification email, never enough to be ``current_user()``.
PENDING_KEY = "auth_pending"
COOKIE = "tr_session"
COOKIE_DAYS = 30
#: Refresh the ID token this many seconds before Firebase says it expires.
REFRESH_MARGIN = 120
#: What survives a session reset: the account that *caused* it (a sign-in
#: keeps its new user; a sign-out has already dropped the old one), the
#: once-per-session restore guard, and the cookie intent the next paint acts
#: on. Nothing that belongs to the previous person.
RESET_KEEPS = frozenset({USER_KEY, "auth_just_signed_in", "auth_restore_tried",
                         "auth_cookie_set", "auth_cookie_clear"})
RESET_FLAG = "auth_reset_pending"


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


def request_reset() -> None:
    """Ask for everything this Streamlit session knows to be forgotten at
    the start of the next run.

    A session is one browser tab, and a tab can sign out and sign in as
    somebody else — so every key the previous person accumulated (the
    wizard's step and picks, the page, a pending flash, the read-through
    marker, form contents) must go, not just the account. It is done at the
    top of the next run rather than here because widget-backed keys can't
    be touched while their widget is on screen; the gate calls
    :func:`apply_pending_reset` before a single widget exists.
    """
    st.session_state[RESET_FLAG] = True


def apply_pending_reset(*, keep: frozenset[str] = RESET_KEEPS) -> bool:
    """Carry out a requested reset. Returns True when one happened."""
    if not st.session_state.pop(RESET_FLAG, False):
        return False
    for key in list(st.session_state.keys()):
        if key not in keep and key != "page":
            del st.session_state[key]
    # The nav is a widget: it is *set* to its default rather than deleted
    # (allowed here, since it isn't drawn yet), so the next person starts on
    # Home whatever page the last one was on.
    st.session_state["page"] = "Home"
    return True


def sign_in_user(creds: Credentials) -> AuthUser:
    """Record a successful Firebase exchange as this session's user and
    queue the cookie that lets a reload pick the session up again.

    Refuses an unverified account: ``creds.email_verified`` comes from
    Firebase's own account record, and until it is true the person stays
    on the verification screen, not in the app. Starts from a clean
    session so nothing of a previous sign-in is carried over.
    """
    if not creds.email_verified:
        raise AuthError(firebase.MESSAGES["EMAIL_NOT_VERIFIED"], "EMAIL_NOT_VERIFIED")
    request_reset()
    user = _from_credentials(creds)
    st.session_state[USER_KEY] = user
    st.session_state["auth_restore_tried"] = True
    st.session_state["auth_cookie_set"] = user.refresh_token
    st.session_state.pop("auth_cookie_clear", None)
    st.session_state["auth_just_signed_in"] = True
    return user


def sign_out() -> None:
    """Forget everything here and tell the browser to drop the cookie. The
    Firebase tokens are simply discarded — there is nothing to keep."""
    st.session_state.pop(USER_KEY, None)          # nothing else in this run sees a user
    st.session_state.pop("auth_just_signed_in", None)
    request_reset()                                # …and the rest goes before the next run
    st.session_state.pop("auth_cookie_set", None)
    st.session_state["auth_cookie_clear"] = True
    # A reload after signing out must not quietly sign back in from the
    # cookie this session was opened with.
    st.session_state["auth_restore_tried"] = True


# ──────────────────────────────────────────────────────────────────────────
# Waiting for a verified email
# ──────────────────────────────────────────────────────────────────────────
def set_pending(creds: Credentials) -> None:
    """Park an unverified account: its email and a short-lived ID token, so
    the verification email can be requested. Not a signed-in user, and —
    until :func:`mark_verification_sent` says otherwise — no email has been
    accepted by Firebase: ``sends`` is 0 and there is no cooldown."""
    st.session_state[PENDING_KEY] = {
        "email": creds.email,
        "id_token": creds.id_token,
        "cooldown_until": 0.0,   # absolute deadline; 0 = resend allowed now
        "sends": 0,              # requests Firebase actually accepted (HTTP 2xx)
    }


def mark_verification_sent(cooldown: float) -> None:
    """Firebase answered 2xx to a VERIFY_EMAIL request: count it and start
    the cooldown from an absolute deadline, so a stale tab or a delayed
    rerun can never make the countdown wrong."""
    pend = pending()
    if pend is None:
        return
    pend["cooldown_until"] = time.time() + cooldown
    pend["sends"] = int(pend.get("sends", 0)) + 1
    st.session_state[PENDING_KEY] = pend


def pending() -> dict | None:
    value = st.session_state.get(PENDING_KEY)
    return value if isinstance(value, dict) and value.get("email") else None


def clear_pending() -> None:
    st.session_state.pop(PENDING_KEY, None)


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
    if not info["email_verified"]:
        print("[auth] stored session not restored: email not verified")
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
    "PENDING_KEY",
    "AuthUser",
    "clear_pending",
    "current_uid",
    "current_user",
    "flush_cookie",
    "id_token",
    "mark_verification_sent",
    "pending",
    "apply_pending_reset",
    "request_reset",
    "restore",
    "set_pending",
    "sign_in_user",
    "sign_out",
]
