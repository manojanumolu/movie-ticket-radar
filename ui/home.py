"""Home's frame — the command center's skeleton (Phase 3B).

Home is two things on one page: the dashboard (what is being watched,
whether anything is live, the rail's command panel, recent activity) and
the Create Monitor wizard. This module draws the frame around them and
decides their order; it owns none of their internals.

The order is the reading order — status, live, command panel, wizard —
and the page renders exactly that, top to bottom, in four keyed
containers. A phone and a tablet read it as it is written. From 1150px
the Home root becomes a CSS grid (``CSS``): the status line spans the
width, the rail takes the right column, live and wizard the left. There
is no ``st.columns`` and so no ratio that can starve the rail: below
1150px it is simply full width, and above it is never narrower than
``RAIL_MIN``.

Everything here is a view over what ``page_home`` already holds —
monitors, states, the cached catalogue view. Nothing is read, nothing is
written, and the one widget (the "Set up a new alert" tile) is a
``flow.pick`` whose callback sets one session key, so a click is one run.
"""

from __future__ import annotations

import streamlit as st

from monitor.models import Availability, Monitor
from monitor.state import MonitorState
from ui import components as C
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
    one tile until asked for. With nothing running it is the page."""
    return active > 0 and wizard_is_pristine() and not st.session_state.get(WIZARD_OPEN)


def open_wizard() -> None:
    st.session_state[WIZARD_OPEN] = True


def hide_wizard() -> None:
    st.session_state.pop(WIZARD_OPEN, None)


def new_alert_tile() -> None:
    """The one tile that stands in for the wizard: a ``pick`` like every
    other tile, so the whole surface is the button."""
    flow.pick("new_alert", "Set up a new alert", lambda: C.html(
        '<div class="tr-newalert">'
        f'<div class="ic">{C.icon("bolt", 20, "#FF8CA0", "1.9")}</div>'
        '<div class="body"><div class="k">Set up a new alert</div>'
        '<div class="s">Create another movie-ticket alert — pick a city, a film, the theatres you want.</div></div>'
        f'<div class="go">{C.icon("chevron", 18, "currentColor", "2")}</div></div>'
    ), on_click=open_wizard)


# ── the status line ──────────────────────────────────────────────────────
def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def status_markup(monitors: list[Monitor], states: dict[str, MonitorState], *,
                  platforms, city: str, theatre_count: int) -> str:
    """The compact line that replaced the hero: the product name, one honest
    sentence about what is being watched, and the platform status. Only
    what the page already knows — counts of the monitors and targets in
    hand, the catalogue's theatre count; nothing invented."""
    active = [m for m in monitors if m.is_running()]
    live = sum(
        1 for m in active
        for ts in states.get(m.id, MonitorState()).targets.values()
        if ts.availability is Availability.AVAILABLE
    )
    theatres = sum(len(m.targets) for m in active)

    if not active:
        state, line = "idle", ('<span class="q">Nothing is being watched right now.</span> '
                              '<span class="s">Set up an alert below — the first check runs within minutes.</span>')
    else:
        first = active[0].movie.title
        more = f' <span class="more">+{len(active) - 1} more</span>' if len(active) > 1 else ""
        bits = [f'Watching <b>{C.e(first)}</b>{more}', C.e(_plural(theatres, "theatre"))]
        if live:
            bits.append(f'<span class="live"><span class="tr-dot ok live"></span>{live} live</span>')
        state = "success" if live else "scanning"
        line = " · ".join(bits)

    plats = []
    for p in platforms:
        if p.slug == "bookmyshow":
            known = (f"{theatre_count} theatres" if theatre_count else "theatres appear once a movie is synced")
            plats.append(f'<span class="p on">{C.CHECK_CIRCLE}BookMyShow · Connected · {C.e(city)} · {C.e(known)}</span>')
        elif p.slug in ("district", "pvr"):
            # The same two the shelf used to show, with the same honest word.
            plats.append(f'<span class="p soon">{C.e(p.name)} · Coming soon</span>')

    return (f'<div class="tr-hero"><div class="tr-hero-inner">'
            f'<div class="mark">{radar.svg(size=44, state=state, label="", cls="mark")}</div>'
            f'<div class="copy"><h1>Movie Ticket Monitor</h1><div class="tr-status-line">{line}</div></div>'
            f'<div class="tr-hero-mark tr-plat">{"".join(plats)}</div>'
            f'</div></div>')


def status_line(monitors, states, *, platforms, city: str, theatre_count: int) -> None:
    """Draw the status line, carrying Home's own stylesheet with it."""
    C.html(CSS + status_markup(monitors, states, platforms=platforms, city=city, theatre_count=theatre_count))


#: Home's stylesheet — the status line, the platform strip, the new-alert
#: tile and the dashboard grid. It rides inside the status line's markdown,
#: so only Home pays for it, and the radar's rules come along for the mark.
CSS = "<style>" + radar.CSS + f"""
/* ── status line: the hero, made a line ─────────────────────────────── */
.tr-hero {{ position:relative; overflow:hidden; padding:16px 20px 16px 18px; border-radius:18px; margin-bottom:14px;
  background: linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 40%), linear-gradient(118deg,#15151F 0%,#1A0C16 60%,#22091A 100%);
  border:1px solid rgba(255,255,255,.10); box-shadow: inset 0 1px 0 rgba(255,255,255,.09), 0 24px 60px -34px rgba(255,51,85,.5), var(--tr-shadow); }}
.tr-hero::before {{ content:''; position:absolute; inset:0; pointer-events:none;
  background: radial-gradient(520px 200px at 92% 0%, rgba(255,51,85,.22), transparent 70%); }}
.tr-hero-inner {{ position:relative; display:flex; align-items:center; gap:16px 22px; flex-wrap:wrap; min-width:0; }}
.tr-hero .mark {{ flex:none; width:44px; height:44px; filter: drop-shadow(0 0 12px rgba(255,51,85,.45)); }}
.tr-hero .mark .tr-radar {{ width:44px; }}
.tr-hero .copy {{ min-width:0; flex:1 1 320px; }}
/* the platform strip is its own row under the line, at every width */
.tr-hero h1 {{ margin:0; padding:0; font-family:var(--tr-mono); font-size:10.5px; font-weight:500; letter-spacing:.3em; text-transform:uppercase; color:#FF8CA0; line-height:1.4; }}
.tr-status-line {{ margin-top:5px; font-size:17px; font-weight:600; letter-spacing:-.015em; color:var(--tr-text-2); line-height:1.35; overflow-wrap:anywhere; }}
.tr-status-line b {{ color:#fff; font-weight:800; }}
.tr-status-line .more {{ font-family:var(--tr-mono); font-size:11px; letter-spacing:.08em; color:var(--tr-text-3); padding:2px 8px; border-radius:999px; border:1px solid var(--tr-border-strong); vertical-align:middle; }}
.tr-status-line .live {{ color:var(--tr-success); font-weight:800; white-space:nowrap; }}
.tr-status-line .live .tr-dot {{ margin-right:7px; vertical-align:middle; }}
.tr-status-line .q {{ color:var(--tr-text); font-weight:700; }}
.tr-status-line .s {{ color:var(--tr-text-3); font-weight:500; font-size:14px; }}
/* the platform strip: mono metadata, connected in green, the rest quiet */
.tr-hero-mark.tr-plat {{ display:flex; flex-direction:row; flex-wrap:wrap; justify-content:flex-start; align-items:center; gap:8px; flex:1 1 100%; width:100%; padding:12px 0 0; margin-top:2px; border-top:1px solid rgba(255,255,255,.08); white-space:normal;
  font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em; text-transform:uppercase; line-height:1.5; color:var(--tr-text-3); }}
.tr-plat .p {{ display:inline-flex; align-items:center; gap:7px; padding:6px 11px; border-radius:999px; border:1px solid var(--tr-border-strong); background:rgba(255,255,255,.04); white-space:nowrap; }}
.tr-plat .p.on {{ color:var(--tr-success); border-color:rgba(62,213,152,.32); background:rgba(62,213,152,.08); }}
.tr-plat .p.on svg {{ width:13px; height:13px; }}
.tr-plat .p.soon {{ color:var(--tr-text-4); border-style:dashed; }}
/* ── the wizard, waiting behind one tile ────────────────────────────── */
.tr-newalert {{ display:flex; align-items:center; gap:16px; padding:18px 20px; border-radius:18px; min-width:0;
  background: linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 45%), var(--tr-surface); border:1px dashed rgba(255,255,255,.18);
  box-shadow: var(--tr-hi); transition: border-color var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease); }}
.tr-newalert .ic {{ width:42px; height:42px; border-radius:12px; flex:none; display:flex; align-items:center; justify-content:center;
  background:rgba(255,51,85,.10); border:1px solid rgba(255,51,85,.35); }}
.tr-newalert .body {{ min-width:0; flex:1; }}
.tr-newalert .k {{ font-size:15px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; color:var(--tr-text); }}
.tr-newalert .s {{ font-size:13px; color:var(--tr-text-3); margin-top:3px; line-height:1.45; }}
.tr-newalert .go {{ flex:none; color:var(--tr-text-4); transform:rotate(-90deg); }}
[class*="st-key-pick_new_alert"]:hover .tr-newalert {{ border-style:solid; border-color:rgba(255,51,85,.5); background:rgba(255,51,85,.06); transform:translateY(-2px); }}
[class*="st-key-pick_new_alert"]:hover .tr-newalert .go {{ color:#FF8CA0; }}
[class*="st-key-pick_new_alert"]:has(button:focus-visible) {{ outline:2px solid var(--tr-accent); outline-offset:2px; border-radius:18px; }}
[class*="st-key-wizard_hide"] .stButton {{ width:auto; display:inline-block; }}
[class*="st-key-wizard_hide"] .stButton button {{ width:auto !important; min-height:34px; padding:4px 12px; font-size:12px; color:var(--tr-text-3); background:transparent; border-color:transparent; box-shadow:none; }}
[class*="st-key-wizard_hide"] .stButton button:hover {{ color:#fff; border-color:var(--tr-border-strong); background:rgba(255,255,255,.05); transform:none; box-shadow:none; }}
/* ── tablet (769–{DESKTOP_MIN - 1}px): one deliberate column, in reading order ─ */
@media (min-width: 769px) and (max-width: {DESKTOP_MIN - 1}px) {{
  [class*="st-key-trrail"] .tr-metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  [class*="st-key-trrail"] .tr-metric.wide {{ grid-column: span 1; }}
}}
/* ── desktop (≥{DESKTOP_MIN}px): the dashboard grid ─────────────────────
   The Home root is a flex column everywhere else (its DOM order is the
   phone's reading order). Here it becomes a grid: the status line spans,
   the rail takes the right column from the second row down, live and the
   wizard stack on the left. No column ratio, no squeeze: the rail is at
   least {RAIL_MIN}px wide by construction. */
@media (min-width: {DESKTOP_MIN}px) {{
  [class*="st-key-trhome"] {{ display:grid !important; grid-template-columns: minmax(0, 2.6fr) minmax({RAIL_MIN}px, 1.4fr); gap: 18px 28px; align-items:start; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"] {{ min-width:0; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trstatus"]) {{ grid-column: 1 / -1; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trrail"]) {{ grid-column: 2; grid-row: 2 / span 2; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trlive"]),
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trwizard"]) {{ grid-column: 1; }}
  .tr-hero {{ margin-bottom:0; }}
}}
@media (max-width: 768px) {{
  .tr-hero {{ padding: 14px 16px; border-radius: 16px; margin-bottom: 12px; }}
  .tr-hero-inner {{ gap: 12px 14px; }}
  .tr-hero .mark, .tr-hero .mark .tr-radar {{ width:38px; height:38px; }}
  .tr-hero .copy {{ flex: 1 1 200px; }}
  .tr-status-line {{ font-size: 15.5px; }}
  .tr-plat .p {{ padding:5px 9px; font-size:9.5px; letter-spacing:.1em; white-space:normal; line-height:1.6; }}
}}
@media (max-width: 480px) {{
  /* the two "coming soon" chips share one row; the connected one may wrap inside itself */
  .tr-plat .p.soon {{ font-size:9px; letter-spacing:.04em; padding:5px 8px; }}
}}
@media (prefers-reduced-motion: reduce) {{ .tr-newalert {{ transition:none; }} }}
</style>"""

__all__ = ["CSS", "DESKTOP_MIN", "RAIL_MIN", "WIZARD_OPEN", "hide_wizard", "new_alert_tile", "open_wizard",
           "status_line", "status_markup", "wizard_collapsed", "wizard_is_pristine"]
