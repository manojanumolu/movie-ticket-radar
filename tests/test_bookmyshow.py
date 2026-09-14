"""Provider tests — parsing, and above all, failing honestly."""

from __future__ import annotations

import pytest
import requests

from monitor.models import Availability
from platforms.base import PlatformBlocked, PlatformError
from platforms.bookmyshow import clean_format, parse_listing_url, region_for
from tests.conftest import ALLU_LIVE, NOT_ON_SALE, FakeResponse, build_payload


# ── URL parsing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url,event,region",
    [
        ("https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890", "ET00478890", "hyderabad"),
        ("https://in.bookmyshow.com/movies/hyderabad/buytickets/ET00478890/20260925", "ET00478890", "hyderabad"),
        ("https://in.bookmyshow.com/movies/chennai/y/buytickets/ET00111111", "ET00111111", "chennai"),
        ("ET00478890", "ET00478890", "hyderabad"),
    ],
)
def test_parse_listing_url(url, event, region):
    parsed = parse_listing_url(url)
    assert parsed["event_code"] == event
    assert parsed["region_slug"] == region


def test_parse_listing_url_keeps_the_date():
    assert parse_listing_url(
        "https://in.bookmyshow.com/movies/hyderabad/buytickets/ET00478890/20260925"
    )["date_code"] == "20260925"


@pytest.mark.parametrize("bad", ["", "https://example.com/movies/hyderabad",
                                 "https://in.bookmyshow.com/movies/hyderabad/no-code"])
def test_parse_listing_url_rejects_junk(bad):
    with pytest.raises(PlatformError):
        parse_listing_url(bad)


def test_unknown_city_falls_back_to_hyderabad():
    assert region_for("atlantis")[0] == "HYD"
    assert region_for("hyderabad")[0] == "HYD"


def test_clean_format_keeps_acronyms():
    assert clean_format("DOLBY CINEMA") == "Dolby Cinema"
    assert clean_format("imax 2d") == "IMAX 2D"
    assert clean_format("") == ""


# ── Parsing a real-shaped payload ────────────────────────────────────────
def test_resolve_extracts_movie_venues_and_formats(provider_factory, listing_url):
    provider = provider_factory([build_payload(ALLU_LIVE)])
    snap = provider.resolve(listing_url)

    assert snap.movie.title == "Avengers: Endgame Encore"
    assert snap.movie.event_code == "ET00478890"
    assert snap.movie.region_code == "HYD"

    names = sorted(v.name for v in snap.venues)
    assert names == ["AMB Cinemas", "Allu Cinemas"]

    allu = next(v for v in snap.venues if v.code == "ALLU")
    assert allu.area == "Attapur, Hyderabad"
    # Formats are discovered from the shows, not hard-coded anywhere.
    assert allu.formats == ("Dolby Cinema",)

    assert snap.bookable_dates == ["20260925"]
    assert snap.closed_dates == ["20260926"]


def test_availability_mapping(provider_factory, listing_url):
    provider = provider_factory([build_payload(ALLU_LIVE)])
    snap = provider.resolve(listing_url)
    by_venue = {s.venue_code: s.availability for s in snap.showtimes}
    assert by_venue["ALLU"] is Availability.AVAILABLE
    # No seat categories at all is "not on sale yet", not "sold out".
    assert by_venue["AMB"] is Availability.NOT_BOOKABLE


def test_filling_fast_counts_as_available(provider_factory, listing_url):
    shows = [{**NOT_ON_SALE[0], "status": "2"}]
    provider = provider_factory([build_payload(shows)])
    snap = provider.resolve(listing_url)
    assert snap.showtimes[0].availability is Availability.AVAILABLE


def test_sold_out_when_every_category_is_full(provider_factory, listing_url):
    shows = [{**NOT_ON_SALE[0], "status": "0"}]
    provider = provider_factory([build_payload(shows)])
    assert provider.resolve(listing_url).showtimes[0].availability is Availability.SOLD_OUT


def test_region_headers_are_sent(provider_factory, listing_url):
    provider = provider_factory([build_payload(ALLU_LIVE)])
    provider.resolve(listing_url)
    headers = provider.session.calls[0]["headers"]
    assert headers["x-region-code"] == "HYD"
    assert headers["x-region-slug"] == "hyderabad"
    assert provider.session.calls[0]["params"]["eventCode"] == "ET00478890"


# ── Failure handling: the part that must never lie ───────────────────────
def test_cloudflare_403_raises_blocked_not_empty(provider_factory, listing_url):
    provider = provider_factory([FakeResponse(403, text="<html>Attention Required! | Cloudflare</html>")])
    with pytest.raises(PlatformBlocked):
        provider.resolve(listing_url)
    # And it does not retry a bot check.
    assert len(provider.session.calls) == 1


def test_timeouts_retry_then_give_up(provider_factory, listing_url):
    provider = provider_factory([requests.Timeout("slow"), requests.Timeout("slow"),
                                 requests.Timeout("slow")])
    with pytest.raises(PlatformError) as exc:
        provider.resolve(listing_url)
    assert len(provider.session.calls) == 3
    assert "Timeout" in str(exc.value)


def test_transient_500_recovers(provider_factory, listing_url):
    provider = provider_factory([FakeResponse(500), build_payload(ALLU_LIVE)])
    snap = provider.resolve(listing_url)
    assert len(snap.showtimes) == 2
    assert len(provider.session.calls) == 2


def test_rate_limit_is_retried(provider_factory, listing_url):
    provider = provider_factory([FakeResponse(429), build_payload(ALLU_LIVE)])
    assert provider.resolve(listing_url).showtimes


def test_malformed_json_raises(provider_factory, listing_url):
    provider = provider_factory([FakeResponse(200, payload=None, text="<html>oops</html>")])
    with pytest.raises(PlatformError):
        provider.resolve(listing_url)


def test_unexpected_shape_yields_no_shows_not_a_crash(provider_factory, listing_url):
    """A restructured payload must degrade to 'nothing listed', not explode."""
    provider = provider_factory([{"data": {"showtimeWidgets": "not-a-list"}}])
    snap = provider.resolve(listing_url)
    assert snap.showtimes == []
    assert snap.venues == []
    # Title is unknown, so it falls back to the event code — never invented.
    assert snap.movie.title == "ET00478890"


def test_404_is_an_error_not_a_no(provider_factory, listing_url):
    provider = provider_factory([FakeResponse(404)])
    with pytest.raises(PlatformError) as exc:
        provider.resolve(listing_url)
    assert "404" in str(exc.value)


# ── Booking URLs are derived, never invented ─────────────────────────────
def test_booking_url_appends_the_date(provider_factory, listing_url):
    provider = provider_factory([build_payload(ALLU_LIVE)])
    movie = provider.resolve(listing_url).movie
    assert provider.booking_url(movie, "20260925") == f"{listing_url}/20260925"
    assert provider.booking_url(movie) == listing_url


def test_booking_url_does_not_double_up_dates(provider_factory):
    from monitor.models import MovieRef

    provider = provider_factory([])
    movie = MovieRef(
        platform="bookmyshow", event_code="ET1", title="t", region_code="HYD",
        region_slug="hyderabad",
        source_url="https://in.bookmyshow.com/movies/hyderabad/buytickets/ET1/20260101",
    )
    assert provider.booking_url(movie, "20260925").endswith("/ET1/20260925")


def test_booking_url_without_a_source_is_still_a_real_bms_path(provider_factory):
    from monitor.models import MovieRef

    provider = provider_factory([])
    movie = MovieRef(platform="bookmyshow", event_code="ET9", title="t",
                     region_code="HYD", region_slug="hyderabad")
    assert provider.booking_url(movie) == (
        "https://in.bookmyshow.com/movies/hyderabad/buytickets/ET9"
    )


# ──────────────────────────────────────────────────────────────────────────
# Show-level links: only what the payload actually publishes
# ──────────────────────────────────────────────────────────────────────────
def test_showtimes_link_to_the_date_page_when_the_payload_has_no_deep_link(provider_factory, listing_url):
    """The live payload (tools/bms_shape2.py, Sept 2026) carries no URL per
    showtime — its cta is {"type": "showTimeRedirect"} with analytics only.
    So every showtime links to the derived date page, never a guessed pattern."""
    snap = provider_factory([build_payload(ALLU_LIVE)]).resolve(listing_url)
    for show in snap.showtimes:
        assert show.booking_url == f"{listing_url}/{show.date_code}"


def test_a_published_show_url_is_picked_up(provider_factory, listing_url):
    payload = build_payload(ALLU_LIVE)
    card = payload["data"]["showtimeWidgets"][1]["data"][0]["data"][0]
    deep = "https://in.bookmyshow.com/buytickets/x-hyderabad/movie-hyd-ET00478890-MT/20260925?sid=SALLU1930"
    card["showtimes"][0]["cta"] = {"type": "showTimeRedirect", "additionalData": {"webUrl": deep}}
    snap = provider_factory([payload]).resolve(listing_url)
    allu = [s for s in snap.showtimes if s.venue_code == "ALLU"][0]
    assert allu.booking_url == deep


def test_a_non_bookmyshow_show_url_is_ignored(provider_factory, listing_url):
    payload = build_payload(ALLU_LIVE)
    card = payload["data"]["showtimeWidgets"][1]["data"][0]["data"][0]
    card["showtimes"][0]["cta"] = {"additionalData": {"url": "https://tracker.example.com/r?x=1"}}
    card["showtimes"][0]["additionalData"]["deeplink"] = "bms://seatlayout/118452"
    snap = provider_factory([payload]).resolve(listing_url)
    allu = [s for s in snap.showtimes if s.venue_code == "ALLU"][0]
    assert allu.booking_url == f"{listing_url}/20260925"


def test_target_result_time_links_follow_showtime_order(provider_factory, listing_url, make_monitor):
    from monitor.checker import evaluate_target
    from tests.conftest import ALLU_LIVE_EXTRA_SHOW

    monitor = make_monitor()
    snap = provider_factory([build_payload(ALLU_LIVE_EXTRA_SHOW)]).resolve(listing_url)
    result = evaluate_target(monitor, monitor.targets[0], snap)
    assert result.time_labels == ["07:30 PM", "09:45 PM"]
    assert result.time_links == [["07:30 PM", f"{listing_url}/20260925"],
                                 ["09:45 PM", f"{listing_url}/20260925"]]


# ──────────────────────────────────────────────────────────────────────────
# The alert links to the theatre's own booking page (verified shape)
# ──────────────────────────────────────────────────────────────────────────
def test_venue_booking_url_is_the_theatre_page_for_that_date(provider_factory, listing_url):
    provider = provider_factory([build_payload(ALLU_LIVE)])
    movie = provider.resolve(listing_url).movie
    assert provider.venue_booking_url(movie, "AMBH", "20260925") == (
        "https://in.bookmyshow.com/buytickets/avengers-endgame-hyderabad/cinema-hyde-AMBH-MT/20260925")
    assert provider.venue_booking_url(movie, "ambh") == (
        "https://in.bookmyshow.com/buytickets/avengers-endgame-hyderabad/cinema-hyde-AMBH-MT")
    # No venue, or no slug to build from: the movie page, never a guess.
    assert provider.venue_booking_url(movie, "", "20260925") == f"{listing_url}/20260925"
    from dataclasses import replace
    bare = replace(movie, source_url="")
    assert provider.venue_booking_url(bare, "AMBH", "20260925").endswith("/buytickets/ET00478890/20260925")


def test_a_live_result_links_to_the_theatre_not_the_movie(provider_factory, listing_url, make_monitor):
    from monitor.checker import evaluate_target
    from notifications.email import is_configured  # noqa: F401 - keeps the import path honest

    monitor = make_monitor()
    snap = provider_factory([build_payload(ALLU_LIVE)]).resolve(listing_url)
    result = evaluate_target(monitor, monitor.targets[0], snap)
    assert result.availability is Availability.AVAILABLE
    assert result.booking_url == (
        "https://in.bookmyshow.com/buytickets/avengers-endgame-hyderabad/cinema-hyde-ALLU-MT/20260925")
    # A theatre still waiting for release links to its page too, undated.
    waiting = evaluate_target(monitor, monitor.targets[0].__class__("GHOST", "Ghost", "", "Any format"), snap)
    assert waiting.booking_url.endswith("/cinema-hyde-GHOST-MT")
