"""Home's cinematic layer — the original Home, made alive (Phase 3).

Home *is* the page built before Phase 3: the hero ("Movie Ticket
Monitor"), the Platform shelf with the BookMyShow, District and PVR logo
cards, the live card, the wizard, the rail's monitor card with View
details and Stop, the theatres, the recent history. Every one of those is
drawn by ``ui.components`` and ``ui.flow`` exactly as before. This module
adds what the Login room has and Home lacked — and replaces nothing:

* **ambience** — the projector beam, a breathing glow, film grain and
  faint lighting, *behind* every card (pseudo-layers on the Home root);
* **the radar** — ``ui.radar`` at 120px in the hero's unused top-right,
  above the "SOME STORIES ARE WORTH THE WAIT" mark, its sweep continuing
  faintly across the hero; ``idle`` / ``scanning`` / ``success`` by truth;
* **the twenty-four crafts** — every ``ui.crafts.CRAFTS`` entry, placed in
  genuine negative space (the band above the hero left of the account
  chip, the foot of the rail, the ledge under the wizard), each with a
  line of cinema as its tooltip on hover and focus. A tablet keeps the
  band and the ledge (16); a phone gets a tap-friendly strip of eight.

Layout is the reading order in keyed containers (a phone reads top to
bottom); from 1150px the Home root is a grid with the rail at least
``RAIL_MIN`` wide, so the rail is never a squeezed column. Everything
here is CSS and inline SVG over data ``page_home`` already holds — no
reads, no widgets, no script, no timers.
"""

from __future__ import annotations

from html import escape

from monitor.models import Availability, Monitor
from monitor.state import MonitorState
from ui import components as C
from ui import crafts
from ui import radar

#: The rail's column never goes below this on a desktop.
RAIL_MIN = 320
#: Where Home stops being a single column and becomes the dashboard grid.
DESKTOP_MIN = 1150

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

#: Where each of the twenty-four sits: zone, then (left %, top %) of that
#: zone. The zones are the page's genuine negative space — the band above
#: the hero (left of the account chip), the foot of the rail under recent
#: history, the ledge under the wizard — so no mark ever sits on text, a
#: card, a button or the navigation. Order follows :data:`crafts.CRAFTS`.
HOME_LAYOUT: dict[str, tuple[str, float, float]] = {
    "direction": ("band", 4, 62), "cinematography": ("band", 15, 30), "screenwriting": ("band", 27, 66),
    "producing": ("band", 39, 28), "acting": ("band", 51, 64), "editing": ("band", 63, 34),
    "projection": ("band", 76, 68), "distribution": ("band", 90, 36),
    "production_design": ("rail", 8, 30), "art_direction": ("rail", 22, 70), "costume_design": ("rail", 36, 26),
    "makeup": ("rail", 50, 68), "hair": ("rail", 64, 30), "set_design": ("rail", 78, 70),
    "casting": ("rail", 92, 32), "color_grading": ("rail", 15, 90),
    "sound": ("foot", 5, 34), "music": ("foot", 17, 70), "sound_mixing": ("foot", 30, 30),
    "lighting": ("foot", 43, 68), "visual_effects": ("foot", 57, 32), "special_effects": ("foot", 70, 70),
    "choreography": ("foot", 83, 30), "stunts": ("foot", 95, 66),
}
assert set(HOME_LAYOUT) == set(crafts.BY_KEY), "every craft has a home"

#: The eight the phone strip shows (the Login page's own selection).
STRIP = ("direction", "cinematography", "sound", "editing", "music", "visual_effects", "lighting", "projection")


def _mark(craft: crafts.Craft, x: float, y: float, *, positioned: bool = True) -> str:
    tip = f"{craft.label} — {LINES.get(craft.key, craft.blurb)}"
    side = ("tip-l" if x > 84 else "tip-r" if x < 10 else "") + (" tip-b" if y < 40 and positioned else "")
    style = f' style="left:{x}%;top:{y}%"' if positioned else ""
    return (f'<span class="tr-craft {side.strip()}"{style} tabindex="0" role="img" '
            f'aria-label="{escape(tip)}" data-tip="{escape(tip)}">{crafts.icon(craft, 17)}</span>')


def crafts_zone(zone: str) -> str:
    """The marks that live in ``zone`` (``band`` / ``rail`` / ``foot``), in
    the constellation's own tooltip mechanics."""
    marks = "".join(_mark(crafts.BY_KEY[k], x, y) for k, (z, x, y) in HOME_LAYOUT.items() if z == zone)
    return (f'<div class="tr-home-zone {zone}" aria-label="The crafts of filmmaking">'
            f'<div class="tr-crafts">{marks}</div></div>')


def crafts_strip() -> str:
    """The phone's eight, in a row: the same marks and tips, so a tap or a
    focus reveals the line where there is nothing to hover."""
    marks = "".join(_mark(crafts.BY_KEY[k], 0, 0, positioned=False) for k in STRIP)
    return f'<div class="tr-home-strip" aria-label="The crafts of filmmaking">{marks}</div>'


def radar_state(monitors: list[Monitor], states: dict[str, MonitorState]) -> str:
    active = [m for m in monitors if m.is_running()]
    live = any(ts.availability is Availability.AVAILABLE
               for m in active for ts in states.get(m.id, MonitorState()).targets.values())
    return "success" if live else ("scanning" if active else "idle")


def hero_radar_markup(monitors: list[Monitor], states: dict[str, MonitorState]) -> str:
    """The radar over the hero's empty top-right, its sweep across the
    hero, clipped to the hero's own box. Pointer-transparent: the hero
    beneath is untouched."""
    state = radar_state(monitors, states)
    return (f'<div class="tr-hero-radar" data-state="{state}" aria-hidden="true">'
            f'<div class="scan"></div>{radar.svg(size=120, state=state, label="", cls="home")}</div>')


# ── drawing ──────────────────────────────────────────────────────────────
def ambience() -> None:
    """Home's stylesheet and its background layers. Drawn once, first."""
    C.html(CSS + '<div class="tr-home-ambience" aria-hidden="true"></div>')


def hero_radar(monitors, states) -> None:
    C.html(hero_radar_markup(monitors, states))


def band() -> None:
    """The eight above the hero, plus the phone's strip (one of the two is
    ever visible)."""
    C.html(crafts_zone("band") + crafts_strip())


def rail_foot() -> None:
    C.html(crafts_zone("rail"))


def foot() -> None:
    C.html(crafts_zone("foot"))


#: Home's stylesheet: the grid, the ambience, the radar's place in the
#: hero, the crafts' zones. It rides inside the first markdown of the page
#: so only Home pays for it; the radar's and the crafts' rules come along
#: because their marks live here. It restyles nothing of the original
#: Home — the hero only gains room for the radar.
CSS = "<style>" + radar.CSS + crafts.CSS + f"""
@keyframes tr-home-breathe {{ 0%,100% {{ opacity:.7; transform:scale(1); }} 50% {{ opacity:1; transform:scale(1.05); }} }}
@keyframes tr-home-rise {{ from {{ opacity:0; transform:translateY(10px); }} to {{ opacity:1; transform:none; }} }}
/* ── ambience: the room's light, behind every card ───────────────────── */
[class*="st-key-trhome"] {{ position:relative; isolation:isolate; }}
[class*="st-key-trhome"] > [data-testid="stLayoutWrapper"] {{ position:relative; z-index:1; }}
[class*="st-key-trhome"] > [data-testid="stElementContainer"] {{ position:relative; z-index:1; }}
.tr-home-ambience {{ position:absolute; left:-60px; right:-60px; top:-140px; bottom:-120px; z-index:0; pointer-events:none; overflow:hidden; }}
.tr-home-ambience::before {{ content:""; position:absolute; right:-12%; top:-8%; width:80%; height:70%; filter: blur(16px); opacity:.9;
  background: conic-gradient(from 200deg at 92% 4%, transparent 0deg, rgba(255,107,133,.12) 14deg, rgba(255,51,85,.045) 30deg, rgba(255,51,85,.012) 44deg, transparent 56deg); }}
.tr-home-ambience::after {{ content:""; position:absolute; left:-10%; bottom:6%; width:56%; height:46%; filter: blur(18px);
  background: radial-gradient(closest-side, rgba(255,51,85,.16), rgba(255,51,85,.04) 55%, transparent 75%); animation: tr-home-breathe 9s ease-in-out infinite; }}
[class*="st-key-trhome"]::after {{ content:""; position:absolute; inset:-140px -60px -120px; z-index:0; pointer-events:none; opacity:.3;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.012) 0 1px, transparent 1px 3px); }}
/* the ambience's own element takes no room */
[class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-ambience) {{ position:absolute; inset:0 auto auto 0; width:0; height:0; margin:0; overflow:visible; z-index:0; }}
/* ── the radar in the hero ────────────────────────────────────────────── */
[class*="st-key-trstatus"] {{ position:relative; }}
[class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-hero-radar),
[class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-zone.band) {{ position:absolute; inset:0 auto auto 0; width:100%; height:0; margin:0; z-index:2; }}
.tr-hero {{ min-height:256px; animation: tr-home-rise .8s var(--tr-ease) backwards; }}
.tr-hero-inner {{ min-height:184px; }}   /* the mark rides the hero's bottom edge; the radar has the top-right to itself */
.tr-hero-inner > div:first-child {{ align-self:flex-start; }}   /* …and the copy stays where it always was, at the top */
.tr-platforms {{ animation: tr-home-rise .8s .1s var(--tr-ease) backwards; }}
.tr-hero-radar {{ position:absolute; left:0; right:0; top:0; height:256px; border-radius:22px; overflow:hidden; pointer-events:none; }}
.tr-hero-radar .scan {{ position:absolute; right:-496px; top:-518px; width:1200px; height:1200px; border-radius:50%; opacity:.7;
  background: conic-gradient(from 0deg, transparent 0deg 292deg, rgba(255,51,85,.025) 334deg, rgba(255,51,85,.07) 359deg, transparent 360deg); animation: tr-radar-sweep 6.5s linear infinite; }}
.tr-hero-radar[data-state="success"] .scan {{ background: conic-gradient(from 0deg, transparent 0deg 292deg, rgba(62,213,152,.025) 334deg, rgba(62,213,152,.07) 359deg, transparent 360deg); }}
.tr-hero-radar .tr-radar.home {{ position:absolute; right:44px; top:22px; filter: drop-shadow(0 0 16px rgba(255,51,85,.35)); animation: tr-home-rise .9s .15s var(--tr-ease) backwards; }}
.tr-hero-radar[data-state="success"] .tr-radar.home {{ filter: drop-shadow(0 0 18px rgba(62,213,152,.4)); }}
.tr-radar.home .ring {{ stroke-width:1.6; }} .tr-radar.home .r4 {{ opacity:.34; }} .tr-radar.home .r3 {{ opacity:.42; }} .tr-radar.home .axis {{ opacity:.22; }}
/* ── the crafts' zones ────────────────────────────────────────────────── */
.tr-home-zone {{ position:relative; pointer-events:none; }}
.tr-home-zone .tr-crafts {{ pointer-events:none; }}
.tr-home-zone .tr-craft {{ opacity:.42; }}
.tr-home-zone.band {{ position:absolute; left:0; right:30px; top:-108px; height:96px; }}   /* the empty band above the hero; the account chip is above the rail */
.tr-home-zone.rail {{ height:112px; margin-top:6px; }}
.tr-home-zone.foot {{ height:88px; margin-top:4px; }}
.tr-home-strip {{ display:none; }}
/* ── tablet (769–{DESKTOP_MIN - 1}px): one deliberate column, band + ledge (16) ─ */
@media (min-width: 769px) and (max-width: {DESKTOP_MIN - 1}px) {{
  [class*="st-key-trrail"] .tr-metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  [class*="st-key-trrail"] .tr-metric.wide {{ grid-column: span 1; }}
  .tr-hero-radar .tr-radar.home {{ width:96px !important; right:34px; top:30px; }}
  .tr-hero-radar .scan {{ right:-518px; top:-522px; }}
  .tr-home-zone.rail {{ display:none; }}
  .tr-home-zone.band {{ right:240px; }}   /* one column here: the account chip is above the hero's right end */
}}
/* ── desktop (≥{DESKTOP_MIN}px): the dashboard grid ─────────────────────
   Rows placed by hand, gaps as margins: a row with nothing in it (no live
   card) costs nothing. No column ratio can starve the rail: it is at
   least {RAIL_MIN}px wide by construction. */
@media (min-width: {DESKTOP_MIN}px) {{
  /* as the original: hero, shelf, live card and wizard down the left; the
     rail beside them from the top. The last row takes whatever the taller
     column leaves, and the crafts' ledge rides its bottom edge. */
  [class*="st-key-trhome"] {{ display:grid !important; grid-template-columns: minmax(0, 2.9fr) minmax({RAIL_MIN}px, 1.1fr); grid-template-rows: auto auto auto 1fr; gap: 0 28px; align-items:start; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"] {{ min-width:0; margin-bottom:18px; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trstatus"]) {{ grid-column: 1; grid-row: 1; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trrail"]) {{ grid-column: 2; grid-row: 1 / span 4; margin-bottom:0; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trlive"]) {{ grid-column: 1; grid-row: 2; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trwizard"]) {{ grid-column: 1; grid-row: 3; }}
  [class*="st-key-trhome"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-trfoot"]) {{ grid-column: 1; grid-row: 4; align-self: end; margin-bottom:0; }}
}}
/* ── phone (≤768px): the original mobile Home; a small radar, the strip of eight ─ */
@media (max-width: 768px) {{
  .tr-hero {{ min-height:0; }}
  .tr-hero-inner {{ min-height:0; }}
  .tr-hero-inner > div:first-child {{ padding-right:64px; }}
  .tr-hero-radar {{ height:120px; border-radius:18px; }}
  .tr-hero-radar .scan {{ display:none; }}
  .tr-hero-radar .tr-radar.home {{ width:56px !important; right:16px; top:18px; }}
  .tr-home-zone.band, .tr-home-zone.rail, .tr-home-zone.foot {{ display:none; }}
  [class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-zone.band) {{ position:static; height:auto; width:auto; }}
  .tr-home-strip {{ display:flex; justify-content:flex-end; gap:4px; padding:2px 2px 0; margin-top:-8px; }}
  .tr-home-strip .tr-craft {{ position:relative; margin:0; width:32px; height:32px; opacity:.55; }}
  .tr-home-strip .tr-craft::after {{ left:auto; right:-4px; transform:translate(0, 4px); white-space:normal; max-width:240px; text-align:right; }}
  .tr-home-strip .tr-craft:hover::after, .tr-home-strip .tr-craft:focus-visible::after, .tr-home-strip .tr-craft:focus::after {{ opacity:1; visibility:visible; transform:translate(0, 0); }}
  .tr-home-strip .tr-craft:focus::before {{ opacity:1; visibility:visible; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .tr-home-ambience::after, .tr-hero-radar .scan {{ animation:none; }}
  .tr-hero, .tr-platforms, .tr-hero-radar .tr-radar.home {{ animation:none; }}
}}
</style>"""

__all__ = ["CSS", "DESKTOP_MIN", "HOME_LAYOUT", "LINES", "RAIL_MIN", "STRIP", "ambience", "band", "crafts_strip",
           "crafts_zone", "foot", "hero_radar", "hero_radar_markup", "radar_state", "rail_foot"]
