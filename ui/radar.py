"""The radar — TicketRadar's identity mark, as one inline SVG.

There is deliberately no radar *image*. The mark is drawn once here as
vector geometry and animated by CSS with ``transform`` and ``opacity`` only
(the compositor's two cheap properties): the sweep rotates, the blips
ping, the core breathes. It scales with its box (``viewBox`` + ``width``),
so the same markup is the Login page's hero at 360px, a 96px loading badge
and, later, a 20px favicon-sized glyph in a card.

State is one attribute on the wrapper, ``data-state``, which the CSS reads::

    idle       slow sweep, no contacts             — a quiet dashboard
    scanning   the sweep with three pinging blips  — the Login page
    detecting  fast sweep, ring pulse              — waiting on a network call
    success    sweep stops, everything turns mint  — signed in / tickets found

The Login page uses *scanning* and *detecting*; the other two cost a few
lines of CSS and are here so a later phase does not draw a second radar.

``prefers-reduced-motion`` freezes everything at a composed angle.
"""

from __future__ import annotations

import math
import re
from html import escape

R = 200                      # centre of the 400×400 drawing
SWEEP_DEG = 68               # how much of the dish the sweep trails over

STATES = ("idle", "scanning", "detecting", "success")


def _polar(radius: float, degrees: float) -> tuple[float, float]:
    """A point on the dish, ``degrees`` clockwise from straight up."""
    rad = math.radians(degrees)
    return R + radius * math.sin(rad), R - radius * math.cos(rad)


def _wedge(radius: float, degrees: float) -> str:
    """The sweep: a sector whose leading edge points straight up and whose
    tail trails ``degrees`` behind (anticlockwise) — the fill fades along it."""
    tx, ty = _polar(radius, -degrees)
    lx, ly = _polar(radius, 0)
    return f"M{R} {R} L{tx:.1f} {ty:.1f} A{radius} {radius} 0 0 1 {lx:.1f} {ly:.1f} Z"


def _ticks(radius: float, count: int, length: float) -> str:
    """``count`` tick marks on a circle, as one dashed stroke."""
    circumference = 2 * math.pi * radius
    gap = circumference / count - 1.6
    return (f'<circle class="ticks" cx="{R}" cy="{R}" r="{radius}" fill="none" '
            f'stroke-width="{length}" stroke-dasharray="1.6 {gap:.3f}"/>')


#: (radius, clockwise degrees) of the three contacts in *scanning*.
BLIPS = ((118, 38), (154, 132), (74, 236))


def svg(*, size: int | str = 320, state: str = "scanning", tag: str = "",
        cls: str = "", label: str = "TicketRadar") -> str:
    """The radar in a square box ``size`` wide (px, or any CSS length).

    ``tag`` is an optional mono caption pinned beside the dish ("TICKETS
    DETECTED"); ``cls`` adds classes to the wrapper for page-specific layout.
    """
    if state not in STATES:
        state = "scanning"
    width = f"{size}px" if isinstance(size, int) else size
    # Paint servers are found by id document-wide, and a hidden twin (the
    # phone's mini radar on a desktop) would shadow this one in Firefox —
    # so every instance names its own, deterministically.
    uid = "trr-" + re.sub(r"[^a-z0-9]", "", f"{cls}{state}{width}".lower())
    blips = "".join(
        f'<g class="blip" style="animation-delay:{i * .9:.1f}s">'
        f'<circle class="ping" cx="{x:.1f}" cy="{y:.1f}" r="7"/>'
        f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/></g>'
        for i, (x, y) in enumerate(_polar(r, d) for r, d in BLIPS)
    )
    caption = f'<div class="tag">{tag}</div>' if tag else ""
    return f"""<div class="tr-radar {escape(cls)}" data-state="{state}" style="width:{escape(width)}" role="img" aria-label="{escape(label)}">
<svg viewBox="0 0 400 400" aria-hidden="true">
<defs>
  <radialGradient id="{uid}-dish"><stop offset="0" stop-color="#FF3355" stop-opacity=".16"/><stop offset=".55" stop-color="#FF3355" stop-opacity=".05"/><stop offset="1" stop-color="#FF3355" stop-opacity="0"/></radialGradient>
  <linearGradient id="{uid}-sweep" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FF3355" stop-opacity=".55"/><stop offset=".45" stop-color="#FF3355" stop-opacity=".16"/><stop offset="1" stop-color="#FF3355" stop-opacity="0"/></linearGradient>
  <radialGradient id="{uid}-core"><stop offset="0" stop-color="#FF6B85" stop-opacity=".9"/><stop offset=".4" stop-color="#FF3355" stop-opacity=".35"/><stop offset="1" stop-color="#FF3355" stop-opacity="0"/></radialGradient>
</defs>
<circle class="dish" cx="{R}" cy="{R}" r="192" fill="url(#{uid}-dish)"/>
<g class="rings" fill="none">
  {_ticks(186, 72, 7)}
  {_ticks(183, 12, 13)}
  <circle class="ring r4" cx="{R}" cy="{R}" r="170"/>
  <circle class="ring r3" cx="{R}" cy="{R}" r="126"/>
  <circle class="ring r2" cx="{R}" cy="{R}" r="82"/>
  <circle class="ring r1" cx="{R}" cy="{R}" r="38"/>
  <path class="axis" d="M{R} 30V370M30 {R}H370"/>
  <path class="axis diag" d="M{R - 120.2:.1f} {R - 120.2:.1f}L{R + 120.2:.1f} {R + 120.2:.1f}M{R + 120.2:.1f} {R - 120.2:.1f}L{R - 120.2:.1f} {R + 120.2:.1f}"/>
  <circle class="orbit" cx="{R}" cy="{R}" r="148" stroke-dasharray="2 10"/>
</g>
<g class="sweep">
  <path d="{_wedge(170, SWEEP_DEG)}" fill="url(#{uid}-sweep)"/>
  <path class="edge-glow" d="M{R} {R}V30"/>
  <path class="edge" d="M{R} {R}V30"/>
</g>
<g class="blips">{blips}</g>
<g class="core">
  <circle class="halo" cx="{R}" cy="{R}" r="26" fill="url(#{uid}-core)"/>
  <circle class="pulse" cx="{R}" cy="{R}" r="12"/>
  <circle class="dot" cx="{R}" cy="{R}" r="5.5"/>
  <circle class="pin" cx="{R}" cy="{R}" r="2"/>
</g>
</svg>{caption}</div>"""


#: The radar's stylesheet fragment (no ``<style>``); a page folds it into
#: its own single CSS block so there is one payload per run.
CSS = """
.tr-radar { position:relative; aspect-ratio:1; --trr:#FF3355; --trr-soft:#FF6B85; --trr-sweep:6.5s; }
.tr-radar svg { display:block; width:100%; height:100%; overflow:visible; }
.tr-radar .ticks { stroke:var(--trr); opacity:.28; }
.tr-radar .ring { stroke:var(--trr); stroke-width:1; }
.tr-radar .r4 { opacity:.22; } .tr-radar .r3 { opacity:.30; } .tr-radar .r2 { opacity:.40; } .tr-radar .r1 { opacity:.65; }
.tr-radar .axis { stroke:var(--trr); stroke-width:1; opacity:.14; }
.tr-radar .axis.diag { opacity:.07; }
.tr-radar .orbit { stroke:var(--trr-soft); stroke-width:1.2; opacity:.35; transform-box:view-box; transform-origin:50% 50%; animation: tr-radar-orbit 40s linear infinite; }
.tr-radar .sweep { transform-box:view-box; transform-origin:50% 50%; animation: tr-radar-sweep var(--trr-sweep) linear infinite; }
.tr-radar .edge { stroke:var(--trr-soft); stroke-width:2; stroke-linecap:round; opacity:.95; }
.tr-radar .edge-glow { stroke:var(--trr); stroke-width:7; stroke-linecap:round; opacity:.28; }
.tr-radar .blip { opacity:0; animation: tr-radar-contact 2.7s ease-out infinite; }
.tr-radar .blip .dot { fill:var(--trr-soft); }
.tr-radar .blip .ping { fill:none; stroke:var(--trr-soft); stroke-width:1.5; transform-box:fill-box; transform-origin:center; animation: tr-radar-ping 2.7s ease-out infinite; animation-delay:inherit; }
.tr-radar .core .pulse { fill:var(--trr); opacity:.28; transform-box:fill-box; transform-origin:center; animation: tr-radar-breathe 2.4s ease-in-out infinite; }
.tr-radar .core .dot { fill:var(--trr); }
.tr-radar .core .pin { fill:#fff; opacity:.9; }
.tr-radar .tag { position:absolute; left:calc(100% - 8%); top:44%; padding:8px 12px; border:1px solid rgba(255,51,85,.7); border-radius:8px; white-space:nowrap;
  font-family:var(--tr-mono); font-size:11px; letter-spacing:.2em; color:#FF6B85; line-height:1.45; background:rgba(12,8,10,.78);
  box-shadow: 0 0 0 1px rgba(255,51,85,.12), 0 12px 30px -14px rgba(255,51,85,.8); backdrop-filter: blur(6px); }
.tr-radar .tag::before { content:""; position:absolute; left:-9px; top:50%; width:8px; height:1px; background:rgba(255,51,85,.7); }

/* the mark: at 60px and under the fine detail is noise — fewer, heavier rings */
.tr-radar.mark .ticks, .tr-radar.mark .axis, .tr-radar.mark .orbit, .tr-radar.mark .blips, .tr-radar.mark .dish { display:none; }
.tr-radar.mark .ring { stroke-width:6; }
.tr-radar.mark .r4 { opacity:.32; } .tr-radar.mark .r3 { opacity:.45; } .tr-radar.mark .r2 { opacity:.6; } .tr-radar.mark .r1 { display:none; }
.tr-radar.mark .edge { stroke-width:8; } .tr-radar.mark .edge-glow { stroke-width:22; opacity:.35; }
.tr-radar.mark .core { transform:scale(2.4); transform-box:fill-box; transform-origin:center; }
.tr-radar.mark .core .pulse { display:none; }

/* states */
.tr-radar[data-state="idle"] { --trr-sweep:11s; }
.tr-radar[data-state="idle"] .blips { display:none; }
.tr-radar[data-state="idle"] .core .pulse { animation:none; opacity:.18; }
.tr-radar[data-state="detecting"] { --trr-sweep:2.2s; }
.tr-radar[data-state="detecting"] .r4 { animation: tr-radar-ringpulse 1.6s ease-out infinite; transform-box:fill-box; transform-origin:center; }
.tr-radar[data-state="detecting"] .blip { animation-duration:1.4s; }
.tr-radar[data-state="detecting"] .blip .ping { animation-duration:1.4s; }
.tr-radar[data-state="success"] { --trr:#3ED598; --trr-soft:#9FEBC9; }
.tr-radar[data-state="success"] .sweep { animation-play-state:paused; opacity:.35; }
.tr-radar[data-state="success"] .blips, .tr-radar[data-state="success"] .orbit { display:none; }
.tr-radar[data-state="success"] .r4 { animation: tr-radar-ringpulse 1.2s ease-out 1 both; transform-box:fill-box; transform-origin:center; }
.tr-radar[data-state="success"] .core .pulse { animation-duration:1.2s; }
.tr-radar[data-state="success"] .tag { color:#9FEBC9; border-color:rgba(62,213,152,.7); }

@keyframes tr-radar-sweep { to { transform:rotate(360deg); } }
@keyframes tr-radar-orbit { to { transform:rotate(-360deg); } }
@keyframes tr-radar-contact { 0% { opacity:0; } 8% { opacity:1; } 70% { opacity:.55; } 100% { opacity:0; } }
@keyframes tr-radar-ping { 0% { transform:scale(.4); opacity:.9; } 100% { transform:scale(3.2); opacity:0; } }
@keyframes tr-radar-breathe { 0%,100% { transform:scale(1); opacity:.28; } 50% { transform:scale(1.6); opacity:.12; } }
@keyframes tr-radar-ringpulse { 0% { transform:scale(.55); opacity:.7; } 100% { transform:scale(1.08); opacity:0; } }
@media (prefers-reduced-motion: reduce) {
  .tr-radar .sweep, .tr-radar .orbit, .tr-radar .core .pulse, .tr-radar .r4 { animation:none !important; }
  .tr-radar .sweep { transform:rotate(38deg); }
  .tr-radar .blip { animation:none !important; opacity:.8; }
  .tr-radar .blip .ping { animation:none !important; transform:scale(1.6); opacity:.35; }
}
"""

__all__ = ["BLIPS", "CSS", "STATES", "SWEEP_DEG", "svg"]
