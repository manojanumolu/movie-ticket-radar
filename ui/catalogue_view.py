"""The catalogue as the UI reads it — parsed once, served from cache.

``monitor.catalogue`` reads and parses ``data/catalogue.json`` on every call,
and a single Streamlit rerun used to ask it a dozen times: the theatre count
on the platform card, the movie grid, the search, every summary row, the
step being drawn. At 140 KB that is most of what made Home feel slow.

The catalogue only changes when the sync job commits a new file, so the
parsed view is cached on the file's *content* (path + digest) and invalidates
itself the moment the file does. Hashing 140 KB is well under a millisecond;
parsing it a dozen times was not.

This is a read-only view. Nothing here writes, and nothing here talks to
BookMyShow — ``monitor.catalogue`` stays the single source for the sync job
and the worker, and everything below is derived from what it stored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import streamlit as st

from config import store
from monitor import catalogue
from monitor.models import MovieRef, Venue


@dataclass(frozen=True)
class MovieCard:
    """One selectable movie, with the numbers the picker shows next to it."""

    id: str
    title: str
    language: str
    poster_url: str
    venue_count: int
    showtime_count: int
    rank: int                 # position in BookMyShow's own listing order

    @property
    def label(self) -> str:
        """What the search box shows: title, then language, never a code."""
        return f"{self.title} · {self.language}" if self.language else self.title

    @property
    def meta(self) -> str:
        if not self.venue_count:
            return f"{self.language} · no theatres yet" if self.language else "no theatres yet"
        theatres = f"{self.venue_count} theatre{'s' if self.venue_count != 1 else ''}"
        return f"{self.language} · {theatres}" if self.language else theatres


@dataclass(frozen=True)
class CatalogueView:
    slug: str
    entries: list[dict[str, Any]]
    movies: list[MovieCard]
    by_id: dict[str, dict[str, Any]]
    venues_by_id: dict[str, list[Venue]]
    theatre_count: int
    sync: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)   # search label -> movie id


# ──────────────────────────────────────────────────────────────────────────
# Cache keyed on the file's content
# ──────────────────────────────────────────────────────────────────────────
def _signature() -> tuple[str, str]:
    path = store.CATALOGUE_FILE
    try:
        digest = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
    except OSError:
        digest = ""
    return str(path), digest


@st.cache_resource(show_spinner=False)
def _build(signature: tuple[str, str], slug: str) -> CatalogueView:
    """Parse once per (file content, city). ``signature`` is only the cache key."""
    entries = catalogue.list_entries(slug)
    by_id: dict[str, dict[str, Any]] = {}
    venues_by_id: dict[str, list[Venue]] = {}
    movies: list[MovieCard] = []
    labels: dict[str, str] = {}
    for rank, entry in enumerate(entries):
        movie = catalogue.movie_from_entry(entry)
        venues = catalogue.venues_from_entry(entry)
        by_id[movie.id] = entry
        venues_by_id[movie.id] = venues
        card = MovieCard(
            id=movie.id, title=movie.title, language=movie.language,
            poster_url=movie.poster_url, venue_count=len(venues),
            showtime_count=int(entry.get("showtime_count", 0) or 0), rank=rank,
        )
        movies.append(card)
        labels.setdefault(card.label, card.id)
    theatre_count = len({v.code for venues in venues_by_id.values() for v in venues})
    return CatalogueView(
        slug=slug, entries=entries, movies=movies, by_id=by_id, venues_by_id=venues_by_id,
        theatre_count=theatre_count, sync=catalogue.sync_state(slug), labels=labels,
    )


def view(slug: str) -> CatalogueView:
    return _build(_signature(), slug)


# ──────────────────────────────────────────────────────────────────────────
# Lookups
# ──────────────────────────────────────────────────────────────────────────
def entry(movie_id: str, slug: str = "") -> dict[str, Any] | None:
    """The catalogue row for a movie, whatever city it belongs to."""
    if not movie_id:
        return None
    if slug:
        found = view(slug).by_id.get(movie_id)
        if found is not None:
            return found
    return _build(_signature(), "").by_id.get(movie_id)


def movie(movie_id: str, slug: str = "") -> MovieRef | None:
    found = entry(movie_id, slug)
    return catalogue.movie_from_entry(found) if found else None


def venues(movie_id: str, slug: str = "") -> list[Venue]:
    """Every theatre the catalogue says this movie plays at — no more, no less."""
    if not movie_id:
        return []
    if slug and movie_id in view(slug).venues_by_id:
        return view(slug).venues_by_id[movie_id]
    return _build(_signature(), "").venues_by_id.get(movie_id, [])


def card(movie_id: str, slug: str) -> MovieCard | None:
    return next((m for m in view(slug).movies if m.id == movie_id), None)


# ──────────────────────────────────────────────────────────────────────────
# Movies: search and the popular shelf
# ──────────────────────────────────────────────────────────────────────────
def search_movies(query: str, slug: str) -> list[MovieCard]:
    """Case-insensitive substring match on title and language, in catalogue order."""
    q = (query or "").strip().lower()
    cards = view(slug).movies
    if not q:
        return list(cards)
    return [m for m in cards if q in f"{m.title} {m.language}".lower()]


def popular(slug: str, limit: int = 6) -> list[MovieCard]:
    """Up to ``limit`` films for the "Now showing" shelf.

    The signal is BookMyShow's own listing order — the order its city page
    ranks films in, which the sync preserves — restricted to films that
    actually have theatres. One tile per film: a title listed in several
    languages shows once, as the language with the most theatres, and the
    other languages stay a search away. Nothing is invented and nothing is
    alphabetical.
    """
    best: dict[str, MovieCard] = {}
    order: list[str] = []
    for m in view(slug).movies:
        if not m.venue_count:
            continue
        key = m.title.strip().lower()
        if key not in best:
            best[key] = m
            order.append(key)
        elif m.venue_count > best[key].venue_count:
            best[key] = m
    return [best[k] for k in order[:limit]]


def movie_for_label(label: str, slug: str) -> str:
    """The movie a search-box choice names, or '' for free text."""
    if not label:
        return ""
    labels = view(slug).labels
    if label in labels:
        return labels[label]
    wanted = label.strip().lower()
    return next((mid for text, mid in labels.items() if text.lower() == wanted), "")


# ──────────────────────────────────────────────────────────────────────────
# Theatres: search and the featured shelf
# ──────────────────────────────────────────────────────────────────────────
def search_venues(query: str, candidates: list[Venue]) -> list[Venue]:
    q = (query or "").strip().lower()
    if not q:
        return list(candidates)
    return [v for v in candidates if q in f"{v.name} {v.area}".lower()]


@dataclass(frozen=True)
class Featured:
    """A premium quick-pick. Matched against the *movie's* theatres by name —
    it is a shortcut to a row that already exists, never a row of its own."""

    name: str
    area: str
    needles: tuple[str, ...]      # any of these in the venue name (lower-case)
    area_needle: str = ""         # and, when set, this in the venue's area


FEATURED: tuple[Featured, ...] = (
    Featured("Allu Cinemas", "Kokapet", ("allu cinemas",)),
    Featured("Prasads Multiplex (PCX)", "Khairtabad", ("prasads",)),
    Featured("AMB Cinemas", "Gachibowli", ("amb cinemas",)),
    Featured("AAA Cinemas", "Ameerpet", ("aaa cinemas",), "ameerpet"),
    Featured("PVR Lakeshore Mall", "Y Junction", ("lakeshore",)),
    Featured("PVR Superplex Inorbit", "Inorbit Mall", ("superplex inorbit",)),
)


def featured(candidates: list[Venue]) -> list[tuple[Featured, Venue | None]]:
    """Each featured theatre paired with the movie's matching venue, or None.

    None means "this movie is not listed there" and is rendered as exactly
    that. Availability is never assumed from the theatre being featured.
    """
    out: list[tuple[Featured, Venue | None]] = []
    for pick in FEATURED:
        match = None
        for venue in candidates:
            name = venue.name.lower()
            area = venue.area.lower()
            if any(n in name for n in pick.needles) and (
                not pick.area_needle or pick.area_needle in area or pick.area_needle in name
            ):
                match = venue
                break
        out.append((pick, match))
    return out


__all__ = [
    "FEATURED",
    "CatalogueView",
    "Featured",
    "MovieCard",
    "card",
    "entry",
    "featured",
    "movie",
    "movie_for_label",
    "popular",
    "search_movies",
    "search_venues",
    "venues",
    "view",
]
