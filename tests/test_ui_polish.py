"""Navigation icons, the account menu, and the first paint of a reload.

Three things a real browser showed on the deployed app:

* Every sidebar item drew the *home* icon. The four icons existed; the CSS
  chose them with ``label:nth-of-type(N)``, and Streamlit wraps each radio
  option in its own ``div``, so every label was the first of its type.
* The account menu's rows were centred by Streamlit's inner flex box, and
  the row with a ``help`` tooltip sat one wrapper deeper than the selector
  reached, so nothing lined up.
* A signed-in reload showed a dark page with one horizontal bar for as long
  as the cookie bridge's round trip took: the bar was Streamlit's skeleton
  for a zero-height component, and the gate drew nothing else on purpose.
  It now draws the brand — the same element the app draws first, in the
  same place — and one honest status line, and hides the placeholder.
"""

from __future__ import annotations

import re

import pytest

from auth import session
from ui import theme
from ui.theme import CSS, NAV_ICONS, _nav_css


# ──────────────────────────────────────────────────────────────────────────
# Sidebar icons
# ──────────────────────────────────────────────────────────────────────────
def test_each_navigation_item_gets_its_own_icon_by_wrapper_position():
    css = _nav_css()
    names = list(NAV_ICONS)                                    # home, monitors, history, settings
    assert len(names) == 4
    uris = []
    for index, name in enumerate(names, start=1):
        # the wrapper's position is what tells the items apart
        rule = re.search(
            rf'\[role="radiogroup"\] > :nth-child\({index}\) label::before[^{{]*\{{ background-image:(url\("[^"]+"\)); \}}', css)
        assert rule, f"no icon rule for item {index} ({name})"
        uris.append(rule.group(1))
        # …and the active colour for the same position
        assert f':nth-child({index}) label:has(input:checked)::before' in css
    assert len(set(uris)) == 4, "two navigation items share an icon"
    # the whole theme carries these rules, so the page gets them on every run
    for index in range(1, 5):
        assert f":nth-child({index}) label::before" in CSS


def test_the_four_icons_are_the_intended_glyphs():
    assert list(NAV_ICONS) == ["home", "monitors", "history", "settings"]
    home, monitors, history, settings = NAV_ICONS.values()
    assert "M4 10.5 12 4l8 6.5" in home[0]                     # a house
    assert "<rect" in monitors[0] and "<circle" in monitors[0]  # a screen with a lens
    assert "3.5 12a8.5 8.5" in history[0] and "12 8v4.4" in history[0]   # a clock with an arrow
    assert "cx='12' cy='12' r='2.9'" in settings[0]           # a gear


# ──────────────────────────────────────────────────────────────────────────
# Account menu
# ──────────────────────────────────────────────────────────────────────────
def test_menu_rows_align_left_at_any_depth_and_the_menu_fits_a_phone():
    assert '[data-testid="stPopoverBody"] .stButton button {' in CSS          # not only `> button`
    assert '[data-testid="stPopoverBody"] .stButton button > div { justify-content:flex-start' in CSS
    assert '[data-testid="stPopoverBody"] .stButton [data-testid="stTooltipHoverTarget"] { width:100%; }' in CSS
    assert "max-width:min(320px, calc(100vw - 24px))" in CSS
    # the destructive row is red in its text, not a bordered button
    assert '[class*="st-key-acct_delete"] button { color:#FF8A8A !important; border-color:transparent !important; }' in CSS


# ──────────────────────────────────────────────────────────────────────────
# The first paint of a reload
# ──────────────────────────────────────────────────────────────────────────
def test_the_bridge_placeholder_is_never_shown():
    assert '[class*="st-key-tr_chrome"] [data-testid="stSkeleton"] { display:none !important; }' in CSS


def test_the_restoring_beat_draws_the_brand_and_a_status_not_the_app_or_login(monkeypatch):
    """Cloud's first run: nobody in memory, the browser has not answered yet.
    The gate waits — and now says so, with the brand in place."""
    from tests.test_auth import run

    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", True)          # like Cloud: only the bridge can answer
    monkeypatch.setattr(session, "run_bridge", lambda **kw: None)  # …and it has not yet
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")

    app = run()
    assert not app.exception
    body = " ".join(m.value for m in app.markdown)
    assert "Restoring your session" in body
    assert 'class="tr-logo"' in body                              # the brand, in the sidebar
    assert "Movie Ticket Monitor" not in body                     # never the app before auth
    assert "SIGN IN" not in body and "Welcome back" not in body   # never the login form while waiting
    assert app.button == []                                       # nothing to act on
    assert app.session_state[session.BRIDGE_RUNS_KEY] == 1


def test_the_brand_in_the_beat_is_the_apps_own_first_sidebar_element(signed_in):
    """Same element, same slot, so the beat does not shift when the app lands."""
    from tests.test_auth import run

    app = run()
    sidebar_first = app.sidebar.markdown[0].value if app.sidebar.markdown else ""
    assert 'class="tr-logo"' in sidebar_first


# ──────────────────────────────────────────────────────────────────────────
# The scroll-to-top helper uses the supported iframe, not the deprecated call
# ──────────────────────────────────────────────────────────────────────────
def test_nothing_in_the_app_uses_the_deprecated_components_html():
    """``st.components.v1.html`` is deprecated (Streamlit logged it on every
    page paint of the deployed app). ``st.iframe`` is the supported way."""
    from pathlib import Path

    for name in ("app.py", "ui/components.py", "ui/flow.py", "ui/login.py", "auth/gate.py", "auth/session.py"):
        src = Path(name).read_text(encoding="utf-8")
        assert "components.v1.html" not in src.replace("``st.components.v1.html``", ""), name
        assert "components.html(" not in src, name


@pytest.mark.parametrize("page", ["Home", "My Monitors", "History", "Settings"])
def test_every_page_paints_without_a_deprecation_warning(page, caplog):
    import logging

    from tests.test_app import run

    with caplog.at_level(logging.WARNING, logger="streamlit"):
        app = run(page)
    assert not app.exception
    assert not [r for r in caplog.records if "components.v1.html" in r.getMessage()]


def test_the_scroll_helper_is_an_iframe_in_a_collapsed_container():
    from tests.test_app import run

    app = run("Home")
    frames = [e for e in app.main if getattr(e, "type", "") == "iframe"]
    assert len(frames) == 1, [getattr(e, "type", "") for e in app.main]
    frame = frames[0]
    assert frame.proto.srcdoc.startswith("<!doctype html>") and "scrollTo" in frame.proto.srcdoc
    assert "Home#" in frame.proto.srcdoc                     # the page token that re-fires it
    # The theme gives its keyed container no room at all.
    assert '[class*="st-key-tr_scrolltop"] { height:0 !important' in CSS


# ──────────────────────────────────────────────────────────────────────────
# Buttons with a tooltip keep the theme's styling
# ──────────────────────────────────────────────────────────────────────────
def test_button_rules_reach_a_button_behind_a_help_tooltip():
    """A ``help`` tooltip wraps the button in stTooltipHoverTarget, one level
    deeper. ``.stButton > button`` rules skipped every such button — the
    card actions went flat the day they gained tooltips. Every button rule
    is the descendant form now, and the wrapper spans the row."""
    import re

    assert ".stButton > button" not in CSS
    assert '.stButton [data-testid="stTooltipHoverTarget"] { display:block; width:100%; }' in CSS
    # The rules that give the card actions their look are still there, in descendant form.
    for selector in ('[class*="st-key-m_stop_"] .stButton button',
                     '[class*="st-key-m_del_"] .stButton button',
                     '[class*="st-key-m_ext_"] .stButton button',
                     '[class~="st-key-m_delete_all"] .stButton button',
                     '.stButton button[kind="primary"]'):
        assert selector in CSS, selector
    # …and the base gloss (gradient + inset highlight + shadow) is intact.
    base = re.search(r"\.stButton button, \.stDownloadButton > button[^{]*\{([^}]*)\}", CSS).group(1)
    assert "linear-gradient" in base and "inset 0 1px 0" in base and "box-shadow" in base


def test_the_card_actions_carry_tooltips_and_are_styled(make_monitor):
    from monitor.state import upsert_monitor
    from tests.test_app import run

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    app = run("My Monitors")
    stop = app.button(key=f"m_stop_{monitor.id}")
    assert stop.proto.help                                   # the tooltip is there…
    assert '[class*="st-key-m_stop_"] .stButton button' in CSS   # …and so is the rule that reaches past it
