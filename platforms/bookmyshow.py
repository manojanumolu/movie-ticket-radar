"""BookMyShow provider.

Mechanism
---------
BookMyShow's web client renders showtimes from a JSON endpoint:

    GET /api/movies-data/v4/showtimes-by-event/primary-dynamic
        ?eventCode=ET00123456&dateCode=YYYYMMDD&regionCode=HYD&...

with a set of ``x-region-*`` headers that tell it which city to answer for.
The response is a widget tree; the parts we need are::

    data.topStickyWidgets[]  type=horizontal-block-list   -> the date strip
    data.showtimeWidgets[]   type=groupList
      └ venueGroup
         └ venue-card        additionalData.venueName / venueCode
            └ showtimes[]    title = "07:30 PM", screenAttr = format
               └ additionalData.categories[].availStatus
                   0 sold out · 1 almost full · 2 filling fast · 3 available

This is an undocumented internal endpoint. It can change shape or start
refusing us without notice, which is exactly why every parse step below is
defensive and every failure raises :class:`PlatformError` rather than
returning an empty listing. An empty listing means "no shows"; a raised error
means "we don't know" — the monitoring engine treats those very differently.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from config.timezone import now_ist
from monitor.models import (
    Availability,
    MovieRef,
    Showtime,
    Snapshot,
    Venue,
    dedupe,
)
from platforms.base import PlatformBlocked, PlatformError

API_URL = "https://in.bookmyshow.com/api/movies-data/v4/showtimes-by-event/primary-dynamic"
SITE = "https://in.bookmyshow.com"

#: City -> (regionCode, regionSlug, latitude, longitude, geohash).
#: V1 targets Hyderabad; the rest are here so the provider isn't city-locked.
REGIONS: dict[str, tuple[str, str, str, str, str]] = {
    "hyderabad": ("HYD", "hyderabad", "17.385", "78.487", "tep"),
    "bengaluru": ("BANG", "bengaluru", "12.972", "77.594", "tdr"),
    "chennai": ("CHEN", "chennai", "13.056", "80.206", "tf3"),
    "mumbai": ("MUMBAI", "mumbai", "19.076", "72.878", "te7"),
    "delhi-ncr": ("NCR", "delhi-ncr", "28.613", "77.209", "ttn"),
    "pune": ("PUNE", "pune", "18.520", "73.856", "te2"),
    "kolkata": ("KOLK", "kolkata", "22.573", "88.364", "tun"),
}
DEFAULT_CITY = "hyderabad"

#: BookMyShow's per-category seat status.
#: 1 and 2 still mean "you can buy a ticket right now", so they count as
#: AVAILABLE — a filling-fast show is a show you want to be emailed about.
AVAIL_STATUS = {
    "0": Availability.SOLD_OUT,
    "1": Availability.AVAILABLE,
    "2": Availability.AVAILABLE,
    "3": Availability.AVAILABLE,
}

DATE_STYLE = {
    "date-selected": "BOOKABLE",
    "date-default": "BOOKABLE",
    "date-disabled": "NOT_OPEN",
}

EVENT_CODE_RE = re.compile(r"^ET\d{6,}$", re.IGNORECASE)
DATE_CODE_RE = re.compile(r"^\d{8}$")

TIMEOUT = 20
MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def parse_listing_url(url: str) -> dict[str, str]:
    """Pull the event code, city slug and optional date out of a BMS URL.

    Accepts any of::

        https://in.bookmyshow.com/movies/hyderabad/<title>/buytickets/ET00123456
        https://in.bookmyshow.com/movies/hyderabad/buytickets/ET00123456/20260925
        ET00123456
    """
    raw = (url or "").strip()
    if not raw:
        raise PlatformError("Paste a BookMyShow movie link first.")

    if EVENT_CODE_RE.match(raw):
        return {"event_code": raw.upper(), "region_slug": DEFAULT_CITY, "date_code": ""}

    parsed = urlparse(raw if "//" in raw else f"https://{raw}")
    if "bookmyshow" not in (parsed.netloc or "").lower():
        raise PlatformError("That doesn't look like a BookMyShow link.")

    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    out = {"event_code": "", "region_slug": "", "date_code": ""}
    for part in parts:
        if EVENT_CODE_RE.match(part):
            out["event_code"] = part.upper()
        elif DATE_CODE_RE.match(part):
            out["date_code"] = part

    for anchor in ("movies", "buytickets"):
        if anchor in parts:
            idx = parts.index(anchor)
            if idx + 1 < len(parts) and not EVENT_CODE_RE.match(parts[idx + 1]):
                out["region_slug"] = parts[idx + 1]
                break

    if not out["event_code"]:
        raise PlatformError(
            "No event code in that link. Open the movie's 'Book tickets' page "
            "on BookMyShow and copy the URL — it contains an ET… code."
        )
    if not out["region_slug"] or out["region_slug"] not in REGIONS:
        out["region_slug"] = DEFAULT_CITY
    return out


def region_for(slug: str) -> tuple[str, str, str, str, str]:
    return REGIONS.get((slug or "").lower().strip(), REGIONS[DEFAULT_CITY])


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _dicts(value: Any) -> Iterable[dict[str, Any]]:
    """Iterate only the dicts in something that may not even be a list."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def clean_format(raw: str) -> str:
    """'2D DOLBY CINEMA' -> 'Dolby Cinema 2D' is overkill; we just tidy case.

    BookMyShow returns things like ``"DOLBY CINEMA"``, ``"ICE 4K LASER"`` or
    ``"2D"``. We title-case words but keep known all-caps tokens intact so
    "IMAX" doesn't become "Imax".
    """
    keep_upper = {"2D", "3D", "4DX", "IMAX", "ICE", "HDR", "4K", "MX4D", "EPIQ", "LUXE"}
    words = [w for w in re.split(r"\s+", (raw or "").strip()) if w]
    out = []
    for w in words:
        out.append(w.upper() if w.upper() in keep_upper else w.title())
    return " ".join(out)


# ──────────────────────────────────────────────────────────────────────────
# Provider
# ──────────────────────────────────────────────────────────────────────────
class BookMyShowProvider:
    slug = "bookmyshow"
    name = "BookMyShow"

    def __init__(self, session: requests.Session | None = None, sleeper=time.sleep) -> None:
        self._session = session or requests.Session()
        self._sleep = sleeper

    # ── HTTP ─────────────────────────────────────────────────────────────
    def _headers(self, region_code: str, region_slug: str, lat: str, lon: str, geohash: str,
                 event_code: str) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{SITE}/movies/{region_slug}/buytickets/{event_code}/",
            "Origin": SITE,
            "sec-ch-ua": '"Chromium";v="140", "Not:A-Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-app-code": "WEB",
            "x-region-code": region_code,
            "x-region-slug": region_slug,
            "x-geohash": geohash,
            "x-latitude": lat,
            "x-longitude": lon,
            "x-location-selection": "manual",
            "x-lsid": "",
        }

    def _get(self, event_code: str, date_code: str, region: tuple[str, str, str, str, str]) -> dict[str, Any]:
        """One API read, with bounded retries.

        Retries only what retrying can fix: timeouts, connection resets, 429
        and 5xx. A 403 is a bot-check — hammering it is both useless and rude,
        so it fails immediately as :class:`PlatformBlocked`.
        """
        region_code, region_slug, lat, lon, geohash = region
        headers = self._headers(region_code, region_slug, lat, lon, geohash, event_code)
        params = {
            "eventCode": event_code,
            "dateCode": date_code or "",
            "isDesktop": "true",
            "regionCode": region_code,
            "xLocationShared": "false",
            "memberId": "",
            "lsId": "",
            "subCode": "",
            "lat": lat,
            "lon": lon,
        }

        last_error = "unknown error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._session.get(API_URL, headers=headers, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    try:
                        payload = resp.json()
                    except ValueError:
                        raise PlatformError(
                            "BookMyShow replied with something that isn't JSON. "
                            "The endpoint may have changed."
                        ) from None
                    if not isinstance(payload, dict):
                        raise PlatformError("Unexpected response shape from BookMyShow.")
                    return payload

                if resp.status_code in (401, 403):
                    raise PlatformBlocked(
                        f"BookMyShow refused the request (HTTP {resp.status_code}). "
                        "This is a bot check, not a 'no tickets' answer."
                    )
                if resp.status_code == 404:
                    raise PlatformError(f"BookMyShow has no listing for {event_code} (HTTP 404).")
                if resp.status_code == 429:
                    last_error = "HTTP 429 (rate limited)"
                elif 500 <= resp.status_code < 600:
                    last_error = f"HTTP {resp.status_code} (BookMyShow server error)"
                else:
                    raise PlatformError(f"Unexpected HTTP {resp.status_code} from BookMyShow.")

            if attempt < MAX_ATTEMPTS:
                # Exponential backoff with jitter, so a scheduled fleet of
                # checks doesn't retry in lockstep.
                self._sleep(BACKOFF_BASE ** attempt + random.uniform(0, 0.75))

        raise PlatformError(f"Could not reach BookMyShow after {MAX_ATTEMPTS} attempts — {last_error}.")

    # ── Public API ───────────────────────────────────────────────────────
    def resolve(self, url: str) -> Snapshot:
        parsed = parse_listing_url(url)
        region = region_for(parsed["region_slug"])
        payload = self._get(parsed["event_code"], parsed["date_code"], region)
        movie = self._movie_ref(payload, parsed, region, source_url=url.strip())
        return self._snapshot(payload, movie)

    def fetch(self, movie: MovieRef, date_codes: list[str] | None = None) -> Snapshot:
        region = region_for(movie.region_slug)
        wanted = [c for c in (date_codes or []) if c] or [""]

        venues: dict[str, Venue] = {}
        showtimes: list[Showtime] = []
        bookable: list[str] = []
        closed: list[str] = []
        resolved = movie
        errors: list[str] = []

        for idx, date_code in enumerate(wanted):
            if idx:  # be a polite client when sweeping several dates
                self._sleep(1.0)
            try:
                payload = self._get(movie.event_code, date_code, region)
            except PlatformError as exc:
                errors.append(str(exc))
                continue

            if idx == 0:
                resolved = self._movie_ref(
                    payload,
                    {"event_code": movie.event_code, "region_slug": movie.region_slug, "date_code": date_code},
                    region,
                    source_url=movie.source_url,
                    fallback_title=movie.title,
                )
            snap = self._snapshot(payload, resolved)
            for v in snap.venues:
                venues.setdefault(v.code, v)
            showtimes.extend(snap.showtimes)
            bookable.extend(snap.bookable_dates)
            closed.extend(snap.closed_dates)

        if errors and not showtimes and len(errors) == len(wanted):
            # Every date failed — we learned nothing. Say so.
            raise PlatformError(errors[0])

        return Snapshot(
            movie=resolved,
            venues=self._merge_venue_formats(list(venues.values()), showtimes),
            showtimes=showtimes,
            bookable_dates=dedupe(bookable),
            closed_dates=[d for d in dedupe(closed) if d not in set(bookable)],
            fetched_at=now_ist(),
        )

    def booking_url(self, movie: MovieRef, date_code: str = "") -> str:
        """Derived from the page the user gave us — never guessed.

        BookMyShow's own URLs take an optional trailing ``/YYYYMMDD``; that is
        the only thing we append.
        """
        base = (movie.source_url or "").strip().rstrip("/")
        if not base:
            base = f"{SITE}/movies/{movie.region_slug}/buytickets/{movie.event_code}"
        else:
            # strip a date already present so we don't end up with two
            base = re.sub(r"/\d{8}$", "", base)
        return f"{base}/{date_code}" if DATE_CODE_RE.match(date_code or "") else base

    # ── Parsing ──────────────────────────────────────────────────────────
    def _movie_ref(self, payload: dict[str, Any], parsed: dict[str, str],
                   region: tuple[str, str, str, str, str], *, source_url: str,
                   fallback_title: str = "") -> MovieRef:
        title, language, poster = self._movie_meta(payload)
        region_code, region_slug, *_ = region
        return MovieRef(
            platform=self.slug,
            event_code=parsed["event_code"],
            title=title or fallback_title or parsed["event_code"],
            region_code=region_code,
            region_slug=region_slug,
            city=region_slug.replace("-", " ").title(),
            language=language,
            poster_url=poster,
            source_url=source_url,
        )

    def _movie_meta(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        """Best-effort title / language / poster.

        These live in presentational widgets that shift around between BMS
        releases, so each lookup is independent and an empty string is an
        acceptable outcome. We never fabricate a title: if nothing is found the
        caller falls back to the event code.
        """
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        title, language, poster = "", "", ""

        for widget in _dicts(data.get("bottomSheetData", {}).get("format-selector", {}).get("widgets", [])
                             if isinstance(data.get("bottomSheetData"), dict) else []):
            for item in _dicts(widget.get("data")):
                if item.get("styleId") == "bottomsheet-subtitle" and not title:
                    title = _text(item.get("text"))

        for widget in _dicts(data.get("topStickyWidgets")):
            if widget.get("type") != "horizontal-text-list":
                continue
            for item in _dicts(widget.get("data")):
                left = item.get("leftText")
                rows = left.get("data") if isinstance(left, dict) else None
                for row in _dicts(rows):
                    for comp in _dicts(row.get("components")):
                        text = _text(comp.get("text"))
                        if not text:
                            continue
                        if "•" in text and not language:
                            language = text
                        elif not title and "•" not in text:
                            title = text

        for key in ("eventTitle", "title", "movieName", "name"):
            if not title:
                title = _text(data.get(key))
        for key in ("posterUrl", "imageUrl", "eventImageUrl"):
            if not poster:
                poster = _text(data.get(key))

        return title, language, poster

    def _snapshot(self, payload: dict[str, Any], movie: MovieRef) -> Snapshot:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        bookable, closed = self._parse_dates(data)
        venues, showtimes = self._parse_shows(data, movie)
        return Snapshot(
            movie=movie,
            venues=self._merge_venue_formats(venues, showtimes),
            showtimes=showtimes,
            bookable_dates=bookable,
            closed_dates=closed,
            fetched_at=now_ist(),
        )

    def _parse_dates(self, data: dict[str, Any]) -> tuple[list[str], list[str]]:
        bookable: list[str] = []
        closed: list[str] = []
        for widget in _dicts(data.get("topStickyWidgets")):
            if widget.get("type") != "horizontal-block-list":
                continue
            for item in _dicts(widget.get("data")):
                code = _text(item.get("id"))
                if not DATE_CODE_RE.match(code):
                    continue
                status = DATE_STYLE.get(_text(item.get("styleId")), "UNKNOWN")
                (closed if status == "NOT_OPEN" else bookable).append(code)
        return dedupe(bookable), dedupe(closed)

    def _parse_shows(self, data: dict[str, Any], movie: MovieRef) -> tuple[list[Venue], list[Showtime]]:
        venues: dict[str, Venue] = {}
        showtimes: list[Showtime] = []

        for widget in _dicts(data.get("showtimeWidgets")):
            if widget.get("type") != "groupList":
                continue
            for group in _dicts(widget.get("data")):
                if group.get("type") != "venueGroup":
                    continue
                for card in _dicts(group.get("data")):
                    if card.get("type") != "venue-card":
                        continue
                    addl = card.get("additionalData")
                    addl = addl if isinstance(addl, dict) else {}
                    code = _text(addl.get("venueCode")) or _text(card.get("id"))
                    name = _text(addl.get("venueName")) or _text(card.get("title"))
                    if not code and not name:
                        continue
                    code = code or name
                    area = (
                        _text(addl.get("venueSubRegion"))
                        or _text(addl.get("subRegionName"))
                        or _text(addl.get("venueAddress"))
                        or _text(card.get("subtitle"))
                    )
                    venues.setdefault(code, Venue(code=code, name=name or code, area=area))
                    showtimes.extend(self._parse_showtimes(card, code, name or code, movie))

        return list(venues.values()), showtimes

    def _parse_showtimes(self, card: dict[str, Any], venue_code: str, venue_name: str,
                         movie: MovieRef) -> list[Showtime]:
        out: list[Showtime] = []
        for show in _dicts(card.get("showtimes")):
            sa = show.get("additionalData")
            sa = sa if isinstance(sa, dict) else {}

            date_code = _text(sa.get("showDateCode")) or _text(sa.get("dateCode"))
            if not DATE_CODE_RE.match(date_code):
                cutoff = _text(sa.get("cutOffDateTime"))
                date_code = cutoff[:8] if DATE_CODE_RE.match(cutoff[:8]) else ""

            fmt = clean_format(
                _text(show.get("screenAttr"))
                or _text(sa.get("attributes"))
                or _text(sa.get("screenAttr"))
            )

            out.append(
                Showtime(
                    venue_code=venue_code,
                    venue_name=venue_name,
                    session_id=_text(sa.get("sessionId")),
                    date_code=date_code,
                    time_label=_text(show.get("title")) or _text(sa.get("showTime")),
                    time_code=_text(sa.get("showTimeCode")),
                    format_label=fmt,
                    availability=self._availability(sa),
                    booking_url=self.booking_url(movie, date_code),
                )
            )
        return out

    def _availability(self, show_data: dict[str, Any]) -> Availability:
        """A show is AVAILABLE when *any* seat category can still be bought.

        No categories at all means the listing exists but isn't on sale yet —
        that's NOT_BOOKABLE, which is distinct from SOLD_OUT.
        """
        categories = list(_dicts(show_data.get("categories")))
        if not categories:
            return Availability.NOT_BOOKABLE

        states = [AVAIL_STATUS.get(str(c.get("availStatus", "")).strip()) for c in categories]
        known = [s for s in states if s is not None]
        if not known:
            return Availability.NOT_BOOKABLE
        if any(s is Availability.AVAILABLE for s in known):
            return Availability.AVAILABLE
        return Availability.SOLD_OUT

    def _merge_venue_formats(self, venues: list[Venue], showtimes: list[Showtime]) -> list[Venue]:
        """Attach the formats each venue actually runs, discovered from shows.

        This is why the format list in the UI is never hard-coded: it is
        whatever this movie is screening at that theatre today.
        """
        by_code: dict[str, list[str]] = {}
        for s in showtimes:
            if s.format_label:
                by_code.setdefault(s.venue_code, []).append(s.format_label)
        return [
            Venue(code=v.code, name=v.name, area=v.area, formats=tuple(sorted(dedupe(by_code.get(v.code, [])))))
            for v in sorted(venues, key=lambda v: v.name.lower())
        ]


__all__ = [
    "API_URL",
    "AVAIL_STATUS",
    "BookMyShowProvider",
    "DEFAULT_CITY",
    "REGIONS",
    "clean_format",
    "parse_listing_url",
    "region_for",
]
