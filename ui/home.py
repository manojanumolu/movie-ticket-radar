"""Home — the cinema control room (Phase 3B frame, 3B-fix skin).

Home is two things on one page: the dashboard (what is being watched,
whether anything is live, the rail's command panel, recent activity) and
the Create Monitor wizard. This module draws the *room* around them and
decides their order; it owns none of their internals.

The order is the reading order — the room's hero, the live card, the
command panel, the wizard — rendered top to bottom in keyed containers.
A phone and a tablet read it as written. From 1150px the Home root
becomes a CSS grid (``CSS``): the hero spans the width, the rail takes
the right column, live / wizard / the atmosphere panel stack on the left.
There is no ``st.columns`` and so no ratio that can starve the rail:
below 1150px it is simply full width; above, never narrower than
``RAIL_MIN``.

The visual language is the Login room's, quieter: the projector beam,
the breathing glow, the radar's sweep across the room, film grain, the
crafts in the negative space with a line of cinema each, the house of
seats fading into the floor. Charcoal, not glass; red as lighting, green
only for a live target. Everything is CSS and inline SVG — no images,
no script, no timers — and everything here is a view over what
``page_home`` already holds. Nothing is read or written; the one widget
(the "Set up a new alert" panel) is a ``flow.pick`` whose callback sets
one session key, so a click is one run.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from config.timezone import fmt_datetime
from monitor.models import Availability, Monitor
from monitor.state import MonitorState
from ui import components as C
from ui import crafts
from ui import flow
from ui import radar

#: Session key: the person opened the wizard while a monitor is running.
WIZARD_OPEN = "home_wizard_open"
#: The rail's column never goes below this on a desktop.
RAIL_MIN = 320
#: Where Home stops being a single column and becomes the dashboard grid.
DESKTOP_MIN = 1150


# ── the wizard's collapsed state ─────────────────────────────────────────
def wizard_is_pristine() -> bool:
    """Nothing chosen yet: step 1 and no film. A wizard in progress is
    never hidden behind a tile."""
    return int(st.session_state.get("step", 1) or 1) == 1 and not st.session_state.get("movie_id")


def wizard_collapsed(active: int) -> bool:
    """With a monitor running the dashboard leads; the wizard waits behind
    one panel until asked for. With nothing running it is the page."""
    return active > 0 and wizard_is_pristine() and not st.session_state.get(WIZARD_OPEN)


def open_wizard() -> None:
    st.session_state[WIZARD_OPEN] = True


def hide_wizard() -> None:
    st.session_state.pop(WIZARD_OPEN, None)


def new_alert_tile() -> None:
    """The one control that stands in for the wizard: a ``pick`` like every
    other tile, so the whole surface is the button."""
    flow.pick("new_alert", "Set up a new alert", lambda: C.html(
        '<div class="tr-newalert">'
        f'<div class="ic">{C.icon("bolt", 20, "#FF8CA0", "1.9")}</div>'
        '<div class="body"><div class="k">Create new monitor</div><div class="t">Set up a new alert</div>'
        '<div class="s">Pick a city, a film and the theatres you want — we watch the booking page from there.</div></div>'
        f'<div class="go">{C.icon("chevron", 18, "currentColor", "2")}</div></div>'
    ), on_click=open_wizard)


# ── lines of cinema ──────────────────────────────────────────────────────
#: One short line per craft, shown with the craft's name when a mark is
#: hovered or focused. TicketRadar's own voice, with two famous fragments
#: kept to a handful of words. Deterministic: the same mark always says the
#: same thing, so a rerun never changes the words under a cursor.
LINES: dict[str, str] = {
    "direction": "And… action.",
    "cinematography": "Every frame is a promise.",
    "screenwriting": "It always starts on the page.",
    "producing": "Somebody has to get it made.",
    "acting": "I can do this all day.",
    "editing": "The story is found in the cut.",
    "production_design": "Build the world, then light it.",
    "art_direction": "Nothing in frame is an accident.",
    "costume_design": "Dress the part before the part.",
    "makeup": "Years, applied by hand.",
    "hair": "Period, from the top.",
    "sound": "Listen. That's the room.",
    "music": "The score knows before you do.",
    "sound_mixing": "Dialogue up. Rain down.",
    "lighting": "Where the light falls, the eye follows.",
    "set_design": "Built to be believed.",
    "visual_effects": "What the camera couldn't see.",
    "special_effects": "Real fire. Real rain. One take.",
    "choreography": "Beat by beat, step by step.",
    "stunts": "With great power comes great responsibility.",
    "casting": "The one face for the part.",
    "color_grading": "The look, one frame at a time.",
    "projection": "Light through film, onto the big screen.",
    "distribution": "How a film finds your theatre.",
}

#: The room's own line for each state of the radar.
STATE_LINES = {
    "success": "And… action. Tickets are live.",
    "scanning": "Your seats are being watched.",
    "idle": "Lights. Camera. Tickets.",
}

#: A second voice for the atmosphere panel, picked by the monitor so it is
#: steady for that monitor and different for the next. Short, original,
#: Telugu-cinema flavoured where it fits.
SUB_LINES = (
    "The next showtime could appear any moment.",
    "Okka ticket chalu — one seat, the right one.",
    "Interval? Not for the radar.",
    "First day, first show — we'll tell you first.",
    "Every sweep, the whole room is checked again.",
)

#: Which crafts sit in the hero's negative space (right side) and where,
#: as (left %, top %) of the room. Placed against the measured copy block
#: at 1150–1600 wide: nothing over the headline, the pills or the radar.
HERO_MARKS: tuple[tuple[str, float, float], ...] = (
    ("cinematography", 88.5, 16), ("projection", 94, 12), ("editing", 97.5, 34),
    ("direction", 89.5, 42), ("lighting", 95, 60), ("sound", 90, 70),
    ("music", 96.5, 86), ("screenwriting", 88, 90),
)
#: …and in the atmosphere panel, around its radar.
ATMO_MARKS: tuple[tuple[str, float, float], ...] = (
    ("acting", 58, 9), ("visual_effects", 67, 6), ("color_grading", 76, 10), ("producing", 85, 5), ("distribution", 94, 9),
    ("casting", 7, 50), ("costume_design", 17, 58), ("makeup", 27, 49), ("hair", 37, 57), ("stunts", 47, 50),
    ("choreography", 12, 68), ("special_effects", 32, 68),
)


def _marks(spec: tuple[tuple[str, float, float], ...]) -> str:
    """Craft marks at the given spots, each carrying its line as the tip."""
    out = []
    for key, x, y in spec:
        craft = crafts.BY_KEY[key]
        tip = f"{craft.label} — {LINES.get(key, craft.blurb)}"
        side = "tip-l" if x > 80 else ("tip-r" if x < 15 else "")
        side += " tip-b" if y < 22 else ""
        out.append(f'<span class="tr-craft {side.strip()}" style="left:{x}%;top:{y}%" tabindex="0" role="img" '
                   f'aria-label="{escape(tip)}" data-tip="{escape(tip)}">{crafts.icon(craft, 17)}</span>')
    return f'<div class="tr-crafts" aria-label="The crafts of filmmaking">{"".join(out)}</div>'


def _seats(back: int = 9, front: int = 7) -> str:
    return (f'<div class="tr-seats" aria-hidden="true"><div class="row back">{"<i></i>" * back}</div>'
            f'<div class="row front">{"<i></i>" * front}</div></div>')


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _watch(monitors: list[Monitor], states: dict[str, MonitorState]) -> tuple[list[Monitor], int, int]:
    active = [m for m in monitors if m.is_running()]
    live = sum(1 for m in active for ts in states.get(m.id, MonitorState()).targets.values()
               if ts.availability is Availability.AVAILABLE)
    return active, live, sum(len(m.targets) for m in active)


def radar_state(monitors: list[Monitor], states: dict[str, MonitorState]) -> str:
    active, live, _ = _watch(monitors, states)
    return "success" if live else ("scanning" if active else "idle")


# ── the hero: the room ───────────────────────────────────────────────────
def hero_markup(monitors: list[Monitor], states: dict[str, MonitorState], *,
                platforms, city: str, theatre_count: int) -> str:
    """The room's hero: the radar sweeping, one honest headline about what
    is being watched, the platform pills, the crafts in the dark. Only
    what the page already knows — the monitors and targets in hand, the
    catalogue's theatre count; nothing invented."""
    active, live, theatres = _watch(monitors, states)
    state = "success" if live else ("scanning" if active else "idle")

    if not active:
        h1 = 'Nothing on the radar <em>yet</em>'
        lede = ("Set up an alert below — the first check runs within minutes, then on your schedule "
                "until your seats open.")
    else:
        first = active[0]
        more = f' <span class="more">+{len(active) - 1} more</span>' if len(active) > 1 else ""
        h1 = f'Watching <em>{C.e(first.movie.title)}</em>{more}'
        bits = [C.e(_plural(theatres, "theatre"))]
        if first.movie.language and len(active) == 1:
            bits.append(C.e(first.movie.language))
        bits += ["BookMyShow", C.e(city)]
        if live:
            bits.append(f'<span class="live"><i></i>{_plural(live, "theatre")} live</span>')
        lede = " · ".join(bits)

    pills = []
    for p in platforms:
        if p.slug == "bookmyshow":
            known = f"{theatre_count} theatres" if theatre_count else "theatres appear once a movie is synced"
            pills.append(f'<span class="p on">{C.CHECK_CIRCLE}BookMyShow · Connected · {C.e(city)} · {C.e(known)}</span>')
        elif p.slug in ("district", "pvr"):
            # The same two the shelf used to show, with the same honest word.
            pills.append(f'<span class="p soon">{C.e(p.name)} · Coming soon</span>')

    return (f'<div class="tr-hero"><div class="tr-room" data-state="{state}">'
            '<div class="beam"></div><div class="glow"></div><div class="grain"></div><div class="scan"></div>'
            f'{radar.svg(size=132, state=state, label="TicketRadar", cls="room")}'
            '<div class="copy">'
            f'<div class="eyebrow">Movie Ticket Monitor<span class="city"> · {C.e(city)}</span></div>'
            f'<h1>{h1}</h1>'
            f'<div class="lede">{lede}</div>'
            f'<div class="line">“{C.e(STATE_LINES[state])}”</div>'
            f'<div class="feats">{"".join(pills)}</div>'
            '</div>'
            f'{_marks(HERO_MARKS)}'
            f'<div class="strip">{crafts.strip()}</div>'
            '</div></div>')


def hero(monitors, states, *, platforms, city: str, theatre_count: int) -> None:
    """Draw the hero, carrying Home's own stylesheet with it."""
    C.html(CSS + hero_markup(monitors, states, platforms=platforms, city=city, theatre_count=theatre_count))


# ── the atmosphere panel: the room, when the wizard is put away ──────────
def atmosphere_markup(monitors: list[Monitor], states: dict[str, MonitorState], *, city: str,
                      compact: bool = False) -> str:
    """Fills the left column under the create panel while a monitor runs and
    the wizard is folded — the radar, the house, the crafts, one line of
    cinema. Decoration with a pulse, not a second dashboard: the numbers
    stay on the command panel. ``compact`` is for a column the live card
    has already made tall: the line and a small radar, no house."""
    active, live, _ = _watch(monitors, states)
    state = "success" if live else ("scanning" if active else "idle")
    first = active[0] if active else None
    sub = SUB_LINES[(sum(ord(c) for c in first.id) if first else 0) % len(SUB_LINES)]
    every = f"every {first.interval_minutes} minutes" if first else "on schedule"
    until = f" until {fmt_datetime(first.monitor_until)}" if first else ""
    house = "" if compact else f'{_marks(ATMO_MARKS)}<div class="screen"></div>{_seats()}<div class="floor"></div>'
    return (f'<div class="tr-atmo{" compact" if compact else ""}" data-state="{state}">'
            '<div class="glow"></div><div class="scan"></div>'
            f'{radar.svg(size=120 if compact else 210, state=state, label="", cls="atmo")}'
            '<div class="copy">'
            '<div class="eyebrow">Control room</div>'
            f'<div class="big">“{C.e(STATE_LINES[state])}”</div>'
            f'<div class="sub">{C.e(sub)} TicketRadar sweeps {C.e(city)}\'s theatres {C.e(every)}{C.e(until)}.</div>'
            '</div>'
            f'{house}'
            '</div>')


def atmosphere(monitors, states, *, city: str, compact: bool = False) -> None:
    C.html(atmosphere_markup(monitors, states, city=city, compact=compact))


#: Home's stylesheet — the room, the command panel's skin, the create
#: panel, the atmosphere panel and the dashboard grid. It rides inside the
#: hero's markdown so only Home pays for it; the radar's and the crafts'
#: rules come along because their marks live here.
CSS = "<style>" + radar.CSS + crafts.CSS + f"""
@keyframes tr-home-breathe {{ 0%,100% {{ opacity:.75; transform:scale(1); }} 50% {{ opacity:1; transform:scale(1.06); }} }}
@keyframes tr-home-rise {{ from {{ opacity:0; transform:translateY(12px); }} to {{ opacity:1; transform:none; }} }}
@keyframes tr-home-scanline {{ from {{ transform:translateY(-120%); }} to {{ transform:translateY(720%); }} }}
@keyframes tr-home-live {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.45; }} }}
/* ── the hero: the room ──────────────────────────────────────────────── */
.tr-hero {{ position:relative; overflow:hidden; isolation:isolate; border-radius:24px; margin-bottom:0; padding:0;
  background: linear-gradient(168deg,#17131A 0%,#0F0D12 52%,#140D14 100%); border:1px solid rgba(255,255,255,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.09), 0 50px 100px -40px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.05), 0 30px 90px -50px rgba(255,51,85,.5); }}
.tr-hero::before {{ content:none; }}
.tr-hero::after {{ content:""; position:absolute; left:14%; right:14%; top:-1px; height:1px; z-index:6; pointer-events:none; inset:auto 14% auto 14%; opacity:1; mask-image:none; -webkit-mask-image:none; background-size:auto;
  background: linear-gradient(90deg, transparent, rgba(255,140,160,.75), transparent); }}
.tr-room {{ position:relative; min-height:232px; padding:26px 30px 24px 28px; }}
.tr-room .beam {{ position:absolute; right:-10%; top:-30%; width:80%; height:170%; z-index:0; pointer-events:none; opacity:.9;
  background: conic-gradient(from 200deg at 92% 6%, transparent 0deg, rgba(255,107,133,.14) 14deg, rgba(255,51,85,.05) 30deg, rgba(255,51,85,.015) 44deg, transparent 56deg); filter: blur(14px); }}
.tr-room .glow {{ position:absolute; left:-6%; bottom:-40%; width:46%; height:120%; z-index:0; pointer-events:none;
  background: radial-gradient(closest-side, rgba(255,51,85,.22), rgba(255,51,85,.05) 55%, transparent 75%); filter: blur(12px); animation: tr-home-breathe 8s ease-in-out infinite; }}
.tr-room .grain {{ position:absolute; inset:0; z-index:0; pointer-events:none; opacity:.35;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.012) 0 1px, transparent 1px 3px); }}
.tr-room .scan {{ position:absolute; left:94px; top:116px; width:1400px; height:1400px; margin:-700px 0 0 -700px; border-radius:50%; z-index:1; pointer-events:none; opacity:.8;
  background: conic-gradient(from 0deg, transparent 0deg 286deg, rgba(255,51,85,.03) 330deg, rgba(255,51,85,.09) 359deg, transparent 360deg); animation: tr-radar-sweep 6.5s linear infinite; }}
.tr-room[data-state="success"] .scan {{ background: conic-gradient(from 0deg, transparent 0deg 286deg, rgba(62,213,152,.03) 330deg, rgba(62,213,152,.09) 359deg, transparent 360deg); }}
.tr-room .tr-radar.room {{ position:absolute; left:28px; top:50px; z-index:2; filter: drop-shadow(0 0 18px rgba(255,51,85,.35)); animation: tr-home-rise .9s .05s var(--tr-ease) both; }}
.tr-room[data-state="success"] .tr-radar.room, .tr-atmo[data-state="success"] .tr-radar.atmo {{ filter: drop-shadow(0 0 20px rgba(62,213,152,.4)); }}
.tr-atmo[data-state="success"] .glow {{ background: radial-gradient(closest-side, rgba(62,213,152,.16), rgba(62,213,152,.04) 55%, transparent 75%); }}
.tr-radar.room .ring, .tr-radar.atmo .ring {{ stroke-width:1.6; }}
.tr-radar.room .r4, .tr-radar.atmo .r4 {{ opacity:.34; }} .tr-radar.room .r3, .tr-radar.atmo .r3 {{ opacity:.42; }}
.tr-radar.room .axis, .tr-radar.atmo .axis {{ opacity:.22; }}
.tr-room .copy {{ position:relative; z-index:4; margin-left:150px; max-width:700px; min-width:0; animation: tr-home-rise .9s .15s var(--tr-ease) both; }}
.tr-room .eyebrow {{ display:flex; align-items:center; gap:12px; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.32em; text-transform:uppercase; color:#FF8CA0; }}
.tr-room .eyebrow::before {{ content:""; width:26px; height:1px; background:rgba(255,107,133,.7); flex:none; }}
.tr-room h1 {{ margin:12px 0 0; padding:0; font-family:var(--tr-sans); font-size:34px; line-height:1.05; letter-spacing:-.035em; font-weight:800; color:#fff; overflow-wrap:anywhere;
  background:none; -webkit-background-clip:border-box; background-clip:border-box; }}
.tr-room h1 em {{ font-style:normal; color:var(--tr-accent); }}
.tr-room h1 .more {{ display:inline-block; vertical-align:middle; margin-left:10px; font-family:var(--tr-mono); font-size:11px; letter-spacing:.1em; color:var(--tr-text-3); padding:3px 9px; border-radius:999px; border:1px solid var(--tr-border-strong); }}
.tr-room .lede {{ margin-top:10px; font-size:15px; line-height:1.5; color:var(--tr-text-2); font-weight:500; overflow-wrap:anywhere; }}
.tr-room .lede .live {{ display:inline-flex; align-items:center; gap:8px; color:#9FEBC9; font-weight:700; white-space:nowrap; }}
.tr-room .lede .live i {{ width:7px; height:7px; border-radius:50%; background:#3ED598; box-shadow:0 0 0 3px rgba(62,213,152,.18), 0 0 12px rgba(62,213,152,.8); animation: tr-home-live 2.2s ease-in-out infinite; }}
.tr-room .line {{ margin-top:10px; font-family:var(--tr-mono); font-size:11px; letter-spacing:.16em; color:var(--tr-text-3); }}
.tr-room .feats {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
.tr-room .feats .p {{ display:inline-flex; align-items:center; gap:7px; padding:7px 12px; border-radius:999px; border:1px solid rgba(255,255,255,.10); background:rgba(255,255,255,.035);
  font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--tr-text-3); white-space:nowrap; box-shadow: var(--tr-hi); }}
.tr-room .feats .p.on {{ color:#9FEBC9; border-color:rgba(62,213,152,.32); background:rgba(62,213,152,.08); }}
.tr-room .feats .p.on svg {{ width:13px; height:13px; }}
.tr-room .feats .p.soon {{ color:var(--tr-text-4); border-style:dashed; }}
.tr-room .tr-crafts {{ z-index:3; }}
.tr-room .tr-craft {{ opacity:.42; }}
.tr-room .strip {{ display:none; }}
.tr-hero .tr-craft-strip span {{ color:rgba(255,255,255,.5); }}
/* ── the command panel's skin: a live console, not an admin card ─────── */
[class*="st-key-trrail"] .tr-rail-title {{ font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.3em; text-transform:uppercase; color:var(--tr-text-3); margin:6px 0 12px; }}
[class*="st-key-trrail"] .tr-rail-title .tr-count {{ font-size:10px; }}
[class*="st-key-trrail"] .tr-monitor {{ position:relative; overflow:hidden; isolation:isolate; border-radius:22px; padding:22px;
  background: radial-gradient(520px 240px at 100% 0%, rgba(62,213,152,.13), transparent 70%), linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 40%), linear-gradient(168deg,#141A18 0%,#0F1311 60%,#0E1210 100%);
  border:1px solid rgba(62,213,152,.34); box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 0 0 1px rgba(62,213,152,.06), 0 34px 80px -40px rgba(62,213,152,.5), 0 40px 80px -40px rgba(0,0,0,1); }}
[class*="st-key-trrail"] .tr-monitor::before {{ content:""; position:absolute; left:0; right:0; top:0; height:14%; z-index:0; pointer-events:none;
  background: linear-gradient(180deg, rgba(62,213,152,.10), transparent); animation: tr-home-scanline 7s linear infinite; opacity:.8; }}
[class*="st-key-trrail"] .tr-monitor::after {{ content:""; position:absolute; inset:0; z-index:0; pointer-events:none; opacity:.35;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.012) 0 1px, transparent 1px 3px); }}
[class*="st-key-trrail"] .tr-monitor > * {{ position:relative; z-index:1; }}
[class*="st-key-trrail"] .tr-monitor.waiting {{ border-color:rgba(232,178,92,.32); background: radial-gradient(520px 240px at 100% 0%, rgba(232,178,92,.12), transparent 70%), linear-gradient(168deg,#1A1712 0%,#12100D 60%,#100E0B 100%); box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 34px 80px -40px rgba(232,178,92,.4), 0 40px 80px -40px rgba(0,0,0,1); }}
[class*="st-key-trrail"] .tr-monitor.waiting::before {{ background: linear-gradient(180deg, rgba(232,178,92,.10), transparent); }}
[class*="st-key-trrail"] .tr-monitor.bad {{ border-color:rgba(255,92,92,.34); background: radial-gradient(520px 240px at 100% 0%, rgba(255,92,92,.12), transparent 70%), linear-gradient(168deg,#1A1214 0%,#120D0E 60%,#100B0C 100%); box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 34px 80px -40px rgba(255,92,92,.4), 0 40px 80px -40px rgba(0,0,0,1); }}
[class*="st-key-trrail"] .tr-monitor.bad::before {{ background: linear-gradient(180deg, rgba(255,92,92,.10), transparent); }}
[class*="st-key-trrail"] .tr-monitor .tr-thumb {{ width:66px; height:94px; border-radius:10px; border:1px solid rgba(255,255,255,.14);
  box-shadow: 0 0 0 1px rgba(62,213,152,.10), 0 18px 40px -18px rgba(62,213,152,.45), 0 14px 30px -16px rgba(0,0,0,1); }}
[class*="st-key-trrail"] .tr-monitor .title {{ font-size:17px; letter-spacing:-.02em; color:#fff; }}
[class*="st-key-trrail"] .tr-monitor .tr-pill {{ font-family:var(--tr-mono); letter-spacing:.18em; }}
[class*="st-key-trrail"] .tr-metrics {{ margin-top:18px; padding-top:16px; gap:14px 12px; border-top:1px solid rgba(255,255,255,.08); }}
[class*="st-key-trrail"] .tr-metric .k {{ letter-spacing:.16em; }}
[class*="st-key-trrail"] .tr-metric .v {{ color:#fff; }}
[class*="st-key-trrail"] .tr-metric .v.mono {{ font-size:17px; text-shadow: 0 0 14px rgba(62,213,152,.45); }}
[class*="st-key-trrail"] .tr-note {{ background:rgba(0,0,0,.28); border-color:rgba(255,255,255,.08); }}
/* View details leads the inspection; Stop is there, quieter, in red */
[class*="st-key-trrail"] [class*="st-key-detail_"] .stButton button {{ min-height:48px; font-family:var(--tr-mono); font-size:11px; font-weight:500; letter-spacing:.2em; text-transform:uppercase;
  color:#fff; border:1px solid rgba(255,51,85,.45); border-style:solid;
  background: linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.03)), linear-gradient(135deg,rgba(255,51,85,.22),rgba(255,51,85,.06)), var(--tr-surface);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 18px 40px -22px rgba(255,51,85,.8); }}
[class*="st-key-trrail"] [class*="st-key-detail_"] .stButton button:hover {{ border-color:rgba(255,107,133,.8); background: linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.04)), linear-gradient(135deg,rgba(255,51,85,.32),rgba(255,51,85,.10)), var(--tr-surface); transform:translateY(-2px); box-shadow: inset 0 1px 0 rgba(255,255,255,.18), 0 22px 46px -22px rgba(255,51,85,.95); }}
[class*="st-key-trrail"] [class*="st-key-stop_"] .stButton button {{ min-height:42px; font-family:var(--tr-mono); font-size:10.5px; font-weight:500; letter-spacing:.18em; text-transform:uppercase;
  color:#FF8A8A !important; background:transparent !important; border:1px solid rgba(255,92,92,.30) !important; box-shadow:none !important; }}
[class*="st-key-trrail"] [class*="st-key-stop_"] .stButton button:hover {{ color:#fff !important; background:rgba(255,51,85,.10) !important; border-color:rgba(255,92,92,.6) !important; transform:none; }}
[class*="st-key-trrail"] .tr-eyebrow {{ letter-spacing:.26em; }}
[class*="st-key-trrail"] .tr-row {{ background: linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0) 50%), var(--tr-sunken); box-shadow: var(--tr-hi); }}
/* ── create new monitor: a compact control, not a placeholder ─────────── */
.tr-newalert {{ position:relative; overflow:hidden; display:flex; align-items:center; gap:18px; padding:16px 20px 16px 18px; border-radius:18px; min-width:0;
  background: linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 45%), linear-gradient(168deg,#17131A 0%,#0F0D12 100%); border:1px solid rgba(255,255,255,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 24px 60px -34px rgba(0,0,0,1); transition: border-color var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease), box-shadow var(--tr-fast) var(--tr-ease); }}
.tr-newalert::before {{ content:""; position:absolute; inset:0; pointer-events:none; background: radial-gradient(420px 160px at 0% 50%, rgba(255,51,85,.14), transparent 70%); }}
.tr-newalert .ic {{ position:relative; width:46px; height:46px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center;
  background: radial-gradient(circle at 40% 35%, rgba(255,120,140,.35), rgba(255,51,85,.08) 60%, transparent 70%); border:1px solid rgba(255,51,85,.55); box-shadow: 0 0 0 5px rgba(255,51,85,.06), 0 0 30px -6px rgba(255,51,85,.7); }}
.tr-newalert .body {{ position:relative; min-width:0; flex:1; }}
.tr-newalert .k {{ font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.3em; text-transform:uppercase; color:#FF8CA0; }}
.tr-newalert .t {{ font-size:17px; font-weight:800; letter-spacing:-.02em; color:#fff; margin-top:4px; }}
.tr-newalert .s {{ font-size:12.5px; color:var(--tr-text-3); margin-top:3px; line-height:1.45; }}
.tr-newalert .go {{ position:relative; flex:none; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:var(--tr-text-3); transform:rotate(-90deg);
  border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.04); transition: color var(--tr-fast) var(--tr-ease), border-color var(--tr-fast) var(--tr-ease); }}
[class*="st-key-pick_new_alert"]:hover .tr-newalert {{ border-color:rgba(255,51,85,.5); transform:translateY(-2px); box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 30px 70px -34px rgba(255,51,85,.7); }}
[class*="st-key-pick_new_alert"]:hover .tr-newalert .go {{ color:#fff; border-color:rgba(255,51,85,.6); }}
[class*="st-key-pick_new_alert"]:has(button:focus-visible) {{ outline:2px solid var(--tr-accent); outline-offset:2px; border-radius:18px; }}
[class*="st-key-wizard_hide"] .stButton {{ width:auto; display:inline-block; }}
[class*="st-key-wizard_hide"] .stButton button {{ width:auto !important; min-height:34px; padding:4px 12px; font-size:12px; color:var(--tr-text-3); background:transparent; border-color:transparent; box-shadow:none; }}
[class*="st-key-wizard_hide"] .stButton button:hover {{ color:#fff; border-color:var(--tr-border-strong); background:rgba(255,255,255,.05); transform:none; box-shadow:none; }}
/* ── the atmosphere panel: the house, lights down ─────────────────────── */
.tr-atmo {{ position:relative; overflow:hidden; isolation:isolate; min-height:300px; height:100%; border-radius:22px; padding:24px 26px;
  background: linear-gradient(168deg,#130F16 0%,#0C0A0F 60%,#0A090D 100%); border:1px solid rgba(255,255,255,.08);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 40px 80px -40px rgba(0,0,0,1); }}
.tr-atmo .glow {{ position:absolute; right:-8%; top:-30%; width:60%; height:120%; z-index:0; pointer-events:none;
  background: radial-gradient(closest-side, rgba(255,51,85,.18), rgba(255,51,85,.04) 55%, transparent 75%); filter: blur(14px); animation: tr-home-breathe 9s ease-in-out infinite; }}
.tr-atmo .scan {{ position:absolute; right:130px; top:150px; width:1200px; height:1200px; margin:-600px -600px 0 0; border-radius:50%; z-index:1; pointer-events:none; opacity:.7;
  background: conic-gradient(from 0deg, transparent 0deg 286deg, rgba(255,51,85,.03) 330deg, rgba(255,51,85,.08) 359deg, transparent 360deg); animation: tr-radar-sweep 8s linear infinite; }}
.tr-atmo[data-state="success"] .scan {{ background: conic-gradient(from 0deg, transparent 0deg 286deg, rgba(62,213,152,.03) 330deg, rgba(62,213,152,.08) 359deg, transparent 360deg); }}
.tr-atmo .tr-radar.atmo {{ position:absolute; right:7%; top:min(38%, 300px); z-index:2; filter: drop-shadow(0 0 22px rgba(255,51,85,.3)); }}
.tr-atmo .screen {{ position:absolute; left:10%; right:10%; top:auto; bottom:118px; height:3px; z-index:1; pointer-events:none; border-radius:3px;
  background: linear-gradient(90deg, transparent, rgba(255,140,160,.55) 30%, rgba(255,140,160,.55) 70%, transparent); box-shadow: 0 0 40px 10px rgba(255,51,85,.14), 0 30px 90px 30px rgba(255,51,85,.08); }}
.tr-atmo .screen::after {{ content:""; position:absolute; left:-6%; right:-6%; top:3px; height:110px; pointer-events:none;
  background: linear-gradient(180deg, rgba(255,90,120,.10), transparent); clip-path: polygon(8% 0, 92% 0, 100% 100%, 0 100%); }}
.tr-atmo .copy {{ position:relative; z-index:4; max-width:52%; min-width:0; }}
.tr-atmo .eyebrow {{ display:flex; align-items:center; gap:12px; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.32em; text-transform:uppercase; color:#FF8CA0; }}
.tr-atmo .eyebrow::before {{ content:""; width:26px; height:1px; background:rgba(255,107,133,.7); flex:none; }}
.tr-atmo .big {{ margin-top:14px; font-size:26px; font-weight:800; letter-spacing:-.03em; line-height:1.15; color:#fff; overflow-wrap:anywhere; }}
.tr-atmo .sub {{ margin-top:10px; font-size:13.5px; line-height:1.55; color:var(--tr-text-3); font-weight:500; }}
.tr-atmo .tr-crafts {{ z-index:3; }}
.tr-atmo .tr-craft {{ opacity:.40; }}
.tr-seats {{ position:absolute; left:-4%; right:-4%; bottom:0; z-index:2; display:flex; flex-direction:column; gap:8px; pointer-events:none; padding-bottom:4px;
  -webkit-mask-image: linear-gradient(180deg, #000 45%, transparent 100%); mask-image: linear-gradient(180deg, #000 45%, transparent 100%); }}
.tr-seats .row {{ display:flex; justify-content:center; gap:10px; }}
.tr-seats .row.back {{ transform:scale(.86); opacity:.6; }}
.tr-seats i {{ width:56px; height:36px; border-radius:14px 14px 5px 5px; flex:none; position:relative;
  background: linear-gradient(180deg,#3E0D1B 0%,#240811 50%,#12040A 100%); border-top:1px solid rgba(255,100,130,.42);
  box-shadow: inset 0 -12px 18px -12px #000, inset 0 1px 0 rgba(255,140,160,.12), 0 -8px 26px -18px rgba(255,51,85,.9); }}
.tr-seats i::after {{ content:""; position:absolute; left:7px; right:7px; bottom:-6px; height:8px; border-radius:0 0 4px 4px; background:#150409; border-top:1px solid rgba(255,90,120,.18); }}
.tr-seats .row.front i {{ width:68px; height:46px; }}
.tr-atmo .floor {{ position:absolute; left:0; right:0; bottom:0; height:120px; z-index:1; pointer-events:none; background: linear-gradient(180deg, transparent, rgba(7,7,10,.8) 80%); }}
.tr-atmo.compact {{ min-height:150px; padding:22px 26px; }}
.tr-atmo.compact .tr-radar.atmo {{ top:50%; transform:translateY(-50%); right:5%; }}
.tr-atmo.compact .scan {{ right:70px; top:50%; }}
.tr-atmo.compact .copy {{ max-width:70%; }}
.tr-atmo.compact .big {{ font-size:22px; margin-top:10px; }}
/* the panel stretches to the rail's height, so the column has no dead end */
[class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-tratmo"]),
[class*="st-key-tratmo"], [class*="st-key-tratmo"] > [data-testid="stElementContainer"], [class*="st-key-tratmo"] .stMarkdown,
[class*="st-key-tratmo"] .stMarkdown > div, [class*="st-key-tratmo"] [data-testid="stMarkdownContainer"] {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; width:100%; }}
[class*="st-key-tratmo"] .tr-atmo {{ flex:1 1 auto; height:auto; }}
/* ── tablet (769–{DESKTOP_MIN - 1}px): one deliberate column, in reading order ─ */
@media (min-width: 769px) and (max-width: {DESKTOP_MIN - 1}px) {{
  [class*="st-key-trrail"] .tr-metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  [class*="st-key-trrail"] .tr-metric.wide {{ grid-column: span 1; }}
  /* the copy runs to the room's edge here: no marks, no strip, just the beam */
  .tr-room .tr-crafts, .tr-room .strip {{ display:none; }}
  .tr-room .copy {{ max-width:none; }}
  .tr-room .feats .p {{ white-space:normal; line-height:1.6; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-tratmo"]) {{ display:none; }}
}}
/* ── desktop (≥{DESKTOP_MIN}px): the dashboard grid ─────────────────────
   The Home root is a flex column everywhere else (its DOM order is the
   phone's reading order). Here it becomes a grid: the hero spans, the
   rail takes the right column from the second row down, live, wizard and
   the atmosphere stack on the left. No column ratio, no squeeze: the
   rail is at least {RAIL_MIN}px wide by construction. */
@media (min-width: {DESKTOP_MIN}px) {{
  /* rows are placed by hand and gaps are margins, so a row with nothing in
     it (no live card) costs nothing, and the last row takes what the rail
     leaves — the atmosphere panel stretches into it */
  [class*="st-key-trhome"] {{ display:grid !important; grid-template-columns: minmax(0, 2.6fr) minmax({RAIL_MIN}px, 1.4fr); grid-template-rows: auto auto auto 1fr; gap: 0 28px; align-items:start; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"] {{ min-width:0; margin-bottom:18px; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trstatus"]) {{ grid-column: 1 / -1; grid-row: 1; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trrail"]) {{ grid-column: 2; grid-row: 2 / span 3; margin-bottom:0; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trlive"]) {{ grid-column: 1; grid-row: 2; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trwizard"]) {{ grid-column: 1; grid-row: 3; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-tratmo"]) {{ grid-column: 1; grid-row: 4; align-self: stretch; margin-bottom:0; }}
  .tr-hero {{ margin-bottom:0; }}
}}
@media (min-width: {DESKTOP_MIN}px) and (max-width: 1365px) {{
  .tr-room h1 {{ font-size:30px; }}
  .tr-room .copy {{ max-width:480px; }}
}}
/* a narrow room (a tablet with the sidebar open, any phone): the radar sits
   above the copy and the copy takes the width */
@media (max-width: 900px) {{
  .tr-hero {{ border-radius: 18px; }}
  .tr-room {{ min-height:0; padding: 18px 16px 16px; }}
  .tr-room .tr-radar.room {{ position:static; width:64px !important; margin-bottom:12px; }}
  .tr-room .copy {{ margin-left:0; max-width:none; }}
  .tr-room h1 {{ font-size: clamp(24px, 4.2vw, 30px); letter-spacing:-.03em; }}
  .tr-room .eyebrow .city {{ display:none; }}
  .tr-room .lede {{ font-size:14px; }}
  .tr-room .feats {{ margin-top:14px; gap:6px; }}
  .tr-room .feats .p {{ padding:6px 9px; font-size:9px; letter-spacing:.08em; white-space:normal; line-height:1.6; }}
  .tr-room .scan, .tr-room .beam, .tr-room .tr-crafts {{ display:none; }}
}}
@media (max-width: 768px) {{
  .tr-room h1 {{ font-size: clamp(24px, 7vw, 30px); }}
  .tr-room .strip {{ display:block; position:absolute; right:14px; top:16px; z-index:3; }}
  .tr-hero .tr-craft-strip {{ max-width:150px; justify-content:flex-end; }}
  .tr-hero .tr-craft-strip span {{ width:26px; height:26px; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-tratmo"]) {{ display:none; }}
  .tr-newalert {{ gap:14px; padding:14px 16px 14px 14px; }}
  .tr-newalert .go {{ display:none; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .tr-room .glow, .tr-room .scan, .tr-atmo .glow, .tr-atmo .scan, [class*="st-key-trrail"] .tr-monitor::before, .tr-room .lede .live i {{ animation:none; }}
  .tr-room .tr-radar.room, .tr-room .copy {{ animation:none; }}
  .tr-newalert {{ transition:none; }}
}}
</style>"""

__all__ = ["CSS", "DESKTOP_MIN", "HERO_MARKS", "LINES", "RAIL_MIN", "STATE_LINES", "WIZARD_OPEN", "atmosphere",
           "atmosphere_markup", "hero", "hero_markup", "hide_wizard", "new_alert_tile", "open_wizard",
           "radar_state", "wizard_collapsed", "wizard_is_pristine"]
