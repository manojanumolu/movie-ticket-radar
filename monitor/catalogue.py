"""The movie catalogue — what's showing in a city, and where.

Why this exists
---------------
The user picks a city and then a movie. Neither of those questions can be
answered without reading BookMyShow, and BookMyShow is exactly the thing that
is unreliable from a browser-facing host: its bot check refuses some networks
outright (Streamlit Cloud's among them, most likely).

So retrieval is separated from presentation. A scheduled GitHub Action syncs
the city's catalogue into ``data/catalogue.json`` and commits it; the UI reads
that file and is therefore instant and always available, even when the app
itself could never have reached BookMyShow. The UI may also refresh live when
its own network happens to work — but it never *depends* on that.

Nothing here invents data. A movie, theatre or format is in the catalogue only
because a provider returned it, and a failed sync leaves the previous
catalogue untouched rather than replacing it with an empty one.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

from config.store import load_catalogue, save_catalogue
from config.timezone import now_ist, parse_iso, to_iso
from monitor.models import Availability, MovieRef, SeatCategory, Snapshot, Venue, dedupe
from platforms import get_provider, is_enabled
from platforms.base import PlatformBlocked, PlatformError

#: How many movies a single sync will resolve theatre/format detail for.
#: Each one is its own request, so this is the difference between a polite
#: two-minute job and hammering BookMyShow a hundred times in a row.
DETAIL_LIMIT = 60

#: Politeness gap between per-movie detail requests, in seconds.
DETAIL_DELAY = 1.2

#: How long a movie's resolved theatre/format detail is trusted before a sync
#: re-reads it. Theatres are added as a release approaches and drop off as
#: the day's shows run out, so a list read once and kept forever is wrong in
#: both directions. Six hours, with four syncs a day, keeps it current.
DETAIL_TTL = timedelta(hours=6)

#: How many of a film's bookable dates the detail read covers, starting with
#: the platform's default date. Formats are per show, and a theatre that
#: lists a film on Friday in Infinity Vision may not list it on Thursday at
#: all — so the catalogue records what is listed *per date*, for the first
#: few dates on sale, and the wizard answers "this movie, this theatre,
#: this date" from that rather than from what the theatre runs in general.
DATE_SWEEP = 3


class SyncStatus(str, Enum):
    """Why the catalogue looks the way it does.

    The UI must be able to tell "this city genuinely has nothing listed" from
    "we could not ask" — showing an empty movie grid for a Cloudflare block is
    exactly the class of lie this whole application is built to avoid.
    """

    OK = "OK"                # synced, movies found
    EMPTY = "EMPTY"          # synced, the city really has nothing listed
    BLOCKED = "BLOCKED"      # bot check / WAF refused us
    ERROR = "ERROR"          # network or parse failure
    NEVER = "NEVER"          # no sync has ever run

    @property
    def is_failure(self) -> bool:
        return self in (SyncStatus.BLOCKED, SyncStatus.ERROR)


# ──────────────────────────────────────────────────────────────────────────
# Entry shape
# ──────────────────────────────────────────────────────────────────────────
def listings_from_showtimes(showtimes) -> dict[str, dict[str, list[str]]]:
    """``{date_code: {venue_code: [formats]}}`` — what the platform lists,
    per date, per theatre, from the shows themselves. A show without a
    format label still lists the theatre for that date (an empty list)."""
    out: dict[str, dict[str, list[str]]] = {}
    for show in showtimes:
        if not show.date_code:
            continue
        formats = out.setdefault(show.date_code, {}).setdefault(show.venue_code, [])
        formats.extend(f for f in show.format_labels if f not in formats)
    return {d: out[d] for d in sorted(out)}


def entry_from_snapshot(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "movie": asdict(snapshot.movie),
        "venues": [
            {"code": v.code, "name": v.name, "area": v.area, "formats": list(v.formats),
             "categories": [c.to_dict() for c in v.categories]}
            for v in snapshot.venues
        ],
        "listings": listings_from_showtimes(snapshot.showtimes),
        "bookable_dates": list(snapshot.bookable_dates),
        "closed_dates": list(snapshot.closed_dates),
        "showtime_count": len(snapshot.showtimes),
        "resolved_at": to_iso(snapshot.fetched_at or now_ist()),
    }


def entry_from_movie(movie: MovieRef) -> dict[str, Any]:
    """A catalogue row for a movie we know exists but haven't detailed yet."""
    return {
        "movie": asdict(movie),
        "venues": [],
        "listings": {},
        "bookable_dates": [],
        "closed_dates": [],
        "showtime_count": 0,
        "resolved_at": None,
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
            categories=categories_from_entry(v.get("categories", []) or []),
        )
        for v in entry.get("venues", [])
        if isinstance(v, dict) and (v.get("code") or v.get("name"))
    ]


def categories_from_entry(raw: Any) -> tuple[SeatCategory, ...]:
    """A venue's stored categories: dicts (with identity) or, from a sync
    before keys, plain names — one per key, first seen first."""
    out: dict[str, SeatCategory] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            cat = SeatCategory.from_dict(item)
        elif isinstance(item, str) and item.strip():
            cat = SeatCategory(code="", name=item.strip(), availability=Availability.UNKNOWN)
        else:
            continue
        if cat.name:
            out.setdefault(cat.key, cat)
    return tuple(out.values())


def listings_from_entry(entry: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    """A row's per-date listings. A row detailed before listings were kept
    has none: callers fall back to the venues' formats, undated."""
    raw = entry.get("listings")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for date_code, venues in raw.items():
        if not isinstance(venues, dict) or not str(date_code).isdigit():
            continue
        out[str(date_code)] = {
            str(code): tuple(dedupe(f for f in fmts if isinstance(f, str)))
            for code, fmts in venues.items() if isinstance(fmts, list)
        }
    return out


def read_detail(provider, movie: MovieRef) -> Snapshot:
    """The platform's default date, then the next bookable dates it names,
    up to ``DATE_SWEEP`` in all — one snapshot, its shows dated. The extra
    dates are read only when the platform lists them, and only here, on the
    runner: the pages never fetch."""
    snapshot = provider.fetch(movie)
    seen = {s.date_code for s in snapshot.showtimes if s.date_code}
    extra = [d for d in snapshot.bookable_dates if d not in seen][:max(0, DATE_SWEEP - max(1, len(seen)))]
    if not extra:
        return snapshot
    try:
        more = provider.fetch(snapshot.movie, extra)
    except Exception as exc:  # noqa: BLE001 - the extra dates are a bonus; the default date's read stands
        print(f"[catalogue]   {snapshot.movie.title or snapshot.movie.event_code}: "
              f"further dates {extra} not read ({type(exc).__name__}); keeping the default date")
        return snapshot
    return merge_snapshots(snapshot, more)


def merge_snapshots(first: Snapshot, second: Snapshot) -> Snapshot:
    """One film's reads on different dates as one snapshot: theatres by
    code, their formats and categories unioned, every show kept."""
    venues: dict[str, Venue] = {v.code: v for v in first.venues}
    for v in second.venues:
        known = venues.get(v.code)
        if known is None:
            venues[v.code] = v
            continue
        venues[v.code] = Venue(
            code=known.code, name=known.name or v.name, area=known.area or v.area,
            formats=tuple(dedupe([*known.formats, *v.formats])),
            categories=tuple({c.key: c for c in (*known.categories, *v.categories)}.values()),
        )
    return Snapshot(
        movie=first.movie,
        venues=sorted(venues.values(), key=lambda v: v.name.lower()),
        showtimes=[*first.showtimes, *second.showtimes],
        bookable_dates=dedupe([*first.bookable_dates, *second.bookable_dates]),
        closed_dates=[d for d in dedupe([*first.closed_dates, *second.closed_dates])
                      if d not in set(first.bookable_dates) | set(second.bookable_dates)],
        fetched_at=first.fetched_at,
    )


def is_detailed(entry: dict[str, Any]) -> bool:
    """Has this movie's theatre/format detail been read yet?"""
    return entry.get("resolved_at") is not None


def is_stale(entry: dict[str, Any], *, at: datetime | None = None) -> bool:
    """Is the detail older than ``DETAIL_TTL``? (Never-resolved counts as stale.)"""
    resolved = parse_iso(entry.get("resolved_at"))
    if resolved is None:
        return True
    return (at or now_ist()) - resolved >= DETAIL_TTL


def siblings_changed(entry: dict[str, Any], listed: MovieRef) -> bool:
    """Does the listing now name premium-format events the detail never swept?

    A film's sibling events (3D, 4DX, IMAX…) each carry their own theatres,
    so detail resolved against a different set of siblings is a different —
    and shorter — theatre list. That detail is stale, not merely old.
    """
    return set(movie_from_entry(entry).variant_codes) != set(listed.variant_codes)


# ──────────────────────────────────────────────────────────────────────────
# Reading
# ──────────────────────────────────────────────────────────────────────────
def platform_of(movie_id: str) -> str:
    """The platform a movie id belongs to — ``"pvr_inox:35288-ENGLISH"`` is
    PVR INOX's; an id without a prefix is BookMyShow's, as it always was."""
    platform, sep, _ = (movie_id or "").partition(":")
    return platform if sep and platform else "bookmyshow"


def list_entries(region_slug: str = "", platform: str = "bookmyshow") -> list[dict[str, Any]]:
    entries = [
        e for e in load_catalogue(platform).get("movies", [])
        if isinstance(e, dict) and e.get("movie")
    ]
    if region_slug:
        entries = [e for e in entries if movie_from_entry(e).region_slug == region_slug]
    return entries


def find_entry(movie_id: str) -> dict[str, Any] | None:
    return next((e for e in list_entries(platform=platform_of(movie_id)) if movie_from_entry(e).id == movie_id),
                None)


def search_entries(query: str, region_slug: str = "", platform: str = "bookmyshow") -> list[dict[str, Any]]:
    entries = list_entries(region_slug, platform)
    q = (query or "").strip().lower()
    if not q:
        return entries
    out = []
    for entry in entries:
        movie = movie_from_entry(entry)
        if q in f"{movie.title} {movie.language} {movie.event_code}".lower():
            out.append(entry)
    return out


def sync_state(region_slug: str = "", platform: str = "bookmyshow") -> dict[str, Any]:
    """The last sync's outcome, for the UI to render honestly."""
    catalogue = load_catalogue(platform)
    states = catalogue.get("sync", {})
    raw = states.get(region_slug) if region_slug else None
    if not isinstance(raw, dict):
        raw = {}
    try:
        status = SyncStatus(raw.get("status", "NEVER"))
    except ValueError:
        status = SyncStatus.NEVER
    return {
        "status": status,
        "message": raw.get("message", ""),
        "at": parse_iso(raw.get("at")),
        "movie_count": int(raw.get("movie_count", 0) or 0),
        "strategy": raw.get("strategy", ""),
    }


def _record_sync(region_slug: str, status: SyncStatus, message: str = "",
                 movie_count: int = 0, *, mirror: bool, platform: str = "bookmyshow") -> None:
    catalogue = load_catalogue(platform)
    catalogue.setdefault("sync", {})[region_slug] = {
        "status": status.value,
        "message": message,
        "at": to_iso(now_ist()),
        "movie_count": movie_count,
    }
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror, platform=platform)


# ──────────────────────────────────────────────────────────────────────────
# Writing
# ──────────────────────────────────────────────────────────────────────────
def _upsert(entries: list[dict[str, Any]], *, region_slug: str, mirror: bool,
            platform: str = "bookmyshow") -> None:
    """Replace this region's rows, leaving other regions alone."""
    catalogue = load_catalogue(platform)
    keep = [
        e for e in catalogue.get("movies", [])
        if isinstance(e, dict) and e.get("movie")
        and movie_from_entry(e).region_slug != region_slug
    ]
    catalogue["movies"] = entries + keep
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror, platform=platform)


def store_snapshot(snapshot: Snapshot, *, mirror: bool = True) -> dict[str, Any]:
    """Upsert one fully-resolved movie, keeping what the city listing knew.

    The two sources describe the same film but not equally well. The city
    listing carries the poster, the language and the marketing title; the
    showtimes response carries the theatres and formats. Letting the second
    overwrite the first wholesale would blank every poster in the grid the
    moment detail was resolved — so listing-only fields are merged forward
    rather than replaced.
    """
    entry = entry_from_snapshot(snapshot)
    platform = snapshot.movie.platform or "bookmyshow"
    catalogue = load_catalogue(platform)

    movies = [e for e in catalogue.get("movies", []) if isinstance(e, dict) and e.get("movie")]
    position = next(
        (i for i, e in enumerate(movies) if movie_from_entry(e).id == snapshot.movie.id),
        None,
    )
    previous = movies[position] if position is not None else None
    if previous is not None:
        known = movie_from_entry(previous)
        merged = dict(entry["movie"])
        for field in ("title", "poster_url", "language", "source_url", "variants"):
            # The listing wins: it is what the user saw and clicked. Resolving
            # detail adds theatres and formats — it must never rename a movie
            # out from under the person who picked it.
            merged[field] = getattr(known, field) or merged.get(field, "")
        entry["movie"] = merged

    if position is None:
        movies.insert(0, entry)
    else:
        # Replace in place. Resolving detail must not reshuffle the grid —
        # the order is BookMyShow's own, and the user's eye is already on it.
        movies[position] = entry
    catalogue["movies"] = movies
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror, platform=platform)
    return entry


def remove_entry(movie_id: str, *, mirror: bool = True) -> None:
    platform = platform_of(movie_id)
    catalogue = load_catalogue(platform)
    catalogue["movies"] = [
        e for e in catalogue.get("movies", [])
        if isinstance(e, dict) and e.get("movie") and movie_from_entry(e).id != movie_id
    ]
    catalogue["updated_at"] = to_iso(now_ist())
    save_catalogue(catalogue, mirror=mirror, platform=platform)


# ──────────────────────────────────────────────────────────────────────────
# Syncing a city
# ──────────────────────────────────────────────────────────────────────────
def sync_region(region_slug: str, platform: str = "bookmyshow", *, mirror: bool = False,
                detail_limit: int = DETAIL_LIMIT, detail: bool = True,
                on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Read a city's listing and cache it. Never raises.

    Returns a summary dict. On failure the previous catalogue is left exactly
    as it was — a bot check must not be allowed to look like "the city has no
    movies today", because tomorrow the UI would show an empty grid and the
    user would believe it.
    """
    say = on_progress or (lambda msg: print(f"[catalogue] {msg}"))
    if not is_enabled(platform):
        # A disabled platform is never read and its catalogue never written.
        message = f"{platform} is not enabled; nothing was synced."
        say(message)
        return {"ok": False, "status": SyncStatus.ERROR, "message": message, "movies": 0}
    provider = get_provider(platform)

    try:
        movies = provider.list_movies(region_slug)
    except PlatformBlocked as exc:
        say(f"blocked: {exc}")
        _record_sync(region_slug, SyncStatus.BLOCKED, str(exc), mirror=mirror, platform=platform)
        return {"ok": False, "status": SyncStatus.BLOCKED, "message": str(exc), "movies": 0}
    except PlatformError as exc:
        say(f"failed: {exc}")
        _record_sync(region_slug, SyncStatus.ERROR, str(exc), mirror=mirror, platform=platform)
        return {"ok": False, "status": SyncStatus.ERROR, "message": str(exc), "movies": 0}

    if not movies:
        say("the city listing came back empty")
        _record_sync(region_slug, SyncStatus.EMPTY, "No movies listed.", mirror=mirror, platform=platform)
        return {"ok": True, "status": SyncStatus.EMPTY, "message": "No movies listed.", "movies": 0}

    say(f"{len(movies)} movie(s) listed in {region_slug}")

    # Keep whatever detail we already have; only re-read what we must.
    existing = {movie_from_entry(e).id: e for e in list_entries(region_slug, platform)}
    entries: list[dict[str, Any]] = []
    for movie in movies:
        prior = existing.get(movie.id)
        if prior and is_detailed(prior) and not siblings_changed(prior, movie):
            # Carry the resolved detail forward, but take the fresher title.
            prior = dict(prior)
            stored = movie_from_entry(prior)
            prior["movie"] = asdict(
                MovieRef(
                    **{
                        **asdict(stored),
                        "title": movie.title or stored.title,
                        "poster_url": movie.poster_url or stored.poster_url,
                        "language": movie.language or stored.language,
                    }
                )
            )
            entries.append(prior)
        else:
            entries.append(entry_from_movie(movie))

    _upsert(entries, region_slug=region_slug, mirror=mirror, platform=platform)

    detailed = failed = 0
    city_read = getattr(provider, "city_snapshots", None)
    if detail and city_read is not None:
        # A provider that reads per cinema (PVR INOX) answers for every film
        # at once: one bounded sweep of the city's cinemas over the first
        # dates on sale, instead of one read per film.
        try:
            snapshots = city_read(region_slug, sale_dates_for_catalogue())
        except PlatformError as exc:
            say(f"theatre/format detail not read: {exc}")
            failed = len(entries)
        else:
            listed = {movie_from_entry(e).id for e in entries}
            for snapshot in snapshots:
                if snapshot.movie.id in listed:
                    store_snapshot(snapshot, mirror=False)
                    detailed += 1
    elif detail:
        # Never-detailed rows first, then the stalest, up to the limit.
        pending = sorted(
            (e for e in entries if not is_detailed(e) or is_stale(e)),
            key=lambda e: (is_detailed(e), e.get("resolved_at") or ""),
        )[:detail_limit]
        say(f"resolving theatres/formats for {len(pending)} movie(s)")
        for index, entry in enumerate(pending):
            movie = movie_from_entry(entry)
            if index:
                provider_sleep(provider, DETAIL_DELAY)
            try:
                snapshot = read_detail(provider, movie)
            except PlatformError as exc:
                failed += 1
                say(f"  {movie.title or movie.event_code}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad movie must not end the sync
                failed += 1
                say(f"  {movie.title or movie.event_code}: unexpected {type(exc).__name__}")
                continue
            store_snapshot(snapshot, mirror=False)
            detailed += 1
            say(
                f"  {snapshot.movie.title}: {len(snapshot.venues)} theatre(s), "
                f"{len(snapshot.showtimes)} showtime(s)"
            )

    _record_sync(region_slug, SyncStatus.OK, "", movie_count=len(entries), mirror=mirror, platform=platform)
    return {
        "ok": True,
        "status": SyncStatus.OK,
        "movies": len(entries),
        "detailed": detailed,
        "failed": failed,
    }


def sale_dates_for_catalogue() -> list[str]:
    """The dates a per-cinema catalogue read covers: today and the next
    ``DATE_SWEEP - 1`` days (IST) — the same breadth BookMyShow's detail
    read has."""
    from datetime import timedelta as _td

    today = now_ist().date()
    return [(today + _td(days=i)).strftime("%Y%m%d") for i in range(DATE_SWEEP)]


def provider_sleep(provider, seconds: float) -> None:
    """Sleep using the provider's own sleeper, so tests stay instant."""
    getattr(provider, "_sleep", lambda _s: None)(seconds)


def resolve_url(url: str, platform: str = "bookmyshow", *, mirror: bool = True) -> dict[str, Any]:
    """Resolve one listing URL into the catalogue.

    Admin/debug path only — reachable from ``resolve_movie.py``, never from
    the UI. The user-facing flow is location -> catalogue -> movie and must
    never ask anyone for a BookMyShow URL.
    """
    snapshot = get_provider(platform).resolve(url)
    return store_snapshot(snapshot, mirror=mirror)


def refresh_entry(movie_id: str, *, mirror: bool = True) -> dict[str, Any]:
    """Re-read one movie's theatres and formats."""
    entry = find_entry(movie_id)
    if entry is None:
        raise KeyError(f"{movie_id} is not in the catalogue.")
    movie = movie_from_entry(entry)
    snapshot = read_detail(get_provider(movie.platform), movie)
    return store_snapshot(snapshot, mirror=mirror)


def ensure_detail(movie_id: str, *, mirror: bool = True) -> tuple[dict[str, Any] | None, str]:
    """Make sure a movie has theatre/format detail, reading it live if not.

    Returns ``(entry, problem)``. ``problem`` is empty on success; when it is
    set the caller shows it verbatim rather than an empty theatre list.
    """
    entry = find_entry(movie_id)
    if entry is None:
        return None, "That movie is no longer in the catalogue."
    if is_detailed(entry):
        return entry, ""
    try:
        return refresh_entry(movie_id, mirror=mirror), ""
    except PlatformBlocked as exc:
        return entry, str(exc)
    except (PlatformError, KeyError) as exc:
        return entry, str(exc)


__all__ = [
    "DATE_SWEEP",
    "DETAIL_LIMIT",
    "DETAIL_TTL",
    "SyncStatus",
    "ensure_detail",
    "entry_from_movie",
    "entry_from_snapshot",
    "find_entry",
    "is_detailed",
    "is_stale",
    "listings_from_entry",
    "listings_from_showtimes",
    "merge_snapshots",
    "platform_of",
    "sale_dates_for_catalogue",
    "read_detail",
    "list_entries",
    "movie_from_entry",
    "refresh_entry",
    "remove_entry",
    "resolve_url",
    "search_entries",
    "siblings_changed",
    "store_snapshot",
    "sync_region",
    "sync_state",
    "venues_from_entry",
]
