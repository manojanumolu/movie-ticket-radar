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


def classify(name: str, area: str = "") -> TheatreFacts:
    """Everything the Explorer filters a theatre on. ``name`` is the short
    name and ``area`` the tail BookMyShow wrote after the colon."""
    operator = operator_of(name)
    # A type marker can sit on either side of BookMyShow's colon.
    full = f"{name} {area}".strip()
    return TheatreFacts(operator=operator, theatre_type=theatre_type_of(full, operator),
                        locality=locality_of(name, area))


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
]
