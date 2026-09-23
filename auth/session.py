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
from pathlib import Path
from urllib.parse import unquote

import streamlit as st

from auth import firebase
from auth.firebase import AuthError, Credentials

USER_KEY = "auth_user"
#: An account that exists but whose email is not yet verified: enough to
#: resend the verification email, never enough to be ``current_user()``.
PENDING_KEY = "auth_pending"
COOKIE = "tr_session"
SESSION_STARTED_COOKIE = "tr_session_started"
#: The one thing a Google sign-in has to remember across the redirect: the
#: ``state`` the link was minted with. Short-lived, written and read through
#: the same bridge as the session cookie, and it carries no authority — it
#: only proves the return belongs to a link this browser was shown.
OAUTH_STATE_COOKIE = "tr_oauth_state"
OAUTH_STATE_KEY = "auth_oauth_state"
#: The one message a Google popup sends its opener when Python has signed it
#: in: a UI signal meaning "look at the jar again", carrying nothing else.
#: The bridge sends it to this exact origin and accepts it from this exact
#: origin only. Python never treats it as proof of anything — the opener
#: restores the session the way it restores a reload, against Google.
GOOGLE_SIGNAL = "ticketradar-google-auth-complete"
#: Set in the popup's session once its sign-in succeeded: the next run draws
#: the closing beat and hands the bridge the signal instead of the app.
POPUP_DONE_KEY = "auth_google_popup_done"
#: How many completion signals this session has already acted on.
SIGNALS_SEEN_KEY = "auth_google_signals_seen"
SESSION_STARTED_KEY = "auth_session_started"
SESSION_EXPIRES_KEY = "auth_session_expires"
# This is an absolute deadline, not a sliding refresh-token lifetime.
COOKIE_DAYS = 7
SESSION_SECONDS = COOKIE_DAYS * 86400
#: Refresh the ID token this many seconds before Firebase says it expires.
REFRESH_MARGIN = 120
#: What survives a session reset: the account that *caused* it (a sign-in
#: keeps its new user; a sign-out has already dropped the old one), the
#: once-per-session restore guard, and the cookie intent the next paint acts
#: on. Nothing that belongs to the previous person.
RESET_KEEPS = frozenset({USER_KEY, "auth_just_signed_in", "auth_restore_tried",
                         "auth_cookie_set", "auth_cookie_clear", "auth_session_cookie_set",
                         SESSION_STARTED_KEY, SESSION_EXPIRES_KEY,
                         # Google popup bookkeeping: "this window is done" is set
                         # right after sign_in_user asks for the reset, and the
                         # count of signals already acted on must outlive a
                         # sign-out, or a stale signal could re-open the door.
                         POPUP_DONE_KEY, SIGNALS_SEEN_KEY,
                         })
RESET_FLAG = "auth_reset_pending"


# ──────────────────────────────────────────────────────────────────────────
# Diagnostics
# ──────────────────────────────────────────────────────────────────────────
#: Every line is safe to ship to a log: booleans, counts, HTTP statuses and
#: Firebase's own error codes. Never a cookie value, a token, a key, a UID or
#: an email address.
def log_restore(message: str) -> None:
    print(f"[auth] restore: {message}", flush=True)


def log_cleared(reason: str) -> None:
    print(f"[auth] session cleared reason={reason}", flush=True)


def _embedded() -> bool:
    """Whether Streamlit thinks the app is running inside a frame. On
    Community Cloud it is: the wrapper page holds the app in an iframe."""
    try:
        return bool(st.context.is_embedded)
    except Exception:  # noqa: BLE001 - older runtimes have no such property
        return False


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
    #: The account's ``admin: true`` custom claim, as Firebase's own account
    #: record reports it on sign-in and on every restore. Server-side only:
    #: it lives here in ``st.session_state``, which the browser cannot write,
    #: and nothing on any page can set it.
    admin: bool = False
    #: From Google, when the account signed in that way. Kept, not rendered.
    photo_url: str = ""

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
        expires = float(st.session_state.get(SESSION_EXPIRES_KEY, 0) or 0)
        if expires and time.time() >= expires:
            sign_out("seven_day_deadline_passed")
            return None
        return user
    return None


def current_uid() -> str:
    user = current_user()
    return user.uid if user else ""


def is_admin() -> bool:
    """Does the signed-in account carry the ``admin`` claim? False for
    nobody, and for every account Firebase did not say so about."""
    user = current_user()
    return bool(user is not None and user.admin)


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
        admin=creds.admin,
        photo_url=creds.photo_url,
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
    started = time.time()
    st.session_state[USER_KEY] = user
    st.session_state[SESSION_STARTED_KEY] = started
    st.session_state[SESSION_EXPIRES_KEY] = started + SESSION_SECONDS
    st.session_state["auth_session_cookie_set"] = str(started)
    st.session_state["auth_restore_tried"] = True
    st.session_state["auth_cookie_set"] = user.refresh_token
    st.session_state.pop("auth_cookie_clear", None)
    st.session_state["auth_just_signed_in"] = True
    return user


def sign_out(reason: str = "sign_out") -> None:
    """Forget everything here and tell the browser to drop the cookie. The
    Firebase tokens are simply discarded — there is nothing to keep."""
    log_cleared(reason)
    st.session_state.pop(USER_KEY, None)          # nothing else in this run sees a user
    st.session_state.pop("auth_just_signed_in", None)
    request_reset()                                # …and the rest goes before the next run
    st.session_state.pop("auth_cookie_set", None)
    st.session_state.pop("auth_session_cookie_set", None)
    st.session_state.pop(SESSION_STARTED_KEY, None)
    st.session_state.pop(SESSION_EXPIRES_KEY, None)
    st.session_state["auth_cookie_clear"] = True
    # A reload after signing out must not quietly sign back in from the
    # cookie this session was opened with.
    st.session_state["auth_restore_tried"] = True


def update_display_name(name: str) -> AuthUser | None:
    """Record a new display name on this session's user.

    A label and nothing else: the UID, the tokens, the ``admin`` claim and
    the seven-day deadline are untouched, so no authorization changes and
    nothing has to be re-read. It exists so the account chip shows the new
    name on the very run that saved it, without a sign-out or a reload.
    """
    user = current_user()
    if user is None:
        return None
    fresh = replace(user, display_name=name)
    st.session_state[USER_KEY] = fresh
    return fresh


def delete_account() -> dict[str, int]:
    """Erase the signed-in account: their data first, then the Firebase user.

    Whose account this is comes from the session's own :class:`AuthUser` and
    the live ID token behind it — never a UID, email or anything else the page
    could supply. The data goes first: if Firebase then refuses (it wants a
    recent sign-in for a destructive change) the person is told to sign in
    again rather than being left with an account whose records are gone.

    Returns what was removed. Raises :class:`AuthError` if Firebase refuses,
    leaving the session untouched so the message can be shown.
    """
    user = current_user()
    if user is None:
        raise AuthError(firebase.MESSAGES["INVALID_ID_TOKEN"], "INVALID_ID_TOKEN")
    token = id_token()                      # live, refreshed if it was about to lapse
    if not token:
        raise AuthError(firebase.MESSAGES["INVALID_ID_TOKEN"], "INVALID_ID_TOKEN")

    from monitor import state as state_store

    removed = state_store.purge_user_data(mirror=False)
    print(f"[auth] account deletion: purged {removed} for the signed-in account", flush=True)
    firebase.FirebaseAuth().delete_account(token)
    sign_out("account_deleted")             # clears the session and the cookie
    return removed


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
        "display_name": creds.display_name,
        "id_token": creds.id_token,
        # Server-side only, like a signed-in session's: what lets this tab
        # notice the verification and continue without a second password.
        "refresh_token": creds.refresh_token,
        "cooldown_until": 0.0,   # absolute deadline; 0 = resend allowed now
        "sends": 0,              # requests Firebase actually accepted (HTTP 2xx)
        "checked_at": 0.0,       # when the account record was last read
    }


def continue_if_verified() -> AuthUser | None:
    """Ask Firebase whether the parked account has verified its address; if
    so, open its session — the password was proven at sign-up, the tokens
    are Firebase's own and never left this process — and forget the
    parking. None while still unverified (or if the tokens have lapsed)."""
    pend = pending()
    if pend is None or not pend.get("refresh_token"):
        return None
    pend["checked_at"] = time.time()
    st.session_state[PENDING_KEY] = pend
    try:
        creds = firebase.FirebaseAuth().account_verified(pend["refresh_token"])
    except AuthError as exc:
        print(f"[auth] verification check: {exc.code}", flush=True)
        return None
    if creds is None:
        return None
    user = sign_in_user(creds)
    st.session_state.pop(PENDING_KEY, None)
    return user


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


def record_verification_answer(status: int, code: str) -> None:
    """What Firebase last said to a VERIFY_EMAIL request — status and code
    only — kept with the pending account so the screen can show it."""
    pend = pending()
    if pend is None:
        return
    pend["last_answer"] = {"status": int(status), "code": code, "at": time.time()}
    st.session_state[PENDING_KEY] = pend


def pending() -> dict | None:
    value = st.session_state.get(PENDING_KEY)
    return value if isinstance(value, dict) and value.get("email") else None


def clear_pending() -> None:
    st.session_state.pop(PENDING_KEY, None)


# ──────────────────────────────────────────────────────────────────────────
# Restoring after a reload
# ──────────────────────────────────────────────────────────────────────────
def _parse_cookie_header(raw: str) -> dict[str, str]:
    """``"a=1; b=2"`` → ``{"a": "1", "b": "2"}``. First value of a name wins."""
    jar: dict[str, str] = {}
    for part in raw.split(";"):
        name, sep, value = part.partition("=")
        if sep and name.strip():
            jar.setdefault(name.strip(), value.strip())
    return jar


def _cookie_header_from_runtime() -> str:
    """The raw ``Cookie`` header off this session's own websocket request.

    The last resort, used only when the documented readers answer with
    nothing. Reaching into the runtime is unpleasant, but it is read-only and
    it is the difference between a reload restoring the session and signing
    the person out.
    """
    try:
        from streamlit.runtime import get_instance
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        if ctx is None:
            return ""
        client = get_instance().get_client(ctx.session_id)
        for holder in (getattr(client, "request", None), client):
            headers = getattr(holder, "headers", None)
            if headers is not None:
                value = headers.get("Cookie") or headers.get("cookie")
                if value:
                    return str(value)
    except Exception:  # noqa: BLE001 - never let a private API break the gate
        return ""
    return ""


def _cookie_jar() -> dict[str, str]:
    """Every cookie the browser sent when this session opened.

    ``st.context.cookies`` is the documented source, but it is not dependable
    on its own:

    * it answers with an **empty mapping and no error** whenever Streamlit
      cannot resolve the client context for that run
      (``ContextProxy.cookies`` → ``_get_client_context()`` → ``None``), and
    * it only exists from Streamlit 1.42.

    Either one makes the session cookie look absent, and an absent cookie is
    indistinguishable from "never signed in" — which is what signed people out
    on a browser refresh. So two fallbacks read the very same Cookie header
    from further down before we conclude there is nothing to restore.
    """
    # The browser's own answer first. On Streamlit Community Cloud it is the
    # only one that ever arrives: every server-side reader there reports zero.
    reported = bridge_jar()
    if reported:
        return reported

    try:
        jar = dict(st.context.cookies)
    except Exception:  # noqa: BLE001 - no browser (tests), or Streamlit < 1.42
        jar = {}
    if jar:
        return {k: v for k, v in jar.items() if isinstance(v, str)}

    raw = ""
    try:  # st.context.headers predates st.context.cookies
        raw = str(st.context.headers.get("Cookie") or "")
    except Exception:  # noqa: BLE001
        raw = ""
    return _parse_cookie_header(raw or _cookie_header_from_runtime())




# ──────────────────────────────────────────────────────────────────────────
# The browser bridge
# ──────────────────────────────────────────────────────────────────────────
#: What the browser last told us it holds, and how many runs we have waited.
BRIDGE_JAR_KEY = "auth_bridge_jar"
BRIDGE_RUNS_KEY = "auth_bridge_runs"
BRIDGE_OPS_KEY = "auth_cookie_ops"
#: How many runs the gate will wait for the browser to answer before giving up
#: and showing the login page. The component answers on its first render, so
#: this is a safety net against a browser that never replies — never a loop.
BRIDGE_MAX_RUNS = 3
#: A browser bridge needs a browser. ``AppTest`` has none, so the suite turns
#: this off and the gate decides from the server-side readers alone; the tests
#: that exercise the bridge itself turn it back on.
BRIDGE_ENABLED = True


def _bridge_component():
    """The component, declared once per process."""
    global _BRIDGE
    if _BRIDGE is None:
        import streamlit.components.v1 as components

        _BRIDGE = components.declare_component(
            "tr_auth_bridge", path=str(Path(__file__).parent / "bridge"))
    return _BRIDGE


_BRIDGE = None


def queue_cookie(name: str, value: str, max_age: int) -> None:
    """Ask the browser to write one cookie on the next bridge render."""
    ops = list(st.session_state.get(BRIDGE_OPS_KEY, []))
    ops.append({"name": name, "value": value, "max_age": int(max_age)})
    st.session_state[BRIDGE_OPS_KEY] = ops


def run_bridge(*, signal: str = "") -> dict | None:
    """Render the bridge: carry out any queued cookie writes and bring back
    what the browser holds. ``None`` until the browser has answered.

    ``signal`` is the one thing Python ever asks the bridge to *say*: in a
    Google popup whose sign-in succeeded, :data:`GOOGLE_SIGNAL`, which the
    bridge delivers to the opener and then closes the popup. It is passed
    only after ``sign_in_user`` has run, so the cookie write it carries in
    the same render lands before the opener is told to look.

    The value travels on Streamlit's own component channel — the same
    private websocket every widget value uses — and is never logged or put
    on screen.
    """
    ops = st.session_state.pop(BRIDGE_OPS_KEY, [])
    try:
        value = _bridge_component()(
            ops=ops,
            names=[COOKIE, SESSION_STARTED_COOKIE, OAUTH_STATE_COOKIE],
            signal=signal,
            # Changes whenever there is work to do, so a repeat write is still
            # a new render rather than a no-op.
            nonce=(len(ops) or bool(signal)) and time.time() or 0,
            key="tr_auth_bridge",
            default=None,
        )
    except Exception as exc:  # noqa: BLE001 - the gate must survive anything here
        print(f"[auth] bridge unavailable: {type(exc).__name__}", flush=True)
        return None
    if isinstance(value, dict):
        st.session_state[BRIDGE_JAR_KEY] = value
        return value
    return None


def bridge_jar() -> dict[str, str]:
    """The cookies the browser reported, or {} before it has answered."""
    value = st.session_state.get(BRIDGE_JAR_KEY)
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items()
            if k in (COOKIE, SESSION_STARTED_COOKIE, OAUTH_STATE_COOKIE) and isinstance(v, str) and v}


def bridge_answered() -> bool:
    return isinstance(st.session_state.get(BRIDGE_JAR_KEY), dict)


def bridge_has_opener() -> bool:
    """Did the browser say this window was opened by another — a popup?
    False before the bridge has answered, and in a plain tab."""
    value = st.session_state.get(BRIDGE_JAR_KEY)
    return isinstance(value, dict) and value.get("opener") is True


def google_signal_pending() -> bool:
    """Has the bridge heard a completion signal this session has not yet
    acted on? Acting on it means letting :func:`restore` run once more —
    nothing else. Each signal is counted so a second sign-in in the same
    session is a new event, and each is consumed exactly once.
    """
    value = st.session_state.get(BRIDGE_JAR_KEY)
    if not isinstance(value, dict):
        return False
    try:
        heard = int(value.get("signals") or 0)
    except (TypeError, ValueError):
        return False
    seen = int(st.session_state.get(SIGNALS_SEEN_KEY, 0) or 0)
    if heard <= seen:
        return False
    st.session_state[SIGNALS_SEEN_KEY] = heard
    return True


def allow_restore_again() -> None:
    """Let :func:`restore` run once more in this session: the popup has
    just written a session cookie this session has never looked at."""
    st.session_state.pop("auth_restore_tried", None)


def cookie_report() -> dict[str, object]:
    """Safe, value-free facts about what this run could see.

    Counts and booleans only — never a cookie value, a token or an address —
    so it is safe both in a log and on screen behind ``?diag=1``.
    """
    report: dict[str, object] = {"embedded": _embedded()}
    try:
        import streamlit as _st

        report["streamlit"] = _st.__version__
    except Exception:  # noqa: BLE001
        report["streamlit"] = "?"

    ctx_names: list[str] = []
    try:
        ctx_names = sorted(dict(st.context.cookies).keys())
    except Exception as exc:  # noqa: BLE001
        report["context_cookies_error"] = type(exc).__name__
    report["context_cookies"] = len(ctx_names)

    header_raw = ""
    try:
        header_raw = str(st.context.headers.get("Cookie") or "")
    except Exception as exc:  # noqa: BLE001
        report["headers_error"] = type(exc).__name__
    report["header_cookies"] = len(_parse_cookie_header(header_raw))

    runtime_raw = _cookie_header_from_runtime()
    report["runtime_cookies"] = len(_parse_cookie_header(runtime_raw))

    jar = _cookie_jar()
    report["jar"] = len(jar)
    report["session_cookie"] = COOKIE in jar
    report["started_cookie"] = SESSION_STARTED_COOKIE in jar
    # Names are not secrets and say whether cookies reach the app at all.
    report["names"] = ",".join(sorted(jar)[:8])
    return report


def log_cookie_report() -> None:
    bits = " ".join(f"{k}={v}" for k, v in cookie_report().items())
    print(f"[auth] cookies: {bits}", flush=True)


def _read_cookie(name: str) -> str:
    """One cookie the browser sent when this session opened, decoded.

    The writer is ``encodeURIComponent``; the reader hands back the *raw*
    header value without percent-decoding it. A Firebase refresh token
    contains ``/``, ``+`` and ``=`` often enough that skipping the decode
    returned ``AMf-vBx%2F…`` to Google, which answered INVALID_REFRESH_TOKEN —
    and every browser refresh signed the person out. Decoding here is what
    makes a reload restore the session.
    """
    raw = _cookie_jar().get(name, "")
    # Only a real cookie string counts: without a browser the lookup may hand
    # back a stand-in object (a Mock) that is not a string at all.
    if not isinstance(raw, str) or not raw:
        return ""
    return unquote(raw)


def _cookie() -> str:
    """The session cookie the browser sent when this session opened."""
    return _read_cookie(COOKIE)


def _cookie_started() -> float:
    """The companion timestamp has no authentication power; Firebase's
    refresh-token exchange and account lookup remain the authentication.
    It solely enforces TicketRadar's absolute local seven-day policy."""
    raw = _read_cookie(SESSION_STARTED_COOKIE)
    if not raw.strip():
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _session_window(started: float | None = None) -> tuple[float, float]:
    start = float(started or 0)
    if start <= 0 or start > time.time():
        start = time.time()
    return start, start + SESSION_SECONDS


def restore() -> AuthUser | None:
    """Turn the cookie's refresh token into a signed-in user by asking Google.

    Returns None when there is nothing to restore or Google refuses; either
    way the login page is what comes next.

    The "only once per session" guard is spent when an exchange is actually
    *attempted*, not merely when this is called. Streamlit can hand back an
    empty cookie jar on a run whose client context it has not resolved, and
    marking the attempt used up on such a run is what turned one blind run
    into a permanent sign-out: the cookie was there, the browser kept sending
    it, and nothing ever looked again. With no cookie in sight there is
    nothing to spend, so a later run of the same session can still restore.
    """
    if st.session_state.get("auth_restore_tried"):
        return None

    # The token goes through ``_cookie()`` so there is one reader; the jar is
    # only read again to report how much Streamlit could see this run.
    token = _cookie()
    jar = _cookie_jar()
    # CASE 1 vs the rest. ``cookies_seen`` is the telling number: zero means
    # Streamlit was handed no cookies at all for this run (an unresolved client
    # context, or a proxy that did not forward them), while a non-zero count
    # without ours means the cookie was never written to the browser.
    log_restore(f"cookie_present={bool(token)} cookies_seen={len(jar)} "
                f"session_cookie={COOKIE in jar} started_cookie={SESSION_STARTED_COOKIE in jar} "
                f"embedded={_embedded()}")
    if not token:
        # Nothing to spend: a later run of this session may still restore.
        return None
    st.session_state["auth_restore_tried"] = True
    log_restore("cookie_parse=OK")
    if not firebase.is_configured():
        log_restore("FAILED reason=firebase_not_configured")
        return None
    started, expires = _session_window(_cookie_started())
    if time.time() >= expires:
        log_restore("FAILED reason=session_deadline_passed")
        st.session_state["auth_cookie_clear"] = True
        return None
    log_restore("session_deadline_valid=True")
    client = firebase.FirebaseAuth()
    try:
        creds = client.refresh(token)
        log_restore(f"refresh_exchange=HTTP {client.last_status} {client.last_code}")
        info = client.lookup(creds.id_token)
        log_restore(f"lookup=HTTP {client.last_status} {client.last_code}")
    except AuthError as exc:
        log_restore(f"FAILED reason={exc.code} http={exc.status}")
        st.session_state["auth_cookie_clear"] = True
        return None
    if not info["email_verified"]:
        log_restore("FAILED reason=email_not_verified")
        st.session_state["auth_cookie_clear"] = True
        return None
    user = _from_credentials(creds, email=info["email"], display_name=info["display_name"])
    if info["uid"]:
        user.uid = info["uid"]
    # The refresh exchange knows nothing of claims; the account record does.
    user.admin = info["admin"]
    if not user.uid:
        log_restore("FAILED reason=no_uid")
        st.session_state["auth_cookie_clear"] = True
        return None
    log_restore(f"uid_present=True admin={str(user.admin).lower()} SUCCESS")
    st.session_state[USER_KEY] = user
    st.session_state[SESSION_STARTED_KEY] = started
    st.session_state[SESSION_EXPIRES_KEY] = expires
    # Firebase revalidation can renew its refresh credential, but never the
    # TicketRadar deadline.
    st.session_state["auth_cookie_set"] = user.refresh_token
    st.session_state["auth_session_cookie_set"] = str(started)
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
    expires = float(st.session_state.get(SESSION_EXPIRES_KEY, 0) or 0)
    if expires and time.time() >= expires:
        sign_out("seven_day_deadline_passed")
        return ""
    if user.expires_at - time.time() > REFRESH_MARGIN or not user.refresh_token:
        return user.id_token
    try:
        creds = firebase.FirebaseAuth().refresh(user.refresh_token)
    except AuthError as exc:
        print(f"[auth] token refresh failed: {exc.code}", flush=True)
        return user.id_token
    fresh = replace(user, id_token=creds.id_token, refresh_token=creds.refresh_token,
                    expires_at=time.time() + max(60, creds.expires_in))
    st.session_state[USER_KEY] = fresh
    if creds.refresh_token != user.refresh_token:
        st.session_state["auth_cookie_set"] = creds.refresh_token
    return fresh.id_token


# ──────────────────────────────────────────────────────────────────────────
# Google sign-in: the state that has to survive the redirect
# ──────────────────────────────────────────────────────────────────────────
def oauth_state() -> str:
    """The ``state`` the Google link is minted with, for this session.

    Made once per session and queued for the bridge to write as a cookie
    *in the same run* that draws the link — the gate calls this before
    ``flush_cookie``. The return from Google opens a new session, whose
    only memory of this one is that cookie; :func:`oauth_state_matches`
    compares against it there.
    """
    from auth import google

    state = str(st.session_state.get(OAUTH_STATE_KEY) or "")
    if not state:
        state = google.new_state()
        st.session_state[OAUTH_STATE_KEY] = state
        queue_cookie(OAUTH_STATE_COOKIE, state, google.STATE_SECONDS)
    return state


def oauth_state_matches(returned: str) -> bool:
    """Does the ``state`` Google sent back match the cookie this browser
    holds? Constant-time, and False for a missing or empty either side."""
    import hmac

    expected = _read_cookie(OAUTH_STATE_COOKIE)
    if not expected or not returned:
        return False
    return hmac.compare_digest(expected, returned)


def clear_oauth_state() -> None:
    """Spent — one link, one return. The cookie goes and the next visit to
    the login page mints a fresh one."""
    st.session_state.pop(OAUTH_STATE_KEY, None)
    queue_cookie(OAUTH_STATE_COOKIE, "", 0)


# ──────────────────────────────────────────────────────────────────────────
# The cookie itself — written by the browser, from a zero-height component
# ──────────────────────────────────────────────────────────────────────────
def flush_cookie() -> None:
    """Hand any pending cookie write or clear to the browser bridge.

    The intent is kept in session state and acted on during a full render,
    because a script queued right before ``st.rerun()`` might never reach the
    browser. The bridge performs the write; nothing here renders HTML.
    """
    if st.session_state.pop("auth_cookie_clear", False):
        queue_cookie(COOKIE, "", 0)
        queue_cookie(SESSION_STARTED_COOKIE, "", 0)
    token = st.session_state.pop("auth_cookie_set", "")
    if not token:
        return
    expires = float(st.session_state.get(SESSION_EXPIRES_KEY, 0) or 0)
    max_age = int(expires - time.time()) if expires else 0
    started = st.session_state.pop("auth_session_cookie_set", "")
    # ``Max-Age=0`` *deletes* a cookie. A missing or already-passed deadline
    # would otherwise throw away the session we are trying to persist.
    if max_age <= 0:
        print("[auth] session cookie not written: no time left on the deadline", flush=True)
        return
    queue_cookie(COOKIE, token, max_age)
    if started:
        queue_cookie(SESSION_STARTED_COOKIE, str(started), max_age)


__all__ = [
    "COOKIE",
    "PENDING_KEY",
    "AuthUser",
    "clear_pending",
    "continue_if_verified",
    "GOOGLE_SIGNAL",
    "POPUP_DONE_KEY",
    "allow_restore_again",
    "bridge_has_opener",
    "clear_oauth_state",
    "current_uid",
    "google_signal_pending",
    "is_admin",
    "oauth_state",
    "oauth_state_matches",
    "cookie_report",
    "current_user",
    "log_cleared",
    "log_restore",
    "delete_account",
    "flush_cookie",
    "id_token",
    "mark_verification_sent",
    "pending",
    "record_verification_answer",
    "apply_pending_reset",
    "request_reset",
    "restore",
    "set_pending",
    "sign_in_user",
    "sign_out",
    "update_display_name",
]
