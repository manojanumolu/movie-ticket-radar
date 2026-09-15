"""The authenticated boundary around the app.

``require_user()`` is the one call ``app.py`` makes before it renders
anything of its own. Either somebody is signed in — from this session, or
restored from the cookie Google just re-validated — and the app continues
with their :class:`AuthUser`; or the login page is drawn and the script
stops right there. Nothing below the call runs for a visitor.
"""

from __future__ import annotations

import streamlit as st

from auth import session
from auth.session import AuthUser


def require_user() -> AuthUser:
    # A sign-out or a sign-in on the previous run asked for the session to
    # be wiped; this is the first thing that runs, before any widget.
    session.apply_pending_reset()
    user = session.current_user() or session.restore()
    if user is None:
        from ui import login

        login.render()
        st.stop()
    return user


__all__ = ["require_user"]
