"""Phase 3B — Home's skeleton (``ui/home.py`` + ``app.page_home``).

Reading order is the page: status line, live card, the rail's command
panel, then the wizard — four keyed containers, no ``st.columns``. From
1150px a CSS grid on the Home root lays them out as a dashboard; below it
they simply stack, so the rail can never be a 117px column again. With a
monitor running, an untouched wizard waits behind one tile.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from monitor.models import Availability
from monitor.state import MonitorState, record_history, save_state, upsert_monitor
from platforms import PLATFORMS
from tests.test_app import run, seeded, text  # noqa: F401 - seeded is a fixture
from ui import home
from ui.home import STATE_LINES

pytestmark = pytest.mark.usefixtures("signed_in")


def _live_state(at):
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=4, success_count=4)
    live = state.target("ALLU::Dolby Cinema")
    live.availability = Availability.AVAILABLE
    live.since = at
    live.booking_url = "https://in.bookmyshow.com/x"
    live.time_labels = ["07:30 PM"]
    return state


def _markup(app) -> str:
    """Home's markup with every ``<style>`` block removed, so a class name
    inside a stylesheet is never mistaken for the element that carries it."""
    return re.sub(r"<style>.*?</style>", "", text(app), flags=re.S)


def _order(body: str, *needles: str) -> list[int]:
    positions = [body.find(n) for n in needles]
    assert all(p >= 0 for p in positions), dict(zip(needles, positions))
    return positions


# ──────────────────────────────────────────────────────────────────────────
# The status line
# ──────────────────────────────────────────────────────────────────────────
def test_the_status_line_says_what_is_watched_from_the_data_in_hand(make_monitor, at):
    monitor = make_monitor()
    body = home.hero_markup([monitor], {monitor.id: _live_state(at)},
                              platforms=PLATFORMS, city="Hyderabad", theatre_count=97)
    assert '<div class="tr-hero">' in body and "<h1>Watching <em>Avengers: Endgame Encore</em></h1>" in body   # the app's own marker, kept
    assert "Movie Ticket Monitor<span class=\"city\"> · Hyderabad</span>" in body
    assert "2 theatres" in body and "1 theatre live" in body                             # counted, not typed
    assert 'data-state="success"' in body and STATE_LINES["success"] in body           # the radar and the room follow the truth
    assert body.count('class="tr-craft ') == len(home.HERO_MARKS) and "And… action." in body   # the crafts, each with a line
    assert "BookMyShow · Connected · Hyderabad · 97 theatres" in body
    assert body.count("Coming soon") == 2 and "District" in body and "PVR Cinemas" in body
    assert "logo-" not in body and "base64" not in body                                  # no inline images


def test_several_monitors_are_counted_honestly_and_none_reads_as_idle(make_monitor):
    a, b, c = make_monitor(), make_monitor(), make_monitor()
    c.stop()
    body = home.hero_markup([a, b, c], {}, platforms=PLATFORMS, city="Hyderabad", theatre_count=0)
    assert "+1 more" in body and "4 theatres" in body and 'class="live"' not in body
    assert 'data-state="scanning"' in body and STATE_LINES["scanning"] in body
    assert "theatres appear once a movie is synced" in body

    idle = home.hero_markup([c], {}, platforms=PLATFORMS, city="Hyderabad", theatre_count=97)
    assert "Nothing on the radar <em>yet</em>" in idle and 'data-state="idle"' in idle


# ──────────────────────────────────────────────────────────────────────────
# Reading order and the layout contract
# ──────────────────────────────────────────────────────────────────────────
def test_home_renders_status_live_rail_then_wizard(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: _live_state(at)}, mirror=False)

    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    body = _markup(app)
    status, live, rail, wizard = _order(body, '<div class="tr-hero">', "TICKETS ARE LIVE", "Active monitor", "tr-newalert")
    assert status < live < rail < wizard
    assert body.count('<div class="tr-hero">') == 1 and body.count("Active monitor") == 1   # nothing drawn twice
    assert "Where are you watching?" not in body                                            # the wizard waits


def test_the_page_is_four_keyed_containers_and_no_columns():
    import app

    src = inspect.getsource(app.page_home)
    keys = re.findall(r'st\.container\(key="(tr\w+)"\)', src)
    assert keys == ["trhome", "trstatus", "trlive", "trrail", "trwizard", "tratmo"]
    assert "st.columns(" not in src                                                # the squeeze is gone
    assert "detail.dialog(monitors, states)" in src and "rail(monitors, states, history)" in src


def test_the_desktop_grid_and_the_tablet_column_are_in_homes_own_stylesheet():
    css = home.CSS
    assert css.startswith("<style>") and len(css) < 32000                          # radar + crafts + the room, Home only
    desktop = css[css.index("@media (min-width: 1150px)"):]
    assert '[class*="st-key-trhome"] { display:grid !important;' in desktop
    assert f"minmax({home.RAIL_MIN}px, 1.4fr)" in desktop and home.RAIL_MIN >= 320
    assert ':has(> [class*="st-key-trrail"]) { grid-column: 2; grid-row: 2 / span 3; margin-bottom:0; }' in desktop
    assert ':has(> [class*="st-key-tratmo"]) { grid-column: 1; grid-row: 4; align-self: stretch; margin-bottom:0; }' in desktop
    assert "grid-template-rows: auto auto auto 1fr" in desktop
    assert ':has(> [class*="st-key-trstatus"]) { grid-column: 1 / -1; grid-row: 1; }' in desktop
    assert "@media (min-width: 769px) and (max-width: 1149px)" in css        # the tablet is its own tier
    assert "@media (max-width: 768px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "http" not in css.replace("http://www.w3.org", "")                # no external assets


# ──────────────────────────────────────────────────────────────────────────
# The wizard behind a tile
# ──────────────────────────────────────────────────────────────────────────
def test_a_running_monitor_folds_the_untouched_wizard_behind_one_tile(make_monitor):
    upsert_monitor(make_monitor(), mirror=False)
    app = run()
    assert not app.exception
    assert app.button(key="new_alert").label == "Set up a new alert"
    assert not any(b.key == "loc_hyderabad" for b in app.button)

    app.button(key="new_alert").click().run()                                # one click, one run
    assert not app.exception
    assert app.session_state[home.WIZARD_OPEN] is True
    assert any(b.key == "loc_hyderabad" for b in app.button)                 # the same wizard, untouched
    assert not any(b.key == "new_alert" for b in app.button)

    app.button(key="wizard_hide").click().run()
    assert home.WIZARD_OPEN not in app.session_state
    assert any(b.key == "new_alert" for b in app.button)


def test_a_wizard_in_progress_is_never_hidden(make_monitor, seeded):
    upsert_monitor(make_monitor(), mirror=False)
    app = run(step=2, location="hyderabad")
    assert not app.exception
    assert not any(b.key == "new_alert" for b in app.button)
    assert any(b.key == "back" for b in app.button)                          # step 2's Back is there
    assert not any(b.key == "wizard_hide" for b in app.button)               # Hide is only for a pristine, opened wizard


def test_with_nothing_running_the_wizard_is_the_page(seeded):
    app = run()
    assert not app.exception
    body = text(app)
    assert "Nothing on the radar" in body and "No active monitors" in body
    assert "tr-atmo" not in body.replace(".tr-atmo", "")                     # the room only fills a folded column
    assert any(b.key == "loc_hyderabad" for b in app.button)
    assert not any(b.key in ("new_alert", "wizard_hide") for b in app.button)
    assert not home.wizard_collapsed(0)


def test_starting_a_monitor_puts_the_wizard_away_again():
    import app

    src = inspect.getsource(app.start_monitor)
    assert "flow.request_reset()" in src and "home.hide_wizard()" in src


# ──────────────────────────────────────────────────────────────────────────
# What must not have moved
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_keeps_its_keys_callbacks_and_recent_activity(make_monitor, at):
    import app

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: _live_state(at)}, mirror=False)
    for i in range(5):
        record_history(monitor, "CREATED", f"event {i}", mirror=False)
    other = make_monitor()                                                  # never saved: its history must not show
    record_history(other, "CREATED", "ghost", mirror=False)

    a = run()
    assert not a.exception
    assert a.button(key=f"detail_{monitor.id}").label == "View details"
    assert a.button(key=f"stop_{monitor.id}").label == "Stop monitoring"
    body = text(a)
    assert body.count('class="tr-history') == 3 and "ghost" not in body   # limit 3, deleted/unknown filtered

    rail = inspect.getsource(app.live_monitor_panel)
    assert "on_click=detail.open_detail" in rail and "on_click=do_stop_monitor_from_rail" in rail
    assert "C.target_rows(monitor, fresh)" in rail and "load_states_for([monitor.id])" in rail
    assert "recent_activity(monitors, history)" in inspect.getsource(app.rail)
    assert inspect.signature(app.recent_activity).parameters.keys() == {"monitors", "history"}


def test_the_skeleton_adds_no_reads_no_fragments_no_widgets_for_decoration():
    import app

    home_src = Path("ui/home.py").read_text(encoding="utf-8")
    code = home_src.split('"""', 2)[2]
    assert "load_" not in code and "state_store" not in code and "st.rerun" not in code
    assert code.count("flow.pick(") == 1 and "st.button(" not in code        # the one tile, nothing else
    page = inspect.getsource(app.page_home) + inspect.getsource(app.wizard)
    assert "load_" not in page and "st.rerun()" not in page
    assert Path("app.py").read_text(encoding="utf-8").count("@st.fragment") == 1
