"""Premium cinema formats, explained — the Explorer's discovery section.

Educational only. Nothing here is read by monitoring, by the wizard's Formats
step or by the BookMyShow provider, and nothing here says what any theatre
shows today: what a theatre lists comes from BookMyShow. The one link to the
app's own data is read-only — a format named in the verified theatre data
(``config.theatre_capabilities.PREMIUM_SCREENS``) carries a quiet note saying
so, and that table is never written.

Two groups, each format in exactly one:

* **A — Hyderabad's screen brands.** The names India's chains give their own
  premium auditoriums, as BookMyShow sells them in Hyderabad.
* **B — International formats.** Licensed systems from Dolby, IMAX, Barco and
  others, found in cinemas worldwide.

They are related, not duplicates: a chain's brand (group A) is usually built
from the technologies in group B — a large screen, laser projection, Atmos
sound — under the chain's own name. Dolby Cinema is Dolby's own licensed
auditorium, so it sits in B, with the note when Hyderabad's data names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from config.theatre_capabilities import PREMIUM_SCREENS, VERIFIED
from ui import home

GROUPS = {
    "A": ("Hyderabad's screen brands", "The names India's chains give their own premium screens."),
    "B": ("International formats", "Licensed systems found in cinemas worldwide."),
}


@dataclass(frozen=True)
class Format:
    name: str
    group: str          # "A" or "B"
    blurb: str          # one or two short, factual sentences
    paths: str          # a 24-unit line icon, in the crafts' language
    match: str = ""     # how the theatre data spells it, when that differs


FORMATS: tuple[Format, ...] = (
    # ── A · Hyderabad's screen brands ───────────────────────────────────
    Format("P[XL]", "A", "PVR INOX's own premium large-format brand: an extra-large screen with "
           "premium projection and immersive sound.",
           "<path d='M7 5H4v14h3M17 5h3v14h-3'/><rect x='8.5' y='9' width='7' height='6' rx='1'/>", "PXL"),
    Format("LUXE", "A", "PVR INOX's luxury auditorium brand: recliner seating and in-seat service.",
           "<path d='M7 5v8.5a2 2 0 0 0 2 2h8'/><path d='M17 15.5V12h2.5'/><path d='M9 15.5 7.5 19M16 15.5l1.5 3.5'/>"),
    Format("PCX", "A", "Prasads Multiplex's own premium large-format screen, in Hyderabad.",
           home.FORMAT_MARKS["PCX"]),
    Format("EPIQ", "A", "A premium-screen name used by Hyderabad's AAA and ART Cinemas. AAA's is a "
           "direct-view LED screen rather than a projector.",
           "<rect x='3' y='5' width='18' height='12' rx='1.5'/><path d='M3 9h18M3 13h18M9 5v12M15 5v12'/><path d='M8 20h8'/>"),
    Format("MB LUXE", "A", "AMB Cinemas' luxury auditorium, named as such in its screen listings.",
           "<path d='M7 6v7.5a2 2 0 0 0 2 2h8'/><path d='M17 15.5V12h2.5'/><path d='M9 15.5 7.5 19M16 15.5l1.5 3.5'/>"
           "<path d='M17 3.5l.6 1.3 1.4.2-1 1 .2 1.4-1.2-.7-1.2.7.2-1.4-1-1 1.4-.2z'/>"),
    Format("VIP", "A", "A premium-seating screen name some theatres use; what it includes varies "
           "from theatre to theatre.",
           "<path d='M4 7h16v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4z'/>"
           "<path d='M12 9.6l.8 1.6 1.7.2-1.2 1.2.3 1.7-1.6-.8-1.6.8.3-1.7-1.2-1.2 1.7-.2z'/>"),
    Format("Macro XE", "A", "Cinépolis's own premium large-format brand: an extra-large screen with "
           "premium sound.",
           "<rect x='6' y='7' width='12' height='10' rx='1'/><path d='M2 12h2.5M19.5 12H22M3.5 9.5 2 12l1.5 2.5M20.5 9.5 22 12l-1.5 2.5'/>"),
    Format("Infinity Vision", "A", "The label BookMyShow uses for Marvel Studios releases on selected "
           "premium screens; TicketRadar shows it only where BookMyShow lists it.",
           "<path d='M12 12c-2-2.7-3.6-4-5.4-4a4 4 0 0 0 0 8c1.8 0 3.4-1.3 5.4-4zm0 0c2 2.7 3.6 4 5.4 4a4 4 0 0 0 0-8c-1.8 0-3.4 1.3-5.4 4z'/>"),
    # ── B · International formats ───────────────────────────────────────
    Format("IMAX", "B", "IMAX Corporation's large-format system (Canada): a taller, wider screen, its "
           "own projection and sound, and films remastered for it.", home.FORMAT_MARKS["IMAX"]),
    Format("Dolby Cinema", "B", "Dolby's premium auditorium: Dolby Vision HDR laser projection paired "
           "with Dolby Atmos sound.", home.FORMAT_MARKS["Dolby Cinema"]),
    Format("HDR by Barco", "B", "Barco's (Belgium) high-dynamic-range laser projection, for deeper "
           "blacks and brighter highlights.", home.FORMAT_MARKS["HDR by Barco"], "HDR By Barco"),
    Format("Dolby Atmos", "B", "Dolby's object-based surround sound, with speakers overhead as well as "
           "around you. A sound format, not a screen.", home.FORMAT_MARKS["Dolby Atmos"]),
    Format("4DX", "B", "CJ 4DPLEX's (South Korea) multi-sensory format: motion seats with effects such "
           "as wind, water and scent.", home.FORMAT_MARKS["4DX"]),
    Format("ScreenX", "B", "CJ 4DPLEX's 270-degree format: for selected scenes the picture extends "
           "onto the side walls.", home.FORMAT_MARKS["ScreenX"]),
    Format("MX4D", "B", "MediaMation's (USA) motion-and-effects format: moving seats with effects "
           "timed to the film.",
           "<path d='M8 5v8h8'/><path d='M8 13l-1 6M16 13l1 6'/><path d='M19 4.5c1.5 1.5 1.5 4 0 5.5M4.5 4.5c-1.5 1.5-1.5 4 0 5.5'/>"),
    Format("D-BOX", "B", "D-BOX's (Canada) motion seats, which move, tilt and vibrate in sync with "
           "the film.",
           "<path d='M7 4v9h8'/><path d='M6 17h10'/><path d='M19.5 5v7M18 6.5 19.5 5 21 6.5M18 10.5l1.5 1.5 1.5-1.5'/>"),
)
assert len({f.name for f in FORMATS}) == len(FORMATS), "each format appears once"


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


#: Format names the verified Hyderabad theatre data uses — read, never written.
IN_DATA = frozenset(_norm(f) for _page, screens in PREMIUM_SCREENS.values() for f in screens)


def in_hyderabad_data(fmt: Format) -> bool:
    return _norm(fmt.match or fmt.name) in IN_DATA


def _chip(fmt: Format) -> str:
    """One format: a focusable chip (hover, keyboard focus or a tap opens its
    card), the icon and the name, and the card itself — real text in the
    document, so a screen reader reads the name, the group and the blurb."""
    group = GROUPS[fmt.group][0]
    note = (f'<span class="data">Named in TicketRadar\'s Hyderabad theatre data ({VERIFIED[:7]}) · '
            'check BookMyShow for what\'s playing</span>' if in_hyderabad_data(fmt) else "")
    return (f'<li class="tr-fx" tabindex="0">'
            f'<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">{fmt.paths}</svg>'
            f'<span class="nm">{escape(fmt.name)}</span>'
            f'<span class="tip" role="tooltip"><span class="t">{escape(fmt.name)}</span>'
            f'<span class="g">{escape(group)}</span><span class="d">{escape(fmt.blurb)}</span>{note}</span></li>')


def section() -> str:
    groups = []
    for key, (title, sub) in GROUPS.items():
        chips = "".join(_chip(f) for f in FORMATS if f.group == key)
        groups.append(f'<div class="tr-fx-group {key.lower()}"><div class="tr-fx-head">'
                      f'<span class="k">{key}</span><span class="h">{escape(title)}</span>'
                      f'<span class="s">{escape(sub)}</span></div>'
                      f'<ul class="tr-fx-grid" aria-label="{escape(title)}">{chips}</ul></div>')
    return ('<section class="tr-fx-wrap" aria-label="Premium formats, explained">'
            '<div class="tr-fx-title"><span class="e tr-mono">Premium formats, explained</span>'
            '<span class="x">For reference only — hover, focus or tap a format. Chains often build '
            'their own brands from the international technologies.</span></div>'
            + "".join(groups) + '</section>')


CSS = """
.tr-fx-wrap { margin-top:26px; padding:20px 22px 22px; border:1px solid var(--tr-border); border-radius:20px;
  background:linear-gradient(180deg, rgba(255,255,255,.025), rgba(255,255,255,.008)); }
.tr-fx-title { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px; margin-bottom:14px; }
.tr-fx-title .e { font-size:10.5px; letter-spacing:.24em; text-transform:uppercase; color:var(--tr-accent-soft); }
.tr-fx-title .x { font-size:12.5px; color:var(--tr-text-3); }
.tr-fx-group + .tr-fx-group { margin-top:16px; padding-top:16px; border-top:1px dashed rgba(255,255,255,.09); }
.tr-fx-head { display:flex; flex-wrap:wrap; align-items:center; gap:4px 10px; margin-bottom:10px; }
.tr-fx-head .k { width:20px; height:20px; border-radius:6px; display:inline-flex; align-items:center; justify-content:center;
  font-size:11px; font-weight:800; color:#fff; background:rgba(255,51,85,.22); border:1px solid rgba(255,51,85,.45); }
.tr-fx-group.b .tr-fx-head .k { background:rgba(120,160,255,.16); border-color:rgba(120,160,255,.42); }
.tr-fx-head .h { font-size:14px; font-weight:700; color:var(--tr-text); }
.tr-fx-head .s { font-size:12px; color:var(--tr-text-3); }
.tr-fx-grid { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; }
.tr-fx { position:relative; display:flex; align-items:center; gap:9px; min-height:42px; padding:8px 12px; margin:0;
  border:1px solid var(--tr-border); border-radius:12px; background:rgba(255,255,255,.02); color:var(--tr-text-2);
  cursor:default; outline:none; transition: transform var(--tr-fast) var(--tr-ease), border-color var(--tr-fast) var(--tr-ease),
  background-color var(--tr-fast) var(--tr-ease), color var(--tr-fast) var(--tr-ease); }
.tr-fx .ic { width:20px; height:20px; flex-shrink:0; fill:none; stroke:currentColor; stroke-width:1.7; stroke-linecap:round;
  stroke-linejoin:round; color:var(--tr-accent-soft); }
.tr-fx-group.b .tr-fx .ic { color:#A9C1FF; }
.tr-fx .nm { font-size:13px; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
@keyframes tr-fx-nod { 0% { transform:scale(1); } 45% { transform:scale(1.18) rotate(-6deg); } 100% { transform:scale(1); } }
.tr-fx:hover, .tr-fx:focus, .tr-fx:focus-within { transform:translateY(-2px); border-color:rgba(255,51,85,.5);
  background:rgba(255,51,85,.07); color:#fff; z-index:5; }
.tr-fx-group.b .tr-fx:hover, .tr-fx-group.b .tr-fx:focus { border-color:rgba(120,160,255,.55); background:rgba(120,160,255,.07); }
.tr-fx:focus-visible { box-shadow:0 0 0 2px rgba(255,51,85,.6); }
.tr-fx:hover .ic, .tr-fx:focus .ic { animation: tr-fx-nod .5s var(--tr-ease) 1; }
.tr-fx .tip { position:absolute; left:0; bottom:calc(100% + 10px); z-index:8; width:max-content;
  max-width:min(300px, calc(100vw - 40px)); display:flex; flex-direction:column; gap:4px; padding:10px 12px; border-radius:11px;
  background:rgba(17,14,18,.97); border:1px solid rgba(255,51,85,.3); box-shadow:0 14px 30px -14px rgba(0,0,0,.9);
  opacity:0; visibility:hidden; transform:translateY(4px); pointer-events:none; white-space:normal;
  transition: opacity var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease), visibility var(--tr-fast); }
.tr-fx:hover .tip, .tr-fx:focus .tip, .tr-fx:focus-within .tip { opacity:1; visibility:visible; transform:none; }
.tr-fx-grid .tr-fx:nth-child(4n) .tip, .tr-fx-grid .tr-fx:nth-child(4n-1) .tip { left:auto; right:0; }
.tr-fx .tip .t { font-size:13.5px; font-weight:700; color:#F2F2F4; }
.tr-fx .tip .g { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.18em; text-transform:uppercase; color:#FF8CA0; }
.tr-fx-group.b .tr-fx .tip .g { color:#A9C1FF; }
.tr-fx .tip .d { font-size:12.5px; line-height:1.45; color:#C8C8D0; }
.tr-fx .tip .data { font-size:11px; line-height:1.4; color:var(--tr-text-3); border-top:1px solid rgba(255,255,255,.08); padding-top:5px; }
@media (max-width: 1100px) {
  .tr-fx-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
  .tr-fx-grid .tr-fx:nth-child(4n-1) .tip { left:0; right:auto; }
  .tr-fx-grid .tr-fx:nth-child(2n) .tip { left:auto; right:0; }
}
@media (max-width: 768px) {
  .tr-fx-wrap { padding:16px 14px 18px; border-radius:16px; }
  .tr-fx { min-height:44px; padding:8px 10px; }
  .tr-fx .tip { max-width:min(260px, calc(100vw - 48px)); }
}
@media (prefers-reduced-motion: reduce) {
  .tr-fx, .tr-fx .tip { transition:none; }
  .tr-fx:hover, .tr-fx:focus, .tr-fx:focus-within { transform:none; }
  .tr-fx:hover .ic, .tr-fx:focus .ic { animation:none; }
}
"""

__all__ = ["CSS", "FORMATS", "GROUPS", "IN_DATA", "Format", "in_hyderabad_data", "section"]
