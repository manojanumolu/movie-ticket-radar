"""PVR INOX provider — read-only, and disabled until a runner proves it may call.

Source
------
The PVR INOX website renders from an undocumented JSON API::

    POST https://api3.pvrcinemas.com/api/v1/booking/content/<operation>

Nothing here was read from PVR by this repository yet: the shapes come from
two MIT-licensed open-source clients that commit raw live captures
(notprashanth/pvr-inox-mcp and karanb192/pvr-inox-radar, captured 21 Aug
2026). ``tools/pvr_diagnose.py`` is how this repository checks them against
Hyderabad from a GitHub Actions runner — and that check is why the platform
ships disabled (``platforms.PLATFORMS``): one of those clients reports that
the API refuses GitHub runners. If it does, the answer is to stop, not to
route around it. This module never retries a refusal, never rotates
anything, and sends only the headers the public website's own anonymous
client sends — including its empty ``Authorization: Bearer`` header, which
carries no credential.

What the captures show, and what this module does about it
----------------------------------------------------------
* Success is HTTP 200 with ``{"status": 302, "output": {...}}`` in the body.
  Any other body status means the date is not on sale at that cinema (a real
  answer). HTTP 500 is also reported for an unopened date — but it is only
  read that way when no cinema answered that date as open; otherwise it is
  a failure.
* ``content/csessions`` answers for **one cinema on one date**, so a read
  names its cinemas (``fetch(..., venue_codes=...)``) and every date
  explicitly — there is no default-date shortcut.
* A film is ``movieRe.id`` / ``filmCommonCode``. It has several *print*
  ids (per language, per format); a show's ``movieId`` is its print. A new
  IMAX print arrives with a new id, so shows are matched on the film and
  never on known print ids.
* A block's title can name the wrong language; the show's own ``language``
  is authoritative.
* 2D/3D and projection formats (IMAX, 4DX, ICE) live in the show's
  ``movieFormat`` only. ``experience`` / ``screenType`` name the screen
  class (INSIGNIA, GOLD, PLAYHOUSE…) and say ``INSIGNIA`` for a 3D show;
  ``filmFormat`` is empty for 4DX 2D; ``soundFormat`` is sound. None of
  those is ever read as a movie format.
* Availability is per show (``status`` / ``statusCode`` / ``statusTxt``).
  Only ``1`` / ``76BE43`` / "Available" is confirmed. Seat categories live
  behind a per-show seat-layout call that this module deliberately never
  makes.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlparse

import requests

from config.timezone import IST, now_ist
from monitor.models import Availability, MovieRef, Showtime, Snapshot, Venue, dedupe
from platforms.base import PlatformBlocked, PlatformError

SLUG = "pvr_inox"
API = "https://api3.pvrcinemas.com/api/v1/booking"
SITE = "https://www.pvrcinemas.com/"
SEAT_PAGE = "https://www.pvrcinemas.com/seatlayout/"
BOOKING_HOSTS = frozenset({"www.pvrcinemas.com", "pvrcinemas.com"})

#: The body status the API wraps a successful answer in.
OK_STATUS = 302


@dataclass(frozen=True)
class City:
    """What the API needs to answer for a city: its name (sent in a header
    and in bodies), its id and its centre."""

    slug: str
    name: str
    city_id: str
    lat: str
    lng: str


#: From ``content/city`` in the 21 Aug 2026 capture: Hyderabad is id 32,
#: TELANGANA, 19 cinemas, no sub-cities, centred at these coordinates.
CITIES: dict[str, City] = {
    "hyderabad": City("hyderabad", "Hyderabad", "32", "17.1827790564", "78.60351551"),
}

# ── politeness: none of these can be raised by a caller ────────────────────
#: Seconds between two requests from this process, strictly sequential.
MIN_INTERVAL = 0.8
#: Seconds a request may take.
TIMEOUT = 20
#: After a 401/403/429 nothing more is sent to PVR from this process for this
#: long; every read fails at once as blocked. Never a retry.
BLOCK_COOLDOWN = 900.0
#: The most requests one read (``fetch`` / ``city_snapshots``) may make. A
#: read that would need more refuses up front rather than truncating — a
#: truncated read would report cinemas it never asked about as "not listed".
MAX_REQUESTS_PER_READ = 100
#: The most cinemas one monitor read may name.
MAX_VENUES = 20
#: An "any date" read covers today and the next six days (IST). PVR sells a
#: rolling window of about five days, so later dates are simply closed.
SALE_WINDOW_DAYS = 7
#: Dates further out than this are never requested at all.
MAX_HORIZON_DAYS = 14

#: Projection formats. Read from a show's ``movieFormat`` only, in this order.
PROJECTION = ("IMAX", "4DX", "ICE", "MX4D", "SCREENX")
#: Tokens that are never a screen class (they are movie formats or sound).
NOT_SCREEN_CLASS = frozenset({*PROJECTION, "2D", "3D", "ATMOS", "DOLBY ATMOS", "PREMIUM", "REGULAR"})

#: ``statusTxt`` values, lower-cased. Only "available" is confirmed by a
#: capture; the others are the labels the website is known to use.
SOLD_OUT_WORDS = ("housefull", "house full", "sold out", "soldout")
NOT_BOOKABLE_WORDS = ("lapsed", "closed", "not available", "unavailable", "coming soon")
AVAILABLE_WORDS = ("available", "filling", "almost full", "fast")

DATE_RE = re.compile(r"^\d{8}$")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def lang_key(language: str) -> str:
    """'English' -> 'ENGLISH' — the comparison key for a language."""
    return re.sub(r"[^A-Z0-9]", "", (language or "").upper())


def film_key(movie: MovieRef) -> tuple[str, str]:
    """``(filmCommonCode, language key)`` out of a PVR ``event_code``
    (``"35288-ENGLISH"``)."""
    common, _, language = (movie.event_code or "").rpartition("-")
    if not common or not language:
        raise PlatformError(f"'{movie.event_code}' is not a PVR INOX film code.")
    return common, language


def event_code_for(common: str, language: str) -> str:
    return f"{common}-{lang_key(language)}"


def event_format(movie_format: str) -> str:
    """The show's actual movie format, canonical: ``"IMAX 3D"``, ``"4DX 2D"``,
    ``"3D"``, ``"2D"``. From ``movieFormat`` alone; sound (ATMOS) and screen
    classes are not formats. No 3D token means 2D."""
    tokens = [t for t in re.split(r"[\s+/,-]+", (movie_format or "").upper()) if t]
    premium = [p for p in PROJECTION if p in tokens]
    return " ".join([*premium, "3D" if "3D" in tokens else "2D"])


def screen_class(experience: str, screen_type: str = "") -> str:
    """The premium screen class a show plays in — ``"INSIGNIA"``, ``"PXL"``,
    ``"GOLD"`` — or ``""``. A bracketed suffix (``"INSIGNIA [ATMOS]"``) is
    sound, not class; ``"P[XL]"`` is PVR's own spelling of PXL. Projection
    formats are never a class: an IMAX auditorium playing a regular print is
    not an IMAX show."""
    raw = _text(experience) or _text(screen_type)
    raw = re.sub(r"(?i)^p\s*\[\s*xl\s*\]", "PXL", raw)
    raw = re.sub(r"\s*\[[^\]]*\]\s*", " ", raw)
    value = " ".join(raw.upper().split())
    return "" if not value or value in NOT_SCREEN_CLASS else value


def is_pvr_url(value: str) -> bool:
    """Only an absolute https link on PVR INOX's own site is ever used."""
    try:
        parsed = urlparse((value or "").strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.netloc or "").lower() in BOOKING_HOSTS


def seat_page(encrypted: str) -> str:
    """The show's own booking page, from the token the API put on the show;
    '' when there is none — never a guessed link."""
    token = _text(encrypted)
    return SEAT_PAGE + quote(token, safe="()=!*'~-_.") if token else ""


def sale_dates(today: datetime | None = None, days: int = SALE_WINDOW_DAYS) -> list[str]:
    """Today and the following days, IST, as ``YYYYMMDD``."""
    start = (today or now_ist()).astimezone(IST).date()
    return [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def _iso_date(date_code: str) -> str:
    return f"{date_code[:4]}-{date_code[4:6]}-{date_code[6:]}"


def _ok_output(payload: Any) -> dict[str, Any] | None:
    """The ``output`` of a successful answer, or None."""
    if isinstance(payload, dict) and payload.get("status") == OK_STATUS and isinstance(payload.get("output"), dict):
        return payload["output"]
    return None


# ──────────────────────────────────────────────────────────────────────────
# Shows
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedShow:
    common: str          # filmCommonCode
    language: str        # the show's own language, as written
    title: str           # the film's common name
    showtime: Showtime


def _availability(show: dict[str, Any], now_ms: float, unknown: set[str]) -> Availability:
    """A show's availability. A show that has started, or is "lapsed", is not
    bookable; "housefull" is sold out; the labels the site uses for a show on
    sale are available. An unknown label on a show PVR lists on an open date
    is read as on sale and recorded for diagnosis — never as sold out."""
    start = show.get("showTimeStamp")
    if isinstance(start, (int, float)) and start and start <= now_ms:
        return Availability.NOT_BOOKABLE
    label = " ".join(_text(show.get("statusTxt")).lower().split())
    if any(w in label for w in NOT_BOOKABLE_WORDS):
        return Availability.NOT_BOOKABLE
    if any(w in label for w in SOLD_OUT_WORDS):
        return Availability.SOLD_OUT
    if any(w in label for w in AVAILABLE_WORDS):
        return Availability.AVAILABLE
    if not label and show.get("status") == 1:
        return Availability.AVAILABLE
    unknown.add(f"{label or '(none)'}|{_text(show.get('status'))}|{_text(show.get('statusCode'))}")
    return Availability.AVAILABLE


def _time_code(show: dict[str, Any]) -> str:
    stamp = show.get("showTimeStamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return datetime.fromtimestamp(stamp / 1000, IST).strftime("%H%M")
    try:
        return datetime.strptime(_text(show.get("showTime")), "%I:%M %p").strftime("%H%M")
    except ValueError:
        return ""


def parse_sessions(output: dict[str, Any], venue_code: str, *, now_ms: float,
                   unknown: set[str] | None = None) -> list[ParsedShow]:
    """Every show in one ``csessions`` answer, each resolved to its own film
    and language. Raises :class:`PlatformError` on a shape it cannot read —
    a malformed answer is never "no shows"."""
    unknown = unknown if unknown is not None else set()
    blocks = output.get("cinemaMovieSessions")
    if not isinstance(blocks, list):
        raise PlatformError("PVR INOX answered without cinemaMovieSessions — the response shape changed.")
    out: list[ParsedShow] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        movie_re = block.get("movieRe") if isinstance(block.get("movieRe"), dict) else {}
        prints = {_text(f.get("filmId")): f for f in movie_re.get("films") or [] if isinstance(f, dict)}
        for exp in block.get("experienceSessions") or []:
            if not isinstance(exp, dict):
                continue
            for show in exp.get("shows") or []:
                if not isinstance(show, dict):
                    continue
                print_ = prints.get(_text(show.get("movieId"))) or {}
                common = _text(print_.get("filmCommonCode")) or _text(movie_re.get("id"))
                language = _text(show.get("language")) or _text(print_.get("language"))
                date_code = _text(show.get("showDate")).replace("-", "")
                session = _text(show.get("sessionId"))
                if not common or not session or not DATE_RE.match(date_code):
                    raise PlatformError("PVR INOX listed a show without its film, session or date.")
                movie_format = _text(show.get("movieFormat")) or _text(print_.get("format"))
                title = (_text(print_.get("filmCommonName")) or _text(movie_re.get("n"))
                         or _text(print_.get("filmNameWeb")) or common)
                out.append(ParsedShow(common=common, language=language, title=title, showtime=Showtime(
                    venue_code=_text(show.get("theatreId")) or venue_code,
                    venue_name=venue_code,
                    session_id=session,
                    date_code=date_code,
                    time_label=_text(show.get("showTime")),
                    time_code=_time_code(show),
                    format_label=screen_class(_text(exp.get("experience")), _text(show.get("screenType"))),
                    availability=_availability(show, now_ms, unknown),
                    booking_url=seat_page(show.get("encrypted")),
                    format_raw=movie_format,
                    event_format=event_format(movie_format),
                )))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Provider
# ──────────────────────────────────────────────────────────────────────────
class PvrInoxProvider:
    slug = SLUG
    name = "PVR INOX"
    #: ``csessions`` answers per cinema: the checker hands ``fetch`` the
    #: cinemas its monitors watch (``monitor.checker.fetch_listing``).
    reads_per_venue = True

    def __init__(self, session=None, sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = now_ist) -> None:
        self._session = session if session is not None else requests.Session()
        self._sleep = sleeper
        self._clock = clock          # pacing and cooldown
        self._wall = wall            # "today" and "has this show started", IST
        self._lock = threading.Lock()
        self._last_call: float | None = None
        self._blocked_until = 0.0
        self._names: dict[str, str] = {}
        #: Requests this process has sent; the diagnostic reports it.
        self.requests_sent = 0

    # ── HTTP ─────────────────────────────────────────────────────────────
    @staticmethod
    def headers(city: str) -> dict[str, str]:
        """What the website's anonymous client sends. The empty bearer is the
        site's own convention for "not signed in" and carries no credential."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Authorization": "Bearer ",
            "chain": "PVR",
            "country": "INDIA",
            "appVersion": "1.0",
            "platform": "WEBSITE",
            "flow": "PVRINOX",
            "city": city,
            "User-Agent": "TicketRadar/1.0 (personal read-only showtime notifier)",
        }

    def post(self, operation: str, body: dict[str, Any], city: str) -> tuple[int, Any]:
        """One paced request. Returns ``(http_status, json_or_None)`` for a
        200 or a 500; raises for anything else. Never retries."""
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                raise PlatformBlocked(
                    f"PVR INOX refused an earlier request; nothing more is sent for "
                    f"{int(self._blocked_until - now)}s. Not a 'no tickets' answer.")
            if self._last_call is not None:
                wait = MIN_INTERVAL - (now - self._last_call)
                if wait > 0:
                    self._sleep(wait)
            self._last_call = self._clock()
            self.requests_sent += 1
            try:
                resp = self._session.post(f"{API}/{operation}", json=body, headers=self.headers(city),
                                          timeout=TIMEOUT)
            except (requests.RequestException, OSError) as exc:
                raise PlatformError(f"Could not reach PVR INOX ({type(exc).__name__}).") from None
            status = int(getattr(resp, "status_code", 0) or 0)
            if status in (401, 403, 429):
                self._blocked_until = self._clock() + BLOCK_COOLDOWN
                raise PlatformBlocked(
                    f"PVR INOX refused the request (HTTP {status}). This is a block, not a "
                    "'no tickets' answer; nothing more is sent from this run.")
        if status not in (200, 500):
            raise PlatformError(f"Unexpected HTTP {status} from PVR INOX.")
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if status == 200 and not isinstance(payload, dict):
            raise PlatformError("PVR INOX replied with something that isn't a JSON object.")
        return status, payload

    def _ok(self, operation: str, body: dict[str, Any], city: City) -> dict[str, Any]:
        status, payload = self.post(operation, body, city.name)
        output = _ok_output(payload) if status == 200 else None
        if output is None:
            raise PlatformError(f"PVR INOX {operation} did not answer with a listing "
                                f"(HTTP {status}, body status {payload.get('status') if isinstance(payload, dict) else '—'}).")
        return output

    @staticmethod
    def city(region_slug: str) -> City:
        city = CITIES.get((region_slug or "").lower())
        if city is None:
            raise PlatformError(f"PVR INOX is not set up for '{region_slug}'.")
        return city

    # ── Discovery ────────────────────────────────────────────────────────
    def city_record(self, region_slug: str) -> dict[str, Any]:
        """``content/city``: this city's record (id, name, cinemaCount)."""
        city = self.city(region_slug)
        output = self._ok("content/city", {"lat": city.lat, "lng": city.lng}, city)
        for rows in output.values():
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and (_text(row.get("id")) == city.city_id
                                              or _text(row.get("name")).lower() == city.name.lower()):
                    return row
        raise PlatformError(f"PVR INOX's city list does not include {city.name}.")

    def cinemas(self, region_slug: str, *, expected: int | None = None) -> list[dict[str, Any]]:
        """``content/cinemas``: every cinema in the city, as PVR describes it.
        With ``expected`` (the city's ``cinemaCount``) a shorter list is an
        error, never a smaller city."""
        city = self.city(region_slug)
        output = self._ok("content/cinemas", {"city": city.name, "lat": city.lat, "lng": city.lng, "text": ""}, city)
        rows = output.get("c")
        if not isinstance(rows, list):
            raise PlatformError("PVR INOX's cinema list is missing — the response shape changed.")
        rows = [r for r in rows if isinstance(r, dict) and _text(r.get("theatreId"))]
        if expected and len(rows) < expected:
            raise PlatformError(f"PVR INOX listed {len(rows)} of the {expected} cinemas it says "
                                f"{city.name} has; the list is incomplete.")
        for row in rows:
            self._names[_text(row["theatreId"])] = _text(row.get("name"))
        return rows

    def list_venues(self, region_slug: str) -> list[Venue]:
        """Every cinema in the city, checked against the city's own count."""
        expected = self.city_record(region_slug).get("cinemaCount")
        rows = self.cinemas(region_slug, expected=expected if isinstance(expected, int) else None)
        return [Venue(code=_text(r["theatreId"]), name=_text(r.get("name")) or _text(r["theatreId"]))
                for r in rows]

    def list_movies(self, region_slug: str = "hyderabad") -> list[MovieRef]:
        """``content/nowshowing``: one row per film and language, as the
        catalogue lists BookMyShow's. Prints are not rows."""
        city = self.city(region_slug)
        output = self._ok("content/nowshowing", {"city": city.name, "lat": city.lat, "lng": city.lng}, city)
        films = output.get("mv")
        if not isinstance(films, list):
            raise PlatformError("PVR INOX's now-showing list is missing — the response shape changed.")
        rows: dict[str, MovieRef] = {}
        for film in films:
            for print_ in (film.get("films") or []) if isinstance(film, dict) else []:
                if not isinstance(print_, dict):
                    continue
                common, language = _text(print_.get("filmCommonCode")), _text(print_.get("language"))
                if not common or not lang_key(language):
                    continue
                code = event_code_for(common, language)
                rows.setdefault(code, self._movie(city, common, language,
                                                  _text(print_.get("filmCommonName")) or _text(print_.get("filmNameWeb"))
                                                  or _text(film.get("filmName")) or common))
        return list(rows.values())

    def _movie(self, city: City, common: str, language: str, title: str) -> MovieRef:
        return MovieRef(platform=self.slug, event_code=event_code_for(common, language), title=title,
                        region_code=city.city_id, region_slug=city.slug, city=city.name,
                        language=language.title() if language.isupper() else language, source_url=SITE)

    # ── Shows ────────────────────────────────────────────────────────────
    def _dates(self, date_codes: Iterable[str] | None) -> list[str]:
        window = sale_dates(self._wall(), days=MAX_HORIZON_DAYS)
        asked = [d for d in dedupe(date_codes or []) if DATE_RE.match(d)]
        return [d for d in asked if d in window] if asked else window[:SALE_WINDOW_DAYS]

    def sessions(self, region_slug: str, venue_codes: list[str], date_codes: list[str],
                 *, unknown: set[str] | None = None) -> tuple[list[ParsedShow], list[str], list[str]]:
        """One ``csessions`` read per cinema per date, strictly bounded.

        Returns ``(shows, open_dates, closed_dates)``. Raises when any read
        failed: a partial answer would report the cinemas it never heard from
        as having no shows."""
        city = self.city(region_slug)
        total = len(venue_codes) * len(date_codes)
        if total > MAX_REQUESTS_PER_READ:
            raise PlatformError(f"This PVR INOX read would need {total} requests; the limit is "
                                f"{MAX_REQUESTS_PER_READ}. Watch fewer cinemas or dates.")
        now_ms = self._wall().timestamp() * 1000
        opened: set[str] = set()
        maybe_closed: list[tuple[str, str]] = []
        failures: list[str] = []
        shows: dict[tuple[str, str], ParsedShow] = {}
        for date_code in date_codes:
            for code in venue_codes:
                body = {"city": city.name, "cid": code, "lat": city.lat, "lng": city.lng,
                        "dated": _iso_date(date_code), "qr": "NO", "cineType": "", "cineTypeQR": ""}
                try:
                    status, payload = self.post("content/csessions", body, city.name)
                except PlatformBlocked:
                    raise
                except PlatformError as exc:
                    failures.append(f"{code}@{date_code}: {exc}")
                    continue
                if status == 500:
                    maybe_closed.append((code, date_code))
                    continue
                output = _ok_output(payload)
                if output is None:
                    if payload.get("status") == OK_STATUS:
                        failures.append(f"{code}@{date_code}: a 302 answer without a listing")
                    continue                          # any other body status: not on sale there yet
                try:
                    parsed = parse_sessions(output, code, now_ms=now_ms, unknown=unknown)
                except PlatformError as exc:
                    failures.append(f"{code}@{date_code}: {exc}")
                    continue
                opened.add(date_code)
                for show in parsed:
                    shows.setdefault((show.showtime.venue_code, show.showtime.session_id), show)
        # HTTP 500 means "not on sale" only for a date nobody answered as open.
        failures += [f"{code}@{d}: HTTP 500 on a date other cinemas are selling" for code, d in maybe_closed
                     if d in opened]
        if failures:
            raise PlatformError(f"PVR INOX: {len(failures)} of {total} reads failed — {failures[0]}")
        return (list(shows.values()), [d for d in date_codes if d in opened],
                [d for d in date_codes if d not in opened])

    def fetch(self, movie: MovieRef, date_codes: list[str] | None = None,
              venue_codes: list[str] | None = None) -> Snapshot:
        """This film's shows at the named cinemas on the named dates (or the
        sale window). A cinema is in ``venues`` only when it lists the film,
        which is what "listed for the movie" means on BookMyShow too."""
        common, language = film_key(movie)
        codes = dedupe(str(c) for c in (venue_codes or []))
        if not codes:
            raise PlatformError("A PVR INOX read must name the cinemas it is for.")
        if len(codes) > MAX_VENUES:
            raise PlatformError(f"A PVR INOX read may name at most {MAX_VENUES} cinemas.")
        dates = self._dates(date_codes)
        unknown: set[str] = set()
        shows, opened, closed = self.sessions(movie.region_slug, codes, dates, unknown=unknown)
        if unknown:
            print(f"[pvr] unrecognised show status(es), read as on sale: {sorted(unknown)}")
        mine = [s.showtime for s in shows if s.common == common and lang_key(s.language) == language]
        return self._snapshot(movie, mine, opened, closed)

    def city_snapshots(self, region_slug: str, date_codes: list[str]) -> list[Snapshot]:
        """Every film at every cinema on the given dates — the catalogue's
        read, one ``csessions`` per cinema per date for all films at once."""
        city = self.city(region_slug)
        venues = self.list_venues(region_slug)
        dates = self._dates(date_codes)
        shows, opened, closed = self.sessions(region_slug, [v.code for v in venues], dates)
        by_film: dict[str, list[ParsedShow]] = {}
        for show in shows:
            by_film.setdefault(event_code_for(show.common, show.language), []).append(show)
        return [self._snapshot(self._movie(city, group[0].common, group[0].language, group[0].title),
                               [s.showtime for s in group], opened, closed)
                for group in by_film.values()]

    def _snapshot(self, movie: MovieRef, showtimes: list[Showtime], opened: list[str],
                  closed: list[str]) -> Snapshot:
        named = [replace(s, venue_name=self._names.get(s.venue_code, s.venue_name)) for s in showtimes]
        venues: dict[str, Venue] = {}
        for s in named:
            known = venues.get(s.venue_code)
            formats = tuple(dedupe([*(known.formats if known else ()), *s.format_labels]))
            venues[s.venue_code] = Venue(code=s.venue_code, name=s.venue_name, formats=formats)
        return Snapshot(movie=movie, venues=sorted(venues.values(), key=lambda v: v.name.lower()),
                        showtimes=named, bookable_dates=list(opened), closed_dates=list(closed),
                        fetched_at=self._wall())

    # ── Links ────────────────────────────────────────────────────────────
    def booking_url(self, movie: MovieRef, date_code: str = "") -> str:  # noqa: ARG002
        """PVR INOX publishes no film or date page this module can name, so
        the only derived link is the site itself. Shows carry their own."""
        return SITE

    def venue_booking_url(self, movie: MovieRef, venue_code: str, date_code: str = "") -> str:  # noqa: ARG002
        return SITE

    def resolve(self, url: str) -> Snapshot:  # noqa: ARG002
        raise PlatformError("PVR INOX films are chosen from the catalogue, never from a link.")


__all__ = [
    "API",
    "CITIES",
    "MAX_REQUESTS_PER_READ",
    "PvrInoxProvider",
    "SITE",
    "SLUG",
    "event_format",
    "film_key",
    "is_pvr_url",
    "lang_key",
    "parse_sessions",
    "sale_dates",
    "screen_class",
    "seat_page",
]
