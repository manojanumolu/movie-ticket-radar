"""The authenticated boundary around the app.

``require_user()`` is the one call ``app.py`` makes before it renders
anything of its own. Either somebody is signed in — from this session, or
restored from the cookie Google just re-validated — and the app continues
with their :class:`AuthUser`; or the login page is drawn and the script
stops right there. Nothing below the call runs for a visitor.

Two fixed, always-present slots
-------------------------------
Both the login page and the signed-in app render inside keyed containers this
gate creates *unconditionally, in the same order, every run*:

* ``tr_chrome`` — the zero-height cookie writer and the sign-in animation.
* ``tr_page``   — the login page, or the whole app.

Streamlit reconciles the DOM by an element's position. A keyed
``st.container`` that moves position between runs — which is exactly what
happened when the cookie writer's iframe appeared on one run and not the next
— is not garbage-collected, so the previous page's account chip and the login
shell piled up on top of each other after a sign-out/sign-in. Pinning the two
slots to a fixed position keeps every login⇄app transition a clean swap: the
app renders inside :func:`app_container`.
"""

from __future__ import annotations

import streamlit as st

from auth import firebase, session
from auth.session import AuthUser

#: The ``tr_page`` slot from the current run, so ``main()`` can render the app
#: into the very container the gate reserved. Valid only within one run.
_page_container = None


def app_container():
    """The fixed ``tr_page`` slot the authenticated app renders into."""
    return _page_container


def require_user() -> AuthUser:
    global _page_container

    firebase.log_config_once()
    # A sign-out or a sign-in on the previous run asked for the session to
    # be wiped; this is the first thing that runs, before any widget.
    session.apply_pending_reset()

    from ui import login

    # A return from Firebase's "email verified" page may sign the user in
    # (or prefill the sign-in form). It has to run before we decide who is
    # signed in, and before either page is drawn.
    login.handle_verified_return()

    # The two fixed slots, always here and always in this order.
    chrome = st.container(key="tr_chrome")
    _page_container = st.container(key="tr_page")

    # The in-memory session is the fast path: while this Streamlit session is
    # authenticated nothing is read from the browser at all.
    from_memory = session.current_user()

    with chrome:
        login.entrance()
        if from_memory is None:
            # Somebody may be about to see the login page, and its Google link
            # needs a ``state`` the browser already holds when it is clicked.
            # Minting it here — before the bridge renders — means the same
            # bridge pass that reports the jar also writes it, so the link
            # and its cookie agree from the first paint. If a restore then
            # signs the person in, an unused ten-minute cookie is all it cost.
            login.prepare_google()
        # Queue any pending cookie write, then run the bridge once. The bridge
        # performs the writes and brings back what the browser holds — on
        # Community Cloud that is the only way the cookie ever reaches Python.
        session.flush_cookie()
        session.run_bridge()

    user = from_memory
    if user is None:
        user = session.restore()
        if user is None and session.BRIDGE_ENABLED and not session.bridge_answered():
            # The browser has not answered yet. Rendering the login page now
            # would be a guess, so wait for the component's reply instead —
            # bounded, so a browser that never answers still reaches login.
            runs = int(st.session_state.get(session.BRIDGE_RUNS_KEY, 0)) + 1
            st.session_state[session.BRIDGE_RUNS_KEY] = runs
            if runs <= session.BRIDGE_MAX_RUNS:
                print(f"[auth] gate: waiting for the browser bridge (run {runs})", flush=True)
                with _page_container:
                    _restoring()
                st.stop()
            print("[auth] gate: bridge never answered; showing login", flush=True)
        if user is None:
            # Back from Google? The browser has answered, so the state cookie
            # is readable; a genuine return signs in and reruns, anything
            # else becomes a line on the login page.
            user = login.handle_google_return()

    session.log_cookie_report()
    # Closes the loop on the two cases a restore log alone cannot show: a user
    # that was restored but is not rendered, and a user cleared after this
    # point. Booleans only — never a UID, an email or a token.
    print(f"[auth] gate: in_memory={from_memory is not None} user_present={user is not None} "
          f"admin={str(bool(user is not None and user.admin)).lower()} "
          f"renders={'app' if user is not None else 'login'}", flush=True)

    if user is None:
        with _page_container:
            login.render()
            _diag_panel()
        st.stop()
    return user


def _restoring() -> None:
    """The beat while the browser is asked what it holds: deliberately nothing.

    Not the login page — showing a sign-in form to somebody who is already
    signed in is the bug this whole path exists to avoid — and not a spinner
    either. The restore is one component round trip, so anything drawn here
    would be a flash and a layout shift on every reload.

    Nothing is rendered at all, which also means nothing can be mis-rendered:
    ``st.markdown`` runs a fragment through a Markdown parser first, and an
    indented line after the opening one becomes a *code block*, which is how
    this very screen printed its own ``<div class="tr-restoring">`` as text.
    ``ui.components.clean_html`` exists for that trap; there is no markup left
    here to need it.

    The page is not blank white: ``app.py`` calls ``inject()`` at import, so
    the theme's dark background is already painted by the time the gate runs.
    """
    return


def _diag_panel() -> None:
    """``?diag=1``: the same value-free facts the log carries, on screen.

    For diagnosing a deployment whose logs are not to hand. It shows counts and
    booleans only — no cookie value, token, key, UID or address — and nothing
    at all unless the parameter is present.
    """
    try:
        if str(st.query_params.get("diag") or "") != "1":
            return
    except Exception:  # noqa: BLE001
        return
    report = session.cookie_report()
    st.caption("cookie diagnostics — " + " · ".join(f"{k}={v}" for k, v in report.items()))


__all__ = ["app_container", "require_user"]
