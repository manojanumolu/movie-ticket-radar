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
from monitor.models import MovieRef, Venue, dedupe, normalise_format


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
        """What the search box shows: title · language · theatre count. Never a code."""
        parts = [self.title]
        if self.language:
            parts.append(self.language)
        parts.append(f"{self.venue_count} theatre{'s' if self.venue_count != 1 else ''}"
                     if self.venue_count else "no theatres yet")
        return " · ".join(parts)

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
    #: Every theatre the catalogue has seen in this city, by BookMyShow venue
    #: code, with every format ever seen there. This is what lets a theatre be
    #: watched *before* it lists a film: the code the worker will match on,
    #: and the formats it is known to run, both come from real listings.
    directory: dict[str, Venue] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────
# Cache keyed on the file's content
# ──────────────────────────────────────────────────────────────────────────
#: The last digest computed, against the file's stat that produced it.
#: ``view()`` is called a dozen times per rerun (every lookup goes through
#: it) and each call was reading and hashing the whole catalogue — 140 KB,
#: twelve times, for a key that had not changed. The file is only ever
#: rewritten when its content differs (``store.sync_from_github``,
#: ``catalogue.sync_region``), so an unchanged ``(mtime_ns, size)`` means an
#: unchanged digest; the content is hashed again only when the stat moves.
_last_stat: tuple[str, int, int] | None = None
_last_digest: str = ""


def _signature() -> tuple[str, str]:
    global _last_stat, _last_digest
    path = store.CATALOGUE_FILE
    try:
        st_ = path.stat()
        stat = (str(path), st_.st_mtime_ns, st_.st_size)
    except OSError:
        _last_stat, _last_digest = None, ""
        return str(path), ""
    if stat != _last_stat:
        try:
            _last_digest = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
        except OSError:
            _last_digest = ""
        _last_stat = stat
    return str(path), _last_digest


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
    directory: dict[str, Venue] = {}
    for venues in venues_by_id.values():
        for v in venues:
            known = directory.get(v.code)
            if known is None:
                directory[v.code] = v
            else:
                directory[v.code] = Venue(
                    code=v.code, name=known.name or v.name, area=known.area or v.area,
                    formats=tuple(dict.fromkeys((*known.formats, *v.formats))),
                    categories=tuple({c.key: c for c in (*known.categories, *v.categories)}.values()),
                )
    return CatalogueView(
        slug=slug, entries=entries, movies=movies, by_id=by_id, venues_by_id=venues_by_id,
        theatre_count=len(directory), sync=catalogue.sync_state(slug), labels=labels,
        directory=directory,
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


def merge_formats(listed: tuple[str, ...] | list[str], known: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Formats listed for this movie first, then every other format the
    theatre is known to run — one entry per format, first spelling kept.

    Equality is on :func:`normalise_format`, the key the checker matches
    showtimes with, so "Dolby Cinema" and "DOLBY CINEMA" are one option and
    "Laser" and "Laser 3D" stay two.
    """
    out: dict[str, str] = {}
    for fmt in (*listed, *known):
        fmt = (fmt or "").strip()
        if fmt:
            out.setdefault(normalise_format(fmt), fmt)
    return tuple(out.values())


def listings(movie_id: str, slug: str = "") -> dict[str, dict[str, tuple[str, ...]]]:
    """``{date_code: {venue_code: formats}}`` — what BookMyShow lists for this
    movie, per date, per theatre, as the catalogue read it. A row detailed
    before listings were kept has none; its venues' formats then stand in,
    undated (key ``""``)."""
    found = entry(movie_id, slug)
    if not found:
        return {}
    dated = catalogue.listings_from_entry(found)
    if dated:
        return dated
    undated = {v.code: v.formats for v in catalogue.venues_from_entry(found)}
    return {"": undated} if undated else {}


def listed_formats(movie_id: str, slug: str, codes: list[str],
                   dates: list[str] | None = None) -> dict[str, tuple[str, ...]]:
    """code -> the formats this movie *actually lists* at that theatre — on
    the given show dates, or across every date the catalogue read when no
    date is chosen. Never a theatre's general capability."""
    by_date = listings(movie_id, slug)
    wanted = [d for d in (dates or []) if d]
    picked = [by_date[d] for d in wanted if d in by_date] if wanted else list(by_date.values())
    if wanted and not picked and "" in by_date:
        picked = [by_date[""]]                       # an old row: undated formats are all it knows
    out: dict[str, tuple[str, ...]] = {}
    for code in codes:
        out[code] = tuple(sorted(dedupe(f for day in picked for f in day.get(code, ()))))
    return out


def listed_dates(movie_id: str, slug: str, code: str) -> dict[str, tuple[str, ...]]:
    """date_code -> formats this movie lists at one theatre on that date."""
    return {d: day[code] for d, day in listings(movie_id, slug).items() if d and code in day}


def capabilities(slug: str, codes: list[str]) -> dict[str, tuple[str, ...]]:
    """code -> every format the city directory has seen the theatre run, for
    any film. Shown as what the theatre *can* do; never offered as a format
    this movie is in."""
    directory = view(slug).directory
    return {code: (tuple(sorted(directory[code].formats)) if code in directory else ()) for code in codes}


def selected_venues(movie_id: str, slug: str, codes: list[str],
                    dates: list[str] | None = None) -> list[Venue]:
    """The Venue for each chosen code, its ``formats`` being exactly what
    this movie lists there — on the chosen dates when given, else on any
    date the catalogue read — and its categories the union of what the
    movie's own listing and the city directory know. Name and area are the
    movie's own when it lists the theatre, the directory's otherwise."""
    own_by_code = {v.code: v for v in venues(movie_id, slug)}
    directory = view(slug).directory
    listed = listed_formats(movie_id, slug, codes, dates)
    out: list[Venue] = []
    for code in codes:
        own, known = own_by_code.get(code), directory.get(code)
        venue = own or known
        if venue is None:
            continue
        categories = tuple({c.key: c for c in (*(own.categories if own else ()),
                                               *(known.categories if known else ()))}.values())
        out.append(Venue(code=venue.code, name=venue.name, area=venue.area,
                         formats=listed.get(code, ()), categories=categories))
    return out


def coming_soon_codes(movie_id: str, slug: str, codes: list[str],
                      dates: list[str] | None = None) -> set[str]:
    """Which of the chosen codes this movie does *not* list — on the chosen
    dates when given, else on any date the catalogue read."""
    by_date = listings(movie_id, slug)
    wanted = [d for d in (dates or []) if d]
    days = [by_date[d] for d in wanted if d in by_date] if wanted else list(by_date.values())
    if wanted and not days and "" in by_date:
        days = [by_date[""]]
    listed = {code for day in days for code in day}
    return {c for c in codes if c not in listed}


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


@dataclass(frozen=True)
class FeaturedMatch:
    pick: Featured
    venue: Venue | None       # the theatre as the catalogue knows it, or None
    released: bool            # True when this movie is listed there right now

    @property
    def selectable(self) -> bool:
        return self.venue is not None


def _match(pick: Featured, candidates) -> Venue | None:
    for venue in candidates:
        name = venue.name.lower()
        area = venue.area.lower()
        if any(n in name for n in pick.needles) and (
            not pick.area_needle or pick.area_needle in area or pick.area_needle in name
        ):
            return venue
    return None


def featured(candidates: list[Venue], directory: dict[str, Venue] | None = None) -> list[FeaturedMatch]:
    """Each featured theatre in one of three states.

    *Released*: the movie is listed there now — the tile shows its formats.
    *Coming soon*: the catalogue knows the theatre (from other films) but not
    for this movie yet — still selectable, so it can be watched until
    BookMyShow releases tickets; the formats shown are the ones the theatre
    is known to run. *Unknown*: the catalogue has never seen the theatre, so
    there is no venue code to watch — the tile says so and cannot be picked.
    Availability is never assumed from the theatre being featured.
    """
    out: list[FeaturedMatch] = []
    known = list((directory or {}).values())
    for pick in FEATURED:
        listed = _match(pick, candidates)
        if listed is not None:
            out.append(FeaturedMatch(pick, listed, True))
            continue
        out.append(FeaturedMatch(pick, _match(pick, known), False))
    return out


__all__ = [
    "FEATURED",
    "CatalogueView",
    "Featured",
    "FeaturedMatch",
    "MovieCard",
    "card",
    "capabilities",
    "coming_soon_codes",
    "listed_dates",
    "listed_formats",
    "listings",
    "merge_formats",
    "entry",
    "featured",
    "movie",
    "movie_for_label",
    "popular",
    "search_movies",
    "search_venues",
    "selected_venues",
    "venues",
    "view",
]
