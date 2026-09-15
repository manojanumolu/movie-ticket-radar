"""Firebase Authentication for the Streamlit app.

Three modules, one boundary:

``firebase``   the Identity Toolkit REST client — the only thing that talks to
               Firebase. It needs the project's *Web API key* and nothing else:
               no Admin SDK, no service account, nothing that must stay secret
               in a browser.
``session``    who is signed in, kept in ``st.session_state`` (server side —
               the browser cannot write to it) and restored across a reload
               from a refresh-token cookie that Google re-validates.
``gate``       ``require_user()``: the one call ``app.py`` makes.

Nothing in here is imported by the worker; ``tests/test_worker_isolation.py``
keeps it that way.
"""

from auth.session import AuthUser, current_uid, current_user  # noqa: F401

__all__ = ["AuthUser", "current_uid", "current_user"]
