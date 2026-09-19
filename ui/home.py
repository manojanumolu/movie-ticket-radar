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

import math
from html import escape

from monitor.models import Availability, Monitor
from monitor.state import MonitorState
from ui import components as C
from ui import crafts
from ui import radar
from ui.theme import _svg_uri

#: The rail's column never goes below this on a desktop.
RAIL_MIN = 320
#: Where Home stops being a single column and becomes the dashboard grid.
DESKTOP_MIN = 1150

#: One line of cinema per craft — a short, famous fragment and who said
#: it — shown as a subtitle card when a mark is hovered or focused. Home
#: only: the Login constellation keeps the crafts' own names and blurbs
#: (``Craft.tip``). Deterministic: the same mark always says the same thing.
LINES: dict[str, tuple[str, str]] = {
    "direction": ("And… action.", "the director's call"),
    "cinematography": ("Here's looking at you, kid.", "Casablanca"),
    "screenwriting": ("Carpe diem. Seize the day.", "Dead Poets Society"),
    "producing": ("Show me the money!", "Jerry Maguire"),
    "acting": ("I can do this all day.", "Captain America"),
    "editing": ("Roads? Where we're going, we don't need roads.", "Back to the Future"),
    "production_design": ("There's no place like home.", "The Wizard of Oz"),
    "art_direction": ("Why so serious?", "The Dark Knight"),
    "costume_design": ("Bond. James Bond.", "Dr. No"),
    "makeup": ("I am Iron Man.", "Iron Man"),
    "hair": ("Nobody puts Baby in a corner.", "Dirty Dancing"),
    "sound": ("Just keep swimming.", "Finding Nemo"),
    "music": ("May the Force be with you.", "Star Wars"),
    "sound_mixing": ("You had me at hello.", "Jerry Maguire"),
    "lighting": ("Keep your friends close.", "The Godfather Part II"),
    "set_design": ("To infinity and beyond!", "Toy Story"),
    "visual_effects": ("I'll be back.", "The Terminator"),
    "special_effects": ("Houston, we have a problem.", "Apollo 13"),
    "choreography": ("Hakuna Matata.", "The Lion King"),
    "stunts": ("With great power comes great responsibility.", "Spider-Man"),
    "casting": ("You talking to me?", "Taxi Driver"),
    "color_grading": ("Hasta la vista, baby.", "Terminator 2"),
    "projection": ("Lights. Camera. Tickets.", "TicketRadar"),
    "distribution": ("Be first in line.", "TicketRadar"),
}

#: The cinema technologies named in the dark behind the reel. A motif, not
#: a claim: which formats a theatre actually lists comes from the catalogue
#: and the wizard's Formats step, never from here.
PREMIUM_FORMATS = ("IMAX", "Dolby Cinema", "4DX", "ScreenX", "HDR by Barco", "Dolby Atmos", "Laser 4K", "PCX")

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
    "sound": ("foot", 50, 26), "music": ("foot", 57, 66), "sound_mixing": ("foot", 64, 22),
    "lighting": ("foot", 71, 62), "visual_effects": ("foot", 78, 24), "special_effects": ("foot", 85, 66),
    "choreography": ("foot", 91, 22), "stunts": ("foot", 96.5, 60),
}
assert set(HOME_LAYOUT) == set(crafts.BY_KEY), "every craft has a home"

#: The eight the phone strip shows (the Login page's own selection).
STRIP = ("direction", "cinematography", "sound", "editing", "music", "visual_effects", "lighting", "projection")


def _mark(craft: crafts.Craft, x: float, y: float, *, positioned: bool = True) -> str:
    """One craft mark carrying its subtitle card: the line, then who said
    it, in the constellation's own hover/focus mechanics (the card is a
    real child element, so the two lines can be set differently)."""
    quote, by = LINES.get(craft.key, (craft.blurb, craft.label))
    tip = f"{craft.label} — “{quote}” — {by}"
    side = ("tip-l" if x > 84 else "tip-r" if x < 10 else "") + (" tip-b" if y < 40 and positioned else "")
    style = f' style="left:{x}%;top:{y}%"' if positioned else ""
    return (f'<span class="tr-craft {side.strip()}"{style} tabindex="0" role="img" '
            f'aria-label="{escape(tip)}" data-tip="{escape(tip)}">{crafts.icon(craft, 17)}'
            f'<span class="tr-sub" aria-hidden="true"><span class="q">“{escape(quote)}”</span>'
            f'<span class="by">— {escape(by)}</span></span></span>')


def crafts_zone(zone: str) -> str:
    """The marks that live in ``zone`` (``band`` / ``rail`` / ``foot``), in
    the constellation's own tooltip mechanics."""
    marks = "".join(_mark(crafts.BY_KEY[k], x, y) for k, (z, x, y) in HOME_LAYOUT.items() if z == zone)
    # the ledge under the wizard also names the premium formats, on its left;
    # its eight crafts keep to the right half, so the two never meet
    extra = formats_markup() if zone == "foot" else ""
    return (f'<div class="tr-home-zone {zone}" aria-label="The crafts of filmmaking">'
            f'{extra}<div class="tr-crafts">{marks}</div></div>')


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


def reel_svg(cls: str) -> str:
    """A cinema film reel as geometry: the rim with a highlight arc, the
    inner ring, six perforations, the hub. Stroke-only, so it reads as a
    real object in the dark and costs nothing to load."""
    holes = "".join(f'<circle cx="{200 + 118 * math.cos(k * math.pi / 3):.1f}" '
                    f'cy="{200 + 118 * math.sin(k * math.pi / 3):.1f}" r="34"/>' for k in range(6))
    return (f'<svg class="tr-reel {cls}" viewBox="0 0 400 400" fill="none" stroke="currentColor" aria-hidden="true">'
            '<circle class="rim" cx="200" cy="200" r="190" stroke-width="14"/>'
            '<circle class="sheen" cx="200" cy="200" r="190" stroke-width="3" stroke-dasharray="180 1014" stroke-linecap="round"/>'
            '<circle class="ring" cx="200" cy="200" r="160" stroke-width="1.5"/>'
            f'<g class="holes" stroke-width="10">{holes}</g>'
            '<circle class="hub" cx="200" cy="200" r="30" stroke-width="12"/>'
            '<circle class="pin" cx="200" cy="200" r="6" fill="currentColor" stroke="none"/></svg>')


def strip_svg() -> str:
    """The film strip leaving the big reel: one curve, drawn three times —
    the band, its sprocket holes, its frames — as the Login's is."""
    d = "M1180 900 C 980 720, 840 760, 700 560 S 420 230, 60 250"
    return ('<svg class="tr-strip" viewBox="0 0 1200 960" fill="none" aria-hidden="true" preserveAspectRatio="none">'
            f'<path class="halo" d="{d}"/><path class="band" d="{d}"/><path class="holes" d="{d}"/>'
            f'<path class="film" d="{d}"/><path class="frames" d="{d}"/></svg>')


def formats_markup() -> str:
    labels = "".join(f"<span>{C.e(f)}</span>" for f in PREMIUM_FORMATS)
    return ('<div class="tr-formats" aria-hidden="true"><div class="h">Premium formats</div>'
            f'<div class="g">{labels}</div></div>')


# ── drawing ──────────────────────────────────────────────────────────────
def ambience() -> None:
    """Home's stylesheet and its background layers — the reels, the strip,
    the formats in the dark, the light — drawn once, first, behind it all."""
    C.html(CSS + '<div class="tr-home-ambience" aria-hidden="true">'
           + reel_svg("big") + reel_svg("small") + strip_svg() + '</div>')


def busy(text: str) -> None:
    """The wait, for a genuinely slow operation only (saving a monitor and
    asking the worker to start, a retry): the radar and a line over a
    blurred page. Its CSS holds it invisible for 600 ms, so a quick answer
    never shows it; the rerun that ends the operation removes it."""
    C.html(f'<div class="tr-home-veil" role="status" aria-live="polite"><div class="box">'
           f'{radar.svg(size=110, state="detecting", cls="veil", label="")}'
           f'<div class="msg">{C.e(text)}</div><div class="sub">ONE MOMENT</div></div></div>')


def _icon_uri(name: str, color: str = "#FF8CA0") -> str:
    return _svg_uri(C.ICON_PATHS[name], color, "1.9")


#: The icon each wizard step carries, beside the number it keeps.
STEP_ICONS = {1: "location", 2: "movie", 3: "theatre", 4: "format", 5: "radar"}


def step_icon_css(step: int) -> None:
    """The current step's icon on its card heading (the heading carries no
    step index, so the page says which one it is)."""
    name = STEP_ICONS.get(int(step), "radar")
    C.html(f'<style>[class*="st-key-trcard_step"] .tr-step-head .tr-step-num:not(.ic)::after '
           f'{{ background-image:{_icon_uri(name)}; }}</style>')


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
_IC = {name: _svg_uri(C.ICON_PATHS[name], "#FF8CA0", "1.9") for name in
       ("location", "movie", "theatre", "format", "radar", "calendar", "clock", "check")}
_ICM = {name: _svg_uri(C.ICON_PATHS[name], "#8E8E98", "1.8") for name in
        ("location", "movie", "theatre", "format", "radar", "calendar", "clock", "check")}

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
/* the ambience's own element is lifted out of the grid and spread over the whole of Home, underneath */
[class*="st-key-trhome"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-ambience) {{ position:absolute; inset:0; width:100%; height:100%; margin:0; z-index:0; pointer-events:none; }}
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
/* ── the reels, the strip, the formats: the house behind the interface ── */
@keyframes tr-home-reel {{ to {{ transform:rotate(360deg); }} }}
@keyframes tr-home-reel-back {{ to {{ transform:rotate(-360deg); }} }}
@keyframes tr-home-wave {{ from {{ transform:translateY(-140%); }} to {{ transform:translateY(900%); }} }}
.tr-home-ambience .tr-reel {{ position:absolute; color:#FF8CA0; pointer-events:none; transform-origin:50% 50%; }}
.tr-home-ambience .tr-reel .rim {{ opacity:.10; }}
.tr-home-ambience .tr-reel .sheen {{ stroke:#FFC9D4; opacity:.22; }}
.tr-home-ambience .tr-reel .ring {{ opacity:.09; }}
.tr-home-ambience .tr-reel .holes {{ opacity:.11; }}
.tr-home-ambience .tr-reel .hub {{ opacity:.12; }}
.tr-home-ambience .tr-reel .pin {{ opacity:.35; }}
.tr-home-ambience .tr-reel.big {{ right:-6%; bottom:-140px; width:min(64vw, 820px); filter: drop-shadow(0 0 40px rgba(255,51,85,.16)); animation: tr-home-reel 84s linear infinite; }}
.tr-home-ambience .tr-reel.small {{ left:-120px; top:38%; width:min(30vw, 380px); opacity:.7; animation: tr-home-reel-back 120s linear infinite; }}
.tr-home-ambience .tr-strip {{ position:absolute; right:-4%; bottom:-40px; width:88%; height:70%; pointer-events:none; opacity:.9; }}
.tr-home-ambience .tr-strip .halo {{ stroke:rgba(255,51,85,.05); stroke-width:96; }}
.tr-home-ambience .tr-strip .band {{ stroke:#1A1219; stroke-width:54; }}
.tr-home-ambience .tr-strip .holes {{ stroke:rgba(255,120,145,.10); stroke-width:54; stroke-dasharray:6 13; }}
.tr-home-ambience .tr-strip .film {{ stroke:#0F0B11; stroke-width:40; }}
.tr-home-ambience .tr-strip .frames {{ stroke:rgba(255,255,255,.045); stroke-width:40; stroke-dasharray:1 96; }}
.tr-formats {{ position:absolute; left:2%; bottom:0; width:270px; pointer-events:none; font-family:var(--tr-mono); text-transform:uppercase; }}
.tr-home-zone.foot {{ min-height:132px; }}
.tr-formats .h {{ font-size:10px; letter-spacing:.34em; color:rgba(255,140,160,.55); padding-bottom:9px; margin-bottom:11px; border-bottom:1px solid rgba(255,140,160,.16); }}
.tr-formats .g {{ display:grid; grid-template-columns:1fr 1fr; gap:9px 14px; font-size:10.5px; letter-spacing:.18em; color:rgba(255,255,255,.22); }}
.tr-formats .g span {{ white-space:nowrap; text-shadow:0 0 14px rgba(255,51,85,.25); }}
.tr-formats .g span:nth-child(1), .tr-formats .g span:nth-child(4), .tr-formats .g span:nth-child(7) {{ color:rgba(255,200,210,.36); }}
/* ── the subtitle card: the crafts' line, then who said it ─────────────── */
.tr-home-zone .tr-craft::after, .tr-home-zone .tr-craft::before, .tr-home-strip .tr-craft::after, .tr-home-strip .tr-craft::before {{ display:none; }}
.tr-craft .tr-sub {{ position:absolute; bottom:calc(100% + 12px); left:50%; transform:translate(-50%, 4px); z-index:6; display:flex; flex-direction:column; gap:4px;
  padding:10px 14px 9px; border-radius:11px; white-space:nowrap; text-align:left; pointer-events:none; opacity:0; visibility:hidden;
  background: linear-gradient(168deg, rgba(23,19,26,.98), rgba(15,13,18,.99)); border:1px solid rgba(255,255,255,.12);
  box-shadow: 0 18px 40px -16px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.10), 0 10px 30px -18px rgba(255,51,85,.6);
  transition: opacity .18s var(--tr-ease), transform .18s var(--tr-ease), visibility .18s; }}
.tr-craft .tr-sub .q {{ font-family:var(--tr-sans); font-size:13.5px; font-weight:600; letter-spacing:0; line-height:1.35; color:#F2F2F4; text-transform:none; }}
.tr-craft .tr-sub .by {{ font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.2em; text-transform:uppercase; color:#FF8CA0; }}
.tr-craft:hover .tr-sub, .tr-craft:focus-visible .tr-sub, .tr-craft:focus .tr-sub {{ opacity:1; visibility:visible; transform:translate(-50%, 0); }}
.tr-craft.tip-r .tr-sub {{ left:-4px; transform:translate(0, 4px); }}
.tr-craft.tip-r:hover .tr-sub, .tr-craft.tip-r:focus .tr-sub, .tr-craft.tip-r:focus-visible .tr-sub {{ transform:translate(0, 0); }}
.tr-craft.tip-l .tr-sub {{ left:auto; right:-4px; transform:translate(0, 4px); }}
.tr-craft.tip-l:hover .tr-sub, .tr-craft.tip-l:focus .tr-sub, .tr-craft.tip-l:focus-visible .tr-sub {{ transform:translate(0, 0); }}
.tr-craft.tip-b .tr-sub {{ bottom:auto; top:calc(100% + 12px); }}
/* ── the active monitor: a green scan moving down the card while it watches ─ */
[class*="st-key-trrail"] .tr-monitor {{ position:relative; overflow:hidden; }}
[class*="st-key-trrail"] .tr-monitor:has(.tr-pill.ok)::before {{ content:""; position:absolute; left:0; right:0; top:0; height:11%; z-index:0; pointer-events:none;
  background: linear-gradient(180deg, transparent, rgba(62,213,152,.13) 55%, rgba(62,213,152,.28) 80%, transparent); animation: tr-home-wave 7s linear infinite; }}
[class*="st-key-trrail"] .tr-monitor > * {{ position:relative; z-index:1; }}
/* ── icons that reinforce the words: the wizard's steps, the answers, the metrics ─ */
.tr-step-pip .l::before, .tr-summary-strip .k::before, [class*="st-key-trrail"] .tr-metric .k::before, [class*="st-key-trcard_step"] .tr-step-head .tr-step-num:not(.ic)::after {{
  content:""; display:inline-block; width:13px; height:13px; margin-right:6px; vertical-align:-2px; background-size:contain; background-repeat:no-repeat; background-position:center; }}
.tr-step-pip .l::before {{ display:none; }}   /* the rail's labels are tight below 1500px; the card's heading carries the icon */
@media (min-width: 1500px) {{ .tr-step-pip .l::before {{ display:inline-block; }} }}
[class*="st-key-pick_step_1"] .tr-step-pip .l::before {{ background-image:{_ICM["location"]}; }}
[class*="st-key-pick_step_2"] .tr-step-pip .l::before {{ background-image:{_ICM["movie"]}; }}
[class*="st-key-pick_step_3"] .tr-step-pip .l::before {{ background-image:{_ICM["theatre"]}; }}
[class*="st-key-pick_step_4"] .tr-step-pip .l::before {{ background-image:{_ICM["format"]}; }}
[class*="st-key-pick_step_5"] .tr-step-pip .l::before {{ background-image:{_ICM["radar"]}; }}
.tr-step-pip.now .l::before {{ filter: brightness(1.6); }}
.tr-summary-strip .i:nth-child(1) .k::before {{ background-image:{_ICM["location"]}; }}
.tr-summary-strip .i:nth-child(2) .k::before {{ background-image:{_ICM["movie"]}; }}
.tr-summary-strip .i:nth-child(3) .k::before {{ background-image:{_ICM["theatre"]}; }}
.tr-summary-strip .i:nth-child(4) .k::before {{ background-image:{_ICM["format"]}; }}
.tr-summary-strip .k::before {{ width:11px; height:11px; margin-right:5px; }}
[class*="st-key-trrail"] .tr-metric .k::before {{ width:11px; height:11px; margin-right:5px; }}
[class*="st-key-trrail"] .tr-metric:nth-child(1) .k::before {{ background-image:{_ICM["clock"]}; }}
[class*="st-key-trrail"] .tr-metric:nth-child(2) .k::before {{ background-image:{_ICM["radar"]}; }}
[class*="st-key-trrail"] .tr-metric:nth-child(3) .k::before {{ background-image:{_ICM["clock"]}; }}
[class*="st-key-trrail"] .tr-metric:nth-child(4) .k::before {{ background-image:{_ICM["check"]}; }}
[class*="st-key-trrail"] .tr-metric:nth-child(5) .k::before {{ background-image:{_ICM["calendar"]}; }}
[class*="st-key-trrail"] .tr-metric:nth-child(6) .k::before {{ background-image:{_ICM["calendar"]}; }}
[class*="st-key-trcard_step"] .tr-step-head {{ position:relative; }}
[class*="st-key-trcard_step"] .tr-step-head .tr-step-num:not(.ic) {{ position:relative; }}
[class*="st-key-trcard_step"] .tr-step-head .tr-step-num:not(.ic)::after {{ position:absolute; right:-9px; bottom:-8px; width:20px; height:20px; margin:0; padding:3px; box-sizing:border-box; border-radius:50%;
  background-color:#14101A; background-size:14px 14px; border:1px solid rgba(255,51,85,.45); box-shadow:0 6px 14px -8px rgba(255,51,85,.9); }}
/* the icon's own <style> element takes no room */
[class*="st-key-trwizard"] [data-testid="stElementContainer"]:has(> .stMarkdown [data-testid="stMarkdownContainer"]:empty) {{ display:none; }}
/* ── the wait, for a slow operation only ─────────────────────────────── */
.tr-home-veil {{ position:fixed; inset:0; z-index:100000; display:flex; align-items:center; justify-content:center; pointer-events:none;
  background: radial-gradient(900px 600px at 50% 50%, rgba(20,10,14,.55), rgba(6,6,9,.78)); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  opacity:0; animation: tr-home-fadein .35s .6s var(--tr-ease) forwards; }}
@keyframes tr-home-fadein {{ to {{ opacity:1; }} }}
.tr-home-veil .box {{ display:flex; flex-direction:column; align-items:center; gap:18px; padding:34px 44px 30px; border-radius:24px;
  background: linear-gradient(168deg, rgba(23,19,26,.96), rgba(15,13,18,.98)); border:1px solid rgba(255,255,255,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 50px 100px -40px rgba(0,0,0,1), 0 30px 90px -50px rgba(255,51,85,.6); }}
.tr-home-veil .msg {{ font-family:var(--tr-mono); font-size:12.5px; letter-spacing:.3em; color:#FF8CA0; text-align:center; }}
.tr-home-veil .sub {{ font-family:var(--tr-mono); font-size:10px; letter-spacing:.3em; color:var(--tr-text-4); }}
/* ── tablet (769–{DESKTOP_MIN - 1}px): one deliberate column, band + ledge (16) ─ */
@media (min-width: 769px) and (max-width: {DESKTOP_MIN - 1}px) {{
  [class*="st-key-trrail"] .tr-metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  [class*="st-key-trrail"] .tr-metric.wide {{ grid-column: span 1; }}
  .tr-hero-radar .tr-radar.home {{ width:96px !important; right:34px; top:30px; }}
  .tr-hero-radar .scan {{ right:-518px; top:-522px; }}
  .tr-home-zone.rail {{ display:none; }}
  .tr-home-zone.band {{ right:240px; }}   /* one column here: the account chip is above the hero's right end */
  .tr-home-ambience .tr-reel.big {{ width:min(58vw, 560px); right:-16%; bottom:-120px; opacity:.75; }}
  .tr-home-ambience .tr-reel.small {{ display:none; }}
  .tr-home-ambience .tr-strip {{ opacity:.6; }}
  .tr-formats {{ left:2%; width:220px; }}
  .tr-formats .g span:nth-child(n+5) {{ display:none; }}
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
  .tr-home-ambience .tr-reel.big {{ width:380px; right:-190px; bottom:-150px; opacity:.6; animation-duration:140s; }}
  .tr-home-ambience .tr-reel.small, .tr-home-ambience .tr-strip, .tr-formats {{ display:none; }}
  .tr-craft .tr-sub {{ white-space:normal; max-width:230px; }}
  .tr-home-strip {{ position:relative; }}
  .tr-home-strip .tr-craft {{ position:static; }}   /* the card anchors to the strip's right edge, never off-screen */
  .tr-home-strip .tr-craft:hover, .tr-home-strip .tr-craft:focus, .tr-home-strip .tr-craft:focus-visible {{ transform:none; }}   /* a transform would re-anchor the card to the mark */
  .tr-home-strip .tr-craft .tr-sub {{ left:auto !important; right:0 !important; bottom:calc(100% + 8px); transform:translate(0, 4px) !important; }}
  .tr-home-strip .tr-craft:hover .tr-sub, .tr-home-strip .tr-craft:focus .tr-sub, .tr-home-strip .tr-craft:focus-visible .tr-sub {{ transform:none !important; }}
  [class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-zone.band) {{ position:static; height:auto; width:auto; }}
  .tr-home-strip {{ display:flex; justify-content:flex-end; gap:4px; padding:2px 2px 0; margin-top:-8px; }}
  .tr-home-strip .tr-craft {{ margin:0; width:32px; height:32px; opacity:.55; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .tr-home-ambience::after, .tr-hero-radar .scan {{ animation:none; }}
  .tr-home-ambience .tr-reel, [class*="st-key-trrail"] .tr-monitor:has(.tr-pill.ok)::before {{ animation:none; }}
  .tr-home-veil {{ animation:none; opacity:1; }}
  .tr-craft .tr-sub {{ transition:none; }}
  .tr-hero, .tr-platforms, .tr-hero-radar .tr-radar.home {{ animation:none; }}
}}
</style>"""

__all__ = ["CSS", "DESKTOP_MIN", "HOME_LAYOUT", "LINES", "PREMIUM_FORMATS", "RAIL_MIN", "STEP_ICONS", "STRIP", "ambience",
           "band", "busy", "crafts_strip", "crafts_zone", "foot", "formats_markup", "hero_radar", "hero_radar_markup",
           "radar_state", "rail_foot", "reel_svg", "step_icon_css", "strip_svg"]
