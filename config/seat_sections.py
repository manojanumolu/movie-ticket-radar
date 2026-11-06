"""A theatre's seat sections, per auditorium, as a person picks them.

BookMyShow names a section ("3D GOLD.") and numbers its area
(``areaCatCode``). An area number is only meaningful *within one
auditorium's layout*: ALLU Cinemas reuses area 2 and area 3 in both of its
auditoriums for physically different seats. So a logical section here is
(theatre, screen, area) — never an area alone, never a name, never a price.

ALLU Cinemas (ALUC), verified from BookMyShow's own data (6 Nov 2026):

* **Dolby Cinema** — four sections. Live read of a Dolby Cinema 3D show
  (21 Sep 2026, ``tests/test_category_watch.py`` ``TWO_GOLDS``): area 2
  "3D PLATINUM" ₹450, area 3 "3D GOLD" ₹395, area 4 "3D DIRECTOR CHOICE"
  ₹450, area 5 "3D GOLD." ₹395. The seat map (6 Nov report) agrees on the
  order: Platinum at the back (row A), Gold above Director Choice (row J).
  Where each block is, is the theatre owner's description.
* **Laser 4K Atmos** — a different auditorium with two sections only (every
  film shown only there lists area 2 PLATINUM and area 3 GOLD: ₹350/₹295 in
  2D, ₹390/₹335 in 3D). Their physical positions are not verified, so it has
  no logical sections here and the picker says so instead of guessing.

Display only: nothing here adds a section to a show, sets a price or decides
availability. Prices always come from BookMyShow, per screen and dimension.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    slug: str
    name: str            # "Gold Area 1"
    where: str           # "Upper seating", or "" when nothing more is known
    area_code: str       # BookMyShow's areaCatCode, as the payload writes it


#: (venue code, screen) -> its verified sections, in seat-map order.
SECTIONS: dict[tuple[str, str], tuple[Section, ...]] = {
    ("ALUC", "Dolby Cinema"): (
        Section("platinum", "Platinum", "Recliners", "0000000002"),
        Section("gold_1", "Gold Area 1", "Upper seating", "0000000003"),
        Section("director_choice", "Director Choice", "Between the two Gold areas", "0000000004"),
        Section("gold_2", "Gold Area 2", "Lower seating", "0000000005"),
    ),
}

#: Theatres with a verified auditorium above: every screen there is either
#: verified or, like ALLU's Laser 4K Atmos, explicitly not.
VERIFIED_VENUES = frozenset(venue for venue, _screen in SECTIONS)


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


def screen_for(venue_code: str, fmt: str) -> str:
    """The verified screen a chosen format names at this theatre ("Dolby
    Cinema" for "Dolby Cinema" or the older "Dolby Cinema 3D"), or ""."""
    for venue, screen in SECTIONS:
        if venue == venue_code and _norm(fmt) in (_norm(screen), _norm(f"{screen} 3d"), _norm(f"{screen} 2d")):
            return screen
    return ""


def sections(venue_code: str, screen: str) -> tuple[Section, ...]:
    return SECTIONS.get((venue_code, screen), ())


def entry_for(venue_code: str, screen: str, section: Section) -> str:
    """What a category watch stores for a logical section."""
    return f"@{venue_code}|{screen}|{section.area_code}"


def parse_entry(entry: str) -> tuple[str, str, str] | None:
    """(venue, screen, area) of a stored logical entry, else None — an
    older entry ("0000000003|3D GOLD", "GOLD") is not one."""
    if not entry.startswith("@"):
        return None
    parts = entry[1:].split("|")
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def section_for_entry(entry: str) -> Section | None:
    parsed = parse_entry(entry)
    if parsed is None:
        return None
    venue, screen, area = parsed
    return next((s for s in sections(venue, screen) if s.area_code == area), None)


__all__ = ["SECTIONS", "VERIFIED_VENUES", "Section", "entry_for", "parse_entry", "screen_for",
           "section_for_entry", "sections"]
