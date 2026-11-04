"""The showtimes the catalogue sync already read, kept for the Explorer.

The scheduled catalogue sync (``sync_catalogue.py`` on a GitHub runner) reads
every film's showtimes for its first few dates on sale — that is how it learns
which theatres list the film — and until now kept only the theatre list. This
module keeps the times too, in ``data/showtimes.json``, from those same
responses: **no extra BookMyShow request is made for it.**

It is the Explorer's fallback, never presented as live. Each film carries the
moment its listing was read, and the page labels anything from here "from
the catalogue sync at …". A runner is also far more likely than a Streamlit
host to get past BookMyShow's bot check, so on a host where live reads are
refused this is what keeps the Explorer useful at all.

Shape (compact on purpose — it is committed four times a day)::

    {"version": 1, "updated_at": "…",
     "movies": {"bookmyshow:ET…": {
         "region": "hyderabad", "read_at": "…",
         "venues": {"AMBH": ["AMB Cinemas", "Gachibowli"]},
         "shows": [[venue, date, time_code, time_label, format, event_format,
                    session_id, sold_out], …]}}}
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from config import store
from config.timezone import now_ist, parse_iso, to_iso
from monitor.models import Availability, Snapshot

FILE_NAME = "showtimes.json"
VERSION = 1
#: A film's snapshot older than this is not offered at all: with four syncs a
#: day and a six-hour detail TTL, it means the film stopped being re-read.
MAX_AGE = timedelta(hours=30)


def path():
    # Resolved per call: the test suite re-points ``store.DATA_DIR``.
    return store.DATA_DIR / FILE_NAME


def load() -> dict[str, Any]:
    data = store.read_json(path())
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("movies"), dict):
        return {"version": VERSION, "updated_at": None, "movies": {}}
    return data


def record_of(snapshot: Snapshot) -> dict[str, Any]:
    venues = {v.code: [v.name, v.area] for v in snapshot.venues}
    shows = []
    for s in snapshot.showtimes:
        if not s.date_code:
            continue
        shows.append([s.venue_code, s.date_code, s.time_code, s.time_label, s.format_label,
                      s.event_format, s.session_id, 1 if s.availability is Availability.SOLD_OUT else 0])
        venues.setdefault(s.venue_code, [s.venue_name, ""])
    return {
        "region": snapshot.movie.region_slug,
        "read_at": to_iso(snapshot.fetched_at or now_ist()),
        "venues": venues,
        "shows": shows,
    }


def record(region_slug: str, snapshots: Iterable[Snapshot], *, listed_ids: set[str],
           mirror: bool = False, at: datetime | None = None) -> int:
    """Merge this sync's reads into the file. Films the city no longer lists
    are dropped, and so are dates already past; a film this sync did not
    re-read keeps its earlier record and that record's own read time."""
    at = at or now_ist()
    today = at.strftime("%Y%m%d")
    data = load()
    movies: dict[str, Any] = data["movies"]
    for snap in snapshots:
        if snap.movie.platform not in ("", "bookmyshow"):
            continue
        movies[snap.movie.id] = record_of(snap)
    kept: dict[str, Any] = {}
    for movie_id, rec in movies.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("region") == region_slug and movie_id not in listed_ids:
            continue
        rec = dict(rec)
        rec["shows"] = [s for s in rec.get("shows", []) if isinstance(s, list) and len(s) >= 8
                        and str(s[1]) >= today]
        kept[movie_id] = rec
    payload = {"version": VERSION, "updated_at": to_iso(at), "movies": kept}
    store.write_json(path(), payload, mirror=mirror, message="chore: update showtimes snapshot")
    return len(kept)


def movie_record(movie_id: str, *, at: datetime | None = None,
                 data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A film's snapshot, or None when there is none fresh enough to offer."""
    rec = (data or load())["movies"].get(movie_id)
    if not isinstance(rec, dict):
        return None
    read_at = parse_iso(rec.get("read_at"))
    if read_at is None or (at or now_ist()) - read_at > MAX_AGE:
        return None
    return rec


__all__ = ["FILE_NAME", "MAX_AGE", "VERSION", "load", "movie_record", "path", "record", "record_of"]
