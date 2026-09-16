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

    # Who is signed in is decided *before* the cookie is flushed, because
    # restoring (and refreshing an ID token) can hand back a rotated refresh
    # token. Filling the reserved ``tr_chrome`` slot afterwards writes that new
    # token in this same run — waiting for the next rerun would leave the
    # browser holding a token Google had already replaced.
    user = session.current_user() or session.restore()
    with chrome:
        login.entrance()
        session.flush_cookie()
        if user is None:
            # Nobody signed in, and no cookie was visible. If the browser
            # actually holds one, Streamlit missed it on this run's handshake —
            # reload once so it is read again, rather than showing a login page
            # to somebody who is already signed in.
            session.restore_hint()
    if user is None:
        with _page_container:
            login.render()
        st.stop()
    return user


__all__ = ["app_container", "require_user"]
