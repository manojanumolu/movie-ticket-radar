"""The movie catalogue — resolved movies, their theatres and their formats.

Why this exists
---------------
Theatres and formats are *discovered*, never hard-coded: the list of formats
Allu Cinemas offers is whatever that movie is actually screening there. But
discovery needs a live call to the platform, and the two places this app runs
are exactly the two places that call is least reliable — Streamlit Cloud and a
home connection both get bot-checked by BookMyShow's WAF often enough to
matter.

So resolution is cached into ``data/catalogue.json``. The UI reads the cache
instantly and offers a refresh; if refreshing from the browser is blocked, the
same resolution can be run from GitHub Actions (``resolve-movie.yml``), whose
runners are not blocked, and the result lands back in the repo.

The cache only ever holds data a provider genuinely returned. Nothing here
invents a movie, a theatre or a format.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from config.store import load_catalogue, save_catalogue
from config.timezone import now_ist, to_iso
from monitor.models import MovieRef, Snapshot, Venue, dedupe
from platforms import get_provider


def entry_from_snapshot(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "movie": asdict(snapshot.movie),
        "venues": [
            {"code": v.code, "name": v.name, "area": v.area, "formats": list(v.formats)}
            for v in snapshot.venues
        ],
        "bookable_dates": list(snapshot.bookable_dates),
        "closed_dates": list(snapshot.closed_dates),
        "showtime_count": len(snapshot.showtimes),
        "resolved_at": to_iso(snapshot.fetched_at or now_ist()),
    }


def movie_from_entry(entry: dict[str, Any]) -> MovieRef:
    raw = dict(entry.get("movie", {}))
    known = MovieRef.__dataclass_fields__.keys()
    return MovieRef(**{k: v for k, v in raw.items() if k in known})


def venues_from_entry(entry: dict[str, Any]) -> list[Venue]:
    return [
        Venue(
            code=v.get("code", ""),
            name=v.get("name", v.get("code", "")),
            area=v.get("area", ""),
            formats=tuple(dedupe(v.get("formats", []))),
        )
        for v in entry.get("venues", [])
        if isinstance(v, dict) and (v.get("code") or v.get("name"))
    ]


def list_entries() -> list[dict[str, Any]]:
    entries = load_catalogue().get("movies", [])
    return [e for e in entries if isinstance(e, dict) and e.get("movie")]


def find_entry(movie_id: str) -> dict[str, Any] | None:
    for entry in list_entries():
        if movie_from_entry(entry).id == movie_id:
            return entry
    return None


def search_entries(query: str) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return list_entries()
    out = []
    for entry in list_entries():
        movie = movie_from_entry(entry)
        haystack = f"{movie.title} {movie.language} {movie.event_code}".lower()
        if q in haystack:
            out.append(entry)
    return out


def store_snapshot(snapshot: Snapshot, *, mirror: bool = True) -> dict[str, Any]:
    """Upsert a resolved movie, newest first."""
    entry = entry_from_snapshot(snapshot)
    catalogue = load_catalogue()
    movies = [
        e for e in catalogue.get("movies", [])
        if isinstance(e, dict) and movie_from_entry(e).id != snapshot.movie.id
    ]
    movies.insert(0, entry)
    catalogue["movies"] = movies[:40]
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror)
    return entry


def remove_entry(movie_id: str, *, mirror: bool = True) -> None:
    catalogue = load_catalogue()
    catalogue["movies"] = [
        e for e in catalogue.get("movies", [])
        if isinstance(e, dict) and movie_from_entry(e).id != movie_id
    ]
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror)


def resolve_url(url: str, platform: str = "bookmyshow", *, mirror: bool = True) -> dict[str, Any]:
    """Read a listing URL live and cache it.

    Raises ``PlatformError`` on failure — the caller decides how to say
    "we couldn't look", and nothing is written to the cache on a failure.
    """
    snapshot = get_provider(platform).resolve(url)
    return store_snapshot(snapshot, mirror=mirror)


def refresh_entry(movie_id: str, *, mirror: bool = True) -> dict[str, Any]:
    entry = find_entry(movie_id)
    if entry is None:
        raise KeyError(f"{movie_id} is not in the catalogue.")
    movie = movie_from_entry(entry)
    snapshot = get_provider(movie.platform).fetch(movie)
    return store_snapshot(snapshot, mirror=mirror)


__all__ = [
    "entry_from_snapshot",
    "find_entry",
    "list_entries",
    "movie_from_entry",
    "refresh_entry",
    "remove_entry",
    "resolve_url",
    "search_entries",
    "store_snapshot",
    "venues_from_entry",
]
