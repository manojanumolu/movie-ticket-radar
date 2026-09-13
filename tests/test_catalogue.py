"""City listing, catalogue sync, and the states the UI must not confuse.

The fixture in ``conftest.build_quickbook`` mirrors the payload captured from
a live runner by ``tools/bms_shape.py``, so these tests exercise the real
shape without touching the network.
"""

from __future__ import annotations

import pytest

from monitor import catalogue
from monitor.catalogue import SyncStatus
from platforms.base import PlatformBlocked, PlatformError
from platforms.bookmyshow import poster_url, split_venue_name
from tests.conftest import (
    ALLU_LIVE,
    QUICKBOOK_HYD,
    FakeResponse,
    build_payload,
    build_quickbook,
)


# ──────────────────────────────────────────────────────────────────────────
# Listing a city  (Test 2 — Hyderabad catalogue loading)
# ──────────────────────────────────────────────────────────────────────────
def test_quickbook_lists_the_city(provider_factory):
    provider = provider_factory([QUICKBOOK_HYD])
    movies = provider.list_movies("hyderabad")

    titles = sorted((m.title, m.language) for m in movies)
    # Five child events for Mandaadi collapse to one per language; the
    # premium-format variants are reached via screenAttr in step 4 instead.
    assert titles == [
        ("Hanuman Ansh", "Hindi"),
        ("Mandaadi", "Tamil"),
        ("Mandaadi", "Telugu"),
    ]

    telugu = next(m for m in movies if m.language == "Telugu")
    assert telugu.event_code == "ET00514261"      # the plain 2D event, not EPIQ
    assert telugu.region_code == "HYD"
    assert telugu.city == "Hyderabad"
    assert telugu.poster_url.startswith("https://in.bmscdn.com/events/moviecard/")
    assert telugu.source_url == (
        "https://in.bookmyshow.com/movies/hyderabad/mandaadi/buytickets/ET00514261"
    )


def test_premium_variant_wins_when_there_is_no_plain_screening(provider_factory):
    payload = build_quickbook([
        {"title": "Only IMAX", "url": "only-imax", "children": [
            {"code": "ET00999001", "language": "Telugu", "dimension": "IMAX 3D"},
        ]},
    ])
    movies = provider_factory([payload]).list_movies("hyderabad")
    assert [m.event_code for m in movies] == ["ET00999001"]


def test_a_city_with_nothing_on_is_empty_not_an_error(provider_factory):
    provider = provider_factory([build_quickbook([])])
    # An empty city listing is an answer. It must not raise.
    assert provider.list_movies("hyderabad") == []


def test_every_strategy_refused_raises_blocked(provider_factory):
    provider = provider_factory([FakeResponse(403), FakeResponse(403), FakeResponse(403)])
    with pytest.raises(PlatformBlocked):
        provider.list_movies("hyderabad")


def test_listing_falls_through_to_the_next_strategy(provider_factory):
    """QUICKBOOK failing must not end the chain."""
    html = (
        '<html><body>'
        '<a href="/movies/hyderabad/kantara/ET00111111">Kantara</a>'
        '<a href="/movies/hyderabad/salaar/ET00222222">Salaar</a>'
        "</body></html>"
    )
    provider = provider_factory([FakeResponse(500), FakeResponse(200, payload=None, text=html)])
    movies = provider.list_movies("hyderabad")
    assert sorted(m.event_code for m in movies) == ["ET00111111", "ET00222222"]
    assert sorted(m.title for m in movies) == ["Kantara", "Salaar"]


def test_probe_reports_every_strategy(provider_factory):
    provider = provider_factory([QUICKBOOK_HYD])
    report = provider.probe_listing("hyderabad")
    assert [r["strategy"] for r in report] == ["quickbook", "browse-html", "explore-api"]
    assert report[0]["ok"] is True
    assert report[0]["count"] == 3


# ──────────────────────────────────────────────────────────────────────────
# Parsing helpers verified against live payloads
# ──────────────────────────────────────────────────────────────────────────
def test_venue_name_splits_into_name_and_area():
    assert split_venue_name("AMB Cinemas: Gachibowli") == ("AMB Cinemas", "Gachibowli")
    assert split_venue_name("Prasads Multiplex: Hyderabad") == ("Prasads Multiplex", "Hyderabad")
    # No separator: keep the name, invent no area.
    assert split_venue_name("Miraj Cinemas") == ("Miraj Cinemas", "")


def test_poster_url_is_empty_without_a_real_code():
    assert poster_url("") == ""
    assert poster_url("mandaadi-et00514261-1789") == (
        "https://in.bmscdn.com/events/moviecard/mandaadi-et00514261-1789.jpg"
    )


# ──────────────────────────────────────────────────────────────────────────
# Syncing  (Tests 13, 14 — empty catalogue, no theatres)
# ──────────────────────────────────────────────────────────────────────────
def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)


def test_sync_stores_movies_and_records_success(provider_factory, monkeypatch):
    provider = provider_factory([QUICKBOOK_HYD])
    _patch_provider(monkeypatch, provider)

    result = catalogue.sync_region("hyderabad", mirror=False, detail=False)

    assert result["ok"] and result["status"] is SyncStatus.OK
    assert result["movies"] == 3
    entries = catalogue.list_entries("hyderabad")
    assert len(entries) == 3
    assert all(not catalogue.is_detailed(e) for e in entries)

    state = catalogue.sync_state("hyderabad")
    assert state["status"] is SyncStatus.OK
    assert state["movie_count"] == 3
    assert state["at"] is not None


def test_sync_resolves_theatres_and_formats(provider_factory, monkeypatch):
    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * 3)
    _patch_provider(monkeypatch, provider)

    result = catalogue.sync_region("hyderabad", mirror=False, detail=True)

    assert result["detailed"] == 3 and result["failed"] == 0
    entry = catalogue.list_entries("hyderabad")[0]
    venues = catalogue.venues_from_entry(entry)
    assert sorted(v.name for v in venues) == ["AMB Cinemas", "Allu Cinemas"]
    allu = next(v for v in venues if v.name == "Allu Cinemas")
    assert allu.area == "Attapur, Hyderabad"
    # Formats are discovered from the shows, never hard-coded.
    assert allu.formats == ("Dolby Cinema",)


def test_a_blocked_sync_records_blocked_and_keeps_the_old_catalogue(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)
    assert len(catalogue.list_entries("hyderabad")) == 3

    _patch_provider(monkeypatch, provider_factory([FakeResponse(403)] * 3))
    result = catalogue.sync_region("hyderabad", mirror=False, detail=False)

    assert result["ok"] is False
    assert result["status"] is SyncStatus.BLOCKED
    # Yesterday's movies are still there — a bot check never empties the grid.
    assert len(catalogue.list_entries("hyderabad")) == 3
    assert catalogue.sync_state("hyderabad")["status"] is SyncStatus.BLOCKED


def test_an_empty_city_is_recorded_as_empty_not_blocked(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([build_quickbook([])]))
    result = catalogue.sync_region("hyderabad", mirror=False, detail=False)
    assert result["status"] is SyncStatus.EMPTY
    assert catalogue.sync_state("hyderabad")["status"] is SyncStatus.EMPTY
    assert SyncStatus.EMPTY.is_failure is False
    assert SyncStatus.BLOCKED.is_failure is True


def test_one_movie_failing_detail_does_not_abort_the_sync(provider_factory, monkeypatch):
    provider = provider_factory([
        QUICKBOOK_HYD,
        build_payload(ALLU_LIVE),
        FakeResponse(403),              # second movie refused
        build_payload(ALLU_LIVE),
    ])
    _patch_provider(monkeypatch, provider)
    result = catalogue.sync_region("hyderabad", mirror=False, detail=True)
    assert result["ok"] is True
    assert result["detailed"] == 2 and result["failed"] == 1


def test_sync_keeps_detail_it_already_has(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * 3))
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    assert all(catalogue.is_detailed(e) for e in catalogue.list_entries("hyderabad"))

    # A later listing-only sync must not throw the theatre detail away.
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entries = catalogue.list_entries("hyderabad")
    assert all(catalogue.is_detailed(e) for e in entries)
    assert catalogue.venues_from_entry(entries[0])


def test_entries_are_scoped_per_city(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)
    assert len(catalogue.list_entries("hyderabad")) == 3
    assert catalogue.list_entries("chennai") == []
    assert len(catalogue.search_entries("mandaadi", "hyderabad")) == 2
    assert catalogue.search_entries("mandaadi", "chennai") == []


def test_ensure_detail_reports_the_problem_instead_of_an_empty_list(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)
    movie_id = catalogue.movie_from_entry(catalogue.list_entries("hyderabad")[0]).id

    _patch_provider(monkeypatch, provider_factory([FakeResponse(403)]))
    entry, problem = catalogue.ensure_detail(movie_id, mirror=False)

    assert entry is not None
    assert catalogue.venues_from_entry(entry) == []
    assert "bot check" in problem       # and the caller shows this, not "no theatres"


def test_ensure_detail_fetches_when_missing(provider_factory, monkeypatch):
    _patch_provider(monkeypatch, provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)
    movie_id = catalogue.movie_from_entry(catalogue.list_entries("hyderabad")[0]).id

    _patch_provider(monkeypatch, provider_factory([build_payload(ALLU_LIVE)]))
    entry, problem = catalogue.ensure_detail(movie_id, mirror=False)

    assert problem == ""
    assert len(catalogue.venues_from_entry(entry)) == 2
