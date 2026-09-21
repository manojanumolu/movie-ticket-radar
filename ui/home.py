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
from ui.theme import RAIL_RIG, _svg_uri, rail_reel

#: The rail's column never goes below this on a desktop.
RAIL_MIN = 320
#: Where Home stops being a single column and becomes the dashboard grid.
DESKTOP_MIN = 1150

#: One line of cinema per craft — a short, famous fragment, who said it,
#: and which house it comes from — shown as a subtitle card when a mark is
#: hovered or focused. Home only: the Login constellation keeps the crafts'
#: own names and blurbs (``Craft.tip``). Telugu lines are transliterated.
#: Deterministic: the same mark always says the same thing.
#:
#: The six Telugu lines open the page: they belong to the first six marks
#: of :data:`HOME_LAYOUT`'s ``band``, which is the first zone drawn and
#: reads left to right, and to the first six of the phone's :data:`STRIP`,
#: so the order is the same at every width. Each names its hero *and* the
#: film, and none of those six heroes says anything anywhere else — the
#: other eighteen are Marvel, Hollywood and two Batman lines.
LINES: dict[str, tuple[str, str, str]] = {
    # ── the band: the six heroes, in order ──────────────────────────────
    "direction": ("Thokkukuntu povaale!", "Jr NTR · RRR", "Tollywood"),
    "cinematography": ("vandha mandhini okesari Ramannu!", "Ram Charan · Magadheera", "Tollywood"),
    "screenwriting": ("Please... I kindly request!", "Prabhas · Salaar", "Tollywood"),
    "producing": ("Pushpa... Pushpa Raj... Thaggede Le!", "Allu Arjun · Pushpa: The Rise", "Tollywood"),
    "acting": ("Okka sari commit aithe... naa maata nene vinanu!", "Mahesh Babu · Pokiri", "Tollywood"),
    "editing": ("Naakkonchem thikka undi... kaani daaniko lekkundi!", "Pawan Kalyan · Gabbar Singh", "Tollywood"),
    "projection": ("To infinity and beyond!", "Toy Story", "Hollywood"),
    "distribution": ("There's no place like home.", "The Wizard of Oz", "Hollywood"),
    # ── the rail ────────────────────────────────────────────────────────
    "production_design": ("Wakanda forever.", "Black Panther", "Marvel"),
    "art_direction": ("Why so serious?", "The Joker · The Dark Knight", "DC"),
    "costume_design": ("Bond. James Bond.", "Dr. No", "Hollywood"),
    "makeup": ("I am Iron Man.", "Tony Stark", "Marvel"),
    "hair": ("That's my secret, Cap. I'm always angry.", "Bruce Banner · The Avengers", "Marvel"),
    "set_design": ("On your left.", "Captain America · The Winter Soldier", "Marvel"),
    "casting": ("You're gonna need a bigger boat.", "Jaws", "Hollywood"),
    "color_grading": ("I love you 3000.", "Iron Man", "Marvel"),
    # ── the foot ────────────────────────────────────────────────────────
    "sound": ("I am Groot.", "Groot", "Marvel"),
    "music": ("May the Force be with you.", "Star Wars", "Hollywood"),
    "sound_mixing": ("Hulk smash!", "Hulk", "Marvel"),
    "lighting": ("I'm Batman.", "Batman", "DC"),
    "visual_effects": ("I'll be back.", "The Terminator", "Hollywood"),
    "special_effects": ("Houston, we have a problem.", "Apollo 13", "Hollywood"),
    "choreography": ("Dread it. Run from it. Destiny arrives.", "Thanos · Infinity War", "Marvel"),
    "stunts": ("With great power comes great responsibility.", "Spider-Man", "Marvel"),
}

#: The six marks that carry the Telugu lines, in the order they are drawn.
TELUGU_SIX: tuple[str, ...] = ("direction", "cinematography", "screenwriting",
                               "producing", "acting", "editing")

#: The cinema technologies named in the dark behind the reel. A motif, not
#: a claim: which formats a theatre actually lists comes from the catalogue
#: and the wizard's Formats step, never from here.
PREMIUM_FORMATS = ("IMAX", "Dolby Cinema", "4DX", "ScreenX", "HDR by Barco", "Dolby Atmos", "Laser 4K", "PCX")

#: A minimal pictogram for each format — our own marks in the crafts' icon
#: language (24-unit grid, round caps), not anyone's trademark: a wide
#: frame, a screen with a wave, a moving seat, three panels, a light, a
#: radial sound field, a projector beam, a large frame with corners.
FORMAT_MARKS: dict[str, str] = {
    "IMAX": "<rect x='2' y='6' width='20' height='12' rx='1.5'/><path d='M6.5 10v4M17.5 10v4'/>",
    "Dolby Cinema": "<rect x='3' y='4.5' width='18' height='11' rx='1.5'/><path d='M5.5 19.5c2.2-2.6 4.3 2.6 6.5 0s4.3-2.6 6.5 0'/>",
    "4DX": "<path d='M7 6.5v6.5a2 2 0 0 0 2 2h7.5'/><path d='M9 13h7.5l1.5-5'/><path d='M3 8.5l2 1.5M3 12h2.5M21.5 4l-2 2M21 8.5h-2.5'/>",
    "ScreenX": "<path d='M2 8.5l5-2.5v12l-5-2.5z'/><rect x='8' y='5' width='8' height='14' rx='1'/><path d='M22 8.5l-5-2.5v12l5-2.5z'/>",
    "HDR by Barco": "<circle cx='12' cy='12' r='3.5'/><path d='M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.2 2.2M16.2 16.2l2.2 2.2M18.4 5.6l-2.2 2.2M7.8 16.2l-2.2 2.2'/>",
    "Dolby Atmos": "<circle cx='12' cy='12' r='2'/><path d='M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14'/>",
    "Laser 4K": "<rect x='2' y='9' width='7' height='6' rx='1.5'/><path d='M9 12l12.5-5.5v11z'/>",
    "PCX": "<path d='M3 8V5h3M21 8V5h-3M3 16v3h3M21 16v3h-3'/><rect x='7' y='8' width='10' height='8' rx='1'/>",
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

#: The eight the phone strip shows: the band's own eight, in the band's
#: order, so a phone reads the same first six lines as a desktop does.
STRIP = tuple(k for k, (z, _x, _y) in HOME_LAYOUT.items() if z == "band")
assert STRIP[:6] == TELUGU_SIX, "the phone strip opens with the six too"


def _icon(craft: crafts.Craft) -> str:
    """The craft's mark for Home. The six presentation attributes that are
    identical on all thirty-two marks live in the stylesheet instead
    (``.tr-craft .tr-ic``), so each rerun carries the paths and nothing
    else; ``ui.crafts.icon`` still writes them out for the Login page,
    which has no stylesheet of Home's."""
    return f'<svg class="tr-ic" viewBox="0 0 24 24" aria-hidden="true">{craft.paths}</svg>'


def _side(x: float, y: float, zone: str, *, positioned: bool) -> str:
    """Which way a mark's subtitle card opens, so it is never cut off.

    The zone decides, because each one sits differently on the page.

    *Across.* The ``band`` and the ``foot`` run the width of their column,
    so a mark in the middle centres its card and only one within 22% of
    either end anchors the card to its own edge. The ``rail`` is a narrow
    column against the right edge of the window — a card centred on a mark
    there reaches past the page — so every rail card opens leftward, into
    the room the page already has. The card is capped at the window's own
    width in CSS as well, so a long line wraps instead of overflowing.

    *Down.* Each zone opens the way it has room to. The ``band`` hangs
    *above* the hero, at the very top of the document: a card opening
    upward from there is cut off by the top of the browser window, which
    is the bug this fixes, so every band card opens downward over the
    hero. The ``foot`` is the opposite — it is the last thing on the page,
    with the version line right under it and nothing else below — so every
    foot card opens upward, into the room the page has. The ``rail`` sits
    in normal flow in between, where opening upward is right and only a
    mark near its own zone's top edge drops its card below.
    """
    side = "tip-l" if (zone == "rail" or x > 78) else "tip-r" if x < 22 else ""
    if positioned and zone != "foot" and (zone == "band" or y < 40):
        side = f"{side} tip-b".strip()
    return side


def _mark(craft: crafts.Craft, x: float, y: float, *, zone: str = "", positioned: bool = True) -> str:
    """One craft mark carrying its subtitle card: the line, then who said
    it, in the constellation's own hover/focus mechanics (the card is a
    real child element, so the two lines can be set differently).

    The line is carried once, by ``aria-label``: Home turns the
    constellation's ``data-tip`` pseudo-element off, so a second copy of
    the same sentence would be markup nobody ever reads.
    """
    quote, by, _house = LINES.get(craft.key, (craft.blurb, craft.label, ""))
    tip = f"{craft.label} — “{quote}” — {by}"
    cls = f"tr-craft {_side(x, y, zone, positioned=positioned)}".strip()
    style = f' style="left:{x}%;top:{y}%"' if positioned else ""
    return (f'<span class="{cls}"{style} tabindex="0" role="img" '
            f'aria-label="{escape(tip)}">{_icon(craft)}'
            f'<span class="tr-sub" aria-hidden="true"><span class="q">“{escape(quote)}”</span>'
            f'<span class="by">— {escape(by)}</span></span></span>')


def crafts_zone(zone: str) -> str:
    """The marks that live in ``zone`` (``band`` / ``rail`` / ``foot``), in
    the constellation's own tooltip mechanics."""
    marks = "".join(_mark(crafts.BY_KEY[k], x, y, zone=zone)
                    for k, (z, x, y) in HOME_LAYOUT.items() if z == zone)
    return (f'<div class="tr-home-zone {zone}" aria-label="The crafts of filmmaking">'
            f'<div class="tr-crafts">{marks}</div></div>')


def crafts_strip() -> str:
    """The phone's eight, in a row: the same marks and tips, so a tap or a
    focus reveals the line where there is nothing to hover."""
    marks = "".join(_mark(crafts.BY_KEY[k], 0, 0, zone="strip", positioned=False) for k in STRIP)
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


def format_badges() -> str:
    """The eight premium formats as badges set into the reel's outer track,
    one every 45°, each with its pictogram and its name. Every badge sits in
    its own group with no transform of its own, so a counter-rotation in CSS
    (``.fmt``) keeps it upright while the reel turns beneath it."""
    out = []
    for i, name in enumerate(PREMIUM_FORMATS):
        angle = i * 45
        label = name.upper()
        w = 30 + 4.6 * len(label)                      # the pill grows with its word
        out.append(
            f'<g transform="rotate({angle} 200 200) translate(200 28)"><g class="fmt"><g transform="rotate({-angle})">'
            f'<rect class="bg" x="{-w / 2:.1f}" y="-10" width="{w:.1f}" height="20" rx="10"/>'
            f'<g class="ic" transform="translate({-w / 2 + 5:.1f} -6.5) scale(0.54)">{FORMAT_MARKS[name]}</g>'
            f'<text class="t" x="{-w / 2 + 20:.1f}" y="2.6">{C.e(label)}</text>'
            f'</g></g></g>')
    return f'<g class="fmts">{"".join(out)}</g>'


def reel_svg(cls: str, *, formats: bool = False) -> str:
    """A cinema film reel as geometry: the rim with a highlight arc, the
    inner ring, six perforations, the hub — and, on the big reel, the
    premium formats set into its outer track. Stroke-only, so it reads as
    a real object in the dark and costs nothing to load."""
    holes = "".join(f'<circle cx="{200 + 118 * math.cos(k * math.pi / 3):.1f}" '
                    f'cy="{200 + 118 * math.sin(k * math.pi / 3):.1f}" r="34"/>' for k in range(6))
    return (f'<svg class="tr-reel {cls}" viewBox="0 0 400 400" fill="none" stroke="currentColor" aria-hidden="true">'
            '<circle class="rim" cx="200" cy="200" r="190" stroke-width="14"/>'
            '<circle class="sheen" cx="200" cy="200" r="190" stroke-width="3" stroke-dasharray="180 1014" stroke-linecap="round"/>'
            '<circle class="ring" cx="200" cy="200" r="160" stroke-width="1.5"/>'
            f'<g class="holes" stroke-width="10">{holes}</g>'
            '<circle class="hub" cx="200" cy="200" r="30" stroke-width="12"/>'
            '<circle class="pin" cx="200" cy="200" r="6" fill="currentColor" stroke="none"/>'
            f'{format_badges() if formats else ""}</svg>')


def strip_svg() -> str:
    """The film strip leaving the big reel: one curve, drawn three times —
    the band, its sprocket holes, its frames — as the Login's is."""
    d = "M1180 900 C 980 720, 840 760, 700 560 S 420 230, 60 250"
    return ('<svg class="tr-strip" viewBox="0 0 1200 960" fill="none" aria-hidden="true" preserveAspectRatio="none">'
            f'<path class="halo" d="{d}"/><path class="band" d="{d}"/><path class="holes" d="{d}"/>'
            f'<path class="film" d="{d}"/><path class="frames" d="{d}"/></svg>')


#: Where the rail's film leaves the first reel and meets the second, in
#: degrees clockwise from three o'clock; and how far it stays wound on each
#: rim before it fades into the spool. Both reels turn clockwise, so their
#: right-hand rims run downward — which is the way the film goes.
RAIL_FILM = {"wound": -80, "leave": 35, "meet": -10, "spool": 110, "fade": 30}


def rail_film_svg() -> str:
    """The strip of film between the rail's two reels, as one path: an arc
    on the first reel's rim, off it on a tangent, one curve down, onto the
    second reel's rim on a tangent, and an arc round that. The reels'
    centres and radii come from ``theme.RAIL_RIG`` — the numbers the theme
    places them by — so the film sits on the rims wherever the rail is
    drawn. Drawn as Home's strip is, five strokes over one path, with each
    wound end faded so the film seems to disappear into the spool."""
    top, bottom = rail_reel("top"), rail_reel("bottom")

    def on(reel, deg):
        cx, cy, r = reel
        return cx + r * math.cos(math.radians(deg)), cy + r * math.sin(math.radians(deg))

    def along(deg):   # the direction of clockwise travel on a rim at ``deg``
        return -math.sin(math.radians(deg)), math.cos(math.radians(deg))

    f = RAIL_FILM
    a0, p1 = on(top, f["wound"]), on(top, f["leave"])
    p2, a3 = on(bottom, f["meet"]), on(bottom, f["spool"])
    reach = 0.4 * math.dist(p1, p2)
    d1, d2 = along(f["leave"]), along(f["meet"])
    c1 = (p1[0] + reach * d1[0], p1[1] + reach * d1[1])
    c2 = (p2[0] - reach * d2[0], p2[1] - reach * d2[1])
    pt = lambda q: f"{q[0]:.1f} {q[1]:.1f}"
    d = (f"M{pt(a0)} A{top[2]:.1f} {top[2]:.1f} 0 0 1 {pt(p1)} "
         f"C{pt(c1)}, {pt(c2)}, {pt(p2)} A{bottom[2]:.1f} {bottom[2]:.1f} 0 0 1 {pt(a3)}")

    def fade(k, reel, end, toward):
        """A mask patch that dims the film from its wound end ``end`` to
        ``toward`` along the rim: black at the end, clear where the film
        is whole again."""
        e, t = on(reel, end), on(reel, toward)
        x0, x1 = min(e[0], t[0]) - 20, max(e[0], t[0]) + 20
        y0, y1 = min(e[1], t[1]) - 20, max(e[1], t[1]) + 20
        return (f'<linearGradient id="tr-side-fade{k}" gradientUnits="userSpaceOnUse" '
                f'x1="{e[0]:.1f}" y1="{e[1]:.1f}" x2="{t[0]:.1f}" y2="{t[1]:.1f}">'
                '<stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#000" stop-opacity="0"/>'
                '</linearGradient>',
                f'<rect x="{x0:.0f}" y="{y0:.0f}" width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" fill="url(#tr-side-fade{k})"/>')
    g1, r1 = fade(1, top, f["wound"], f["wound"] + f["fade"])
    g2, r2 = fade(2, bottom, f["spool"], f["spool"] - f["fade"])
    w, h = RAIL_RIG["width"], RAIL_RIG["height"]
    return (f'<svg class="tr-side-film" viewBox="0 0 {w} {h}" width="{w}" height="{h}" fill="none" aria-hidden="true">'
            f'<defs>{g1}{g2}<mask id="tr-side-spool" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">'
            f'<rect width="{w}" height="{h}" fill="#fff"/>{r1}{r2}</mask></defs>'
            '<g mask="url(#tr-side-spool)" stroke-linecap="butt">'
            f'<path class="halo" d="{d}"/><path class="edges" d="{d}"/><path class="band" d="{d}"/>'
            f'<path class="holes" d="{d}"/><path class="film" d="{d}"/><path class="frames" d="{d}"/></g></svg>')


# ── drawing ──────────────────────────────────────────────────────────────
def ambience() -> None:
    """Home's stylesheet and its background layers — the reels, the strip,
    the formats in the dark, the light — drawn once, first, behind it all."""
    C.html(CSS + '<div class="tr-home-ambience" aria-hidden="true">'
           + reel_svg("big", formats=True) + reel_svg("small") + strip_svg() + '</div>')


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
/* the formats, set into the reel's outer track: the pill turns with the reel, its face stays upright */
.tr-home-ambience .tr-reel .fmts {{ opacity:.78; }}
.tr-home-ambience .tr-reel .fmt {{ transform-box:fill-box; transform-origin:center; animation: tr-home-reel-back 84s linear infinite; }}
.tr-home-ambience .tr-reel .fmt .bg {{ fill:rgba(22,13,19,.92); stroke:rgba(255,140,160,.34); stroke-width:1; }}
.tr-home-ambience .tr-reel .fmt .ic {{ stroke:#FF8CA0; stroke-width:1.9; stroke-linecap:round; stroke-linejoin:round; fill:none; }}
.tr-home-ambience .tr-reel .fmt .t {{ font-family:var(--tr-mono); font-size:6.2px; letter-spacing:.9px; fill:rgba(255,224,230,.9); stroke:none; }}
.tr-home-ambience .tr-reel .fmts .fmt:nth-child(odd) .bg {{ stroke:rgba(255,140,160,.5); }}
/* ── the subtitle card: the crafts' line, then who said it ─────────────── */
.tr-home-zone .tr-craft::after, .tr-home-zone .tr-craft::before, .tr-home-strip .tr-craft::after, .tr-home-strip .tr-craft::before {{ display:none; }}
/* the six attributes every mark's icon shares, written once instead of thirty-two times */
.tr-craft .tr-ic {{ width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round; }}
/* max-content keeps a short line on one line, as it always was; the cap is
   what makes a long one wrap rather than reach past the edge of the page */
.tr-craft .tr-sub {{ position:absolute; bottom:calc(100% + 12px); left:50%; transform:translate(-50%, 4px); z-index:6; display:flex; flex-direction:column; gap:4px;
  padding:10px 14px 9px; border-radius:11px; white-space:normal; width:max-content; max-width:min(380px, calc(100vw - 32px)); text-align:left; pointer-events:none; opacity:0; visibility:hidden;
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
/* the rail's controls keep the theme's faces but stand on charcoal, so the reel never shows through a button */
[class*="st-key-trrail"] .stButton button {{ background-color: var(--tr-surface); }}
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
  /* …which leaves the band about 340px wide, narrower than a card. A centred card
     would reach left of the page's own content and sit under the sidebar, so here
     a band card anchors to its mark and is capped to the room in front of it.
     The cap is the drawn width divided by the 1.18 the mark grows to on hover,
     which the card inherits: 200px of text is 236px on screen, which still
     clears the account chip at the narrow end of this range. */
  .tr-home-zone.band .tr-sub {{ max-width:200px; }}
  .tr-home-zone.band .tr-craft:not(.tip-l) .tr-sub {{ left:0; right:auto; transform:translate(0, 4px); }}
  .tr-home-zone.band .tr-craft:not(.tip-l):hover .tr-sub,
  .tr-home-zone.band .tr-craft:not(.tip-l):focus .tr-sub,
  .tr-home-zone.band .tr-craft:not(.tip-l):focus-visible .tr-sub {{ transform:translate(0, 0); }}
  .tr-home-ambience .tr-reel.big {{ width:min(58vw, 560px); right:-16%; bottom:-120px; opacity:.75; }}
  .tr-home-ambience .tr-reel.small {{ display:none; }}
  .tr-home-ambience .tr-strip {{ opacity:.6; }}
  .tr-home-ambience .tr-reel .fmts {{ opacity:.7; }}
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
  .tr-home-ambience .tr-reel.small, .tr-home-ambience .tr-strip, .tr-home-ambience .tr-reel .fmts {{ display:none; }}   /* a cropped reel carries no legible badges */
  .tr-craft .tr-sub {{ max-width:min(300px, calc(100vw - 28px)); }}
  .tr-home-strip {{ position:relative; }}
  .tr-home-strip .tr-craft {{ position:static; }}   /* the card anchors to the strip's right edge, never off-screen */
  .tr-home-strip .tr-craft:hover, .tr-home-strip .tr-craft:focus, .tr-home-strip .tr-craft:focus-visible {{ transform:none; }}   /* a transform would re-anchor the card to the mark */
  /* the strip rides just under the hero, near the top of the scroll: the card drops
     below it, because opening upward is what the top of the window cuts off */
  .tr-home-strip .tr-craft .tr-sub {{ left:auto !important; right:0 !important; bottom:auto !important; top:calc(100% + 8px); transform:translate(0, -4px) !important; }}
  .tr-home-strip .tr-craft:hover .tr-sub, .tr-home-strip .tr-craft:focus .tr-sub, .tr-home-strip .tr-craft:focus-visible .tr-sub {{ transform:none !important; }}
  /* back in the flow, but still above what follows it: the card drops over the
     Platform shelf, and the shelf comes later in the document */
  [class*="st-key-trstatus"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-home-zone.band) {{ position:relative; inset:auto; z-index:7; height:auto; width:auto; }}
  .tr-home-strip {{ display:flex; justify-content:flex-end; gap:4px; padding:2px 2px 0; margin-top:-8px; }}
  .tr-home-strip .tr-craft {{ margin:0; width:32px; height:32px; opacity:.55; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .tr-home-ambience::after, .tr-hero-radar .scan {{ animation:none; }}
  .tr-home-ambience .tr-reel, .tr-home-ambience .tr-reel .fmt, [class*="st-key-trrail"] .tr-monitor:has(.tr-pill.ok)::before {{ animation:none; }}
  .tr-home-veil {{ animation:none; opacity:1; }}
  .tr-craft .tr-sub {{ transition:none; }}
  .tr-hero, .tr-platforms, .tr-hero-radar .tr-radar.home {{ animation:none; }}
}}
</style>"""

__all__ = ["CSS", "DESKTOP_MIN", "FORMAT_MARKS", "HOME_LAYOUT", "LINES", "PREMIUM_FORMATS", "RAIL_MIN", "STEP_ICONS", "STRIP",
           "ambience", "band", "busy", "crafts_strip", "crafts_zone", "foot", "format_badges", "hero_radar", "hero_radar_markup",
           "radar_state", "rail_film_svg", "rail_foot", "reel_svg", "step_icon_css", "strip_svg"]
