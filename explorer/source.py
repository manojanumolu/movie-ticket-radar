"""Where the Explorer's showtimes come from, and how politely.

The source
----------
BookMyShow's web client draws a film's showtimes from one endpoint::

    GET /api/movies-data/v4/showtimes-by-event/primary-dynamic?eventCode=…&dateCode=…

and one response holds **every Hyderabad theatre** listing that event on
that date. It is the endpoint the monitor and the catalogue sync already
use (``platforms.bookmyshow``), verified from GitHub runners. So:

* **By movie** — one read per catalogue row (one language) plus one per
  premium sibling event (IMAX, 4DX, Infinity Vision…), for the chosen date.
  Never a read per theatre.
* **By theatre** — BookMyShow's theatre page has a venue-level showtimes
  source, but its shape has never been verified from here, so it is not
  used. Instead the catalogue says which films list the theatre on that date
  (its per-date listings, or the films' theatre lists), and only those films
  are read — each read city-wide and cached, so it also serves By movie.

Safeguards
----------
* One process-wide cache keyed by ``(event, date)`` — shared by every
  session, because the listing is the same for everybody. Filters never
  cause a read; only a new film/date or the Refresh action does.
* Single-flight: two sessions asking for the same event and date wait on one
  request.
* A token bucket caps the Explorer at ``BUDGET`` requests in a burst,
  refilling at ``REFILL_PER_SECOND``; at most ``IN_FLIGHT`` requests are open
  at once; a theatre query reads at most ``MAX_EVENTS`` events.
* A refusal (403 / bot check) opens a circuit: live reads pause for
  ``BLOCK_PAUSE`` and the page falls back to the sync's snapshot, labelled.
* 429 and 5xx are retried with backoff by the provider; timeouts and broken
  JSON surface as an honest failure. A failed sibling makes a result
  *partial*, and the page says which part is missing.

The Explorer runs in the Streamlit process. The monitoring worker runs on a
GitHub runner with its own client, so nothing here can delay or starve a
monitor's checks.
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from config.timezone import now_ist, parse_iso
from explorer import snapshot as snap_store
from explorer.classify import classify
from explorer.query import ExplorerShow, parse_minutes
from monitor.models import Availability, MovieRef
from platforms.base import PlatformBlocked, PlatformError
from platforms.bookmyshow import BookMyShowProvider, is_bookmyshow_url, split_venue_name
from urllib.parse import urlparse

#: A live read is served from cache for this long; after it, a new film/date
#: selection reads again. Refresh can always read sooner (``COOLDOWN``).
CACHE_TTL = 600.0
#: A live read younger than this is "live"; older but cached is "cached".
FRESH_FOR = 120.0
#: Refresh within this long of the last live read returns that read.
COOLDOWN = 45.0
BUDGET = 30
REFILL_PER_SECOND = 0.5
#: How long a request may wait for budget before the read gives up.
MAX_BUDGET_WAIT = 10.0
IN_FLIGHT = 2
MAX_EVENTS = 40
BLOCK_PAUSE = 300.0
MAX_CACHE = 300

LIVE, CACHED, SNAPSHOT, FAILED = "live", "cached", "snapshot", "failed"


class BudgetExhausted(PlatformError):
    """The Explorer's own request budget is spent; try again shortly."""


class TokenBucket:
    def __init__(self, capacity: int = BUDGET, rate: float = REFILL_PER_SECOND,
                 clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep):
        self.capacity, self.rate = capacity, rate
        self._tokens = float(capacity)
        self._clock, self._sleep = clock, sleeper
        self._at = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._at) * self.rate)
        self._at = now

    def acquire(self, max_wait: float = MAX_BUDGET_WAIT) -> None:
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait = (1 - self._tokens) / self.rate
            if wait > max_wait:
                raise BudgetExhausted(
                    "The Explorer has used its BookMyShow request allowance for the moment. "
                    f"Try again in about {int(wait)} seconds.")
            self._tokens -= 1  # reserve it; the wait below pays for it
        self._sleep(wait)

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


class GatedSession:
    """An HTTP session whose every request first takes budget and a slot."""

    def __init__(self, inner, bucket: TokenBucket, slots: threading.BoundedSemaphore):
        self._inner, self._bucket, self._slots = inner, bucket, slots
        self.requests = 0

    def get(self, url, headers=None, params=None, timeout=20):
        self._bucket.acquire()
        with self._slots:
            self.requests += 1
            return self._inner.get(url, headers=headers, params=params, timeout=timeout)


@dataclass
class MovieDay:
    """One film's shows on one date, and where they came from."""

    movie_id: str
    date_code: str
    shows: list[ExplorerShow]
    origin: str                    # LIVE · CACHED · SNAPSHOT · FAILED
    read_at: datetime | None       # when BookMyShow was read (never "now" for old data)
    error: str = ""                # why the live read failed (origin SNAPSHOT/FAILED)
    missing: list[str] = field(default_factory=list)   # partial: which events were not read
    skipped: bool = False          # not attempted (over the per-query cap)


@dataclass
class _Entry:
    day: Any          # platforms.bookmyshow.DayRead
    at: float         # monotonic
    read_at: datetime


def _booking_url(provider: BookMyShowProvider, movie: MovieRef, venue_code: str, date_code: str) -> str:
    """The theatre's page for that date (the redirect form ``venue_booking_url``
    documents as verified), else the film's page for that date — and only ever
    an https BookMyShow address."""
    for url in (provider.venue_booking_url(movie, venue_code, date_code),
                provider.booking_url(movie, date_code)):
        if is_bookmyshow_url(url):
            return url
    return ""


def _formats(screen: str, event_format: str, base_format: str) -> tuple[str, ...]:
    """A show's format names, dimension first: the premium sibling's own label
    ("3D", "4DX 3D") or, for the base event, the city listing's dimension
    ("2D") — then the screen BookMyShow named ("HDR By Barco")."""
    return tuple(dict.fromkeys(f for f in (event_format or base_format, screen) if f))


_POSTER_HOSTS = ("in.bmscdn.com", "assets-in.bmscdn.com")


def safe_poster(url: str) -> str:
    """Only BookMyShow's own image CDN, over https; anything else draws no image."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    return url if parsed.scheme == "https" and (parsed.netloc or "").lower() in _POSTER_HOSTS else ""


#: What a failure is called on the page. The provider's own messages can carry
#: a network library's exception text (proxy hosts, addresses); none of that is
#: shown — only which kind of failure it was. The full text stays server-side.
_REASONS = (
    (("allowance",), "The Explorer's own request limit was reached — try again shortly"),
    (("paused",), "BookMyShow refused recent requests, so live refresh is paused for a few minutes"),
    (("bot check", "http 403", "http 401"), "BookMyShow refused the request (its bot check)"),
    (("429", "rate limit"), "BookMyShow is rate-limiting requests right now"),
    (("timeout", "timed out"), "BookMyShow didn't respond in time"),
    (("server error",), "BookMyShow had a server error"),
    (("could not reach", "connection"), "BookMyShow couldn't be reached"),
    (("isn't json", "not json", "unexpected"), "BookMyShow sent a response that couldn't be read"),
    (("404", "no listing"), "BookMyShow has no listing for this film on that date"),
)


def public_reason(message: str) -> str:
    text = (message or "").lower()
    for needles, reason in _REASONS:
        if any(n in text for n in needles):
            return reason
    return "BookMyShow couldn't be reached"


def shows_from_day(day, movie: MovieRef, movie_id: str, *, title: str = "", poster: str = "",
                   provider: BookMyShowProvider | None = None, base_format: str = "") -> list[ExplorerShow]:
    """Turn a provider read into Explorer shows. Theatre facts come from the
    theatre's own BookMyShow name and area (``explorer.classify``)."""
    provider = provider or BookMyShowProvider(session=object())
    venues = {v.code: v for v in day.venues}
    out = []
    for s in day.showtimes:
        venue = venues.get(s.venue_code)
        name = (venue.name if venue else "") or s.venue_name or s.venue_code
        area = venue.area if venue else ""
        facts = classify(name, area)
        out.append(ExplorerShow(
            movie_id=movie_id, movie_title=title or movie.title, language=movie.language,
            event_code=movie.event_code, venue_code=s.venue_code, venue_name=name, area=area,
            locality=facts.locality, operator=facts.operator, theatre_type=facts.theatre_type,
            date_code=s.date_code, time_label=s.time_label,
            minutes=parse_minutes(s.time_code, s.time_label),
            formats=_formats(s.format_label, s.event_format, base_format),
            dimension=s.event_format or base_format,
            session_id=s.session_id, booking_url=_booking_url(provider, movie, s.venue_code, s.date_code),
            sold_out=s.availability is Availability.SOLD_OUT, poster_url=safe_poster(poster),
        ))
    return out


def shows_from_snapshot(rec: dict[str, Any], movie: MovieRef, movie_id: str, *, title: str = "",
                        poster: str = "", base_format: str = "") -> list[ExplorerShow]:
    provider = BookMyShowProvider(session=object())
    venues = rec.get("venues") if isinstance(rec.get("venues"), dict) else {}
    out = []
    for row in rec.get("shows", []):
        if not isinstance(row, list) or len(row) < 8:
            continue
        code, date_code, time_code, label, fmt, event_fmt, session, sold = row[:8]
        name, area = (venues.get(code) or [code, ""])[:2]
        if not area:
            name, area = split_venue_name(name)
        facts = classify(name, area)
        out.append(ExplorerShow(
            movie_id=movie_id, movie_title=title or movie.title, language=movie.language,
            event_code=movie.event_code, venue_code=str(code), venue_name=str(name), area=str(area),
            locality=facts.locality, operator=facts.operator, theatre_type=facts.theatre_type,
            date_code=str(date_code), time_label=str(label), minutes=parse_minutes(str(time_code), str(label)),
            formats=_formats(str(fmt), str(event_fmt), base_format),
            dimension=str(event_fmt) or base_format,
            session_id=str(session), booking_url=_booking_url(provider, movie, str(code), str(date_code)),
            sold_out=bool(sold), poster_url=safe_poster(poster),
        ))
    return out


def _default_factory(bucket: TokenBucket, slots: threading.BoundedSemaphore) -> BookMyShowProvider:
    from platforms.http import build_session

    return BookMyShowProvider(session=GatedSession(build_session(), bucket, slots))


class LiveReader:
    """The process-wide reader. One instance (``reader()``) serves the app."""

    def __init__(self, factory: Callable[[TokenBucket, threading.BoundedSemaphore], Any] | None = None, *,
                 clock: Callable[[], float] = time.monotonic, bucket: TokenBucket | None = None):
        self._clock = clock
        self.bucket = bucket or TokenBucket(clock=clock)
        self.slots = threading.BoundedSemaphore(IN_FLIGHT)
        self._factory = factory or _default_factory
        self._providers: queue.Queue = queue.Queue()
        self._made = 0
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], _Entry] = {}
        self._flights: dict[tuple[str, str], threading.Lock] = {}
        self.blocked_until = 0.0
        self.blocked_reason = ""
        self.reads = 0   # provider read_day calls actually made

    # ── providers: one per worker, never shared between threads ──────────
    def _borrow(self):
        try:
            return self._providers.get_nowait()
        except queue.Empty:
            with self._lock:
                self._made += 1
            return self._factory(self.bucket, self.slots)

    def _return(self, provider) -> None:
        self._providers.put(provider)

    # ── state ────────────────────────────────────────────────────────────
    def paused_for(self) -> float:
        return max(0.0, self.blocked_until - self._clock())

    def cached(self, event_code: str, date_code: str) -> _Entry | None:
        entry = self._cache.get((event_code, date_code))
        if entry is None or self._clock() - entry.at > CACHE_TTL:
            return None
        return entry

    def age(self, entry: _Entry) -> float:
        return self._clock() - entry.at

    def read(self, movie: MovieRef, date_code: str, *, force: bool = False) -> tuple[_Entry, bool]:
        """``(entry, from_cache)``. Raises PlatformError when nothing could be read."""
        key = (movie.event_code, date_code)
        entry = self.cached(*key)
        if entry is not None and (not force or self.age(entry) < COOLDOWN):
            return entry, True
        with self._lock:
            flight = self._flights.setdefault(key, threading.Lock())
        with flight:
            # Somebody may have read it while this thread waited.
            entry = self.cached(*key)
            if entry is not None and (not force or self.age(entry) < COOLDOWN):
                return entry, True
            if self.paused_for() > 0:
                raise PlatformBlocked(self.blocked_reason or "BookMyShow refused recent requests.")
            provider = self._borrow()
            try:
                self.reads += 1
                day = provider.read_day(movie, date_code)
            except PlatformBlocked as exc:
                self.blocked_until = self._clock() + BLOCK_PAUSE
                self.blocked_reason = ("BookMyShow refused this server's requests (bot check). "
                                       "Live refresh is paused for a few minutes.")
                raise PlatformBlocked(f"{self.blocked_reason} ({exc})") from None
            finally:
                self._return(provider)
            entry = _Entry(day=day, at=self._clock(), read_at=day.fetched_at or now_ist())
            with self._lock:
                self._cache[key] = entry
                if len(self._cache) > MAX_CACHE:
                    oldest = min(self._cache, key=lambda k: self._cache[k].at)
                    self._cache.pop(oldest, None)
            return entry, False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.blocked_until = 0.0
            self.blocked_reason = ""


_reader: LiveReader | None = None
_reader_lock = threading.Lock()


def reader() -> LiveReader:
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = LiveReader()
        return _reader


def install(new: LiveReader | None) -> None:
    """Replace the process's reader (tests; ``None`` builds a fresh one on use)."""
    global _reader
    with _reader_lock:
        _reader = new


@dataclass(frozen=True)
class Wanted:
    """One catalogue row to read: the film as BookMyShow knows it, and how
    the page names it."""

    movie: MovieRef
    movie_id: str
    title: str = ""
    poster: str = ""
    #: The city listing's dimension for this row's own event ("2D"), when known.
    base_format: str = ""
    #: By theatre: True when the catalogue lists this film at the theatre on
    #: the date; False when it is read in case the theatre has added it since.
    listed: bool = True

    @property
    def events(self) -> int:
        return 1 + len(self.movie.variant_codes)


def _snapshot_day(w: Wanted, date_code: str, error: str, data: dict | None) -> MovieDay:
    rec = snap_store.movie_record(w.movie_id, data=data)
    if rec is None:
        return MovieDay(w.movie_id, date_code, [], FAILED, None, error=error)
    shows = shows_from_snapshot(rec, w.movie, w.movie_id, title=w.title, poster=w.poster,
                                base_format=w.base_format)
    return MovieDay(w.movie_id, date_code, shows, SNAPSHOT, parse_iso(rec.get("read_at")), error=error)


def read_one(w: Wanted, date_code: str, *, force: bool = False, live: bool = True,
             snapshot_data: dict | None = None, rd: LiveReader | None = None) -> MovieDay:
    rd = rd or reader()
    if not live:
        return _snapshot_day(w, date_code, "", snapshot_data)
    try:
        entry, from_cache = rd.read(w.movie, date_code, force=force)
    except PlatformError as exc:
        print(f"[explorer] live read failed: {type(exc).__name__}", flush=True)
        return _snapshot_day(w, date_code, public_reason(str(exc)), snapshot_data)
    except Exception as exc:  # noqa: BLE001 - a parser surprise is a failed read, not a crash
        print(f"[explorer] live read failed: unexpected {type(exc).__name__}", flush=True)
        return _snapshot_day(w, date_code, "BookMyShow sent a response that couldn't be read", snapshot_data)
    shows = shows_from_day(entry.day, w.movie, w.movie_id, title=w.title, poster=w.poster,
                           base_format=w.base_format)
    origin = CACHED if from_cache and rd.age(entry) >= FRESH_FOR else LIVE
    missing = [public_reason(msg) for _code, msg in entry.day.errors]
    return MovieDay(w.movie_id, date_code, shows, origin, entry.read_at, missing=missing)


def read_many(wanted: list[Wanted], date_code: str, *, force: bool = False, live: bool = True,
              max_events: int = MAX_EVENTS, rd: LiveReader | None = None) -> list[MovieDay]:
    """Read several films for one date, at most ``IN_FLIGHT`` at a time and at
    most ``max_events`` events in all. Films over the cap are not read live:
    their snapshot is offered when there is one, and they are marked skipped."""
    rd = rd or reader()
    data = snap_store.load()
    budgeted: list[Wanted] = []
    over: list[Wanted] = []
    used = 0
    for w in wanted:
        if used + w.events <= max_events or not budgeted:
            budgeted.append(w)
            used += w.events
        else:
            over.append(w)
    if len(budgeted) <= 1 or not live:
        days = [read_one(w, date_code, force=force, live=live, snapshot_data=data, rd=rd) for w in budgeted]
    else:
        with ThreadPoolExecutor(max_workers=IN_FLIGHT, thread_name_prefix="tr-explorer") as pool:
            days = list(pool.map(
                lambda w: read_one(w, date_code, force=force, live=live, snapshot_data=data, rd=rd), budgeted))
    for w in over:
        day = _snapshot_day(w, date_code, "", data)
        day.skipped = True
        days.append(day)
    return days


__all__ = [
    "BLOCK_PAUSE", "BUDGET", "BudgetExhausted", "CACHED", "CACHE_TTL", "COOLDOWN", "FAILED", "FRESH_FOR",
    "GatedSession", "IN_FLIGHT", "LIVE", "LiveReader", "MAX_EVENTS", "MovieDay", "SNAPSHOT", "TokenBucket",
    "Wanted", "install", "public_reason", "read_many", "read_one", "reader", "safe_poster", "shows_from_day",
    "shows_from_snapshot",
]
