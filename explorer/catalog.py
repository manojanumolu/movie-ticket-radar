"""What the catalogue tells the Explorer before anything is read live.

The catalogue (``data/catalogue.json``, written by the scheduled sync) lists
each film once per language, with the theatres it plays at and — for its
first few dates on sale — which theatres list it on which date. The Explorer
uses that for three things, none of which reads BookMyShow:

* the movie picker, by title — "Ramayana" once, with its languages, so the
  Language filter chooses which language rows are read;
* the theatre picker — every theatre the catalogue has seen in the city;
* for By theatre, which films to read: the ones the catalogue lists at that
  theatre on that date, or — for a date past the catalogue's per-date
  listings — the films whose theatre list includes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from explorer.classify import TheatreFacts, classify
from explorer.source import Wanted
from monitor.catalogue import listings_from_entry, movie_from_entry, venues_from_entry


@dataclass(frozen=True)
class Title:
    key: str                         # casefolded title
    title: str
    languages: tuple[str, ...]
    rows: tuple[Wanted, ...]         # one per language, catalogue order
    theatre_count: int
    rank: int

    @property
    def label(self) -> str:
        parts = [self.title]
        if self.languages:
            parts.append(", ".join(self.languages))
        parts.append(f"{self.theatre_count} theatre{'s' if self.theatre_count != 1 else ''}"
                     if self.theatre_count else "no theatres yet")
        return " · ".join(parts)


@dataclass(frozen=True)
class Theatre:
    code: str
    name: str
    area: str
    facts: TheatreFacts

    @property
    def label(self) -> str:
        return f"{self.name} · {self.area}" if self.area else self.name


def titles(entries: list[dict[str, Any]]) -> list[Title]:
    groups: dict[str, dict[str, Any]] = {}
    for rank, entry in enumerate(entries):
        movie = movie_from_entry(entry)
        if movie.platform != "bookmyshow" or not movie.title:
            continue
        key = " ".join(movie.title.casefold().split())
        g = groups.setdefault(key, {"title": movie.title, "rows": [], "venues": set(), "rank": rank})
        g["rows"].append(Wanted(movie=movie, movie_id=movie.id, title=g["title"], poster=movie.poster_url))
        g["venues"].update(v.code for v in venues_from_entry(entry))
    out = []
    for key, g in groups.items():
        langs = tuple(dict.fromkeys(w.movie.language for w in g["rows"] if w.movie.language))
        out.append(Title(key=key, title=g["title"], languages=langs, rows=tuple(g["rows"]),
                         theatre_count=len(g["venues"]), rank=g["rank"]))
    return sorted(out, key=lambda t: t.rank)


def theatres(entries: list[dict[str, Any]]) -> list[Theatre]:
    seen: dict[str, Theatre] = {}
    for entry in entries:
        for v in venues_from_entry(entry):
            if v.code and v.code not in seen:
                seen[v.code] = Theatre(code=v.code, name=v.name, area=v.area, facts=classify(v.name, v.area))
    return sorted(seen.values(), key=lambda t: (t.name.casefold(), t.area.casefold()))


def films_at(entries: list[dict[str, Any]], venue_code: str, date_code: str) -> list[Wanted]:
    """The catalogue rows worth reading for one theatre on one date."""
    out = []
    for entry in entries:
        movie = movie_from_entry(entry)
        if movie.platform != "bookmyshow":
            continue
        listings = listings_from_entry(entry)
        if date_code in listings:
            listed = venue_code in listings[date_code]
        else:
            listed = any(v.code == venue_code for v in venues_from_entry(entry))
        if listed:
            out.append(Wanted(movie=movie, movie_id=movie.id, title=movie.title, poster=movie.poster_url))
    return out


__all__ = ["Theatre", "Title", "films_at", "theatres", "titles"]
