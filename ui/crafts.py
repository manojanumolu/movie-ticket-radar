"""The twenty-four crafts of filmmaking, as line icons with a line each.

They are the Login page's atmosphere: faint marks scattered across the
cinematic zone that name what goes into a film, each with a tooltip on
hover or keyboard focus. They are drawn here, not shipped as image files,
in the one icon style the UI already has — a 24-unit grid, round caps,
1.7px strokes (``ui.components.ICON_PATHS``) — so a craft icon and a
sidebar icon are visibly one family and cost nothing to load.

``constellation()`` lays them out at fixed fractions of the visual so they
sit in the negative space around the radar, the posters and the headline,
never on the form. The tooltip is CSS (``data-tip`` → ``::after``): no
script, and nothing that could conflict with Streamlit's own ``help``
tooltips, which use a different element entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Craft:
    key: str
    label: str
    blurb: str
    paths: str

    @property
    def tip(self) -> str:
        return f"{self.label} — {self.blurb}"


CRAFTS: tuple[Craft, ...] = (
    Craft("direction", "Direction", "Bringing the story and performances to life.",
          "<path d='M3.5 9.5h17v9a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z'/><path d='M3.8 9.5 5 5l15.2 2.6-.7 1.9'/><path d='M8 6l2 3.5M12 6.7l2 3.5M16 7.4l2 3.5'/>"),
    Craft("cinematography", "Cinematography", "The visual language of a film.",
          "<rect x='3' y='9.5' width='13' height='9.5' rx='2'/><path d='M16 12.5l5-2.5v9l-5-2.5'/><circle cx='7' cy='5.8' r='2.3'/><circle cx='12.5' cy='5.8' r='2.3'/>"),
    Craft("screenwriting", "Screenwriting", "Where every film begins — on the page.",
          "<path d='M4 20l3.6-.9L18 8.7l-2.7-2.7L4.9 16.4z'/><path d='M13.6 7.7l2.7 2.7'/><path d='M16.4 4.9l1-1a1.9 1.9 0 0 1 2.7 2.7l-1 1'/>"),
    Craft("producing", "Producing", "Turning an idea into a film that gets made.",
          "<rect x='3' y='7.5' width='18' height='12' rx='2'/><path d='M9 7.5V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1.5'/><path d='M3 12.5h18'/>"),
    Craft("acting", "Acting", "The people you believe in for two hours.",
          "<path d='M3.5 4.5h8v6a4 4 0 0 1-8 0z'/><path d='M6 7.7h.8M8.3 7.7h.8M6 10.2c.8.8 2.2.8 3 0'/><path d='M12.5 9.5h8v6a4 4 0 0 1-8 0z'/><path d='M15 12.7h.8M17.3 12.7h.8M15 16c.8-.8 2.2-.8 3 0'/>"),
    Craft("editing", "Editing", "Shaping scenes into the final story.",
          "<circle cx='6' cy='6.5' r='2.5'/><circle cx='6' cy='17.5' r='2.5'/><path d='M8.2 8l11.8 10M8.2 16L20 6'/>"),
    Craft("production_design", "Production Design", "The world the story lives in.",
          "<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M3 10h18M10.5 10v10'/><path d='M6 7h3'/>"),
    Craft("art_direction", "Art Direction", "Every surface, colour and prop in frame.",
          "<path d='M12 3.5a8.5 8.5 0 1 0 0 17h1.4a2 2 0 0 0 0-4H12a1.5 1.5 0 0 1 0-3h3.6a5 5 0 0 0 5-5c0-2.8-3.8-5-8.6-5z'/><circle cx='7.6' cy='11' r='1'/><circle cx='9.8' cy='7.4' r='1'/><circle cx='14.4' cy='7' r='1'/>"),
    Craft("costume_design", "Costume Design", "Character, told in what they wear.",
          "<path d='M8.5 4l3.5 1.8L15.5 4l4.5 2.8-2 3.4-1.7-.9V20H7.7V9.3L6 10.2 4 6.8z'/>"),
    Craft("makeup", "Makeup", "Faces, scars and years — applied by hand.",
          "<path d='M4.5 19.5l2.2-2.2'/><path d='M7.2 16.8l7.2-7.2 2.7 2.7-7.2 7.2a1.9 1.9 0 0 1-2.7-2.7z'/><path d='M14.4 9.6l3.1-3.1a1.9 1.9 0 0 1 2.7 2.7l-3.1 3.1'/>"),
    Craft("hair", "Hair", "Period, mood and character, from the top.",
          "<rect x='4' y='4' width='16' height='5' rx='1.6'/><path d='M6.6 9v10.5M10.2 9v7.5M13.8 9v10.5M17.4 9v7.5'/>"),
    Craft("sound", "Sound", "Everything you hear beyond the image.",
          "<rect x='9' y='3' width='6' height='11' rx='3'/><path d='M5.5 11a6.5 6.5 0 0 0 13 0'/><path d='M12 17.5V21M9 21h6'/>"),
    Craft("music", "Music", "The score that tells you how to feel.",
          "<path d='M9 18.5V6l10-2v12'/><circle cx='6.5' cy='18.5' r='2.5'/><circle cx='16.5' cy='16' r='2.5'/>"),
    Craft("sound_mixing", "Sound Mixing", "Dialogue, effects and score in balance.",
          "<path d='M6 4v16M12 4v16M18 4v16'/><rect x='4' y='9' width='4' height='3.2' rx='1'/><rect x='10' y='13' width='4' height='3.2' rx='1'/><rect x='16' y='6' width='4' height='3.2' rx='1'/>"),
    Craft("lighting", "Lighting", "Where the light falls, and where it doesn't.",
          "<path d='M9 18h6M10 21h4'/><path d='M8.6 14.6a5.6 5.6 0 1 1 6.8 0c-.8.7-1 1.5-1 2.4h-4.8c0-.9-.2-1.7-1-2.4z'/>"),
    Craft("set_design", "Set Design", "Built, dressed and lit to be believed.",
          "<path d='M3 4h18'/><path d='M5 4v16M19 4v16'/><path d='M5 6c3 0 5 3 5 8v6M19 6c-3 0-5 3-5 8v6'/><path d='M3 20h18'/>"),
    Craft("visual_effects", "Visual Effects", "What the camera couldn't capture — made real.",
          "<path d='M4 20l9.5-9.5'/><path d='M14.5 3.5l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z'/><path d='M19 13l.6 1.6 1.6.6-1.6.6L19 17.4l-.6-1.6-1.6-.6 1.6-.6z'/>"),
    Craft("special_effects", "Special Effects", "Fire, rain and wreckage — done on set.",
          "<path d='M12 3c1.2 3 4.5 4.6 4.5 9.2a4.5 4.5 0 0 1-9 0c0-1.6.5-2.7 1.3-3.6.3 1.1.9 1.8 1.9 2C10.3 8.3 11 5.6 12 3z'/><path d='M7 21h10'/>"),
    Craft("choreography", "Choreography", "Movement, designed beat by beat.",
          "<circle cx='13.2' cy='4.6' r='1.8'/><path d='M4 12.5l5-1.2 3-2.3 4 2.5 4-2'/><path d='M12 9l-2.2 6.2L13 21M9.8 15.2L5.5 20.5'/>"),
    Craft("stunts", "Stunts", "The risk on screen, performed for real.",
          "<circle cx='15.5' cy='5' r='1.8'/><path d='M8 20.5l4-6 4 2 3-5-4-2.2-4 3-4-1'/><path d='M3 8.5h2.2M3 12.5h3'/>"),
    Craft("casting", "Casting", "Finding the one face for the part.",
          "<circle cx='9' cy='8.5' r='3'/><path d='M3.5 19.5a5.5 5.5 0 0 1 11 0'/><path d='M17.5 3.8l.9 2 2.1.3-1.5 1.5.4 2.1-1.9-1-1.9 1 .4-2.1-1.5-1.5 2.1-.3z'/>"),
    Craft("color_grading", "Colour Grading", "The final look, one frame at a time.",
          "<circle cx='12' cy='8.5' r='4.8'/><circle cx='8.6' cy='14.4' r='4.8'/><circle cx='15.4' cy='14.4' r='4.8'/>"),
    Craft("projection", "Projection", "Light through film, onto the big screen.",
          "<rect x='3' y='8' width='12' height='9' rx='2'/><circle cx='9' cy='12.5' r='2.4'/><path d='M15 10.5l6-3v10l-6-3'/><path d='M6 17v3M12 17v3'/>"),
    Craft("distribution", "Distribution", "How a film reaches your theatre.",
          "<circle cx='12' cy='12' r='8.5'/><path d='M3.5 12h17M12 3.5c3 3 3 14 0 17M12 3.5c-3 3-3 14 0 17'/>"),
)

BY_KEY = {c.key: c for c in CRAFTS}

#: Where each craft sits in the desktop visual, as (left %, top %) of the
#: cinematic zone — in the negative space: a thin column between the radar
#: and the posters, the band beneath the posters, and the floor under the
#: headline. Order follows :data:`CRAFTS`.
LAYOUT: tuple[tuple[float, float], ...] = (
    (45.5, 7), (47, 21), (44.5, 36), (48.5, 49),
    (64, 58), (75, 55.5), (87, 57), (97, 62),
    (61, 69), (71.5, 72), (83, 68), (94, 75.5),
    (65, 84.5), (78, 87), (90, 83), (98, 92),
    (5, 93), (17, 96), (29, 92), (41, 96), (53, 93),
    (4, 44), (16, 47.5), (27, 43),
)


def icon(craft: Craft, size: int = 17) -> str:
    return (f'<svg class="tr-ic" viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{craft.paths}</svg>')


def _side(x: float, y: float) -> str:
    """Which way the tooltip opens so it never leaves the zone."""
    cls = []
    if x < 18:
        cls.append("tip-r")
    elif x > 82:
        cls.append("tip-l")
    if y < 16:
        cls.append("tip-b")
    return " ".join(cls)


def constellation() -> str:
    """The twenty-four, positioned for the desktop visual."""
    marks = []
    for craft, (x, y) in zip(CRAFTS, LAYOUT):
        marks.append(
            f'<span class="tr-craft {_side(x, y)}" style="left:{x}%;top:{y}%" tabindex="0" '
            f'role="img" aria-label="{escape(craft.tip)}" data-tip="{escape(craft.tip)}">{icon(craft)}</span>'
        )
    return f'<div class="tr-crafts" aria-label="The crafts of filmmaking">{"".join(marks)}</div>'


def strip(keys: tuple[str, ...] = ("direction", "cinematography", "sound", "editing",
                                   "music", "visual_effects", "lighting", "projection")) -> str:
    """A short row for the phone header — decoration only, no tooltips,
    because there is nothing to hover on a touch screen."""
    return ('<div class="tr-craft-strip" aria-hidden="true">'
            + "".join(f'<span title="{escape(BY_KEY[k].label)}">{icon(BY_KEY[k], 15)}</span>' for k in keys)
            + "</div>")


#: Stylesheet fragment for the constellation and its tooltips (no ``<style>``).
CSS = """
.tr-crafts { position:absolute; inset:0; z-index:3; pointer-events:none; }
.tr-craft { position:absolute; width:36px; height:36px; margin:-18px 0 0 -18px; display:flex; align-items:center; justify-content:center;
  border-radius:50%; color:rgba(255,255,255,.66); opacity:.42; pointer-events:auto; cursor:default; outline:none;
  transition: opacity .2s var(--tr-ease), transform .2s var(--tr-ease), color .2s var(--tr-ease), background .2s var(--tr-ease), box-shadow .2s var(--tr-ease); }
.tr-craft svg { display:block; }
.tr-craft:hover, .tr-craft:focus-visible { opacity:1; color:#FF8CA0; transform:scale(1.18); background:rgba(255,51,85,.10);
  box-shadow: 0 0 0 1px rgba(255,51,85,.38), 0 0 22px -4px rgba(255,51,85,.75); z-index:5; }
.tr-craft::after { content:attr(data-tip); position:absolute; bottom:calc(100% + 10px); left:50%; transform:translate(-50%, 4px);
  white-space:nowrap; padding:8px 12px; border-radius:9px; font-family:var(--tr-sans); font-size:12.5px; font-weight:500; line-height:1.35; letter-spacing:0;
  color:#F2F2F4; background:rgba(17,14,18,.97); border:1px solid rgba(255,255,255,.12);
  box-shadow: 0 16px 36px -14px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.08); opacity:0; visibility:hidden; pointer-events:none;
  transition: opacity .18s var(--tr-ease), transform .18s var(--tr-ease); }
.tr-craft::before { content:""; position:absolute; bottom:calc(100% + 5px); left:50%; margin-left:-5px; border:5px solid transparent; border-top-color:rgba(17,14,18,.97);
  opacity:0; visibility:hidden; transition: opacity .18s var(--tr-ease); }
.tr-craft:hover::after, .tr-craft:focus-visible::after { opacity:1; visibility:visible; transform:translate(-50%, 0); }
.tr-craft:hover::before, .tr-craft:focus-visible::before { opacity:1; visibility:visible; }
.tr-craft.tip-r::after { left:-4px; transform:translate(0, 4px); }
.tr-craft.tip-r:hover::after, .tr-craft.tip-r:focus-visible::after { transform:translate(0, 0); }
.tr-craft.tip-l::after { left:auto; right:-4px; transform:translate(0, 4px); }
.tr-craft.tip-l:hover::after, .tr-craft.tip-l:focus-visible::after { transform:translate(0, 0); }
.tr-craft.tip-b::after { bottom:auto; top:calc(100% + 10px); }
.tr-craft.tip-b::before { bottom:auto; top:calc(100% + 5px); border-top-color:transparent; border-bottom-color:rgba(17,14,18,.97); }
.tr-craft-strip { display:flex; gap:6px; flex-wrap:wrap; }
.tr-craft-strip span { width:30px; height:30px; display:inline-flex; align-items:center; justify-content:center; border-radius:50%;
  color:rgba(255,255,255,.55); border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); }
@media (prefers-reduced-motion: reduce) { .tr-craft, .tr-craft::after, .tr-craft::before { transition:none; } }
"""

__all__ = ["BY_KEY", "CRAFTS", "CSS", "Craft", "LAYOUT", "constellation", "icon", "strip"]
