"""Filtering, ordering and grouping showtimes — pure functions, no I/O.

Times are Asia/Kolkata throughout. A show's moment is its own BookMyShow
``date_code`` (YYYYMMDD) plus its own clock time, read from ``time_code``
("1930") or, failing that, the label ("07:30 PM"). A show whose time cannot
be read is never placed in a time window by guesswork: it is counted and
left out, and the page says how many.

A time window is one calendar day's [start, end], both inclusive, start
strictly before end. Overnight windows are not offered: BookMyShow dates a
show by its own date code, so "after midnight" is the next date in the date
strip, which is where the person finds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dtime
from typing import Iterable

from config.timezone import IST
from explorer.classify import LOCALITY_ORDER, UNKNOWN

#: The quick picks offered beside the custom From / To inputs.
PRESETS: tuple[tuple[str, dtime, dtime], ...] = (
    ("Morning", dtime(6, 0), dtime(11, 59)),
    ("Afternoon", dtime(12, 0), dtime(16, 59)),
    ("Evening", dtime(17, 0), dtime(20, 59)),
    ("Night", dtime(21, 0), dtime(23, 59)),
    ("All day", dtime(0, 0), dtime(23, 59)),
)
DAY_START, DAY_END = dtime(0, 0), dtime(23, 59)

_LABEL_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*([AaPp])\.?\s*[Mm]\.?\s*$")
_CODE_RE = re.compile(r"^\d{4}$")
_DATE_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class ExplorerShow:
    """One screening as the Explorer shows it. Every field is BookMyShow's
    own, or derived from BookMyShow's own text (``explorer.classify``)."""

    movie_id: str            # the catalogue row ("bookmyshow:ET…")
    movie_title: str
    language: str
    event_code: str          # the event the show was listed under (base or premium sibling)
    venue_code: str
    venue_name: str
    area: str                # BookMyShow's own address fragment
    locality: str
    operator: str
    theatre_type: str
    date_code: str
    time_label: str
    minutes: int | None      # minutes after midnight, IST; None = unreadable
    formats: tuple[str, ...] = ()
    session_id: str = ""
    booking_url: str = ""    # "" when no verified BookMyShow URL exists
    sold_out: bool = False   # only when every seat category said so
    poster_url: str = ""
    #: The show's dimension as BookMyShow names its event ("2D", "3D", "4DX 3D");
    #: "" when unknown. Always ``formats[0]`` when set.
    dimension: str = ""

    @property
    def starts_at(self) -> datetime | None:
        if self.minutes is None or not _DATE_RE.match(self.date_code or ""):
            return None
        day = datetime.strptime(self.date_code, "%Y%m%d")
        return day.replace(hour=self.minutes // 60, minute=self.minutes % 60, tzinfo=IST)

    @property
    def dedupe_key(self) -> tuple:
        # A session id names one screening; without one, the same theatre,
        # date, minute, film and format is the same screening read twice.
        if self.session_id:
            return (self.venue_code, self.date_code, "s", self.session_id)
        return (self.venue_code, self.date_code, self.minutes, self.time_label,
                self.movie_id, self.formats)

    @property
    def display_time(self) -> tuple[str, str]:
        """("9:05", "AM") — the clock, then the half of the day."""
        if self.minutes is None:
            return (self.time_label or "—", "")
        hour, minute = divmod(self.minutes, 60)
        half = "AM" if hour < 12 else "PM"
        return (f"{(hour % 12) or 12}:{minute:02d}", half)


def parse_minutes(time_code: str = "", time_label: str = "") -> int | None:
    """Minutes after midnight from "1930" or "07:30 PM"; None if neither reads."""
    code = (time_code or "").strip()
    if _CODE_RE.match(code):
        hour, minute = int(code[:2]), int(code[2:])
        if hour < 24 and minute < 60:
            return hour * 60 + minute
    match = _LABEL_RE.match(time_label or "")
    if match:
        hour, minute, half = int(match.group(1)), int(match.group(2)), match.group(3).upper()
        if 1 <= hour <= 12 and minute < 60:
            hour = (hour % 12) + (12 if half == "P" else 0)
            return hour * 60 + minute
    return None


def minutes_of(value: dtime) -> int:
    return value.hour * 60 + value.minute


class WindowError(ValueError):
    """A time window the page must refuse, with the sentence to show."""


def validate_window(start: dtime | None, end: dtime | None) -> tuple[int, int]:
    if start is None or end is None:
        raise WindowError("Choose both a start and an end time.")
    lo, hi = minutes_of(start), minutes_of(end)
    if hi <= lo:
        raise WindowError("The end time must be later than the start time. For shows after "
                          "midnight, pick the next date.")
    return lo, hi


def fmt_clock(value: dtime) -> str:
    return datetime(2000, 1, 1, value.hour, value.minute).strftime("%I:%M %p").lstrip("0")


@dataclass
class Filters:
    date_code: str
    start: dtime = DAY_START
    end: dtime = DAY_END
    localities: tuple[str, ...] = ()       # () = All Hyderabad
    operators: tuple[str, ...] = ()        # () = all operators
    theatre_type: str = ""                 # "" = all
    languages: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    venue_code: str = ""                   # theatre mode: this theatre only
    title_key: str = ""                    # theatre mode, one film: that film only (``catalog.title_key``)
    hide_started_before: datetime | None = None   # today: shows already begun


@dataclass
class FilterResult:
    shows: list[ExplorerShow]
    wrong_date: int = 0           # listed for another date (BookMyShow's fallback date)
    unreadable_time: int = 0      # no time we could read
    already_started: int = 0      # today, before now
    outside_window: int = 0
    filtered_out: int = 0         # by area / operator / type / language / format
    duplicates: int = 0
    notes: list[str] = field(default_factory=list)


def dedupe_shows(shows: Iterable[ExplorerShow]) -> tuple[list[ExplorerShow], int]:
    seen: dict[tuple, ExplorerShow] = {}
    dropped = 0
    for show in shows:
        key = show.dedupe_key
        if key in seen:
            dropped += 1
            known = seen[key]
            # Keep one, with every format either read named.
            merged = tuple(dict.fromkeys((*known.formats, *show.formats)))
            if merged != known.formats:
                seen[key] = replace(known, formats=merged)
            continue
        seen[key] = show
    return list(seen.values()), dropped


def _matches_format(show: ExplorerShow, wanted: tuple[str, ...]) -> bool:
    if not wanted:
        return True
    names = {f.casefold() for f in show.formats}
    return any(w.casefold() in names for w in wanted)


def apply(shows: Iterable[ExplorerShow], f: Filters) -> FilterResult:
    lo, hi = validate_window(f.start, f.end)
    unique, dupes = dedupe_shows(shows)
    out = FilterResult(shows=[], duplicates=dupes)
    for show in unique:
        if f.venue_code and show.venue_code != f.venue_code:
            continue
        if f.title_key and " ".join(show.movie_title.casefold().split()) != f.title_key:
            continue
        if show.date_code != f.date_code:
            out.wrong_date += 1
            continue
        if show.minutes is None:
            out.unreadable_time += 1
            continue
        if f.hide_started_before is not None:
            start = show.starts_at
            if start is not None and start < f.hide_started_before:
                out.already_started += 1
                continue
        if not lo <= show.minutes <= hi:
            out.outside_window += 1
            continue
        if ((f.localities and show.locality not in f.localities)
                or (f.operators and show.operator not in f.operators)
                or (f.theatre_type and show.theatre_type != f.theatre_type)
                or (f.languages and show.language not in f.languages)
                or not _matches_format(show, f.formats)):
            out.filtered_out += 1
            continue
        out.shows.append(show)
    out.shows = sort_by_time(out.shows)
    return out


def sort_by_time(shows: Iterable[ExplorerShow]) -> list[ExplorerShow]:
    """Chronological across every theatre; ties by theatre, then film."""
    return sorted(shows, key=lambda s: (s.date_code, s.minutes if s.minutes is not None else 24 * 60,
                                        s.venue_name.casefold(), s.area.casefold(),
                                        s.movie_title.casefold(), s.formats))


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    subtitle: str
    shows: tuple[ExplorerShow, ...]
    booking_url: str = ""
    poster_url: str = ""


def group_by_theatre(shows: Iterable[ExplorerShow]) -> list[Group]:
    """Theatres in order of their first matching show, each show in time order."""
    buckets: dict[str, list[ExplorerShow]] = {}
    for show in sort_by_time(shows):
        buckets.setdefault(show.venue_code, []).append(show)
    groups = []
    for code, items in buckets.items():
        first = items[0]
        where = first.locality if first.locality != UNKNOWN else (first.area or "")
        groups.append(Group(key=code, title=first.venue_name, subtitle=where, shows=tuple(items),
                            booking_url=first.booking_url))
    return groups


def group_by_movie(shows: Iterable[ExplorerShow]) -> list[Group]:
    buckets: dict[str, list[ExplorerShow]] = {}
    for show in sort_by_time(shows):
        buckets.setdefault(show.movie_id, []).append(show)
    return [Group(key=mid, title=items[0].movie_title, subtitle=items[0].language,
                  shows=tuple(items), booking_url=items[0].booking_url, poster_url=items[0].poster_url)
            for mid, items in buckets.items()]


#: The heading for shows whose catalogue row names no language.
NO_LANGUAGE = "Language not listed"


def group_by_language(shows: Iterable[ExplorerShow]) -> list[Group]:
    """One film at one theatre, cut by language: each language in order of
    its first show, its shows in time order. A row BookMyShow lists with no
    language stays apart under :data:`NO_LANGUAGE` — never guessed."""
    buckets: dict[str, list[ExplorerShow]] = {}
    for show in sort_by_time(shows):
        buckets.setdefault(show.language or NO_LANGUAGE, []).append(show)
    return [Group(key=lang, title=lang, subtitle=items[0].movie_title, shows=tuple(items),
                  booking_url=items[0].booking_url, poster_url=items[0].poster_url)
            for lang, items in buckets.items()]


def by_hour(shows: Iterable[ExplorerShow]) -> list[tuple[str, list[ExplorerShow]]]:
    """The time-first list cut into hour bands — "9 AM", "10 AM" — for scanning."""
    out: list[tuple[str, list[ExplorerShow]]] = []
    for show in shows:
        hour = (show.minutes // 60) if show.minutes is not None else -1
        label = "—" if hour < 0 else f"{(hour % 12) or 12} {'AM' if hour < 12 else 'PM'}"
        if not out or out[-1][0] != label:
            out.append((label, []))
        out[-1][1].append(show)
    return out


def options(shows: Iterable[ExplorerShow]) -> dict[str, list[str]]:
    """What the filters can offer, from what was actually read."""
    shows = list(shows)
    localities = sorted({s.locality for s in shows},
                        key=lambda x: (x == UNKNOWN, LOCALITY_ORDER.get(x, 999), x))
    operators = sorted({s.operator for s in shows}, key=lambda x: (x.startswith("Independent"), x.casefold()))
    return {
        "localities": localities,
        "operators": operators,
        "languages": sorted({s.language for s in shows if s.language}, key=str.casefold),
        "formats": sorted({f for s in shows for f in s.formats}, key=str.casefold),
    }


__all__ = [
    "DAY_END",
    "DAY_START",
    "ExplorerShow",
    "FilterResult",
    "Filters",
    "Group",
    "NO_LANGUAGE",
    "PRESETS",
    "WindowError",
    "apply",
    "by_hour",
    "dedupe_shows",
    "fmt_clock",
    "group_by_language",
    "group_by_movie",
    "group_by_theatre",
    "minutes_of",
    "options",
    "parse_minutes",
    "sort_by_time",
    "validate_window",
]
