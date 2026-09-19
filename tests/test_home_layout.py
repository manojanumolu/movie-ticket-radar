"""Phase 3 — the original Home, made alive (``ui/home.py`` + ``app.page_home``).

Home is the page built before Phase 3 — hero, Platform logo cards, live
card, the wizard, the rail — drawn by the same components as before, in
reading order inside keyed containers (no ``st.columns``, so the rail is
never a squeezed column). ``ui.home`` adds only what the Login room has:
ambience behind the cards, the radar in the hero, and all twenty-four
crafts with a line of cinema each.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from monitor.models import Availability
from monitor.state import MonitorState, record_history, save_state, upsert_monitor
from tests.test_app import run, seeded, text  # noqa: F401 - seeded is a fixture
from ui import crafts, home

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
# The original Home is all there
# ──────────────────────────────────────────────────────────────────────────
def test_the_original_hero_platform_cards_and_wizard_are_drawn_with_a_monitor_running(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: _live_state(at)}, mirror=False)

    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    body = _markup(app)
    # the hero, word for word
    assert '<div class="tr-hero">' in body and "<h1>Movie Ticket Monitor</h1>" in body
    assert "Know the moment your tickets go live." in body
    assert "We watch the booking page for you, so you don" in body
    assert "<span>Some stories</span><span>are worth</span><b>the wait</b>" in body
    # the Platform shelf with the real logo cards, in their original states
    assert '<span class="tr-eyebrow">Platform</span>' in body
    assert 'alt="BookMyShow"' in body and "Connected" in body
    assert 'alt="District by Zomato"' in body and 'alt="PVR Cinemas"' in body
    assert body.count("Coming soon") >= 2 and 'class="tr-platform live"' in body
    # the wizard is open, as it always was — even with a monitor running
    assert "Where are you watching?" in body
    assert any(b.key == "loc_hyderabad" for b in app.button)
    assert not any(b.key in ("new_alert", "wizard_hide") for b in app.button)
    # reading order: hero → platform → live → rail → wizard
    hero, platform, live, rail, wizard = _order(body, '<div class="tr-hero">', 'class="tr-platforms"',
                                                "TICKETS ARE LIVE", "Active monitor", "Where are you watching?")
    assert hero < platform < live < rail < wizard
    assert body.count('<div class="tr-hero">') == 1 and body.count('class="tr-platforms"') == 1


def test_the_rail_keeps_its_keys_callbacks_styling_and_recent_activity(make_monitor, at):
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
    # the buttons keep the theme's original weights: nothing in Home's CSS touches them or the cards
    assert "st-key-detail_" not in home.CSS and "st-key-stop_" not in home.CSS
    assert ".tr-platform " not in home.CSS and ".tr-live" not in home.CSS
    # the card itself keeps the theme's skin: Home only clips it and lays the green wave over it while it watches
    rules = re.findall(r'\[class\*="st-key-trrail"\] \.tr-monitor([^{]*)\{([^}]*)\}', home.CSS)
    assert [sel.strip() for sel, _ in rules] == ["", ":has(.tr-pill.ok)::before", "> *", ":has(.tr-pill.ok)::before"]
    assert rules[0][1].strip() == "position:relative; overflow:hidden;"
    assert "tr-home-wave" in rules[1][1] and "pointer-events:none" in rules[1][1]
    assert "animation:none" in rules[3][1]                                          # …and stops under reduced motion


def test_the_page_is_keyed_containers_in_reading_order_and_no_columns():
    import app

    src = inspect.getsource(app.page_home)
    keys = re.findall(r'st\.container\(key="(tr\w+)"\)', src)
    assert keys == ["trhome", "trstatus", "trlive", "trrail", "trwizard", "trfoot"]
    assert "st.columns(" not in src                                                # the squeeze is gone
    for call in ('C.hero("Movie Ticket Monitor"', 'C.rule("Platform")', "C.platform_selector(PLATFORMS",
                 "detail.dialog(monitors, states)", "rail(monitors, states, history)",
                 "wizard(monitors, settings=settings)"):
        assert call in src, call
    assert "flow.request_reset()\n    st.rerun()" in inspect.getsource(app.start_monitor)


# ──────────────────────────────────────────────────────────────────────────
# What the cinematic layer adds — and only adds
# ──────────────────────────────────────────────────────────────────────────
def test_all_twenty_four_crafts_are_on_home_each_with_a_line(make_monitor):
    upsert_monitor(make_monitor(), mirror=False)
    app = run()
    body = _markup(app)
    tips = re.findall(r'class="tr-craft[^"]*"[^>]*data-tip="([^"]+)"', body)
    # the three desktop zones hold all 24; the phone strip repeats eight of them
    assert len(tips) == 24 + len(home.STRIP)
    labels = {t.split(" — ")[0] for t in tips}
    assert labels == {c.label for c in crafts.CRAFTS}                      # every craft, by its own name
    assert set(home.HOME_LAYOUT) == set(crafts.BY_KEY) and set(home.LINES) == set(crafts.BY_KEY)
    assert all(len(line) <= 48 for line in home.LINES.values())           # short, a line each
    assert "And… action." in body and "I can do this all day." in body
    assert 'tabindex="0"' in body                                          # reachable by keyboard
    assert re.findall(r'class="tr-home-zone (\w+)"', body) == ["band", "rail", "foot"]
    for zone in ("band", "rail", "foot"):
        assert sum(1 for z in home.HOME_LAYOUT.values() if z[0] == zone) == 8


def test_the_radar_sits_in_the_hero_and_follows_the_truth(make_monitor, at):
    monitor = make_monitor()
    live = home.hero_radar_markup([monitor], {monitor.id: _live_state(at)})
    assert 'class="tr-hero-radar" data-state="success"' in live and 'class="tr-radar home"' in live
    assert 'data-state="scanning"' in home.hero_radar_markup([monitor], {})
    stopped = make_monitor()
    stopped.stop()
    assert 'data-state="idle"' in home.hero_radar_markup([stopped], {})
    css = home.CSS
    assert ".tr-hero { min-height:256px;" in css and ".tr-hero-inner { min-height:184px; }" in css                          # room for the radar above the mark
    assert ".tr-hero-radar .tr-radar.home { position:absolute; right:44px; top:22px;" in css
    assert "pointer-events:none" in css.split(".tr-hero-radar {")[1].split("}")[0]


def test_homes_stylesheet_is_an_enhancement_layer_only():
    css = home.CSS
    assert css.startswith("<style>") and len(css) < 36000                 # radar, crafts, reel, icons as data URIs
    assert ".tr-home-ambience" in css and "conic-gradient" in css and "repeating-linear-gradient" in css
    desktop = css[css.index("@media (min-width: 1150px)"):]
    assert '[class*="st-key-trhome"] { display:grid !important;' in desktop
    assert f"minmax({home.RAIL_MIN}px, 1.1fr)" in desktop and home.RAIL_MIN >= 320
    assert ':has(> [class*="st-key-trrail"]) { grid-column: 2; grid-row: 1 / span 4; margin-bottom:0; }' in desktop   # beside the hero, as before
    assert ':has(> [class*="st-key-trstatus"]) { grid-column: 1; grid-row: 1; }' in desktop
    assert "grid-template-rows: auto auto auto 1fr" in desktop
    tablet = css[css.index("@media (min-width: 769px) and (max-width: 1149px)"):css.index("@media (min-width: 1150px)")]
    assert ".tr-home-zone.rail { display:none; }" in tablet                 # 16 on a tablet
    phone = css[css.index("@media (max-width: 768px)"):]
    assert ".tr-home-zone.band, .tr-home-zone.rail, .tr-home-zone.foot { display:none; }" in phone
    assert ".tr-home-strip { display:flex;" in phone                        # the eight, as a strip
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "http" not in css.replace("http://www.w3.org", "").replace("http%3A%2F%2Fwww.w3.org", "")   # no external assets
    assert "base64" not in css                                              # no inline images (icons are the app's own SVG paths)


def test_the_layer_adds_no_reads_no_fragments_no_widgets():
    import app

    home_src = Path("ui/home.py").read_text(encoding="utf-8")
    code = home_src.split('"""', 2)[2]
    assert "load_" not in code and "state_store" not in code and "st.rerun" not in code
    assert "st.button(" not in code and "flow.pick(" not in code and "import streamlit" not in code
    page = inspect.getsource(app.page_home) + inspect.getsource(app.wizard)
    assert "load_" not in page and "st.rerun()" not in page
    assert Path("app.py").read_text(encoding="utf-8").count("@st.fragment") == 1


def test_with_nothing_running_the_page_is_the_original_empty_home(seeded):
    app = run()
    assert not app.exception
    body = _markup(app)
    assert "<h1>Movie Ticket Monitor</h1>" in body and "No active monitors" in body
    assert 'data-state="idle"' in body                                       # the radar rests
    assert any(b.key == "loc_hyderabad" for b in app.button)
