"""Normalised domain model shared by the UI, the checker and every platform.

Nothing in here knows what BookMyShow is. Providers in ``platforms/`` translate
their own payloads into these types, so adding District or PVR later means
writing one more provider — not touching the monitoring engine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable

from config.timezone import IST, now_ist, parse_iso, to_iso

ANY_FORMAT = "Any format"


# ──────────────────────────────────────────────────────────────────────────
# Availability
# ──────────────────────────────────────────────────────────────────────────
class Availability(str, Enum):
    """What we know about one theatre+format target, right now.

    Deliberately *not* a boolean. The difference between "we looked and the
    tickets are not on sale" and "we could not look" is the whole point of
    this application — collapsing them is how you end up trusting a network
    timeout as an answer.
    """

    UNKNOWN = "UNKNOWN"                          # never checked yet
    NOT_FOUND = "NOT_FOUND"                      # the movie/event itself is gone
    THEATRE_NOT_AVAILABLE = "THEATRE_NOT_AVAILABLE"  # movie listed, this theatre isn't
    SHOW_NOT_AVAILABLE = "SHOW_NOT_AVAILABLE"    # theatre listed, no show in this format
    NOT_BOOKABLE = "NOT_BOOKABLE"                # shows listed but booking not open
    AVAILABLE = "AVAILABLE"                      # at least one seat category is buyable
    SOLD_OUT = "SOLD_OUT"                        # shows exist, every category is full
    ERROR = "ERROR"                              # we could not check — NOT an answer
    EXPIRED = "EXPIRED"                          # monitor passed its end time
    STOPPED = "STOPPED"                          # user stopped the monitor

    @property
    def is_answer(self) -> bool:
        """True when the platform actually told us something.

        ``ERROR`` / ``UNKNOWN`` are not answers and must never overwrite the
        last real observation, or a flaky night would silently reset state and
        re-trigger notifications.
        """
        return self not in (Availability.ERROR, Availability.UNKNOWN)

    @property
    def is_bookable(self) -> bool:
        return self is Availability.AVAILABLE

    @property
    def is_terminal(self) -> bool:
        return self in (Availability.EXPIRED, Availability.STOPPED)


#: States that, when followed by AVAILABLE, are worth an email.
NOTIFIABLE_FROM = frozenset(
    {
        Availability.UNKNOWN,
        Availability.NOT_FOUND,
        Availability.THEATRE_NOT_AVAILABLE,
        Availability.SHOW_NOT_AVAILABLE,
        Availability.NOT_BOOKABLE,
        Availability.SOLD_OUT,
    }
)


class MonitorStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"

    @property
    def is_running(self) -> bool:
        return self is MonitorStatus.ACTIVE


# ──────────────────────────────────────────────────────────────────────────
# Platform-neutral view of a listing
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Showtime:
    """One bookable (or not) screening."""

    venue_code: str
    venue_name: str
    session_id: str
    date_code: str          # YYYYMMDD
    time_label: str         # "07:30 PM"
    time_code: str          # "1930"
    format_label: str       # "Dolby Cinema 2D", "IMAX", "" when unknown
    availability: Availability
    #: Where a click on this showtime should land. Either a show-level link the
    #: platform itself published, or the most specific page we can *derive*
    #: (the date's booking page). Never a guessed pattern.
    booking_url: str = ""

    @property
    def key(self) -> str:
        """Stable identity of a screening, used for new-showtime detection."""
        return f"{self.venue_code}|{self.date_code}|{self.session_id or self.time_code}"


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    area: str = ""
    formats: tuple[str, ...] = ()

    @property
    def abbr(self) -> str:
        words = [w for w in self.name.replace("-", " ").split() if w]
        if not words:
            return "???"
        if len(words) == 1:
            return words[0][:3].upper()
        return "".join(w[0] for w in words[:3]).upper()


@dataclass(frozen=True)
class MovieRef:
    """Everything needed to re-fetch a movie, plus what we show the user."""

    platform: str            # "bookmyshow"
    event_code: str          # "ET00123456"
    title: str
    region_code: str         # "HYD"
    region_slug: str         # "hyderabad"
    city: str = "Hyderabad"
    language: str = ""
    poster_url: str = ""
    source_url: str = ""     # the buytickets URL this was resolved from
    #: Sibling events for the same film in the same language — the premium
    #: formats BookMyShow lists as separate bookable events ("4DX 3D", "IMAX
    #: 3D", "EPIQ"…), as ``(event_code, format label)``. Their showtimes are
    #: *not* part of this event's answer, so a provider sweeps them too; the
    #: movie is still one row, identified by ``event_code``.
    variants: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        # JSON round-trips tuples as lists; keep the field hashable either way.
        pairs = tuple(
            (str(pair[0]), str(pair[1]) if len(pair) > 1 else "")
            for pair in (self.variants or ())
            if isinstance(pair, (list, tuple)) and pair and pair[0]
        )
        object.__setattr__(self, "variants", pairs)

    @property
    def id(self) -> str:
        return f"{self.platform}:{self.event_code}"

    @property
    def variant_codes(self) -> tuple[str, ...]:
        return tuple(code for code, _ in self.variants if code != self.event_code)


@dataclass
class Snapshot:
    """A successful read of a platform listing. Never constructed on failure."""

    movie: MovieRef
    venues: list[Venue]
    showtimes: list[Showtime]
    bookable_dates: list[str] = field(default_factory=list)
    closed_dates: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=now_ist)

    def venue(self, code: str) -> Venue | None:
        return next((v for v in self.venues if v.code == code), None)

    def shows_for(self, venue_code: str) -> list[Showtime]:
        return [s for s in self.showtimes if s.venue_code == venue_code]


# ──────────────────────────────────────────────────────────────────────────
# Monitor configuration
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class TheatreTarget:
    """One theatre inside a monitor, with the format the user cares about.

    Formats are per-target on purpose: "Allu Cinemas / Dolby" and
    "AMB Cinemas / HDR by Barco" are two independent things to watch, and one
    going live must never stop the other.
    """

    venue_code: str
    venue_name: str
    area: str = ""
    fmt: str = ANY_FORMAT

    @property
    def key(self) -> str:
        return f"{self.venue_code}::{self.fmt}"

    @property
    def label(self) -> str:
        return f"{self.venue_name} · {self.fmt}"

    def matches_format(self, showtime_format: str) -> bool:
        if self.fmt == ANY_FORMAT or not self.fmt.strip():
            return True
        return normalise_format(self.fmt) in normalise_format(showtime_format)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TheatreTarget:
        return cls(
            venue_code=raw["venue_code"],
            venue_name=raw.get("venue_name", raw["venue_code"]),
            area=raw.get("area", ""),
            fmt=raw.get("fmt") or ANY_FORMAT,
        )


def normalise_format(value: str) -> str:
    """Loose comparison key for format strings.

    BookMyShow writes the same format several ways ("DOLBY CINEMA",
    "Dolby Cinema 2D", "2D Dolby-Cinema"), so we compare on a squashed,
    lowercase, alphanumeric-only key and use containment rather than equality.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


@dataclass
class Monitor:
    """A user-created alert. This is what gets persisted and scheduled."""

    movie: MovieRef
    targets: list[TheatreTarget]
    interval_minutes: int = 10
    monitor_until: datetime = field(default_factory=lambda: now_ist() + timedelta(days=1))
    start_immediately: bool = True
    notify_email: str = ""
    date_codes: list[str] = field(default_factory=list)  # [] = every date the platform offers
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: MonitorStatus = MonitorStatus.ACTIVE
    created_at: datetime = field(default_factory=now_ist)
    stopped_at: datetime | None = None
    stopped_reason: str = ""
    #: When the UI asked the worker for an immediate first check (a workflow
    #: dispatch). None means it could not, and the schedule will pick it up.
    first_check_requested_at: datetime | None = None
    #: A failure the UI itself knows about — e.g. the first check could not be
    #: started. ``{"kind": ..., "message": ..., "at": iso}`` or None. Shown as
    #: PROBLEM OCCURRED until the worker's first real check supersedes it.
    problem: dict[str, Any] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or now_ist()) >= self.monitor_until

    def is_running(self, at: datetime | None = None) -> bool:
        """The single source of truth for 'should the worker check this?'."""
        return self.status.is_running and not self.is_expired(at)

    def expire(self, at: datetime | None = None) -> None:
        self.status = MonitorStatus.EXPIRED
        self.stopped_at = at or now_ist()
        self.stopped_reason = "Reached the end time you set."

    def stop(self, at: datetime | None = None) -> None:
        self.status = MonitorStatus.STOPPED
        self.stopped_at = at or now_ist()
        self.stopped_reason = "Stopped manually."

    def extend(self, hours: int = 24, at: datetime | None = None) -> None:
        base = max(self.monitor_until, at or now_ist())
        self.monitor_until = base + timedelta(hours=hours)
        self.status = MonitorStatus.ACTIVE
        self.stopped_at = None
        self.stopped_reason = ""

    def target(self, key: str) -> TheatreTarget | None:
        return next((t for t in self.targets if t.key == key), None)

    def set_problem(self, kind: str, message: str, at: datetime | None = None) -> None:
        self.problem = {"kind": kind, "message": message, "at": to_iso(at or now_ist())}

    def clear_problem(self) -> None:
        self.problem = None

    @property
    def date_range_label(self) -> str:
        """'25 Sep 2026' · '25–28 Sep 2026' · '' when every date is watched."""
        return describe_date_codes(self.date_codes)

    # ── persistence ──────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "movie": asdict(self.movie),
            "targets": [t.to_dict() for t in self.targets],
            "interval_minutes": self.interval_minutes,
            "monitor_until": to_iso(self.monitor_until),
            "start_immediately": self.start_immediately,
            "notify_email": self.notify_email,
            "date_codes": list(self.date_codes),
            "status": self.status.value,
            "created_at": to_iso(self.created_at),
            "stopped_at": to_iso(self.stopped_at) if self.stopped_at else None,
            "stopped_reason": self.stopped_reason,
            "first_check_requested_at": to_iso(self.first_check_requested_at),
            "problem": dict(self.problem) if self.problem else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Monitor:
        return cls(
            id=raw.get("id") or uuid.uuid4().hex[:12],
            movie=MovieRef(**raw["movie"]),
            targets=[TheatreTarget.from_dict(t) for t in raw.get("targets", [])],
            interval_minutes=int(raw.get("interval_minutes", 10)),
            monitor_until=parse_iso(raw["monitor_until"]),
            start_immediately=bool(raw.get("start_immediately", True)),
            notify_email=raw.get("notify_email", ""),
            date_codes=list(raw.get("date_codes", [])),
            status=MonitorStatus(raw.get("status", "ACTIVE")),
            created_at=parse_iso(raw.get("created_at")) or now_ist(),
            stopped_at=parse_iso(raw.get("stopped_at")),
            stopped_reason=raw.get("stopped_reason", ""),
            first_check_requested_at=parse_iso(raw.get("first_check_requested_at")),
            problem=dict(raw["problem"]) if isinstance(raw.get("problem"), dict) else None,
        )


# ──────────────────────────────────────────────────────────────────────────
# Results of a single check
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class TargetResult:
    """What one check said about one theatre+format target."""

    target_key: str
    venue_name: str
    fmt: str
    availability: Availability
    showtimes: list[Showtime] = field(default_factory=list)
    date_code: str = ""
    booking_url: str = ""
    detail: str = ""

    @property
    def showtime_keys(self) -> list[str]:
        return sorted({s.key for s in self.showtimes})

    @property
    def date_codes(self) -> list[str]:
        """Every distinct show date in this result, ascending."""
        return sorted({s.date_code for s in self.showtimes if s.date_code})

    def _label(self, show: Showtime) -> str:
        """'07:30 PM', or '25 Sep · 07:30 PM' when the result spans dates."""
        if len(self.date_codes) > 1 and show.date_code:
            return f"{short_date(show.date_code)} · {show.time_label}"
        return show.time_label

    @property
    def time_labels(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in sorted(self.showtimes, key=lambda s: (s.date_code, s.time_code or s.time_label)):
            if s.time_label:
                seen.setdefault(self._label(s), None)
        return list(seen)

    @property
    def time_links(self) -> list[list[str]]:
        """``[[label, url], …]`` in showtime order — what the email links.

        The URL is whatever the provider attached to the showtime; when a
        label has no URL of its own it inherits the target's booking page, so
        a chip is never a dead end. Serialised as lists so it round-trips
        through JSON unchanged.
        """
        seen: dict[str, str] = {}
        for s in sorted(self.showtimes, key=lambda s: (s.date_code, s.time_code or s.time_label)):
            if s.time_label:
                seen.setdefault(self._label(s), s.booking_url or self.booking_url)
        return [[label, url] for label, url in seen.items()]


@dataclass
class CheckOutcome:
    """The whole result of checking one monitor once."""

    monitor_id: str
    checked_at: datetime
    ok: bool
    results: list[TargetResult] = field(default_factory=list)
    error: str = ""
    #: True when the platform *refused* us (bot check / WAF), as opposed to a
    #: network or parsing failure. The UI words the two differently.
    blocked: bool = False

    @property
    def available_results(self) -> list[TargetResult]:
        return [r for r in self.results if r.availability.is_bookable]


def short_date(date_code: str) -> str:
    """'20260925' -> '25 Sep'."""
    try:
        return datetime.strptime(date_code, "%Y%m%d").strftime("%d %b").lstrip("0")
    except (ValueError, TypeError):
        return date_code


def describe_date_codes(codes: Iterable[str]) -> str:
    """Human label for a set of show dates: '', '25 Sep 2026', '25–28 Sep 2026',
    '30 Sep – 2 Oct 2026' or, for gaps, '25 Sep, 27 Sep 2026'."""
    days = []
    for c in sorted({c for c in codes if c}):
        try:
            days.append(datetime.strptime(c, "%Y%m%d"))
        except ValueError:
            continue
    if not days:
        return ""
    first, last = days[0], days[-1]
    if len(days) == 1:
        return f"{first.day} {first.strftime('%b %Y')}"
    contiguous = (last - first).days == len(days) - 1
    if contiguous and first.month == last.month and first.year == last.year:
        return f"{first.day}–{last.day} {first.strftime('%b %Y')}"
    if contiguous and first.year == last.year:
        return f"{first.day} {first.strftime('%b')} – {last.day} {last.strftime('%b %Y')}"
    if contiguous:
        return f"{first.day} {first.strftime('%b %Y')} – {last.day} {last.strftime('%b %Y')}"
    return ", ".join(f"{d.day} {d.strftime('%b')}" for d in days) + f" {last.year}"


def date_codes_between(start, end) -> list[str]:
    """Inclusive YYYYMMDD codes from ``start`` to ``end`` (date objects)."""
    if end < start:
        start, end = end, start
    out = []
    day = start
    while day <= end:
        out.append(day.strftime("%Y%m%d"))
        day = day + timedelta(days=1)
    return out


def dedupe(values: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication, used for format lists."""
    out: dict[str, None] = {}
    for v in values:
        v = (v or "").strip()
        if v:
            out.setdefault(v, None)
    return list(out)


__all__ = [
    "ANY_FORMAT",
    "Availability",
    "CheckOutcome",
    "IST",
    "Monitor",
    "MonitorStatus",
    "MovieRef",
    "NOTIFIABLE_FROM",
    "Showtime",
    "Snapshot",
    "TargetResult",
    "TheatreTarget",
    "Venue",
    "date_codes_between",
    "dedupe",
    "describe_date_codes",
    "normalise_format",
    "short_date",
]
