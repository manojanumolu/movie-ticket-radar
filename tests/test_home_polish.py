"""The polish pass over the original Home and the Monitor Details dialog.

The film reel and strip behind the interface, the premium-format motif in
the dark, the crafts' subtitle cards, the green scan over an active
monitor, the wizard's icons, the dialog's living states and the wait for
a genuinely slow operation — all of it layered over the same components,
none of it changing what they say.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from monitor.models import Availability
from monitor.state import MonitorState
from ui import crafts, detail, home

pytestmark = pytest.mark.usefixtures("signed_in")


# ──────────────────────────────────────────────────────────────────────────
# The reel, the strip, the formats — behind everything
# ──────────────────────────────────────────────────────────────────────────
def test_the_reel_is_geometry_behind_the_page_and_never_in_the_way():
    reel = home.reel_svg("big")
    assert reel.startswith('<svg class="tr-reel big"') and 'aria-hidden="true"' in reel
    assert reel.count("<circle") == 6 + 5                                    # six perforations, rim, sheen, ring, hub, pin
    assert "<image" not in reel and "href" not in reel                       # drawn, not loaded
    strip = home.strip_svg()
    assert 'class="tr-strip"' in strip and strip.count("<path") == 5
    css = home.CSS
    assert ".tr-home-ambience .tr-reel { position:absolute; color:#FF8CA0; pointer-events:none;" in css
    assert "animation: tr-home-reel 84s linear infinite" in css              # slow, one direction
    assert "animation: tr-home-reel-back 120s linear infinite" in css        # the small one, the other way
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-home-ambience .tr-reel" in reduced and "animation:none" in reduced
    assert ".tr-home-ambience { position:absolute;" in css and "z-index:0; pointer-events:none;" in css
    assert '[class*="st-key-trhome"] > [data-testid="stLayoutWrapper"] { position:relative; z-index:1; }' in css


def test_premium_formats_ride_the_reel_each_with_its_own_mark_and_never_as_a_shelf():
    reel = home.reel_svg("big", formats=True)
    assert reel.count('<g class="fmt">') == 8 and reel.count('class="ic"') == 8 and reel.count('<text class="t"') == 8
    for i, name in enumerate(home.PREMIUM_FORMATS):
        assert f'rotate({i * 45} 200 200) translate(200 28)' in reel                 # one every 45°, on the outer track
        assert f">{name.upper()}</text>" in reel                                     # name…
        assert home.FORMAT_MARKS[name] in reel                                       # …and its pictogram
    assert len(home.PREMIUM_FORMATS) == 8 and set(home.FORMAT_MARKS) == set(home.PREMIUM_FORMATS)
    assert all(m.startswith("<") and "http" not in m and "<image" not in m for m in home.FORMAT_MARKS.values())   # drawn marks, no logos loaded
    assert "fmt" not in home.reel_svg("small")                                       # only the big reel carries them
    assert 'reel_svg("big", formats=True)' in inspect.getsource(home.ambience)
    # the reel is the one presentation: no standalone shelf anywhere
    assert not hasattr(home, "formats_markup") and ".tr-formats" not in home.CSS
    assert "Premium formats" not in home.crafts_zone("foot") and "tr-formats" not in home.crafts_zone("foot")
    for word in ("available", "Available", "theatre", "Connected", "tracked"):
        assert word not in reel                                                      # decoration says nothing about availability
    css = home.CSS
    assert ".tr-home-ambience .tr-reel .fmt { transform-box:fill-box; transform-origin:center; animation: tr-home-reel-back 84s linear infinite; }" in css   # upright while the reel turns
    phone = css[css.index("@media (max-width: 768px)"):]
    assert ".tr-home-ambience .tr-reel .fmts { display:none; }" in phone            # a cropped reel carries no badges


# ──────────────────────────────────────────────────────────────────────────
# The crafts' subtitle cards — Home only
# ──────────────────────────────────────────────────────────────────────────
def test_every_craft_has_a_short_line_with_an_attribution_and_login_keeps_its_names():
    from collections import Counter

    assert set(home.LINES) == set(crafts.BY_KEY)
    for key, (quote, by, house) in home.LINES.items():
        assert 3 <= len(quote) <= 60 and by and house, key                          # a fragment, never a passage
    houses = Counter(house for _, _, house in home.LINES.values())
    assert set(houses) == {"Marvel", "Tollywood", "DC", "Hollywood"}                 # four houses
    assert houses.most_common(1)[0][0] == "Marvel" and houses["Marvel"] >= 8          # Marvel leads
    assert houses["Tollywood"] == 6 and houses["DC"] == 2
    assert home.LINES["acting"][:2] == ("I can do this all day.", "Captain America")
    assert home.LINES["makeup"][0] == "I am Iron Man." and home.LINES["stunts"][0] == "With great power comes great responsibility."
    assert home.LINES["distribution"][0] == "Thokkukuntu povale."
    telugu = [q for q, _, h in home.LINES.values() if h == "Tollywood"]
    assert all(q.isascii() for q in telugu)                                          # transliterated, not Telugu script
    batman = [(q, by) for q, by, h in home.LINES.values() if h == "DC"]
    assert all("Batman" in by or "Dark Knight" in by for _, by in batman)            # both clearly Batman
    # the Telugu lines are spread over the zones, not grouped
    zones = {home.HOME_LAYOUT[k][0] for k, (_, _, h) in home.LINES.items() if h == "Tollywood"}
    assert zones == {"band", "rail", "foot"}
    mark = home._mark(crafts.BY_KEY["acting"], 50, 50)
    assert '<span class="tr-sub" aria-hidden="true"><span class="q">“I can do this all day.”</span><span class="by">— Captain America</span></span>' in mark
    assert 'tabindex="0"' in mark and 'aria-label="Acting — “I can do this all day.” — Captain America"' in mark
    # the Login constellation is untouched: the crafts' own tips, no subtitle cards
    login = crafts.constellation()
    assert "tr-sub" not in login and 'data-tip="Acting — The people you believe in for two hours."' in login
    assert "I can do this all day" not in login and "Thokkukuntu" not in login
    assert crafts.BY_KEY["acting"].tip == "Acting — The people you believe in for two hours."
    css = home.CSS
    assert ".tr-craft:hover .tr-sub, .tr-craft:focus-visible .tr-sub, .tr-craft:focus .tr-sub { opacity:1; visibility:visible;" in css
    assert ".tr-home-zone .tr-craft::after, .tr-home-zone .tr-craft::before, .tr-home-strip .tr-craft::after, .tr-home-strip .tr-craft::before { display:none; }" in css
    assert ".tr-craft .tr-sub .q { font-family:var(--tr-sans);" in css and ".tr-craft .tr-sub .by { font-family:var(--tr-mono);" in css
    assert "transition:none" in css[css.index("@media (prefers-reduced-motion: reduce)"):]


# ──────────────────────────────────────────────────────────────────────────
# The green scan over an active monitor
# ──────────────────────────────────────────────────────────────────────────
def test_the_green_wave_rides_only_a_monitor_that_is_actually_watching(make_monitor, at):
    from ui import components as C

    css = home.CSS
    wave = css[css.index('[class*="st-key-trrail"] .tr-monitor:has(.tr-pill.ok)::before'):].split("}")[0]
    assert "rgba(62,213,152" in wave and "tr-home-wave 7s" in wave and "pointer-events:none" in wave
    # the hook is the card's own phase pill: ok for watching/available, never for sold out, waiting, errors or finished
    monitor = make_monitor()
    running = MonitorState(last_check_at=at, last_success_at=at, check_count=3, success_count=3)
    assert C.phase_for(monitor, running, at=at).css == "ok"
    sold = MonitorState(last_check_at=at, last_success_at=at, check_count=3, success_count=3)
    for key in ("ALLU::Dolby Cinema", "AMB::Any format"):
        sold.target(key).availability = Availability.SOLD_OUT
    assert C.phase_for(monitor, sold, at=at).css == "warn"
    assert C.phase_for(monitor, MonitorState(), at=at).css == "warn"        # waiting for the first check
    stopped = make_monitor()
    stopped.stop(at)
    assert C.phase_for(stopped, running, at=at).css == "neutral"


# ──────────────────────────────────────────────────────────────────────────
# Icons beside the words — words and callbacks untouched
# ──────────────────────────────────────────────────────────────────────────
def test_wizard_and_metric_icons_are_css_only_and_come_from_the_apps_own_paths():
    import app
    from ui import components as C

    assert home.STEP_ICONS == {1: "location", 2: "movie", 3: "theatre", 4: "format", 5: "radar"}
    assert set(home.STEP_ICONS.values()) <= set(C.ICON_PATHS)
    css = home.CSS
    for n in range(1, 6):
        assert f'[class*="st-key-pick_step_{n}"] .tr-step-pip .l::before {{ background-image:url("data:image/svg+xml' in css
    for n in range(1, 5):
        assert f".tr-summary-strip .i:nth-child({n}) .k::before {{ background-image:url(" in css
    for n in range(1, 7):
        assert f'[class*="st-key-trrail"] .tr-metric:nth-child({n}) .k::before {{ background-image:url(' in css
    assert "home.step_icon_css(step)" in inspect.getsource(app.wizard)
    # nothing about the wizard's words or wiring moved
    src = Path("ui/flow.py").read_text(encoding="utf-8")
    assert "Where are you watching?" in src and 'key="loc_continue"' in src
    assert Path("ui/components.py").read_text(encoding="utf-8") == Path("ui/components.py").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# The dialog, alive
# ──────────────────────────────────────────────────────────────────────────
def _live_state(at):
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=13)
    live = state.target("ALLU::Dolby Cinema")
    live.availability = Availability.AVAILABLE
    live.since = at
    live.booking_url = "https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"
    live.time_labels = ["07:30 PM"]
    state.target("AMB::Any format").availability = Availability.NOT_BOOKABLE
    return state


def test_the_dialog_keeps_every_fact_and_gains_its_living_layer(make_monitor, at):
    monitor = make_monitor(interval=15)
    watching = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=13)
    body = detail.markup(monitor, watching, at=at)
    for fact in ("Title", "Language", "Show dates", "Status", "Check interval", "Checks completed",
                 "Last checked", "Next check", "Monitoring until", "Allu Cinemas", "AMB Cinemas", "Every 15 min", "13 of 14"):
        assert fact in body, fact
    assert 'data-phase="not_released" data-running="1"' in body
    assert '<div class="tr-det-bg" aria-hidden="true"><div class="sweep"></div><div class="line"></div><svg class="tr-reel det"' in body
    assert "<span>LIVE MONITOR · SCANNING</span>" in body and "<span>CHECK #13</span>" in body and "NEXT SCAN" in body
    assert body.count('class="tr-radar watch"') == 2                          # both targets still being watched
    assert body.count("BOOK ON BOOKMYSHOW") == 0

    live = detail.markup(monitor, _live_state(at), at=at)
    assert 'data-phase="available"' in live and "LIVE MONITOR · TICKETS FOUND" in live
    assert live.count('class="tr-radar watch"') == 1                          # the live one is found, not watched
    assert live.count("BOOK ON BOOKMYSHOW") == 1                              # the link exactly as before

    stopped = make_monitor()
    stopped.stop(at)
    ended = detail.markup(stopped, watching, at=at)
    assert 'data-running="0"' in ended and "<span>STOPPED</span>" in ended and "tr-radar watch" not in ended


def test_the_close_control_belongs_to_the_room_and_stays_reachable():
    css = detail.CSS
    close = css[css.index('[class*="st-key-close_detail"] .stButton button {'):].split("}")[0]
    assert "border-radius:999px" in close and "font-family:var(--tr-sans)" in close and "text-transform:none" in close
    assert "linear-gradient(168deg,#17131A,#0F0D12)" in close and "rgba(255,51,85,.06)" in close       # the room's charcoal and a trace of red
    assert "min-width:150px" in close and "min-height:42px" in close and "width:auto !important" in close   # compact, not huge
    assert '[class*="st-key-close_detail"] .stButton { display:flex; justify-content:flex-end; }' in css
    assert '[class*="st-key-close_detail"] .stButton button:focus-visible { outline:2px solid var(--tr-accent);' in css
    assert '[class*="st-key-close_detail"] .stButton button:hover {' in css
    phone = css[css.index("@media (max-width: 768px)"):]
    assert '[class*="st-key-close_detail"] .stButton button { width:100% !important; min-height:46px; }' in phone
    assert 'st.button("Close", key="close_detail", use_container_width=True)' in inspect.getsource(detail.dialog)   # the control itself unchanged


def test_the_dialogs_movement_follows_the_state_and_reduced_motion():
    css = detail.CSS
    assert '.tr-det[data-running="1"][data-phase="not_released"] .tr-det-head .tr-pill' in css and "tr-det-breathe" in css
    assert '.tr-det[data-phase="sold_out"] .tr-det-bg .line' in css and "rgba(232,178,92" in css   # amber, not green
    assert '.tr-det[data-running="0"] .tr-det-bg .line, .tr-det[data-running="0"] .tr-det-bg .sweep { display:none; }' in css
    assert ".tr-det-bg { position:absolute;" in css and "pointer-events:none" in css.split(".tr-det-bg {")[1].split("}")[0]
    assert "@keyframes tr-det-scan" in css and "8s" in css.split("animation: tr-det-scan")[1][:6]
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-det-bg .sweep, .tr-det-bg .line, .tr-det-bg .tr-reel" in reduced and "animation:none" in reduced
    for name in ("movie", "radar", "theatre", "calendar", "clock", "check", "monitor"):
        pass                                                                  # icons come from components.ICON_PATHS
    assert '<div class="tr-eyebrow">' in detail._eyebrow("movie", "Movie") and "<svg" in detail._eyebrow("movie", "Movie")


# ──────────────────────────────────────────────────────────────────────────
# The wait, only where the wait is real
# ──────────────────────────────────────────────────────────────────────────
def test_the_veil_appears_only_around_the_two_slow_operations():
    import app

    src = Path("app.py").read_text(encoding="utf-8")
    assert src.count("home.busy(") == 2
    assert "home.busy(" in inspect.getsource(app.start_monitor) and "home.busy(" in inspect.getsource(app.retry_check)
    assert "home.busy(" not in inspect.getsource(app.page_home) and "home.busy(" not in inspect.getsource(app.live_monitor_panel)
    veil = home.CSS[home.CSS.index(".tr-home-veil {"):].split("}")[0]
    assert "position:fixed" in veil and "pointer-events:none" in veil and "backdrop-filter: blur(10px)" in veil
    assert "animation: tr-home-fadein .35s .6s" in veil                     # held invisible for 600 ms: a quick answer never shows it
    assert "time.sleep" not in src and "sleep(" not in Path("ui/home.py").read_text(encoding="utf-8")
    markup = home.busy("Saving your monitor")
    assert markup is None                                                    # it draws; the rerun at the end of the operation removes it


def test_the_polish_adds_no_reads_no_fragments_no_widgets():
    import app

    code = Path("ui/home.py").read_text(encoding="utf-8").split('"""', 2)[2]
    assert "load_" not in code and "state_store" not in code and "import streamlit" not in code and "st.button(" not in code
    dcode = Path("ui/detail.py").read_text(encoding="utf-8").split('"""', 2)[2]
    assert "load_" not in dcode and "state_store" not in dcode
    assert Path("app.py").read_text(encoding="utf-8").count("@st.fragment") == 1
    assert "load_" not in inspect.getsource(app.page_home)
