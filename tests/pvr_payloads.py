"""PVR INOX responses shaped exactly like the live API.

Every key and value type here is copied from the raw captures committed by
karanb192/pvr-inox-radar (``tests/fixtures/*.json``, live 21 Aug 2026, MIT)
— the same captures ``platforms/pvr_inox.py`` was written against:

* the envelope ``{"status": 302, "code": 10001, "result": "success",
  "msg": "Record found", "output": {...}}`` on HTTP 200;
* ``content/city``: ``output.ot`` / ``output.pc`` city rows with ``id`` (int),
  ``name``, ``state``, ``cinemaCount``, ``lat``/``lng`` strings;
* ``content/cinemas``: ``output.c`` rows with ``theatreId`` (string),
  ``name``, ``cityName``, ``address1``, ``showCount`` and a ``screens`` map
  of screenId → ``{screenId, screenName, screenType}``;
* ``content/nowshowing``: ``output.mv`` films, each with ``films`` prints;
* ``content/csessions``: ``output.cinemaMovieSessions`` blocks —
  ``movieRe`` (``id`` = filmCommonCode, ``n``, ``films`` prints) and
  ``experienceSessions`` of ``shows`` with ``sessionId`` an **int**,
  ``showTimeStamp`` epoch ms, and ``movieFormat`` / ``filmFormat`` /
  ``screenType`` / ``soundFormat`` exactly as captured (``filmFormat`` is
  ``""`` for a 4DX 2D show; a 3D show on an INSIGNIA screen says so only in
  ``movieFormat``).

The one shape not in any capture is a closed date's body: it is only known
to be "a JSON body whose ``status`` is not 302". ``closed()`` uses the
capture's own envelope keys with a non-302 status and no output.
"""

from __future__ import annotations

from datetime import datetime

from config.timezone import IST
from tests.conftest import FakeResponse

CITY = "Hyderabad"


def ok(output: dict) -> dict:
    return {"status": 302, "code": 10001, "result": "success", "msg": "Record found", "output": output}


def closed() -> dict:
    return {"status": 400, "code": 10002, "result": "error", "msg": "No Record found", "output": None}


def city_row(name: str = CITY, city_id: int = 32, count: int = 19) -> dict:
    return {"id": city_id, "name": name, "region": "SOUTH-A", "hasSubCities": False, "vakaoo": False,
            "state": "TELANGANA", "formats": "", "cinemaCount": count, "subcities": [],
            "lat": "17.1827790564", "lng": "78.60351551", "image": "", "imageR": ""}


def city_payload(*rows: dict) -> dict:
    rows = rows or (city_row(),)
    return ok({"cc": None, "nb": [], "ot": list(rows), "pc": list(rows), "nc": []})


def cinema(theatre_id: str, name: str, *, shows: int = 50, screens: dict | None = None) -> dict:
    screens = screens or {"901": {"screenId": 901, "screenName": "AUDI 01", "screenType": "Premium",
                                  "handicap": True, "vakaao": False, "occupancy": 0, "admits": 0, "minSeats": 5}}
    return {"theatreId": theatre_id, "name": name, "cityName": CITY, "address1": "Mall, Hyderabad",
            "latitude": "17.44", "longitude": "78.38", "alertTxt": "", "like": "false", "distance": 5525,
            "distanceText": "5.5 km away", "showCount": shows, "movieRes": [], "fbDeliveryOnSeat": False,
            "handicapRamp": False, "handicap": True, "adFree": False, "adFreeText": "", "vakaao": False,
            "screens": screens, "vakaaoScreens": [], "foodAvailable": True, "isPosAvailable": False,
            "amenities": "", "stype": "N", "miv": "", "mih": "", "vs": None}


def cinemas_payload(*rows: dict) -> dict:
    return ok({"cinemaMovieSessions": None, "map": None, "defaultMap": None, "fmsg": "", "tnc": [],
               "defaultDistance": 5, "maxDistance": 25, "slider": None, "displayMsg": "", "filmRe": None,
               "pu": None, "ph": None, "c": list(rows)})


def film_print(film_id: str, common: str, language: str, fmt: str = "", *, name: str = "") -> dict:
    return {"filmId": film_id, "filmType": "F", "filmName": name or f"FILM {common} ({language.upper()} {fmt})".strip(),
            "format": fmt, "releaseDate": "Sep 25, 2026", "language": language, "filmCommonCode": common,
            "filmCommonName": f"FILM {common}", "filmNameWeb": f"FILM {common}", "stopTimeInMin": 0,
            "xlCinema": False, "subtitle": "", "vakaao": False, "nowshowing": True, "mtrailerurl": ""}


def nowshowing_payload(*films: list[dict]) -> dict:
    mv = [{"films": prints, "campaign": None, "filmName": prints[0]["filmName"], "format": "", "releaseDate": "",
           "showCount": 10, "experiences": [], "specialTags": []} for prints in films]
    return ok({"offers": [], "specialTags": [], "mv": mv, "banners": [], "hollywood": []})


def stamp(date_code: str, hhmm: str) -> int:
    """Epoch ms of an IST show time, as ``showTimeStamp`` carries it."""
    return int(datetime.strptime(date_code + hhmm, "%Y%m%d%H%M").replace(tzinfo=IST).timestamp() * 1000)


def show(theatre_id: str, session: int, movie_id: str, date_code: str, hhmm: str, *,
         language: str = "English", movie_format: str = "", film_format: str | None = None,
         screen_type: str = "", screen: str = "AUDI 01", sound: str = "", status: int = 1,
         code: str = "76BE43", text: str = "Available", encrypted: str | None = "FAKE(TOKEN)abc==") -> dict:
    hour = int(hhmm[:2])
    label = f"{(hour % 12) or 12:02d}:{hhmm[2:]} {'AM' if hour < 12 else 'PM'}"
    start = stamp(date_code, hhmm)
    iso = f"{date_code[:4]}-{date_code[4:6]}-{date_code[6:]}"
    return {"theatreId": theatre_id, "sessionId": session, "screenId": 595, "movieId": movie_id, "filmType": "F",
            "showDateStr": iso, "showTimeStamp": start, "stopTimeStamp": start + 2700000,
            "endTimeStamp": start + 7440000, "showDate": iso, "showTime": label, "endTime": "",
            "cvpApp": False, "showClass": "RP-RE-", "screenType": screen_type, "screenName": screen,
            "soundFormat": sound, "filmFormat": movie_format if film_format is None else film_format,
            "pgrpId": None, "comments": "", "language": language, "movieFormat": movie_format,
            "handicap": False, "handicapRamp": False, "subtitle": True, "statusCode": code, "status": status,
            "statusTxt": text, "alertTxt": None, "xl": False, "encrypted": encrypted, "flexi": False}


def block(common: str, prints: list[dict], experiences: dict[str, list[dict]], *, title: str = "") -> dict:
    """One film at one cinema: ``experiences`` maps the experience label
    ("", "INSIGNIA", "IMAX", "INSIGNIA [ATMOS]") to its shows."""
    return {
        "movieRe": {"filmName": title or prints[0]["filmName"], "promotion": False, "metaCampaign": False,
                    "adult": False, "releaseDate": "Sep 25, 2026", "specialTags": [], "films": prints,
                    "experiences": [{"expKey": "atmos", "expName": "DOLBY ATMOS", "expUrl": ""}],
                    "releaseDateLk": None, "certificateLk": None, "filmIds": [p["filmId"] for p in prints],
                    "showCount": sum(len(v) for v in experiences.values()), "adultMessage": "",
                    "restrictedCities": "", "id": common, "n": f"FILM {common}", "othergenres": "",
                    "surl": "", "miv": "", "mih": "", "otherlanguages": "", "mfs": [], "grs": [], "ce": "UA",
                    "mlength": "2h 30m", "mtrailerurl": ""},
        "showCount": sum(len(v) for v in experiences.values()),
        "experienceSessions": [
            {"experience": label, "experienceKey": label.lower(), "experienceUrl": "", "showCount": len(shows),
             "shows": shows}
            for label, shows in experiences.items()
        ],
    }


def sessions_payload(*blocks: dict) -> dict:
    return ok({"cinemaRe": None, "showCount": 0, "days": [], "nearbyThreaters": None,
               "cinemaMovieSessions": list(blocks), "xlh": {}, "pu": None, "ph": None})


class FakePostSession:
    """Replays queued responses to POSTs and records every one."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json or {}, "headers": headers or {}})
        if not self.responses:
            raise AssertionError("FakePostSession ran out of queued responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(200, item)

    def operations(self) -> list[str]:
        return [c["url"].rsplit("/booking/", 1)[-1] for c in self.calls]

    def cinema_dates(self) -> list[tuple[str, str]]:
        return [(c["json"].get("cid"), c["json"].get("dated")) for c in self.calls
                if c["url"].endswith("content/csessions")]
