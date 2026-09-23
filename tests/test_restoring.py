"""The beat between opening TicketRadar and being back inside it.

It replaced a blank dark page with a skeleton bar and one grey line. The
thing that must stay true of the replacement is that it is a *cover*, not a
delay: it may not wait, poll, sleep, rerun or otherwise make the restore
take one millisecond longer than it already does. These tests pin that, and
that nothing private is on screen while the gate is still deciding who is
here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from auth import session
from ui import restoring

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

SRC = Path(restoring.__file__).read_text(encoding="utf-8")


@pytest.fixture
def waiting(monkeypatch):
    """Cloud's first run: nobody in memory, the browser has not answered."""
    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", True)
    monkeypatch.setattr(session, "run_bridge", lambda **kw: None)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")


def body(app) -> str:
    return " ".join(m.value for m in app.markdown)


# ──────────────────────────────────────────────────────────────────────────
# It costs nothing
# ──────────────────────────────────────────────────────────────────────────
def test_the_beat_never_waits_polls_or_reruns():
    for banned in ("time.sleep", "sleep(", "st.rerun", "run_every", "st.fragment",
                   "autorefresh", "setInterval", "setTimeout", "<script"):
        assert banned not in SRC, f"the loading beat must not use {banned!r}"


def test_the_beat_is_css_only_and_moves_nothing_but_the_compositor():
    """Every animated property is ``transform`` or ``opacity`` — a slow phone
    spends its main thread on the restore, not on this."""
    animated = set(re.findall(r"^\s*(?:from|to|\d+%(?:,\s*\d+%)*)\s*\{([^}]*)\}",
                              restoring.CSS, re.M))
    for block in animated:
        for decl in block.split(";"):
            prop = decl.split(":")[0].strip()
            if prop:
                assert prop in {"transform", "opacity", "color"}, prop


def test_it_is_one_markdown_block_so_the_app_replaces_it_cleanly():
    markup = restoring.markup()
    assert markup.count("<style>") == 1
    assert markup.startswith("<style>")


def test_reduced_motion_is_honoured():
    assert "@media (prefers-reduced-motion: reduce)" in restoring.CSS
    tail = restoring.CSS[restoring.CSS.index("@media (prefers-reduced-motion: reduce)"):]
    assert "animation:none !important" in tail


# ──────────────────────────────────────────────────────────────────────────
# What it says
# ──────────────────────────────────────────────────────────────────────────
def test_the_line_is_ticketradars_own_and_the_status_is_honest():
    assert restoring.LEAD == "Waiting for the next showtime."
    assert "Restoring your session" in restoring.STATUS
    markup = restoring.markup()
    assert restoring.LEAD in markup and "Restoring your session" in markup


def test_the_radar_and_the_brand_are_the_apps_own(waiting):
    from tests.test_auth import run

    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    page = body(app)
    assert 'class="tr-radar' in page and 'data-state="detecting"' in page
    assert 'class="tr-logo"' in page                       # same element the app draws
    assert "TICKETRADAR" in page


def test_nothing_private_and_nothing_to_click_while_it_waits(waiting):
    from tests.test_auth import run

    app = run()
    page = body(app)
    assert app.button == [], "the beat offers nothing to act on"
    assert "Movie Ticket Monitor" not in page               # never the app before auth
    assert "SIGN IN" not in page and "Welcome back" not in page
    # Nothing of anybody's: the beat's own markup carries no address at all.
    assert "@" not in re.sub(r"@(media|keyframes|import|font-face)", "", restoring.markup())
    assert app.session_state[session.BRIDGE_RUNS_KEY] == 1


def test_the_gate_still_hands_markdown_nothing_indented():
    """``st.markdown`` parses Markdown first and an indented line becomes a
    code block — which is how this screen once printed its own ``<div>`` as
    text. Everything goes through ``ui.components.html``, which cleans it."""
    src = Path("auth/gate.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("<div", "<style", "<span")) and line.startswith("    "):
            raise AssertionError(f"indented raw markup: {stripped[:60]}")
    assert "C.html(" in SRC or "components.html" in SRC
