"""Cinema formats for the Explorer's filter — read out of BookMyShow's own text.

The problem this solves
-----------------------
BookMyShow names a show's screen with one free-text string per show (its
``screenAttr``), and a theatre writes it however it likes. From the synced
Hyderabad data (``data/showtimes.json``, Nov 2026)::

    "Atmos"  "Dolby Atmos"  "4K Dolby Atmos"  "Laser 4K Atmos"
    "Barco Flagship Laser Dolby Atmos"  "Infinity Vision Dolby Atmos"
    "4K Rgb Laser Large Screen Dolby 7.1"  "Lalitha Air Conditioned"  "Screen 3"

The filter used to offer each whole string as an option and match it exactly,
so choosing "Dolby Atmos" missed every show whose string also named its
projector — most Atmos shows in the city. Here each string is read into the
separate **features** it names (Dolby Atmos, 4K Laser, Barco…), and a show is
found by any feature it has.

Rules
-----
* A feature is claimed only when the show's own text names it, as a whole
  word (``\\b``), case-insensitively. Nothing is inferred from a theatre's
  name, its chain, or another show: Dolby Cinema does not imply Dolby Atmos,
  a laser does not imply 4K, "7.1" without "Dolby" is not Dolby 7.1.
* "Atmos" alone is read as Dolby Atmos: Atmos is Dolby's trademark and the
  name of no other system, so the string has no other meaning.
* Distinct technologies stay distinct: 2K, 4K, HDR, PXL, IMAX, Dolby Cinema
  and Dolby Atmos are separate features, never folded into each other.
* A string that names no known feature is kept whole, as its own option
  under "More formats" ("Screen 3", "Playhouse"): nothing BookMyShow wrote is
  silently dropped from the filter.
* A show with no format text at all has no features. It matches only when no
  format is chosen ("All formats").

Matching is OR: a show is kept when it has *any* chosen feature — so a show
with Dolby Atmos and 4K Laser appears under either. Choosing every offered
feature is the same as choosing none ("Select all" == "All formats"), so a
show with no format text never disappears because everything was ticked.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

STANDARD = "Standard"
SCREEN = "Premium screen & projection"
AUDIO = "Premium audio"
MORE = "More formats"
#: The filter's groups, in the order the picker shows them.
GROUPS: tuple[str, ...] = (STANDARD, SCREEN, AUDIO, MORE)
#: Short, stable keys for widget names.
GROUP_KEYS = {STANDARD: "std", SCREEN: "screen", AUDIO: "audio", MORE: "more"}


def _w(pattern: str) -> re.Pattern[str]:
    """A whole-word, case-insensitive pattern. ``\\b`` would not end after
    "7.1", so word edges are spelled out."""
    return re.compile(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", re.I)


_DOLBY = _w(r"dolby")
_ATMOS = _w(r"atmos")
_SEVEN_ONE = _w(r"7\.1")
_LASER = _w(r"laser")
_4K = _w(r"4\s?k")
_RGB = _w(r"rgb")

#: (feature, group, pattern), in the order options are listed within a group.
#: Features that need more than one word (4K Laser, Dolby 7.1) are below.
RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("2D", STANDARD, _w(r"2\s?d")),
    ("3D", STANDARD, _w(r"3\s?d")),
    ("2K", STANDARD, _w(r"2\s?k")),
    ("IMAX", SCREEN, _w(r"imax")),
    ("Dolby Cinema", SCREEN, _w(r"dolby\s+cinema")),
    ("4K", SCREEN, _4K),
    ("Laser projection", SCREEN, _LASER),
    ("RGB Laser", SCREEN, _w(r"rgb\s+laser")),
    ("HDR", SCREEN, _w(r"hdr")),
    ("PXL", SCREEN, _w(r"p\s?\[?xl\]?")),
    ("4DX", SCREEN, _w(r"4\s?dx")),
    ("MX4D", SCREEN, _w(r"mx4d")),
    ("Infinity Vision", SCREEN, _w(r"infinity\s*(?:vision|vsn)|inf\s+vsn")),
    ("EPIQ", SCREEN, _w(r"epiq")),
    ("PCX", SCREEN, _w(r"pcx")),
    ("Macro XE", SCREEN, _w(r"macro\s*xe")),
    ("ICE", SCREEN, _w(r"ice")),
    ("LED screen", SCREEN, _w(r"led(?:\s+screen)?")),
    ("70MM", SCREEN, _w(r"70\s?mm")),
    ("Barco projection", SCREEN, _w(r"barco")),
    ("Christie projection", SCREEN, _w(r"christie")),
    ("Dolby Atmos", AUDIO, _ATMOS),
    ("LUXE", MORE, _w(r"luxe")),
    ("Recliner", MORE, _w(r"recliners?")),
    ("VIP screen", MORE, _w(r"vip(?:\s+screen)?")),
    ("Large screen", MORE, _w(r"large\s+screen")),
    ("Air cooled", MORE, _w(r"air\s*cooled")),
    ("Air conditioned", MORE, _w(r"air\s*conditioned")),
    ("Playhouse", MORE, _w(r"playhouse")),
)

#: Composite features, read from one string at a time.
FOUR_K_LASER = "4K Laser"
DOLBY_71 = "Dolby 7.1"
SURROUND_71 = "7.1 surround"
DOLBY_SOUND = "Dolby sound"

#: Every feature in the order the picker lists it within its group.
_ORDER: tuple[str, ...] = (
    "2D", "3D", "2K",
    "IMAX", "Dolby Cinema", "4K", FOUR_K_LASER, "Laser projection", "RGB Laser", "HDR", "PXL", "4DX", "MX4D",
    "Infinity Vision", "EPIQ", "PCX", "Macro XE", "ICE", "LED screen", "70MM", "Barco projection",
    "Christie projection",
    "Dolby Atmos", DOLBY_71, SURROUND_71, DOLBY_SOUND,
    "LUXE", "Recliner", "VIP screen", "Large screen", "Air cooled", "Air conditioned", "Playhouse",
)
GROUP_OF: dict[str, str] = {f: g for f, g, _ in RULES} | {FOUR_K_LASER: SCREEN, DOLBY_71: AUDIO,
                                                           SURROUND_71: AUDIO, DOLBY_SOUND: AUDIO}
RANK: dict[str, int] = {f: i for i, f in enumerate(_ORDER)}
assert set(_ORDER) == set(GROUP_OF), "every feature needs a place in the picker"

#: Short explanations for the picker's tooltips — what the label means, never
#: a claim about any theatre.
HELP = {
    "2D": "Standard flat picture.",
    "3D": "Shown in 3D — glasses needed.",
    "2K": "2K digital projection.",
    "4K": "4K digital projection, as the listing names it.",
    FOUR_K_LASER: "4K projection with a laser light source — both named in the same listing.",
    "Laser projection": "A laser projector, as the listing names it.",
    "RGB Laser": "RGB laser projection.",
    "HDR": "High-dynamic-range projection (BookMyShow lists it as \"HDR by Barco\").",
    "Dolby Cinema": "Dolby's premium auditorium format. Its listing does not say Atmos by itself.",
    "Dolby Atmos": "Dolby's object-based surround sound (listed as \"Atmos\" or \"Dolby Atmos\").",
    DOLBY_71: "Dolby 7.1 surround sound.",
    SURROUND_71: "7.1 surround sound; the listing does not name Dolby.",
    DOLBY_SOUND: "Dolby sound; the listing names no specific Dolby system.",
}


@lru_cache(maxsize=4096)
def features_of_text(text: str) -> tuple[str, ...]:
    """The features one BookMyShow format string names, in display order.
    A string that names none is returned as itself ("Screen 3"), trimmed."""
    raw = " ".join((text or "").split())
    if not raw:
        return ()
    found = {f for f, _g, pattern in RULES if pattern.search(raw)}
    if "4K" in found and "Laser projection" in found:
        found.add(FOUR_K_LASER)
    if "RGB Laser" in found:
        found.add("Laser projection")
    dolby = _DOLBY.search(raw) is not None
    if _SEVEN_ONE.search(raw):
        found.add(DOLBY_71 if dolby else SURROUND_71)
    # "Dolby" naming no system the rules know ("2K Dolby") is Dolby sound —
    # unless the Dolby word is Dolby Cinema's or Atmos's own.
    if dolby and not (found & {"Dolby Atmos", DOLBY_71, "Dolby Cinema"}):
        found.add(DOLBY_SOUND)
    if not found:
        return (raw,)
    return tuple(sorted(found, key=lambda f: RANK.get(f, 10_000)))


@lru_cache(maxsize=4096)
def features_of(formats: tuple[str, ...]) -> frozenset[str]:
    """Every feature a show's format strings name (``ExplorerShow.formats``)."""
    return frozenset(f for text in formats for f in features_of_text(text))


def group_of(feature: str) -> str:
    """The group a feature is listed under; an unknown string is "More formats"."""
    return GROUP_OF.get(feature, MORE)


def sort_key(feature: str) -> tuple[int, str]:
    return (RANK.get(feature, 10_000), feature.casefold())


def grouped(features: Iterable[str]) -> list[tuple[str, list[str]]]:
    """``[(group, [feature, …]), …]`` — only groups that have something, in
    :data:`GROUPS` order, each in a fixed order (unknown strings last, A–Z)."""
    buckets: dict[str, list[str]] = {g: [] for g in GROUPS}
    for f in set(features):
        buckets[group_of(f)].append(f)
    return [(g, sorted(items, key=sort_key)) for g, items in buckets.items() if items]


def effective(selected: Iterable[str], available: Iterable[str]) -> tuple[str, ...]:
    """The features to filter by. Nothing chosen, or every offered feature
    chosen, means no format filter at all — "Select all" and "All formats"
    show the same shows, including shows that list no format."""
    chosen = tuple(dict.fromkeys(selected))
    offered = set(available)
    if not chosen or (offered and offered <= set(chosen)):
        return ()
    return chosen


def matches(formats: tuple[str, ...], wanted: Iterable[str]) -> bool:
    """OR matching: a show is kept when it has any wanted feature."""
    wanted = tuple(wanted)
    if not wanted:
        return True
    have = features_of(formats)
    return any(w in have for w in wanted)


__all__ = [
    "AUDIO", "DOLBY_71", "DOLBY_SOUND", "FOUR_K_LASER", "GROUPS", "GROUP_KEYS", "HELP", "MORE", "SCREEN",
    "STANDARD", "SURROUND_71", "effective", "features_of", "features_of_text", "group_of", "grouped",
    "matches", "sort_key",
]
