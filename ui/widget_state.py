"""Keep a widget's value in step between the server and the browser.

Streamlit sends a keyed widget's value to the browser only on the run where
it was set through ``st.session_state``. On every other run the browser is
trusted to remember it, and the server sends only the widget's built-in
default.

That trust breaks after a run that ends early: ``st.stop()`` (the auth
gate's restoring and login screens, storage being unavailable) or an
uncaught exception. Streamlit then skips its server-side clean-up of widgets
that run did not draw, so the session keeps the value, while the browser
forgets the widget. The next run that draws it again sends no value, and the
browser falls back to the built-in default. For ``st.time_input`` that
default is ``"now"`` on the *server's* clock (UTC on Streamlit Cloud): the
Explorer's From and To both showed the same server time, and the page
refused the window. For the navigation radio the default is the first page:
the rail showed Home while another page was open, and the next click went
there.

:func:`hold` closes the gap: called before the widget is drawn, it writes
the value the session already holds back to the same key. Nothing changes
for the person — their own pick is that value — but the write marks it as
set through the Session State API, so this run sends it to the browser, and
a browser that forgot the widget is told what it shows. Only a missing or
invalid value is replaced, and only by the page's own default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st


def hold(key: str, default: Any, *, valid: Callable[[Any], bool] | None = None) -> Any:
    """Re-assert ``key``'s value for this run (see the module docstring).

    Must run before the widget with ``key`` is created on this run; Streamlit
    refuses a write to a widget key after that point.
    """
    value = st.session_state.get(key, default)
    if valid is not None and not valid(value):
        value = default
    st.session_state[key] = value
    return value


__all__ = ["hold"]
