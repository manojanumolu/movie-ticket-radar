"""What a theatre's BookMyShow seat sections are, physically — display only.

BookMyShow names a section ("3D GOLD.") and numbers its area
(``areaCatCode``); the area is the stable identity a category watch is
stored against (``SeatCategory.key``), the name and price are not. This file
adds what the payload cannot say — where the section is — for a theatre whose
sections have been verified, and nothing else: it never adds a section,
never sets a price and never decides availability.

ALLU Cinemas (venue ALUC), verified against BookMyShow's own data:
* 3D, Dolby Cinema (live read, 21 Sep 2026 — ``tests/test_category_watch.py``
  ``TWO_GOLDS``): area 2 "3D PLATINUM" ₹450, area 3 "3D GOLD" ₹395, area 4
  "3D DIRECTOR CHOICE" ₹450, area 5 "3D GOLD." ₹395;
* 2D (catalogue, 6 Nov 2026): area 2 "PLATINUM" ₹350, area 3 "GOLD" ₹295.
The same area is the same seats in 2D and 3D; only the price differs. Where
each block sits is the theatre owner's description (6 Nov 2026).
"""

from __future__ import annotations

#: venue code -> {area code without leading zeros: physical section}
SECTIONS: dict[str, dict[str, str]] = {
    "ALUC": {
        "2": "Platinum · recliners",
        "3": "Gold Area 1 · upper",
        "4": "Director Choice · between the Gold areas",
        "5": "Gold Area 2 · lower",
    },
}


def section_name(venue_code: str, area_code: str) -> str:
    """The physical section for a BookMyShow area at a verified theatre, or ""."""
    area = (area_code or "").lstrip("0")
    return SECTIONS.get(venue_code, {}).get(area, "") if area else ""


__all__ = ["SECTIONS", "section_name"]
