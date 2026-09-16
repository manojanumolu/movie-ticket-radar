"""The phone layout, and the language row on monitor cards.

The mobile bug (15 Sep 2026): on a 390px phone the page kept its desktop
columns — a 60px right rail with "No active monitors" wrapping one letter
per line, District and PVR lockups overlapping, five unlabelled step boxes.
The cause was one line of theme CSS: a global ``[data-testid="stColumn"]
{ min-width: 0 }`` that cancelled Streamlit's own phone rule (each column
gets ``min-width: calc(100% - …)`` under 640px), so ``st.columns`` never
stacked at any width.

These tests pin the contract the theme now keeps, and the way the flow lays
grids out so a phone can fold them without scrambling their order. The real
browser check is ``tools/ui_audit.py`` (Playwright, needs the app running).
"""

from __future__ import annotations

import re

import pytest

from monitor.models import ANY_FORMAT, MovieRef, TheatreTarget
from monitor.state import record_history, upsert_monitor
from ui import theme

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def mobile_block() -> str:
    """The CSS inside ``@media (max-width: 768px) { … }``."""
    start = theme.CSS.index("@media (max-width: 768px) {")
    depth, i = 0, start
    while True:
        ch = theme.CSS[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return theme.CSS[start:i + 1]
        i += 1


def rule(css: str, selector: str, *, exact: bool = False) -> str:
    """The declarations of the first rule whose selector list contains
    ``selector`` (or, with ``exact``, is exactly it)."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        found = m.group(1).strip()
        if (found == selector) if exact else (selector in found):
            return m.group(2)
    raise AssertionError(f"no rule for {selector!r}")


# ──────────────────────────────────────────────────────────────────────────
# The theme's phone contract
# ──────────────────────────────────────────────────────────────────────────
def test_the_column_min_width_reset_is_desktop_only():
    """The one line that broke phones must never be global again."""
    global_rules = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", theme.CSS)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", global_rules):
        if '[data-testid="stColumn"]' in m.group(1) and "min-width" in m.group(2):
            raise AssertionError(f"global column min-width rule: {m.group(0)[:120]}")
    desktop = re.search(r"@media \(min-width: 769px\) \{([^}]*\})", theme.CSS)
    assert desktop and 'stColumn"] { min-width: 0; }' in desktop.group(1)


def test_every_column_stacks_on_a_phone():
    css = mobile_block()
    col = rule(css, '[data-testid="stColumn"]', exact=True)
    assert "flex: 1 1 100% !important" in col
    assert "min-width: 100% !important" in col
    assert "flex-wrap: wrap !important" in rule(css, '[data-testid="stHorizontalBlock"]')


def test_grids_pairs_and_the_step_rail_are_the_only_exceptions():
    css = mobile_block()
    grid = rule(css, '[class*="st-key-trgrid_"] [data-testid="stColumn"]', exact=True)
    assert "--tr-cols" in grid and "min-width: 0 !important" in grid
    assert "flex-wrap: nowrap !important" in rule(css, '[class*="st-key-trpair"] [data-testid="stHorizontalBlock"]')
    assert "flex-wrap: nowrap !important" in rule(css, '[class*="st-key-trsteps"] [data-testid="stHorizontalBlock"]')
    # posters two-up, theatre rows one-up, the three-tile pickers three-up
    assert "--tr-cols: 1" in rule(css, '[class*="st-key-trgrid_th"]')
    assert "--tr-cols: 3" in rule(css, '[class*="st-key-trgrid_interval"]')
    assert "--tr-cols: 2" in rule(theme.CSS[theme.CSS.index("@media (max-width: 600px)"):], 'st-key-trgrid_movie')


def test_phone_platform_cards_hero_and_step_rail_reflow():
    css = mobile_block()
    assert "grid-template-columns: 1fr 1fr" in rule(css, ".tr-platforms")
    assert "grid-column: span 2" in rule(css, ".tr-platform.live")
    assert "white-space: nowrap" in rule(css, ".tr-platform .lockup .name")   # never C-i-n-e-m-a-s
    assert "clamp(" in rule(css, ".tr-hero h1")
    assert "display: flex" in rule(css, ".tr-hero-mark")                        # the mark stays, reflowed
    assert "display: block" in rule(css, ".tr-step-mobile")                     # STEP n OF 5 · NAME
    assert "display: none" in rule(css, ".tr-step-pip .l")
    gutter = rule(css, ".block-container")
    assert "1rem" in gutter                                                      # 16px side gutter


def test_the_sidebar_can_be_reopened():
    """Streamlit 1.59 keeps the open-sidebar button inside stToolbar; hiding
    the toolbar wholesale left phones (and a collapsed desktop) with no nav."""
    assert '[data-testid="stToolbar"], #MainMenu' not in theme.CSS
    assert 'button[data-testid="stExpandSidebarButton"]' in theme.CSS
    assert '[data-testid="stAppDeployButton"]' in theme.CSS  # the Deploy button still goes


def test_app_opens_the_sidebar_automatically_not_forced(tmp_path):
    from pathlib import Path

    source = Path("app.py").read_text(encoding="utf-8")
    assert 'initial_sidebar_state="auto"' in source


# ──────────────────────────────────────────────────────────────────────────
# Grids are laid out row by row, inside keyed containers
# ──────────────────────────────────────────────────────────────────────────
def test_grid_yields_rows_in_reading_order():
    from unittest.mock import MagicMock, patch

    from ui import flow

    rows: list[str] = []
    cols: list[int] = []

    class Ctx:
        def __init__(self, key): rows.append(key)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def columns(n, gap="small"):
        cols.append(n)
        return [MagicMock(name=f"col{i}") for i in range(n)]

    with patch.object(flow.st, "container", Ctx), patch.object(flow.st, "columns", columns):
        laid = [(c._mock_name, item) for c, item in flow.grid(list("ABCDEFG"), 3, "x")]
    assert rows == ["trgrid_x_0", "trgrid_x_1", "trgrid_x_2"]
    assert cols == [3, 3, 3]
    assert [item for _, item in laid] == list("ABCDEFG")
    assert [c for c, _ in laid] == ["col0", "col1", "col2"] * 2 + ["col0"]


# ──────────────────────────────────────────────────────────────────────────
# Language on My Monitors, the rail, and History
# ──────────────────────────────────────────────────────────────────────────
def _monitor(language: str):
    from datetime import datetime, timedelta

    from config.timezone import IST
    from monitor.models import Monitor

    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET00514163", title="Avengers Endgame: Encore",
                       region_code="HYD", region_slug="hyderabad", language=language),
        targets=[TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Dolby Cinema")],
        monitor_until=datetime.now(IST) + timedelta(days=2), notify_email="w@example.com",
        # Owned by the signed-in test session (``signed_in`` fixture), so the
        # rendered app — which shows only the signed-in person's own records —
        # actually sees it.
        owner_uid="uid-test-1",
    )


def _run(page):
    app = AppTest.from_file("app.py", default_timeout=60)
    app.session_state["page"] = page
    return app.run()


def _markup(app) -> str:
    return " ".join(m.value for m in app.markdown)


def test_my_monitors_shows_the_language_under_the_title():
    upsert_monitor(_monitor("Telugu"), mirror=False)
    body = _markup(_run("My Monitors"))
    assert '<div class="title">Avengers Endgame: Encore</div>' in body
    assert re.search(r'<div class="lang">.*?<span>Telugu</span></div>', body, re.S)
    # …and not smuggled into the "where" line any more
    assert re.search(r'class="where">BookMyShow · Hyderabad\s*· created', body)


def test_the_rail_card_shows_the_language_too():
    upsert_monitor(_monitor("English"), mirror=False)
    body = _markup(_run("Home"))
    assert re.search(r'<div class="title">Avengers Endgame: Encore</div>\s*<div class="lang">.*?<span>English</span>', body, re.S)


def test_a_monitor_without_a_language_shows_none_rather_than_a_guess():
    upsert_monitor(_monitor(""), mirror=False)
    body = _markup(_run("My Monitors"))
    assert '<div class="lang">' not in body
    assert "Avengers Endgame: Encore" in body


def test_history_shows_the_language_from_the_record_or_the_monitor():
    telugu = _monitor("Telugu")
    upsert_monitor(telugu, mirror=False)
    record_history(telugu, "TICKETS_LIVE", "ALLU Cinemas · Dolby Cinema", mirror=False)
    # An older record that never stored a language, for a monitor that no
    # longer exists: the title stands alone.
    from config.store import load_history, save_history
    history = load_history()
    history.append({"monitor_id": "gone", "kind": "STOPPED", "message": "Stopped", "movie": "Old Film",
                    "poster_url": "", "targets": ["Somewhere · Any format"], "at": "2026-09-01T10:00:00+05:30",
                    "owner_uid": "uid-test-1"})
    save_history(history, mirror=False)

    body = _markup(_run("History"))
    assert re.search(r'Avengers Endgame: Encore <span class="lang">· Telugu</span>', body)
    assert 'Old Film</div>' in body and "Old Film <span" not in body
    assert history[0]["language"] == "Telugu"          # new records carry it


def test_history_falls_back_to_the_monitor_s_stored_language():
    english = _monitor("English")
    upsert_monitor(english, mirror=False)
    from config.store import save_history
    save_history([{"monitor_id": english.id, "kind": "CREATED", "message": "Monitor created.",
                   "movie": english.movie.title, "poster_url": "", "targets": [], "at": "2026-09-15T16:19:00+05:30",
                   "owner_uid": "uid-test-1"}],
                 mirror=False)
    body = _markup(_run("History"))
    assert re.search(r'Avengers Endgame: Encore <span class="lang">· English</span>', body)


def test_email_carries_the_language_on_the_city_line(make_monitor, at):
    from dataclasses import replace

    from monitor.changes import Change, ChangeKind
    from monitor.models import Availability
    from notifications import email as mail

    monitor = make_monitor()
    monitor.movie = replace(monitor.movie, language="English")
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=monitor.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title=monitor.movie.title,
                    previous=Availability.NOT_BOOKABLE, current=Availability.AVAILABLE, date_code="20260925",
                    booking_url="https://in.bookmyshow.com/x", time_labels=["07:30 PM"], detected_at=at)
    subject, html, text = mail.render_change(monitor, change)
    assert "Dolby Cinema &middot; English · Hyderabad" in html
    assert "Allu Cinemas · Dolby Cinema · English · Hyderabad" in text
    assert subject == "TICKETS ARE LIVE — Avengers: Endgame Encore at Allu Cinemas"   # subject unchanged
