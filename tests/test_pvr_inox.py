"""PVR INOX provider: parsing, formats, availability, dates, failures, links.

All responses come from ``tests/pvr_payloads.py``, which copies the live
API's field names and types (see its docstring). The provider is never on
the network here: a fake session answers every POST and records it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from config.timezone import IST
from monitor.models import Availability, MovieRef, TheatreTarget
from platforms import is_platform_url
from platforms.base import PlatformBlocked, PlatformError
from platforms.pvr_inox import (
    MAX_REQUESTS_PER_READ,
    MIN_INTERVAL,
    SITE,
    PvrInoxProvider,
    event_format,
    film_key,
    is_pvr_url,
    screen_class,
    seat_page,
)
from tests.conftest import FakeResponse
from tests.pvr_payloads import (
    FakePostSession,
    block,
    cinema,
    cinemas_payload,
    city_payload,
    city_row,
    closed,
    film_print,
    nowshowing_payload,
    sessions_payload,
    show,
)

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=IST)
D1, D2 = "20260925", "20260926"
WINDOW = ["20260925", "20260926", "20260927", "20260928", "20260929", "20260930", "20261001"]
COMMON = "35288"
EN_2D, EN_IMAX, HI_2D = film_print("38431", COMMON, "English"), film_print("38460", COMMON, "English", "IMAX"), \
    film_print("38433", COMMON, "Hindi")
FILM_EN = MovieRef("pvr_inox", f"{COMMON}-ENGLISH", "FILM 35288", "32", "hyderabad", city="Hyderabad",
                   language="English", source_url=SITE)
FILM_HI = MovieRef("pvr_inox", f"{COMMON}-HINDI", "FILM 35288", "32", "hyderabad", city="Hyderabad",
                   language="Hindi", source_url=SITE)


def provider(responses: list, *, wall: datetime = NOW) -> tuple[PvrInoxProvider, FakePostSession, list[float]]:
    session, sleeps = FakePostSession(responses), []
    return PvrInoxProvider(session=session, sleeper=sleeps.append, clock=lambda: 0.0, wall=lambda: wall), \
        session, sleeps


def one_film(*shows_by_experience, prints=None, common=COMMON, title="") -> dict:
    """A csessions answer with one film block."""
    experiences: dict[str, list] = {}
    for label, shows in shows_by_experience:
        experiences.setdefault(label, []).extend(shows)
    return sessions_payload(block(common, prints or [EN_2D, EN_IMAX, HI_2D], experiences, title=title))


# ──────────────────────────────────────────────────────────────────────────
# Transport: the envelope, refusals, pacing, headers
# ──────────────────────────────────────────────────────────────────────────
def test_http_200_with_body_status_302_is_a_listing():
    p, session, _ = provider([one_film(("", [show("101", 45373, "38431", D1, "1000")]))])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101"])
    [s] = snap.showtimes
    assert (s.venue_code, s.session_id, s.date_code, s.time_label, s.time_code) == ("101", "45373", D1, "10:00 AM", "1000")
    assert snap.bookable_dates == [D1] and snap.closed_dates == []
    assert session.operations() == ["content/csessions"]


def test_a_non_302_body_is_a_date_not_on_sale_not_an_error():
    p, _, _ = provider([closed()])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101"])
    assert snap.showtimes == [] and snap.venues == [] and snap.closed_dates == [D1]


@pytest.mark.parametrize("status", [403, 429, 401])
def test_a_refusal_raises_blocked_and_nothing_more_is_sent(status):
    p, session, _ = provider([FakeResponse(status, {}), one_film()])
    with pytest.raises(PlatformBlocked):
        p.fetch(FILM_EN, [D1, D2], venue_codes=["101"])
    assert len(session.calls) == 1                      # the second date was never asked
    with pytest.raises(PlatformBlocked):                # …nor is anything, during the cooldown
        p.fetch(FILM_EN, [D1], venue_codes=["101"])
    assert len(session.calls) == 1


def test_a_302_without_a_listing_is_malformed_never_no_shows():
    p, _, _ = provider([{"status": 302, "code": 10001, "result": "success", "msg": "Record found",
                         "output": {"cinemaRe": None, "days": []}}])
    with pytest.raises(PlatformError, match="shape changed"):
        p.fetch(FILM_EN, [D1], venue_codes=["101"])


def test_a_non_json_200_is_an_error():
    p, _, _ = provider([FakeResponse(200, None, text="<html>captcha</html>")])
    with pytest.raises(PlatformError):
        p.fetch(FILM_EN, [D1], venue_codes=["101"])


def test_requests_are_paced_sequentially_and_never_retried():
    p, session, sleeps = provider([closed(), closed(), closed()])
    p.fetch(FILM_EN, [D1, D2, "20260927"], venue_codes=["101"])
    assert len(session.calls) == 3 and sleeps == [MIN_INTERVAL, MIN_INTERVAL]


def test_only_the_public_web_clients_headers_are_sent():
    p, session, _ = provider([closed()])
    p.fetch(FILM_EN, [D1], venue_codes=["101"])
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer "              # the site's anonymous, empty bearer
    assert headers["city"] == "Hyderabad" and headers["chain"] == "PVR"
    assert not any(k.lower() in ("cookie", "x-api-key", "x-forwarded-for") for k in headers)
    assert session.calls[0]["url"] == "https://api3.pvrcinemas.com/api/v1/booking/content/csessions"
    assert session.calls[0]["json"] == {"city": "Hyderabad", "cid": "101", "lat": "17.1827790564",
                                        "lng": "78.60351551", "dated": "2026-09-25", "qr": "NO",
                                        "cineType": "", "cineTypeQR": ""}


# ──────────────────────────────────────────────────────────────────────────
# City and cinema discovery
# ──────────────────────────────────────────────────────────────────────────
def test_hyderabad_is_found_by_its_id_in_the_city_list():
    p, session, _ = provider([city_payload(city_row("Chennai", 9, 16), city_row())])
    record = p.city_record("hyderabad")
    assert (record["id"], record["cinemaCount"]) == (32, 19) and session.operations() == ["content/city"]


def test_a_city_list_without_hyderabad_is_an_error():
    p, _, _ = provider([city_payload(city_row("Chennai", 9, 16))])
    with pytest.raises(PlatformError, match="does not include Hyderabad"):
        p.city_record("hyderabad")


def test_cinemas_are_discovered_from_the_api_with_their_ids():
    rows = [cinema("101", "PVR Test Mall, Hyderabad"), cinema("202", "INOX Test Centre, Hyderabad")]
    p, session, _ = provider([city_payload(city_row(count=2)), cinemas_payload(*rows)])
    venues = p.list_venues("hyderabad")
    assert [(v.code, v.name) for v in venues] == [("101", "PVR Test Mall, Hyderabad"),
                                                   ("202", "INOX Test Centre, Hyderabad")]
    assert session.operations() == ["content/city", "content/cinemas"]


def test_a_cinema_list_shorter_than_the_citys_own_count_is_incomplete():
    p, _, _ = provider([city_payload(city_row(count=19)), cinemas_payload(cinema("101", "A"), cinema("202", "B"))])
    with pytest.raises(PlatformError, match="2 of the 19"):
        p.list_venues("hyderabad")


def test_films_are_one_row_per_film_and_language_never_per_print():
    p, _, _ = provider([nowshowing_payload([EN_2D, EN_IMAX, HI_2D], [film_print("9", "777", "Telugu")])])
    rows = p.list_movies("hyderabad")
    assert sorted(m.id for m in rows) == ["pvr_inox:35288-ENGLISH", "pvr_inox:35288-HINDI", "pvr_inox:777-TELUGU"]
    assert {m.platform for m in rows} == {"pvr_inox"} and {m.variants for m in rows} == {()}


def test_an_unknown_city_is_refused_before_any_request():
    p, session, _ = provider([])
    with pytest.raises(PlatformError, match="not set up"):
        p.city_record("chennai")
    assert session.calls == []


# ──────────────────────────────────────────────────────────────────────────
# Which shows are this film's
# ──────────────────────────────────────────────────────────────────────────
def test_every_print_of_the_film_matches_including_one_first_seen_later():
    new_print = film_print("39999", COMMON, "English", "IMAX 3D")          # never seen before
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000")]),
                                 ("IMAX", [show("101", 2, "38460", D1, "1300", movie_format="IMAX")]),
                                 ("IMAX", [show("101", 3, "39999", D1, "1900", movie_format="IMAX 3D")]),
                                 prints=[EN_2D, EN_IMAX, HI_2D, new_print])])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101"])
    assert sorted(s.session_id for s in snap.showtimes) == ["1", "2", "3"]


def test_another_film_at_the_same_cinema_is_not_this_one():
    other = block("5555", [film_print("50", "5555", "English")], {"": [show("101", 9, "50", D1, "1000")]})
    mine = block(COMMON, [EN_2D], {"": [show("101", 1, "38431", D1, "1100")]})
    p, _, _ = provider([sessions_payload(other, mine)])
    assert [s.session_id for s in p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes] == ["1"]


def test_the_shows_own_language_beats_the_block_title():
    """A block titled in one language can hold shows in another; the show's
    ``language`` is authoritative."""
    hindi_titled = one_film(("", [show("101", 1, "38431", D1, "1000", language="English"),
                                  show("101", 2, "38433", D1, "1400", language="Hindi")]),
                            title="FILM 35288 (HINDI)")
    p, _, _ = provider([hindi_titled, hindi_titled])
    assert [s.session_id for s in p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes] == ["1"]
    assert [s.session_id for s in p.fetch(FILM_HI, [D1], venue_codes=["101"]).showtimes] == ["2"]


def test_a_session_listed_twice_is_one_show():
    dup = show("101", 45373, "38431", D1, "1000")
    p, _, _ = provider([one_film(("", [dup]), ("INSIGNIA", [dup]))])
    assert len(p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes) == 1


def test_an_empty_show_list_is_an_open_date_with_nothing_on():
    p, _, _ = provider([sessions_payload()])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101"])
    assert snap.showtimes == [] and snap.bookable_dates == [D1] and snap.venues == []


def test_a_show_without_its_date_is_malformed():
    broken = show("101", 1, "38431", D1, "1000")
    broken["showDate"] = ""
    p, _, _ = provider([one_film(("", [broken]))])
    with pytest.raises(PlatformError):
        p.fetch(FILM_EN, [D1], venue_codes=["101"])


# ──────────────────────────────────────────────────────────────────────────
# Formats: the movie's format vs the screen it plays on
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("movie_format, expected", [
    ("", "2D"), ("3D", "3D"), ("IMAX", "IMAX 2D"), ("4DX", "4DX 2D"), ("3D 4DX", "4DX 3D"),
    ("3D ICE", "ICE 3D"), ("3D ATMOS", "3D"), ("ATMOS", "2D"), ("IMAX 3D", "IMAX 3D"),
])
def test_movie_format_is_canonical_and_read_from_movieformat_alone(movie_format, expected):
    assert event_format(movie_format) == expected


@pytest.mark.parametrize("experience, screen_type, expected", [
    ("INSIGNIA", "INSIGNIA", "INSIGNIA"), ("INSIGNIA [ATMOS]", "INSIGNIA", "INSIGNIA"), ("GOLD", "GOLD", "GOLD"),
    ("P[XL]", "", "PXL"), ("", "", ""), ("IMAX", "IMAX", ""), ("4DX", "4DX", ""), ("ICE", "ICE", ""),
    ("", "PLAYHOUSE", "PLAYHOUSE"),
])
def test_screen_class_is_the_experience_never_a_projection_format(experience, screen_type, expected):
    assert screen_class(experience, screen_type) == expected


def test_the_captured_format_shapes_parse_as_the_movie_is_actually_shown():
    """The capture's traps: 3D on an INSIGNIA screen says so only in
    movieFormat; a 4DX 2D show has an empty filmFormat; an IMAX auditorium
    with a regular print is not IMAX; Atmos is sound."""
    p, _, _ = provider([one_film(
        ("INSIGNIA", [show("101", 1, "38431", D1, "1000", movie_format="3D", film_format="3D", screen_type="INSIGNIA")]),
        ("4DX", [show("101", 2, "38431", D1, "1100", movie_format="4DX", film_format="", screen_type="4DX")]),
        ("IMAX", [show("101", 3, "38431", D1, "1200", movie_format="", screen_type="IMAX")]),
        ("INSIGNIA [ATMOS]", [show("101", 4, "38431", D1, "1300", movie_format="ATMOS", sound="ATMOS",
                                   screen_type="INSIGNIA")]),
        ("IMAX", [show("101", 5, "38460", D1, "1400", movie_format="IMAX", screen_type="IMAX")]),
    )])
    by = {s.session_id: s for s in p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes}
    assert by["1"].format_labels == ("INSIGNIA", "3D")
    assert by["2"].format_labels == ("4DX 2D",)
    assert by["3"].format_labels == ("2D",)
    assert by["4"].format_labels == ("INSIGNIA", "2D") and by["4"].format_raw == "ATMOS"
    assert by["5"].format_labels == ("IMAX 2D",)


def test_targets_match_only_the_format_the_show_is_in():
    p, _, _ = provider([one_film(
        ("", [show("101", 1, "38431", D1, "1000")]),
        ("INSIGNIA", [show("101", 2, "38431", D1, "1100", movie_format="3D", screen_type="INSIGNIA")]),
        ("IMAX", [show("101", 3, "38460", D1, "1200", movie_format="IMAX 3D", screen_type="IMAX")]),
        ("IMAX", [show("101", 4, "38431", D1, "1300", movie_format="", screen_type="IMAX")]),
    )])
    by = {s.session_id: s for s in p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes}

    def hits(fmt):
        return sorted(k for k, s in by.items() if TheatreTarget("101", "PVR", fmt=fmt).matches_show(s))

    assert hits("IMAX 3D") == ["3"]
    assert hits("IMAX") == ["3"]                 # the IMAX *screen* with a regular print is not IMAX
    assert hits("INSIGNIA") == ["2"]
    assert hits("3D") == ["2", "3"]
    assert "2" not in hits("2D")                 # 3D on an INSIGNIA screen is not 2D


# ──────────────────────────────────────────────────────────────────────────
# Availability
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, status, code, expected", [
    ("Available", 1, "76BE43", Availability.AVAILABLE),
    ("Filling Up Fast", 2, "FFA500", Availability.AVAILABLE),
    ("Almost Full", 3, "FF0000", Availability.AVAILABLE),
    ("Housefull", 4, "999999", Availability.SOLD_OUT),
    ("Lapsed", 5, "CCCCCC", Availability.NOT_BOOKABLE),
    ("", 1, "76BE43", Availability.AVAILABLE),
])
def test_show_status_maps_to_the_existing_states(text, status, code, expected):
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000", text=text, status=status, code=code)]))])
    [s] = p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes
    assert s.availability is expected


def test_an_unknown_status_is_on_sale_never_sold_out_and_is_logged(capsys):
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000", text="Grab Now", status=7, code="ABCDEF")]))])
    [s] = p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes
    assert s.availability is Availability.AVAILABLE
    out = capsys.readouterr().out
    assert "unrecognised show status" in out and "grab now" in out


def test_a_show_that_has_started_is_not_bookable():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "0700")]))])       # NOW is 08:00
    [s] = p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes
    assert s.availability is Availability.NOT_BOOKABLE


# ──────────────────────────────────────────────────────────────────────────
# Dates and cinemas: explicit, bounded, all-or-nothing
# ──────────────────────────────────────────────────────────────────────────
def test_any_date_sweeps_today_and_the_next_six_days_explicitly():
    p, session, _ = provider([closed()] * 7)
    p.fetch(FILM_EN, None, venue_codes=["101"])
    assert [d for _, d in session.cinema_dates()] == [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in WINDOW]


def test_past_and_far_future_dates_are_never_requested():
    p, session, _ = provider([closed()])
    p.fetch(FILM_EN, ["20260920", D1, "20261201"], venue_codes=["101"])
    assert session.cinema_dates() == [("101", "2026-09-25")]


def test_several_dates_are_each_read_and_kept_apart():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000")])),
                        one_film(("", [show("101", 2, "38431", D2, "1000")]))])
    snap = p.fetch(FILM_EN, [D1, D2], venue_codes=["101"])
    assert sorted((s.date_code, s.session_id) for s in snap.showtimes) == [(D1, "1"), (D2, "2")]


def test_only_the_named_cinemas_are_read():
    p, session, _ = provider([closed(), closed()])
    p.fetch(FILM_EN, [D1], venue_codes=["101", "202"])
    assert session.cinema_dates() == [("101", "2026-09-25"), ("202", "2026-09-25")]


def test_a_read_must_name_its_cinemas():
    p, session, _ = provider([])
    with pytest.raises(PlatformError, match="name the cinemas"):
        p.fetch(FILM_EN, [D1])
    assert session.calls == []


def test_a_read_that_would_exceed_the_request_ceiling_is_refused_up_front():
    p, session, _ = provider([])
    codes = [str(100 + i) for i in range(MAX_REQUESTS_PER_READ // 7 + 1)]
    with pytest.raises(PlatformError, match="limit is"):
        p.fetch(FILM_EN, None, venue_codes=codes)
    assert session.calls == []


def test_a_failed_cinema_read_fails_the_whole_read():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000")])), ConnectionError("reset")])
    with pytest.raises(PlatformError, match="1 of 2 reads failed"):
        p.fetch(FILM_EN, [D1], venue_codes=["101", "202"])


def test_http_500_on_a_date_nobody_has_opened_is_closed():
    p, _, _ = provider([FakeResponse(500, None), FakeResponse(500, None)])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101", "202"])
    assert snap.closed_dates == [D1] and snap.showtimes == []


def test_http_500_on_a_date_another_cinema_is_selling_is_a_failure():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000")])), FakeResponse(500, None)])
    with pytest.raises(PlatformError, match="HTTP 500 on a date other cinemas are selling"):
        p.fetch(FILM_EN, [D1], venue_codes=["101", "202"])


def test_a_cinema_is_listed_only_when_it_shows_the_film():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000")])), sessions_payload()])
    snap = p.fetch(FILM_EN, [D1], venue_codes=["101", "202"])
    assert [v.code for v in snap.venues] == ["101"] and snap.venues[0].formats == ("2D",)


# ──────────────────────────────────────────────────────────────────────────
# Links
# ──────────────────────────────────────────────────────────────────────────
def test_a_show_links_to_its_own_seat_page_from_its_token():
    p, _, _ = provider([one_film(("", [show("101", 1, "38431", D1, "1000", encrypted="FAKE(TOKEN)abc==")]))])
    [s] = p.fetch(FILM_EN, [D1], venue_codes=["101"]).showtimes
    assert s.booking_url == "https://www.pvrcinemas.com/seatlayout/FAKE(TOKEN)abc=="
    assert is_pvr_url(s.booking_url)


def test_no_token_means_no_invented_link():
    assert seat_page("") == "" and seat_page(None) == ""
    assert seat_page("a/b+c") == "https://www.pvrcinemas.com/seatlayout/a%2Fb%2Bc"
    p, _, _ = provider([])
    assert p.booking_url(FILM_EN, D1) == SITE and p.venue_booking_url(FILM_EN, "101", D1) == SITE


@pytest.mark.parametrize("url, ok", [
    ("https://www.pvrcinemas.com/seatlayout/abc", True),
    ("https://pvrcinemas.com/", True),
    ("http://www.pvrcinemas.com/seatlayout/abc", False),
    ("https://www.pvrcinemas.com.evil.io/x", False),
    ("https://api3.pvrcinemas.com/api/v1/booking/content/csessions", False),
    ("https://in.bookmyshow.com/x", False),
    ("javascript:alert(1)", False),
    ("", False),
])
def test_pvr_links_are_allow_listed_on_their_own_hosts(url, ok):
    assert is_pvr_url(url) is ok
    assert is_platform_url(url, "pvr_inox") is ok


def test_one_platforms_links_are_never_accepted_for_another():
    assert not is_platform_url("https://www.pvrcinemas.com/seatlayout/abc", "bookmyshow")
    assert not is_platform_url("https://in.bookmyshow.com/buytickets/x", "pvr_inox")
    assert is_platform_url("https://in.bookmyshow.com/buytickets/x", "bookmyshow")
    assert not is_platform_url("https://www.pvrcinemas.com/", "district")


def test_a_pvr_film_code_carries_its_film_and_language():
    assert film_key(FILM_EN) == ("35288", "ENGLISH")
    with pytest.raises(PlatformError):
        film_key(MovieRef("pvr_inox", "ET00514163", "x", "32", "hyderabad"))


def test_links_are_never_resolved_from_a_url():
    p, session, _ = provider([])
    with pytest.raises(PlatformError):
        p.resolve("https://www.pvrcinemas.com/anything")
    assert session.calls == []
