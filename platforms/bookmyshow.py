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

Each category (verified from a live read, Sept 2026) is a price tier::

    {"priceCode": "0002", "priceDesc": "GOLD", "curPrice": "295.00",
     "areaCatCode": "0000000002", "availStatus": "3", "seatLayout": true,
     "categoryRange": "1|2|3|4|5|6"}

That is the finest grain this endpoint has. It carries no rows, no seats and
no seat map — ``seatLayout`` is a flag that the web client may draw one,
from an endpoint this module does not know. ``parse_seat_categories`` reads
the categories onto each ``Showtime`` (from the same response — no second
request) and nothing finer.

This is an undocumented internal endpoint. It can change shape or start
refusing us without notice, which is exactly why every parse step below is
defensive and every failure raises :class:`PlatformError` rather than
returning an empty listing. An empty listing means "no shows"; a raised error
means "we don't know" — the monitoring engine treats those very differently.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from config.timezone import now_ist
from platforms.http import REQUEST_ERRORS, build_session, transport_name
from monitor.models import (
    Availability,
    MovieRef,
    SeatCategory,
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
ANY_EVENT_CODE_RE = re.compile(r"ET\d{6,}")

TIMEOUT = 20
MAX_ATTEMPTS = 3
BACKOFF_BASE = 2.0

#: Where the browse page for a city lives. Used both as a Referer for the
#: listing endpoints and as the HTML fallback strategy's target.
BROWSE_PATH = "/explore/movies-{slug}"


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


def _dig(node: Any, *keys: str) -> Any:
    """Follow a key path, returning None the moment it doesn't exist."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _walk_dicts(node: Any, depth: int = 0):
    """Yield every dict inside an arbitrarily nested payload."""
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item, depth + 1)


def _event_code_in(node: dict[str, Any]) -> str:
    """The ET code carried by a node, if it has one on a plausible key."""
    for key, value in node.items():
        if not isinstance(value, str):
            continue
        candidate = value.strip().upper()
        if EVENT_CODE_RE.match(candidate) and (
            "code" in key.lower() or "id" in key.lower() or key.lower().startswith("event")
        ):
            return candidate
    return ""


def _first_text(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [v.strip() for v in value if isinstance(v, str) and v.strip()]
            if parts:
                return ", ".join(dict.fromkeys(parts))
    return ""


def _first_url(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = _first_text(node, keys)
    if value.startswith("//"):
        return f"https:{value}"
    return value if value.startswith("http") else ""


#: Keys under which a showtime *could* carry its own link. Live payloads
#: (see tools/bms_shape2.py output, Sept 2026) carry none of these — a
#: showtime's ``cta`` is ``{"type": "showTimeRedirect"}`` with analytics only,
#: and the web client builds the seat-layout navigation in JavaScript. So this
#: is opportunistic: if BookMyShow ever publishes one, we use it; until then
#: the showtime links to the date's booking page, which is derived, not made up.
SHOW_URL_KEYS = ("url", "webUrl", "deeplink", "deepLink", "redirectUrl",
                 "navigationUrl", "seatLayoutUrl", "href")


def is_bookmyshow_url(value: str) -> bool:
    """Only an absolute https link on a BookMyShow host is ever used."""
    try:
        parsed = urlparse((value or "").strip())
    except ValueError:
        return False
    host = (parsed.netloc or "").lower()
    return parsed.scheme == "https" and (host == "bookmyshow.com" or host.endswith(".bookmyshow.com"))


def published_show_url(show: dict[str, Any]) -> str:
    """A show-level link, if and only if the payload itself contains one."""
    for node in (show, show.get("cta"), _dig(show, "cta", "additionalData"),
                 show.get("additionalData")):
        if not isinstance(node, dict):
            continue
        for key in SHOW_URL_KEYS:
            value = _text(node.get(key))
            if value.startswith("//"):
                value = f"https:{value}"
            if is_bookmyshow_url(value):
                return value
    return ""


def _price(value: Any) -> str:
    """A price as the payload wrote it — "295.00" — or a number's text."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return _text(value)


def parse_seat_categories(show_data: Any) -> tuple[SeatCategory, ...]:
    """The seat categories of one showtime's ``additionalData``.

    Reads only what the payload carries — name, code, price, area code,
    status, the seat-layout flag. Anything that is not a list of category
    dicts, or a category with neither a name nor a code, yields nothing:
    an empty tuple is "no categories published", which is what a listing
    not yet on sale looks like, never a guess.
    """
    if not isinstance(show_data, dict):
        return ()
    out: list[SeatCategory] = []
    for cat in _dicts(show_data.get("categories")):
        name = _text(cat.get("priceDesc")) or _text(cat.get("categoryName")) or _text(cat.get("name"))
        code = _text(cat.get("priceCode")) or _text(cat.get("categoryCode"))
        if not name and not code:
            continue
        status = AVAIL_STATUS.get(str(cat.get("availStatus", "")).strip())
        out.append(SeatCategory(
            code=code,
            name=name or code,
            availability=status or Availability.NOT_BOOKABLE,
            price=_price(cat.get("curPrice")),
            area_code=_text(cat.get("areaCatCode")),
            has_seat_layout=cat.get("seatLayout") is True or _text(cat.get("seatLayout")).lower() == "true",
        ))
    return tuple(out)


#: Script blocks that carry a page's server-rendered data.
_EMBEDDED_JSON_RE = re.compile(
    r"<script[^>]*(?:id=[\"'](?:__NEXT_DATA__|__NUXT_DATA__)[\"']|"
    r"type=[\"']application/json[\"'])[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_ASSIGNED_JSON_RE = re.compile(
    r"(?:window\.__INITIAL_STATE__|window\.__PRELOADED_STATE__|window\.__DATA__)\s*=\s*(\{.*?\})\s*;?\s*</script>",
    re.DOTALL,
)


def _embedded_json(html: str):
    """Yield each parseable JSON blob embedded in a page."""
    for pattern in (_EMBEDDED_JSON_RE, _ASSIGNED_JSON_RE):
        for match in pattern.finditer(html or ""):
            blob = match.group(1).strip()
            if not blob.startswith(("{", "[")):
                continue
            try:
                yield json.loads(blob)
            except ValueError:
                continue


#: BookMyShow serves posters keyed by the ``EventImageCode`` the listing
#: hands us. Verified against the live CDN from a runner: the
#: discovery-catalog path 404s, this one returns the image.
POSTER_CDN = "https://in.bmscdn.com/events/moviecard/{code}.jpg"


def poster_url(image_code: str) -> str:
    """A poster URL for a real image code, or '' — never a placeholder."""
    code = (image_code or "").strip()
    return POSTER_CDN.format(code=code) if code else ""


#: Dimensions that are just "the normal screening". Anything else is a
#: premium-format variant of the same film in the same language.
BASE_DIMENSIONS = {"2D", ""}


def _primary_children(children: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One *row* per language, with the plain screening as its canonical event.

    BookMyShow lists a film once per language *and* once per premium format:
    Avengers Endgame: Encore appears as English 2D, English 3D, English 4DX
    3D, English MS-Infinity Vision (twice) and Telugu 2D. Showing all six in a
    movie picker is noise — the user wants "Avengers Endgame: Encore,
    English", and then to pick the theatre and format.

    The premium siblings are **not** interchangeable with the base event,
    though: each is its own event with its own showtimes, and the base
    event's answer does not include them. Verified live (bms-diagnose, Sept
    2026): the English 2D event returned 3 theatres, while the five siblings
    held the other 6 — every PVR, AMB and Cinepolis screen. So the siblings
    are kept on the row as :attr:`MovieRef.variants` and swept by
    :meth:`BookMyShowProvider.fetch`; collapsing them out of the *picker* is
    fine, dropping them from the *data* is how a theatre list ends up a third
    of the truth.
    """
    by_language: dict[str, dict[str, Any]] = {}
    for child in children:
        language = _text(child.get("EventLanguage"))
        dimension = _text(child.get("EventDimension")).upper()
        current = by_language.get(language)
        if current is None:
            by_language[language] = child
            continue
        # Prefer the plain screening as the canonical event for a language.
        current_dim = _text(current.get("EventDimension")).upper()
        if dimension in BASE_DIMENSIONS and current_dim not in BASE_DIMENSIONS:
            by_language[language] = child
    return by_language


def _sibling_events(children: list[dict[str, Any]], primary: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """``(event_code, format label)`` for every other event in the primary's language."""
    language = _text(primary.get("EventLanguage"))
    primary_code = _text(primary.get("EventCode")).upper()
    out: list[tuple[str, str]] = []
    seen = {primary_code}
    for child in children:
        code = _text(child.get("EventCode")).upper()
        if _text(child.get("EventLanguage")) != language or code in seen:
            continue
        if not EVENT_CODE_RE.match(code):
            continue
        seen.add(code)
        out.append((code, clean_format(_text(child.get("EventDimension")))))
    return tuple(out)


@dataclass(frozen=True)
class ListingResult:
    """What one listing strategy came back with.

    ``authoritative`` says whether an empty ``movies`` list can be trusted to
    mean "this city has nothing on". A structured endpoint whose wrapper we
    found and parsed can vouch for that; an HTML scrape that simply matched
    nothing cannot, and must let the next strategy try.
    """

    movies: list[MovieRef]
    authoritative: bool = False


def split_venue_name(full_name: str) -> tuple[str, str]:
    """'AMB Cinemas: Gachibowli' -> ('AMB Cinemas', 'Gachibowli').

    BookMyShow encodes the locality in the venue name rather than a separate
    field, so the split is how the UI gets a clean title and an area line.
    Names without the separator are returned unchanged with no area, rather
    than having one guessed for them.
    """
    name = (full_name or "").strip()
    if ":" in name:
        head, _, tail = name.partition(":")
        head, tail = head.strip(), tail.strip()
        if head and tail:
            return head, tail
    return name, ""


#: BookMyShow's spellings of Marvel Studios' Infinity Vision screen — "MS -
#: Infinity Vision", "Ms-Infinity Vsn 3D", "MS - Infinity Vision 3D" — as one
#: label each for 2D and 3D. Only a string that names it is ever mapped;
#: nothing is inferred from a theatre or a film.
INFINITY_VISION_RE = re.compile(r"^\s*ms\s*-?\s*infinity\s*(?:vision|vsn)\.?\s*(3\s*d|2\s*d)?\s*$", re.I)
INFINITY_VISION = "Infinity Vision"


def clean_format(raw: str) -> str:
    """'2D DOLBY CINEMA' -> 'Dolby Cinema 2D' is overkill; we just tidy case.

    BookMyShow returns things like ``"DOLBY CINEMA"``, ``"ICE 4K LASER"`` or
    ``"2D"``. We title-case words but keep known all-caps tokens intact so
    "IMAX" doesn't become "Imax". The one spelling we canonicalise is
    Infinity Vision (``INFINITY_VISION_RE``), which BookMyShow writes three
    ways for one screen.
    """
    infinity = INFINITY_VISION_RE.match(raw or "")
    if infinity:
        dim = (infinity.group(1) or "").replace(" ", "").upper() or "2D"
        return f"{INFINITY_VISION} {dim}"
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

    def __init__(self, session=None, sleeper=time.sleep) -> None:
        # Default to the TLS-impersonating client; BookMyShow refuses plain
        # `requests` outright (platforms/http.py documents the evidence).
        self._session = session if session is not None else build_session(sleeper)
        self._sleep = sleeper

    @property
    def transport(self) -> str:
        return transport_name(self._session)

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

    def _browse_headers(self, region: tuple[str, str, str, str, str], *,
                        accept: str = "application/json, text/plain, */*") -> dict[str, str]:
        """Headers for city-wide browsing, where there is no event yet."""
        region_code, region_slug, lat, lon, geohash = region
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{SITE}{BROWSE_PATH.format(slug=region_slug)}",
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
            # The legacy endpoints key the city off this cookie rather than a header.
            "Cookie": f"Rgn=Code%3D{region_code}; bmsId=; _region_slug={region_slug}",
        }

    def _raw_get(self, url: str, headers: dict[str, str], params: dict[str, str] | None = None):
        """A single unretried request. Returns the response or raises.

        Used by the catalogue strategies, which each get one shot: when one
        approach is refused we want to fall through to the next rather than
        spend three retries proving the same block.
        """
        return self._session.get(url, headers=headers, params=params or {}, timeout=TIMEOUT)

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
            except REQUEST_ERRORS as exc:
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

    # ── City catalogue ───────────────────────────────────────────────────
    #
    # BookMyShow has no public "what's showing in this city" API. Several
    # internal ones are known to exist, they are not documented, and which of
    # them answers depends on the caller's network and the day. Rather than
    # betting the feature on one guess, we try a chain of approaches and use
    # the first that returns real data; `probe_listing` reports what every
    # single one did, which is how the working strategy was established from
    # a CI runner (see README §Catalogue).
    #
    # The chain deliberately ends with an HTML strategy: parsing the page a
    # browser loads is the slowest and ugliest option, but it is the one that
    # cannot be turned off without also breaking the website.

    def listing_strategies(self):
        """(name, callable) pairs returning ListingResult, best-evidenced first.

        QUICKBOOK leads because it is the one that actually answered from a
        runner, and it answers with the richest data: 39 movie groups for
        Hyderabad, each with per-language child events, dimensions, censor
        rating, release date and a poster image code.
        """
        return [
            ("quickbook", self._listing_quickbook),
            ("browse-html", self._listing_browse_html),
            # explore-api answered HTTP 500 on every runner attempt, so it is
            # last: kept because it costs nothing to try when the other two
            # have already failed, not because it has ever worked.
            ("explore-api", self._listing_explore_api),
        ]

    def list_movies(self, region_slug: str = DEFAULT_CITY) -> list[MovieRef]:
        """Every movie currently listed in a city.

        Raises :class:`PlatformError` when no strategy could get an answer —
        which is emphatically different from returning ``[]``, meaning
        "we looked and the city has nothing listed".
        """
        region = region_for(region_slug)
        failures: list[str] = []

        for name, strategy in self.listing_strategies():
            try:
                result = strategy(region)
            except PlatformError as exc:
                failures.append(f"{name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad parser must not end the chain
                failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
                continue

            # An empty list from a strategy that genuinely parsed the listing
            # is an *answer*: this city has nothing on. Only fall through when
            # the strategy couldn't vouch for its own emptiness, or the city
            # would look unreachable every time it was simply quiet.
            if result.movies or result.authoritative:
                print(f"[bookmyshow] catalogue via '{name}': {len(result.movies)} movie(s)")
                return result.movies
            failures.append(f"{name}: returned nothing")

        blocked = any("refused" in f or "403" in f for f in failures)
        message = "Could not read the BookMyShow city listing — " + "; ".join(failures)
        raise (PlatformBlocked if blocked else PlatformError)(message)

    def probe_listing(self, region_slug: str = DEFAULT_CITY) -> list[dict[str, Any]]:
        """Run every strategy and report what each did. Diagnostics only."""
        region = region_for(region_slug)
        report = []
        for name, strategy in self.listing_strategies():
            entry: dict[str, Any] = {"strategy": name}
            try:
                result = strategy(region)
            except Exception as exc:  # noqa: BLE001 - reporting, not flow control
                entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            else:
                entry.update(
                    ok=bool(result.movies) or result.authoritative,
                    count=len(result.movies),
                    authoritative=result.authoritative,
                    sample=[f"{m.title} [{m.event_code}]" for m in result.movies[:5]],
                )
            report.append(entry)
        return report

    def _check_listing_response(self, resp, name: str) -> None:
        if resp.status_code == 200:
            return
        if resp.status_code in (401, 403):
            raise PlatformBlocked(f"refused (HTTP {resp.status_code})")
        raise PlatformError(f"HTTP {resp.status_code}")

    def _listing_explore_api(self, region: tuple[str, str, str, str, str]) -> ListingResult:
        """The JSON the browse page's own client calls."""
        region_code, region_slug, *_ = region
        url = f"{SITE}/api/explore/v1/discover/movies-{region_slug}"
        try:
            resp = self._raw_get(url, self._browse_headers(region), {"regionCode": region_code})
        except REQUEST_ERRORS as exc:
            raise PlatformError(f"{type(exc).__name__}") from None
        self._check_listing_response(resp, "explore-api")
        try:
            payload = resp.json()
        except ValueError:
            raise PlatformError("not JSON") from None
        return ListingResult(self._movies_from_json(payload, region))

    def _listing_quickbook(self, region: tuple[str, str, str, str, str]) -> ListingResult:
        """The endpoint the city page's quick-book rail uses.

        Confirmed shape (see ``tools/bms_shape.py`` output)::

            moviesData.BookMyShow.arrEvents[]      one entry per movie *group*
              EventTitle      "Mandaadi"
              EventCode       "ET00442702"          group-level code
              ChildEvents[]                          one per language/format
                EventCode     "ET00514261"           the bookable code
                EventName     "Mandaadi (Telugu)"
                EventLanguage "Telugu"
                EventDimension "2D"
                EventImageCode "mandaadi-et00514261-…"

        The child events are what you can actually book, so each becomes its
        own selectable movie — that is how "Kantara (Telugu)" and
        "Kantara (Hindi)" end up as separate, separately-watchable rows.
        """
        region_code, region_slug, *_ = region
        headers = self._browse_headers(region)
        headers["Cookie"] = f"Rgn=Code%3D{region_code}"
        try:
            resp = self._raw_get(
                f"{SITE}/serv/getData",
                headers,
                {"cmd": "QUICKBOOK", "type": "MT", "f": "json"},
            )
        except REQUEST_ERRORS as exc:
            raise PlatformError(f"{type(exc).__name__}") from None
        self._check_listing_response(resp, "quickbook")
        try:
            payload = resp.json()
        except ValueError:
            raise PlatformError("not JSON") from None

        events = _dig(payload, "moviesData", "BookMyShow", "arrEvents")
        if isinstance(events, list):
            # We found the wrapper, so we can vouch for an empty result.
            return ListingResult(self._movies_from_quickbook(payload, region), authoritative=True)
        # The wrapper moved: fall back to a generic tree walk and let a later
        # strategy speak if this finds nothing.
        return ListingResult(self._movies_from_json(payload, region))

    def _movies_from_quickbook(self, payload: Any,
                               region: tuple[str, str, str, str, str]) -> list[MovieRef]:
        groups = payload
        for key in ("moviesData", "BookMyShow", "arrEvents"):
            groups = groups.get(key) if isinstance(groups, dict) else None
            if groups is None:
                return []
        if not isinstance(groups, list):
            return []

        out: dict[str, MovieRef] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_title = _text(group.get("EventTitle"))
            children = [c for c in _dicts(group.get("ChildEvents"))] or [group]

            for language, child in _primary_children(children).items():
                code = _text(child.get("EventCode")).upper()
                if not EVENT_CODE_RE.match(code) or code in out:
                    continue
                title = group_title or _text(child.get("EventName"))
                if not title:
                    continue
                out[code] = MovieRef(
                    platform=self.slug,
                    event_code=code,
                    title=title,
                    region_code=region[0],
                    region_slug=region[1],
                    city=region[1].replace("-", " ").title(),
                    language=language,
                    poster_url=poster_url(_text(child.get("EventImageCode"))),
                    source_url=self._listing_url(
                        region[1], code,
                        _text(child.get("EventURL")) or _text(group.get("EventURLTitle")),
                    ),
                    variants=_sibling_events(children, child),
                )
        return list(out.values())

    def _listing_browse_html(self, region: tuple[str, str, str, str, str]) -> ListingResult:
        """Parse the browse page itself.

        BookMyShow renders the city's movie list server-side and ships the
        data as JSON inside the document. We look for that payload first and
        only fall back to scraping links, because the embedded JSON carries
        titles and languages while the links carry only codes.
        """
        _, region_slug, *_ = region
        url = f"{SITE}{BROWSE_PATH.format(slug=region_slug)}"
        headers = self._browse_headers(
            region, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
        try:
            resp = self._raw_get(url, headers)
        except REQUEST_ERRORS as exc:
            raise PlatformError(f"{type(exc).__name__}") from None
        self._check_listing_response(resp, "browse-html")

        html = resp.text or ""
        for payload in _embedded_json(html):
            movies = self._movies_from_json(payload, region)
            if movies:
                return ListingResult(movies)
        # Never authoritative: matching nothing in a page is as likely to mean
        # "the markup changed" as "the city is empty".
        return ListingResult(self._movies_from_links(html, region))

    # ── Turning whatever came back into MovieRefs ────────────────────────
    def _movies_from_json(self, payload: Any, region: tuple[str, str, str, str, str]) -> list[MovieRef]:
        """Walk an arbitrary payload for objects that look like a movie.

        These endpoints nest their results differently and rename their
        wrappers between releases, so instead of hard-coding a path we walk
        the tree for any dict carrying an event code plus a title. That
        survives re-nesting, which a fixed path does not.
        """
        found: dict[str, MovieRef] = {}
        for node in _walk_dicts(payload):
            code = _event_code_in(node)
            if not code or code in found:
                continue
            title = _first_text(node, ("EventTitle", "eventTitle", "title", "name",
                                       "EventName", "eventName", "movieName"))
            if not title:
                continue
            found[code] = MovieRef(
                platform=self.slug,
                event_code=code,
                title=title,
                region_code=region[0],
                region_slug=region[1],
                city=region[1].replace("-", " ").title(),
                language=_first_text(node, ("EventLanguage", "eventLanguage", "language",
                                            "languages", "movieLanguage")),
                poster_url=_first_url(node, ("EventImageURL", "eventImageUrl", "imageUrl",
                                             "posterUrl", "image", "portraitImageUrl")),
                source_url=self._listing_url(region[1], code),
            )
        return list(found.values())

    def _movies_from_links(self, html: str, region: tuple[str, str, str, str, str]) -> list[MovieRef]:
        """Last resort: event codes scraped out of the markup.

        Titles recovered this way are approximate — they come from the URL
        slug — so this strategy is only reached when nothing structured was
        available, and the sync step overwrites these titles with the real
        ones as soon as each movie's own listing is read.
        """
        out: dict[str, MovieRef] = {}
        pattern = re.compile(
            r'href="[^"]*?/movies/(?P<slug>[a-z0-9-]+)/(?P<title>[a-z0-9-]+)/(?P<code>ET\d{6,})',
            re.IGNORECASE,
        )
        for match in pattern.finditer(html):
            code = match.group("code").upper()
            if code in out:
                continue
            out[code] = MovieRef(
                platform=self.slug,
                event_code=code,
                title=match.group("title").replace("-", " ").title(),
                region_code=region[0],
                region_slug=region[1],
                city=region[1].replace("-", " ").title(),
                source_url=self._listing_url(region[1], code),
            )
        if out:
            return list(out.values())

        # Even the links changed shape: fall back to bare codes with no title,
        # which the sync step will fill in. Better than claiming the city is empty.
        return [
            MovieRef(
                platform=self.slug, event_code=code, title="",
                region_code=region[0], region_slug=region[1],
                city=region[1].replace("-", " ").title(),
                source_url=self._listing_url(region[1], code),
            )
            for code in dict.fromkeys(ANY_EVENT_CODE_RE.findall(html))
        ]

    def _listing_url(self, region_slug: str, event_code: str, title_slug: str = "") -> str:
        """The page a human would book on.

        BookMyShow accepts the form without a title slug, so the slug is
        included only when the listing gave us one — never guessed.
        """
        if title_slug:
            return f"{SITE}/movies/{region_slug}/{title_slug}/buytickets/{event_code}"
        return f"{SITE}/movies/{region_slug}/buytickets/{event_code}"

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
                resolved = replace(
                    self._movie_ref(
                        payload,
                        {"event_code": movie.event_code, "region_slug": movie.region_slug, "date_code": date_code},
                        region,
                        source_url=movie.source_url,
                        fallback_title=movie.title,
                    ),
                    variants=movie.variants,
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

        # The premium-format siblings (3D, 4DX, IMAX, EPIQ…) are separate
        # events whose venues are *not* in the base event's answer — see
        # `_primary_children`. Sweep them too, so the snapshot is the whole
        # film. Best-effort per sibling: a refused 4DX event must not lose the
        # 2D answer we already have, and the checker treats what is returned
        # as the truth for the venues it contains.
        for code in resolved.variant_codes:
            sibling = replace(resolved, event_code=code, variants=(),
                              source_url=self._sibling_url(resolved, code))
            fallback = dict(resolved.variants).get(code, "")
            for date_code in wanted:
                self._sleep(1.0)
                try:
                    payload = self._get(code, date_code, region)
                except PlatformError as exc:
                    errors.append(f"{code}: {exc}")
                    continue
                snap = self._snapshot(payload, sibling, default_format=fallback)
                for v in snap.venues:
                    venues.setdefault(v.code, v)
                showtimes.extend(snap.showtimes)
                bookable.extend(snap.bookable_dates)
                closed.extend(snap.closed_dates)

        return Snapshot(
            movie=resolved,
            venues=self._merge_venue_formats(list(venues.values()), showtimes),
            showtimes=showtimes,
            bookable_dates=dedupe(bookable),
            closed_dates=[d for d in dedupe(closed) if d not in set(bookable)],
            fetched_at=now_ist(),
        )

    def _sibling_url(self, movie: MovieRef, code: str) -> str:
        """The sibling's own booking page: the base page with its code swapped.

        BookMyShow's buytickets URLs end in the event code, and the form
        without a title slug is one it documents as accepted (`_listing_url`),
        so nothing here is guessed.
        """
        base = re.sub(r"/\d{8}$", "", (movie.source_url or "").strip().rstrip("/"))
        if base.upper().endswith("/" + movie.event_code.upper()):
            return base[: -len(movie.event_code)] + code
        return self._listing_url(movie.region_slug, code)

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

    def venue_booking_url(self, movie: MovieRef, venue_code: str, date_code: str = "") -> str:
        """The booking page for one theatre, on one date.

        Verified on a runner (bms-diagnose, 2026-09-15): BookMyShow answers
        ``/buytickets/<movie-slug>-<city>/cinema-<region>-<VENUE>-MT/<date>``
        with a redirect to its canonical theatre page
        (``/cinemas/hyde/amb-cinemas-gachibowli/buytickets/AMBH/20260925``,
        titled "AMB Cinemas: Gachibowli | Movie Showtimes & Ticket Booking"),
        while a bogus venue code lands on the generic cinemas index. The
        movie slug is the one from the listing's own URL — nothing here is
        invented, and without a venue code or slug the movie page is used.
        """
        code = (venue_code or "").strip().upper()
        match = re.search(r"/movies/[^/]+/([^/]+)/buytickets/", movie.source_url or "")
        slug = match.group(1) if match else ""
        if not code or not slug or not movie.region_slug:
            return self.booking_url(movie, date_code)
        url = f"{SITE}/buytickets/{slug}-{movie.region_slug}/cinema-{movie.region_slug[:4]}-{code}-MT"
        return f"{url}/{date_code}" if DATE_CODE_RE.match(date_code or "") else url

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

        header = data.get("header")
        if not title and isinstance(header, dict):
            header_title = header.get("title")
            if isinstance(header_title, dict):
                title = _text(header_title.get("text"))

        meta = payload.get("metadata")
        analytics = meta.get("analytics") if isinstance(meta, dict) else None
        if isinstance(analytics, dict):
            title = title or _text(analytics.get("title"))
            language = language or " · ".join(
                x for x in (_text(analytics.get("language")), _text(analytics.get("format"))) if x
            )

        for key in ("eventTitle", "title", "movieName", "name"):
            if not title:
                title = _text(data.get(key))
        for key in ("posterUrl", "imageUrl", "eventImageUrl"):
            if not poster:
                poster = _text(data.get(key))

        return title, language, poster

    def _snapshot(self, payload: dict[str, Any], movie: MovieRef, *,
                  default_format: str = "") -> Snapshot:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        bookable, closed = self._parse_dates(data)
        venues, showtimes = self._parse_shows(data, movie, default_format=default_format)
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

    def _parse_shows(self, data: dict[str, Any], movie: MovieRef, *,
                     default_format: str = "") -> tuple[list[Venue], list[Showtime]]:
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
                    # Live payloads carry no area field; the locality is the
                    # tail of the venue name ("AMB Cinemas: Gachibowli").
                    short_name, area = split_venue_name(name or code)
                    area = (
                        _text(addl.get("venueSubRegion"))
                        or _text(addl.get("subRegionName"))
                        or area
                    )
                    venues.setdefault(code, Venue(code=code, name=short_name, area=area))
                    showtimes.extend(self._parse_showtimes(card, code, short_name, movie,
                                                           default_format=default_format))

        return list(venues.values()), showtimes

    def _parse_showtimes(self, card: dict[str, Any], venue_code: str, venue_name: str,
                         movie: MovieRef, *, default_format: str = "") -> list[Showtime]:
        """``default_format`` names the show when the card carries no screen
        attribute — a sibling event's own format label ("4DX 3D"), which is
        real BookMyShow data for that event, never a guess."""
        out: list[Showtime] = []
        for show in _dicts(card.get("showtimes")):
            sa = show.get("additionalData")
            sa = sa if isinstance(sa, dict) else {}

            date_code = _text(sa.get("showDateCode")) or _text(sa.get("dateCode"))
            if not DATE_CODE_RE.match(date_code):
                cutoff = _text(sa.get("cutOffDateTime"))
                date_code = cutoff[:8] if DATE_CODE_RE.match(cutoff[:8]) else ""

            raw_fmt = (_text(show.get("screenAttr")) or _text(sa.get("attributes"))
                       or _text(sa.get("screenAttr")))
            # the sibling's label may have been stored before canonicalisation
            fmt = clean_format(raw_fmt) or clean_format(default_format)

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
                    booking_url=published_show_url(show) or self.booking_url(movie, date_code),
                    categories=parse_seat_categories(sa),
                    format_raw=raw_fmt,
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
            # Some payloads report only the showtime-level status.
            overall = AVAIL_STATUS.get(str(show_data.get("availStatus", "")).strip())
            return overall or Availability.NOT_BOOKABLE

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
        cats: dict[str, dict[str, SeatCategory]] = {}
        for s in showtimes:
            if s.format_label:
                by_code.setdefault(s.venue_code, []).append(s.format_label)
            for c in s.categories:
                # one entry per platform identity — two "GOLD"s stay two
                cats.setdefault(s.venue_code, {}).setdefault(c.key, c)
        return [
            Venue(code=v.code, name=v.name, area=v.area, formats=tuple(sorted(dedupe(by_code.get(v.code, [])))),
                  categories=tuple(cats.get(v.code, {}).values()))
            for v in sorted(venues, key=lambda v: v.name.lower())
        ]


__all__ = [
    "API_URL",
    "AVAIL_STATUS",
    "BookMyShowProvider",
    "DEFAULT_CITY",
    "REGIONS",
    "INFINITY_VISION",
    "clean_format",
    "is_bookmyshow_url",
    "parse_listing_url",
    "parse_seat_categories",
    "published_show_url",
    "region_for",
]
