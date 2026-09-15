"""Shared fixtures.

Two rules drive everything here:

1. **No network.** BookMyShow is replaced by a fake session that replays a
   payload shaped like the real one. Tests must be deterministic at 2am on a
   train, and must not poke a third party on every commit.
2. **No shared filesystem.** Every test gets its own ``data/`` directory via
   monkeypatched module paths, so a test run can never clobber real monitors.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from config.timezone import IST


# ──────────────────────────────────────────────────────────────────────────
# Isolated data directory
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Point every persistence path at a throwaway directory."""
    from config import store

    data = tmp_path / "data"
    data.mkdir()
    files = {
        "MONITORS_FILE": "monitors.json",
        "STATE_FILE": "state.json",
        "CATALOGUE_FILE": "catalogue.json",
        "HISTORY_FILE": "history.json",
        "SETTINGS_FILE": "settings.json",
        "DISCOVERY_FILE": "discovery.json",
    }
    for attr, name in files.items():
        monkeypatch.setattr(store, attr, data / name)
    monkeypatch.setattr(store, "DATA_DIR", data)

    # Re-point the modules that imported the paths by value.
    from monitor import state as state_mod

    monkeypatch.setattr(state_mod, "MONITORS_FILE", data / "monitors.json")
    monkeypatch.setattr(state_mod, "STATE_FILE", data / "state.json")

    # Never mirror to GitHub from a test.
    monkeypatch.setattr(store, "push_to_github", lambda *a, **k: False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return data


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    """Every ``AppTest`` run starts behind the authentication gate. Here the
    session is *restored* the way a returning browser's would be — except the
    exchange with Google is replaced by a fixed account — so the wizard and
    page tests exercise the app exactly as a signed-in person sees it.
    ``tests/test_auth.py`` switches this off to test the gate itself."""
    from auth import firebase, session

    user = session.AuthUser(uid="uid-test-1", email="tester@example.com",
                            display_name="Test Person", id_token="id.token",
                            refresh_token="refresh.token", expires_at=4102444800.0)

    def restore():
        import streamlit as st

        # Once per session, like the real one — so signing out stays signed out.
        if st.session_state.get("auth_restore_tried"):
            return None
        st.session_state["auth_restore_tried"] = True
        st.session_state[session.USER_KEY] = user
        return user

    monkeypatch.setattr(session, "restore", restore)
    # Never let a test reach Firebase: no key, and no transport.
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to call Firebase over the network")

    monkeypatch.setattr(firebase, "_post", no_network)
    return user


@pytest.fixture(autouse=True)
def no_live_discovery(monkeypatch):
    """The worker re-lists a city on its own while a monitor is unresolved
    (``monitor.discovery``). By default that listing is unreachable here —
    exactly what BookMyShow's bot check does to a blocked host — so every
    scenario in the suite also proves a failed discovery changes nothing.
    Tests of discovery itself replace ``discovery.get_provider``."""
    from monitor import discovery
    from platforms.base import PlatformError

    class Unreachable:
        slug = "bookmyshow"

        def list_movies(self, region_slug):
            raise PlatformError("no city listing in tests")

    monkeypatch.setattr(discovery, "get_provider", lambda slug: Unreachable())


@pytest.fixture(autouse=True)
def no_smtp(monkeypatch):
    """Fail loudly if a test ever tries to open a real SMTP connection."""
    import smtplib

    def explode(*args, **kwargs):
        raise AssertionError("a test tried to open a real SMTP connection")

    monkeypatch.setattr(smtplib, "SMTP_SSL", explode)


# ──────────────────────────────────────────────────────────────────────────
# BookMyShow payload builder
# ──────────────────────────────────────────────────────────────────────────
FIXTURES = Path(__file__).parent / "fixtures"


def build_payload(shows: list[dict], *, title: str = "Avengers: Endgame Encore",
                  bookable_dates: tuple[str, ...] = ("20260925",),
                  closed_dates: tuple[str, ...] = ("20260926",)) -> dict:
    """Assemble a response with the same widget shape the real API returns.

    Each entry in ``shows`` is
    ``{venue_code, venue_name, area, time, time_code, date, fmt, status}``
    where ``status`` is BookMyShow's per-category ``availStatus``
    (``"3"`` available, ``"0"`` sold out, or ``None`` for "no categories",
    which is how a not-yet-on-sale listing looks).
    """
    venues: dict[str, dict] = {}
    for s in shows:
        # Live payloads put the locality in the venue name and carry no
        # separate area field — see tools/bms_shape2.py output.
        display_name = s["venue_name"]
        if s.get("area"):
            display_name = f'{s["venue_name"]}: {s["area"]}'
        card = venues.setdefault(
            s["venue_code"],
            {
                "type": "venue-card",
                "additionalData": {
                    "venueCode": s["venue_code"],
                    "venueName": display_name,
                    "isFnBAvailable": True,
                },
                "showtimes": [],
            },
        )
        categories = (
            []
            if s.get("status") is None
            else [{"priceDesc": "RECLINER", "curPrice": "450", "availStatus": s["status"]}]
        )
        card["showtimes"].append(
            {
                "title": s["time"],
                "screenAttr": s.get("fmt", ""),
                "styleId": "green-pill-with-border",
                "additionalData": {
                    "sessionId": s.get("session_id", f"S{s['venue_code']}{s['time_code']}"),
                    "availStatus": s.get("status") or "",
                    "showDateCode": s.get("date", "20260925"),
                    "showDateTime": f"{s.get('date', '20260925')}{s['time_code']}",
                    "showTimeCode": s["time_code"],
                    "showTime": s["time"],
                    "categories": categories,
                },
            }
        )

    date_widgets = [
        {"id": code, "styleId": "date-default", "data": ["Fri", "25", "Sep"]}
        for code in bookable_dates
    ] + [
        {"id": code, "styleId": "date-disabled", "data": ["Sat", "26", "Sep"]}
        for code in closed_dates
    ]

    return {
        "data": {
            "topStickyWidgets": [
                {"type": "horizontal-block-list", "data": date_widgets},
                {
                    "type": "horizontal-text-list",
                    "data": [{"leftText": {"data": [{"components": [
                        {"text": title}, {"text": "English • Re-release"}]}]}}],
                },
            ],
            "bottomSheetData": {
                "format-selector": {
                    "widgets": [
                        {"type": "vertical-text-list",
                         "data": [{"styleId": "bottomsheet-subtitle", "text": title}]}
                    ]
                }
            },
            "header": {"title": {"styleId": "header-title-v2", "text": title}},
            "additionalData": {"eventCode": "ET00478890", "dateCode": bookable_dates[0]
                               if bookable_dates else ""},
            "showtimeWidgets": [
                # Live responses lead with an ad slot; the parser must skip it.
                {"type": "adtech", "data": [{"id": "AD_MOVIE_SHOWTIMES_CARD"}]},
                {"type": "groupList", "data": [{"type": "venueGroup", "data": list(venues.values())}]},
                {"type": "info", "data": []},
            ],
        }
    }


NOT_ON_SALE = [
    {"venue_code": "ALLU", "venue_name": "Allu Cinemas", "area": "Attapur, Hyderabad",
     "time": "07:30 PM", "time_code": "1930", "fmt": "DOLBY CINEMA", "status": None},
    {"venue_code": "AMB", "venue_name": "AMB Cinemas", "area": "Gachibowli, Hyderabad",
     "time": "08:00 PM", "time_code": "2000", "fmt": "HDR BY BARCO", "status": None},
]

SOLD_OUT = [{**s, "status": "0"} for s in NOT_ON_SALE]

ALLU_LIVE = [
    {**NOT_ON_SALE[0], "status": "3"},
    NOT_ON_SALE[1],  # AMB still not on sale — proves one theatre doesn't mute the other
]

ALLU_LIVE_EXTRA_SHOW = ALLU_LIVE + [
    {"venue_code": "ALLU", "venue_name": "Allu Cinemas", "area": "Attapur, Hyderabad",
     "time": "09:45 PM", "time_code": "2145", "fmt": "DOLBY CINEMA", "status": "3"},
]


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Replays a queue of responses and records every request."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        if not self.responses:
            raise AssertionError("FakeSession ran out of queued responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def provider_factory():
    """Build a BookMyShowProvider backed by queued responses, with no sleeping."""
    from platforms.bookmyshow import BookMyShowProvider

    def make(responses):
        queue = [
            r if isinstance(r, (FakeResponse, Exception)) else FakeResponse(200, r)
            for r in responses
        ]
        session = FakeSession(queue)
        provider = BookMyShowProvider(session=session, sleeper=lambda _s: None)
        provider.session = session  # exposed for assertions
        return provider

    return make


@pytest.fixture
def listing_url():
    return "https://in.bookmyshow.com/movies/hyderabad/avengers-endgame/buytickets/ET00478890"


@pytest.fixture
def make_monitor(listing_url):
    from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget

    def make(*, interval=10, until=None, targets=None, email="watcher@example.com"):
        return Monitor(
            movie=MovieRef(
                platform="bookmyshow",
                event_code="ET00478890",
                title="Avengers: Endgame Encore",
                region_code="HYD",
                region_slug="hyderabad",
                city="Hyderabad",
                source_url=listing_url,
            ),
            targets=targets
            or [
                TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema"),
                TheatreTarget("AMB", "AMB Cinemas", "Gachibowli, Hyderabad", ANY_FORMAT),
            ],
            interval_minutes=interval,
            monitor_until=until or datetime(2026, 9, 26, 23, 59, tzinfo=IST),
            notify_email=email,
        )

    return make


@pytest.fixture
def at():
    """A fixed 'now' so nothing in the suite depends on the wall clock."""
    return datetime(2026, 9, 25, 22, 20, tzinfo=IST)


@pytest.fixture
def later(at):
    def step(minutes: int) -> datetime:
        return at + timedelta(minutes=minutes)

    return step


# ──────────────────────────────────────────────────────────────────────────
# QUICKBOOK city listing — mirrors the live shape captured by
# tools/bms_shape.py: moviesData.BookMyShow.arrEvents[], each a movie *group*
# whose ChildEvents are the per-language / per-premium-format bookable events.
# ──────────────────────────────────────────────────────────────────────────
def build_quickbook(groups: list[dict]) -> dict:
    """`groups` = [{title, url, children: [{code, language, dimension, image}]}]"""
    events = []
    for g in groups:
        children = [
            {
                "EventCode": c["code"],
                "EventImageCode": c.get("image", f"{g['url']}-{c['code'].lower()}-1789"),
                "EventType": "MT",
                "EventLanguage": c.get("language", "Telugu"),
                "EventStatus": "NS",
                "EventName": c.get("name", g["title"]),
                "EventDimension": c.get("dimension", "2D"),
                "EventURL": c.get("url", g["url"]),
                "EventDate": "2026-09-10",
            }
            for c in g["children"]
        ]
        events.append({
            "EventGroup": g.get("group", "EG00000001"),
            "EventTitle": g["title"],
            "EventCode": g["children"][0]["code"],
            "EventURLTitle": g["url"],
            "ChildEvents": children,
        })
    return {
        "moviesData": {"BookMyShow": {"arrLanguages": [], "arrEvents": events}},
        "preferredLanguages": None,
        "cinemas": {"BookMyShow": {"aiVN": {"venues": [], "error": None}}},
    }


#: A film listed in two languages with premium-format variants in each — the
#: exact shape that made a naive parser emit six rows for one movie.
QUICKBOOK_HYD = build_quickbook([
    {
        "title": "Mandaadi", "url": "mandaadi", "children": [
            {"code": "ET00514261", "language": "Telugu", "dimension": "2D"},
            {"code": "ET00516384", "language": "Telugu", "dimension": "EPIQ"},
            {"code": "ET00516197", "language": "Telugu", "dimension": "DOLBY CINEMA 2D"},
            {"code": "ET00442702", "language": "Tamil", "dimension": "2D"},
            {"code": "ET00516312", "language": "Tamil", "dimension": "HDR By Barco"},
        ],
    },
    {
        "title": "Hanuman Ansh", "url": "hanuman-ansh", "children": [
            {"code": "ET00507738", "language": "Hindi", "dimension": "2D"},
        ],
    },
])

#: Detail requests one full sync of QUICKBOOK_HYD makes: every row's base
#: event *plus* its premium-format siblings (Telugu 2D + EPIQ + Dolby Cinema,
#: Tamil 2D + HDR, Hindi 2D). Six, not three, because a sibling event's
#: theatres are not in the base event's answer — see
#: ``platforms.bookmyshow._primary_children``.
DETAIL_REQUESTS_HYD = 6
