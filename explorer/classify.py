"""Operator, theatre type and locality — from BookMyShow's own text only.

BookMyShow gives a theatre a venue code, a name and (in the name's tail, see
``platforms.bookmyshow.split_venue_name``) an address fragment::

    "PVR: Nexus Mall Kukatpally, Hyderabad"   -> "PVR", "Nexus Mall Kukatpally, Hyderabad"
    "Sudarshan 35MM 4k Laser & Dolby Atmos: RTC X Roads"

It publishes no operator, no screen count and no structured locality. So
every classification here is a rule over that text, written down, and a
theatre that no rule covers is reported as unknown rather than guessed:

* **Operator** — the *start* of the theatre's name must be the brand
  ("PVR…", "INOX…", "Cinepolis…", "Asian…"). A brand word in the middle of a
  name ("Allu Cinemas", "Platinum Movietime") proves nothing and is not used.
* **Theatre type** — *Multiplex* when the name says so ("Multiplex",
  "Cineplex", "Superplex") or the operator is one of the national multiplex
  chains, which run no single screens; *Single screen* when BookMyShow lists
  the hall by its film gauge ("Devi 70MM", "Sandhya 35mm") — the naming
  Hyderabad's single-screen halls use. Everything else is *Unknown*, and an
  unknown theatre appears only under "All".
* **Locality** — a theatre gets a locality only when its BookMyShow area or
  name contains one of the Hyderabad place names below. The list is a
  vocabulary of places, not a theatre-to-place mapping: nothing here says
  which theatre is where; the theatre's own listing has to say it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

UNKNOWN = "Unknown"
INDEPENDENT = "Independent & others"

MULTIPLEX = "Multiplex"
SINGLE_SCREEN = "Single screen"
THEATRE_TYPES = (MULTIPLEX, SINGLE_SCREEN)

#: (operator, pattern on the start of the theatre's own name). Order matters
#: only for readability — no two patterns can match the same name.
OPERATOR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PVR", re.compile(r"^\s*pvr\b", re.I)),
    ("INOX", re.compile(r"^\s*inox\b", re.I)),
    ("Cinepolis", re.compile(r"^\s*cin[eé]polis\b", re.I)),
    ("AMB Cinemas", re.compile(r"^\s*amb\s+cinemas\b", re.I)),
    ("Asian Cinemas", re.compile(r"^\s*asian\b", re.I)),
    ("Miraj Cinemas", re.compile(r"^\s*miraj\s+cinemas\b", re.I)),
)

#: National multiplex chains: every theatre they operate is a multiplex.
MULTIPLEX_CHAINS = frozenset({"PVR", "INOX", "Cinepolis", "Miraj Cinemas"})

_MULTIPLEX_RE = re.compile(r"\b(multiplex|cineplex|superplex)\b", re.I)
_GAUGE_RE = re.compile(r"\b(70|35)\s?mm\b", re.I)

#: Hyderabad place names, as BookMyShow spells them, each with the other
#: spellings its listings have been seen to use. Shown in this order.
LOCALITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Gachibowli", ()),
    ("Kondapur", ()),
    ("Kukatpally", ()),
    ("Miyapur", ()),
    ("Ameerpet", ()),
    ("Banjara Hills", ("Banjara Hil",)),
    ("Jubilee Hills", ()),
    ("Hitech City", ("Hi-Tech City", "Hitec City")),
    ("Madhapur", ()),
    ("Kokapet", ()),
    ("Narsingi", ()),
    ("Nallagandla", ()),
    ("Chandanagar", ()),
    ("Nizampet", ()),
    ("Moosapet", ()),
    ("Balanagar", ()),
    ("Erragadda", ()),
    ("Borabanda", ()),
    ("Panjagutta", ("Punjagutta",)),
    ("Khairatabad", ("Kairathabad",)),
    ("Nampally", ()),
    ("Abids", ()),
    ("RTC X Roads", ("RTC Cross Roads",)),
    ("Musheerabad", ()),
    ("Kavadiguda", ()),
    ("Secunderabad", ()),
    ("Malkajgiri", ()),
    ("Alwal", ()),
    ("Kompally", ()),
    ("Suchitra", ()),
    ("Jeedimetla", ()),
    ("Chintal", ()),
    ("Medchal", ()),
    ("Shameerpet", ("Aliabad",)),
    ("ECIL", ()),
    ("Nacharam", ()),
    ("Uppal", ()),
    ("Kachiguda", ()),
    ("Amberpet", ()),
    ("Chilakalguda", ()),
    ("Kothapet", ()),
    ("Karmanghat", ()),
    ("Dilsukhnagar", ()),
    ("LB Nagar", ()),
    ("Vanasthalipuram", ()),
    ("Musarambagh", ()),
    ("Malakpet", ()),
    ("Yakutpura", ()),
    ("Bahadurpura", ()),
    ("Attapur", ()),
    ("Langer House", ()),
    ("Irrum Manzil", ()),
    ("Cyberabad", ()),
    ("RC Puram", ("Ramachandrapuram",)),
    ("Balapur", ()),
    ("Shamshabad", ()),
    ("Ibrahimpatnam", ()),
)


def _pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    parts = [r"\s+".join(re.escape(w) for w in n.split()) for n in names]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])", re.I)


_LOCALITY_PATTERNS = tuple((name, _pattern((name, *aliases))) for name, aliases in LOCALITIES)
LOCALITY_ORDER = {name: i for i, (name, _) in enumerate(LOCALITIES)}


@dataclass(frozen=True)
class TheatreFacts:
    operator: str        # an OPERATOR_RULES name, or INDEPENDENT
    theatre_type: str    # MULTIPLEX, SINGLE_SCREEN or UNKNOWN
    locality: str        # a LOCALITIES name, or UNKNOWN


def operator_of(name: str) -> str:
    for operator, rule in OPERATOR_RULES:
        if rule.search(name or ""):
            return operator
    return INDEPENDENT


def theatre_type_of(name: str, operator: str = "") -> str:
    text = name or ""
    operator = operator or operator_of(text)
    if _MULTIPLEX_RE.search(text) or operator in MULTIPLEX_CHAINS:
        return MULTIPLEX
    if _GAUGE_RE.search(text):
        return SINGLE_SCREEN
    return UNKNOWN


def locality_of(name: str, area: str = "") -> str:
    """The first known place named in the area, else in the name, else
    UNKNOWN — "Atrium Gachibowli, Hyderabad" is Gachibowli; a bare
    "Hyderabad" is unknown. The earliest mention in the text wins."""
    for text in (area or "", name or ""):
        best: tuple[int, str] | None = None
        for locality, pattern in _LOCALITY_PATTERNS:
            match = pattern.search(text)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), locality)
        if best is not None:
            return best[1]
    return UNKNOWN


@lru_cache(maxsize=8192)
def classify(name: str, area: str = "") -> TheatreFacts:
    """Everything the Explorer filters a theatre on. ``name`` is the short
    name and ``area`` the tail BookMyShow wrote after the colon.

    Cached: it is a pure function of BookMyShow's own two strings, and a
    city-wide read repeats each theatre once per show (about sixty locality
    patterns per call, which was over half the cost of building a read's
    shows)."""
    operator = operator_of(name)
    # A type marker can sit on either side of BookMyShow's colon.
    full = f"{name} {area}".strip()
    return TheatreFacts(operator=operator, theatre_type=theatre_type_of(full, operator),
                        locality=locality_of(name, area))


#: The city every catalogue venue is in. BookMyShow often closes a venue's
#: name with it ("PVR: Irrum Manzil, Hyderabad"); it never tells two
#: theatres apart, so it is dropped from the first line.
CITY = "Hyderabad"
_CITY_PATTERN = _pattern((CITY,))
#: What may sit between a venue's distinguishing words and a trailing place
#: name: "Central Mall, Panjagutta", "LUXE, PXL, 4DX: Cyberabad", "Atrium Gachibowli".
_TRIM = " \t,;:-\u2013\u2014"


def _trailing_place(text: str) -> tuple[str, str] | None:
    """``(rest, place)`` when ``text`` ends with a known place name or the
    city, else None. The longest trailing match wins ("Hitech City" over a
    shorter name ending at the same point)."""
    best: tuple[int, str] | None = None
    for place, pattern in (*_LOCALITY_PATTERNS, (CITY, _CITY_PATTERN)):
        for match in pattern.finditer(text):
            if text[match.end():].strip(" .,") == "" and (best is None or match.start() < best[0]):
                best = (match.start(), place)
    if best is None:
        return None
    return text[:best[0]].rstrip(_TRIM), best[1]


@lru_cache(maxsize=8192)
def venue_display(name: str, area: str = "") -> tuple[str, str]:
    """A theatre's two display lines from BookMyShow's own venue name.

    BookMyShow names a venue "<brand>: <distinguishing name>, <place>"
    ("PVR: Atrium Gachibowli, Hyderabad"); ``split_venue_name`` keeps the
    part before the colon as ``name`` and the rest as ``area``. Returned:

    * **title** — the full official name, brand and distinguishing words
      kept ("PVR: Atrium", "PVR ICON: Hitech", "PVR Superplex Inorbit:
      LUXE, PXL, 4DX"), less only the trailing place names, which move to
      the second line. When the words after the colon are nothing *but* a
      place ("PVR: Musarambagh"), they stay: they are what tells that
      theatre from the chain's others. Never shortened to the brand alone.
    * **where** — the place names BookMyShow wrote at the end of the
      venue's name ("Gachibowli"; "ECIL, Secunderabad"), else the first
      known place named anywhere in it, else the city. Nothing is invented.
    """
    head = " ".join((name or "").split())
    tail = " ".join((area or "").split())
    if not tail and ":" in head:
        h, _, t = head.partition(":")
        if h.strip() and t.strip():
            head, tail = h.strip(), t.strip()

    places: list[str] = []
    rest = tail
    while rest:
        found = _trailing_place(rest)
        if found is None:
            break
        shorter, place = found
        if not shorter:
            # The tail is only a place. The city alone says nothing and goes;
            # any other place is the theatre's own name and stays.
            if place == CITY:
                rest = ""
            elif not places:
                places.insert(0, place)
            break
        rest = shorter
        if place != CITY:
            places.insert(0, place)

    title = f"{head}: {rest}" if head and rest else (head or rest)
    if not title:                      # a venue with nothing but a city name
        title = tail or head
    if places:
        where = ", ".join(dict.fromkeys(places))
    else:
        locality = locality_of(head, tail)
        where = locality if locality != UNKNOWN else CITY
    return title, where


__all__ = [
    "INDEPENDENT",
    "LOCALITIES",
    "LOCALITY_ORDER",
    "MULTIPLEX",
    "MULTIPLEX_CHAINS",
    "OPERATOR_RULES",
    "SINGLE_SCREEN",
    "THEATRE_TYPES",
    "TheatreFacts",
    "UNKNOWN",
    "classify",
    "locality_of",
    "operator_of",
    "theatre_type_of",
    "venue_display",
]
