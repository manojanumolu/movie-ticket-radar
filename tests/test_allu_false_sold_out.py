"""The 6 November 2026 ALLU Cinemas incident, and the guards around it.

What happened (established from BookMyShow's own data — ``data/showtimes.json``
read 02:05 IST on 6 Nov, and the catalogue): BookMyShow sells each language of
Ramayana as its own event. At ALLU Cinemas, Dolby Cinema, on 6 Nov:

* the **Hindi** event's Dolby Cinema 3D sibling listed 11:00 AM (session 6329),
  3:00 PM and 11:00 PM — the 11:00 AM seat map in the report;
* the **Telugu** event's Dolby Cinema 3D sibling listed 7:00 AM (6328) and
  7:00 PM (6709), the 7:00 PM marked sold out by BookMyShow itself.

The monitor was on the Telugu event. It reported the Telugu shows correctly and
never read the Hindi 11:00 AM show — and nothing on the page said so. The
parser and the verdict were right; the identity of what was watched was
invisible. These tests pin the verdicts on the incident's shapes, the
availability rules, ALLU's sections and formats, the transitions, and the
notes that now say which listing a monitor watches.

The payloads are ``tests.conftest.build_payload`` (the real response shape),
the category lists BookMyShow's own (``TWO_GOLDS``: ALLU, Dolby Cinema, 3D,
read live 21 Sep 2026). They are fixtures, not captured 6 Nov responses — no
live BookMyShow read is possible from the test environment.
"""

from __future__ import annotations

import copy

import pytest

from config.seat_sections import section_name
from monitor.checker import evaluate_target
from monitor.models import (
    ANY_FORMAT,
    Availability,
    Monitor,
    MovieRef,
    SeatCategory,
    TheatreTarget,
    merge_categories,
)
from monitor.state import get_monitor_state as get_state
from monitor.state import upsert_monitor
from platforms.bookmyshow import parse_seat_categories
from tests.conftest import FakeResponse, build_payload
from tests.test_category_watch import TWO_GOLDS, _run

DAY = "20260925"
GOLD_1 = "0000000003|3D GOLD"        # area 3 — Gold Area 1
GOLD_2 = "0000000005|3D GOLD."       # area 5 — Gold Area 2


def allu(time, code, status_by_area, fmt="DOLBY CINEMA", date=DAY):
    """One ALLU show whose categories are TWO_GOLDS with these statuses
    (area number -> availStatus); areas left out keep the live status."""
    cats = copy.deepcopy(TWO_GOLDS)
    for c in cats:
        area = c["areaCatCode"].lstrip("0")
        if area in status_by_area:
            c["availStatus"] = status_by_area[area]
    return {"venue_code": "ALUC", "venue_name": "ALLU Cinemas", "area": "Kokapet", "time": time,
            "time_code": code, "date": date, "fmt": fmt, "status": "3", "cats": cats}


def payload(shows):
    p = build_payload(shows, bookable_dates=(DAY,), closed_dates=())
    by_code = {s["time_code"]: s for s in shows}
    for widget in p["data"]["showtimeWidgets"]:
        for group in widget.get("data", []):
            for card in (group.get("data", []) if isinstance(group, dict) else []):
                for show in card.get("showtimes", []):
                    mine = by_code[show["additionalData"]["showTimeCode"]]
                    if "cats" in mine:
                        show["additionalData"]["categories"] = mine["cats"]
    return p


def snapshot(provider_factory, listing_url, shows):
    return provider_factory([payload(shows)]).resolve(listing_url)


def monitor(make_monitor, language="Telugu", categories=(), show_time="", fmt="Dolby Cinema"):
    m = make_monitor(targets=[TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", fmt)])
    m.movie = MovieRef(**{**m.movie.__dict__, "title": "Ramayana", "language": language})
    m.categories = list(categories)
    m.show_time = show_time
    return m


SOLD = {"2": "0", "3": "0", "4": "0", "5": "0"}


# ── 1 · The incident, as BookMyShow listed it ────────────────────────────
def test_the_telugu_monitor_reports_its_own_shows_and_never_the_hindi_11am(make_monitor, provider_factory, listing_url):
    telugu = snapshot(provider_factory, listing_url, [allu("07:00 PM", "1900", SOLD)])
    result = evaluate_target(monitor(make_monitor), monitor(make_monitor).targets[0], telugu)
    assert result.availability is Availability.SOLD_OUT                       # true for the Telugu shows
    assert [s.time_label for s in result.showtimes] == ["07:00 PM"]          # never an 11:00 AM show


def test_the_hindi_11am_with_free_gold_seats_is_available(make_monitor, provider_factory, listing_url):
    hindi = snapshot(provider_factory, listing_url,
                     [allu("11:00 AM", "1100", {"2": "2", "3": "2", "4": "2", "5": "0"})])
    plain = monitor(make_monitor, language="Hindi")
    assert evaluate_target(plain, plain.targets[0], hindi).availability is Availability.AVAILABLE
    gold = monitor(make_monitor, language="Hindi", categories=[GOLD_1], show_time="11:00 AM")
    result = evaluate_target(gold, gold.targets[0], hindi)
    assert result.availability is Availability.AVAILABLE and "11:00 AM" in [s.time_label for s in result.showtimes]


def test_a_show_time_the_listing_does_not_have_is_not_sold_out(make_monitor, provider_factory, listing_url):
    telugu = snapshot(provider_factory, listing_url, [allu("07:00 PM", "1900", SOLD)])
    m = monitor(make_monitor, categories=[GOLD_1], show_time="11:00 AM")
    assert evaluate_target(m, m.targets[0], telugu).availability is Availability.SHOW_NOT_AVAILABLE


def test_the_page_now_says_which_listing_a_monitor_watches(make_monitor):
    from monitor.state import MonitorState, TargetState
    from ui import detail

    m = monitor(make_monitor)
    state = MonitorState()
    state.targets[m.targets[0].key] = TargetState(availability=Availability.SOLD_OUT, time_labels=["07:00 PM"])
    real = detail._other_languages
    detail._other_languages = lambda mon, code: ("Hindi",)                    # what the catalogue says at ALLU
    try:
        html = detail.markup(m, state)
    finally:
        detail._other_languages = real
    assert "Every seat is taken at the Telugu shows watched here (07:00 PM)" in html
    assert "Watching the Telugu listing only. Ramayana is also listed here in Hindi" in html


def test_other_language_listings_reads_the_real_catalogue():
    import json

    from monitor import catalogue
    from ui import catalogue_view as cv

    with open("data/catalogue.json", encoding="utf-8") as fh:
        rows = json.load(fh)["movies"]
    telugu = next((r for r in rows if r["movie"]["event_code"] == "ET00518312"), None)
    if telugu is None:
        pytest.skip("the synced catalogue no longer lists Ramayana (Telugu)")
    langs = cv.other_language_listings(catalogue.movie_from_entry(telugu), "ALUC", rows=rows)
    assert "Hindi" in langs and "Telugu" not in langs


# ── 2 · Availability rules ───────────────────────────────────────────────
def test_one_open_category_among_sold_ones_is_available(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {"2": "0", "3": "0", "4": "0", "5": "1"})])
    m = monitor(make_monitor)
    assert evaluate_target(m, m.targets[0], snap).availability is Availability.AVAILABLE


def test_every_category_sold_is_sold_out(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", SOLD)])
    m = monitor(make_monitor)
    assert evaluate_target(m, m.targets[0], snap).availability is Availability.SOLD_OUT


def test_the_watched_category_decides_not_the_others(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {"2": "3", "3": "0", "4": "3", "5": "3"})])
    gold1 = monitor(make_monitor, categories=[GOLD_1])
    assert evaluate_target(gold1, gold1.targets[0], snap).availability is Availability.SOLD_OUT   # others open: not ours
    gold2 = monitor(make_monitor, categories=[GOLD_2])
    assert evaluate_target(gold2, gold2.targets[0], snap).availability is Availability.AVAILABLE
    both = monitor(make_monitor, categories=[GOLD_1, GOLD_2])
    assert evaluate_target(both, both.targets[0], snap).availability is Availability.AVAILABLE


@pytest.mark.parametrize("code", ["", "9", "X", "-1"])
def test_an_unreadable_category_status_is_never_sold_out(make_monitor, provider_factory, listing_url, code):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {"3": code})])
    m = monitor(make_monitor, categories=[GOLD_1])
    result = evaluate_target(m, m.targets[0], snap)
    assert result.availability is Availability.NOT_BOOKABLE and "no status we can read" in result.detail


def test_an_unreadable_status_is_never_counted_as_bookable():
    cats = parse_seat_categories({"categories": [{"priceDesc": "3D GOLD", "areaCatCode": "0000000003",
                                                  "availStatus": "7"}]})
    assert cats[0].availability is Availability.NOT_BOOKABLE


def test_no_categories_published_is_not_sold_out(make_monitor, provider_factory, listing_url):
    show = allu("11:00 AM", "1100", {})
    show["cats"] = []
    snap = snapshot(provider_factory, listing_url, [show])
    m = monitor(make_monitor, categories=[GOLD_1])
    assert evaluate_target(m, m.targets[0], snap).availability is Availability.NOT_BOOKABLE


def test_a_watched_section_missing_from_the_response_is_not_sold_out(make_monitor, provider_factory, listing_url):
    show = allu("11:00 AM", "1100", SOLD)
    show["cats"] = [c for c in show["cats"] if c["areaCatCode"] != "0000000003"]
    snap = snapshot(provider_factory, listing_url, [show])
    m = monitor(make_monitor, categories=[GOLD_1])
    result = evaluate_target(m, m.targets[0], snap)
    assert result.availability is Availability.NOT_BOOKABLE and "not listed" in result.detail


@pytest.mark.parametrize("failure", [403, 429, 500])
def test_a_refused_or_failed_read_is_an_error_never_sold_out(make_monitor, provider_factory, monkeypatch, later, failure):
    m = monitor(make_monitor, categories=[GOLD_1])
    upsert_monitor(m, mirror=False)
    sent = []
    _run(provider_factory([FakeResponse(failure)] * 4), monkeypatch, later(0), sent)
    state = get_state(m.id)
    assert state.consecutive_errors >= 1
    assert state.targets.get(m.targets[0].key) is None or \
        state.targets[m.targets[0].key].availability is not Availability.SOLD_OUT
    assert sent == []


def test_an_error_keeps_the_last_real_answer_and_sends_nothing(make_monitor, provider_factory, monkeypatch, later):
    m = monitor(make_monitor, categories=[GOLD_1])
    upsert_monitor(m, mirror=False)
    key = m.targets[0].key
    sent = []
    _run(provider_factory([payload([allu("11:00 AM", "1100", {"3": "3"})])]), monkeypatch, later(0), sent)
    first = get_state(m.id).targets[key]
    _run(provider_factory([FakeResponse(403)] * 4), monkeypatch, later(10), sent)
    after = get_state(m.id).targets[key]
    assert after.availability is first.availability is Availability.AVAILABLE and after.since == first.since
    assert len(sent) == 1                                                     # the first read's one email, nothing more


# ── 3 · Transitions ──────────────────────────────────────────────────────
def test_sold_out_to_available_emails_once_and_back_to_sold_out_updates(make_monitor, provider_factory, monkeypatch, later):
    m = monitor(make_monitor, categories=[GOLD_1])
    upsert_monitor(m, mirror=False)
    key = m.targets[0].key
    sent = []
    sold = payload([allu("11:00 AM", "1100", SOLD)])
    open_ = payload([allu("11:00 AM", "1100", {**SOLD, "3": "2"})])
    _run(provider_factory([sold]), monkeypatch, later(0), sent)
    assert sent == [] and get_state(m.id).targets[key].availability is Availability.SOLD_OUT
    _run(provider_factory([open_]), monkeypatch, later(10), sent)
    assert len(sent) == 1 and sent[0][1].previous is Availability.SOLD_OUT
    for step in (20, 30):
        _run(provider_factory([open_]), monkeypatch, later(step), sent)       # identical scans: no duplicates
    assert len(sent) == 1
    _run(provider_factory([sold]), monkeypatch, later(40), sent)
    assert get_state(m.id).targets[key].availability is Availability.SOLD_OUT and len(sent) == 1


def test_unknown_then_available_is_one_email_and_error_in_between_is_none(make_monitor, provider_factory, monkeypatch, later):
    m = monitor(make_monitor, categories=[GOLD_2])
    upsert_monitor(m, mirror=False)
    sent = []
    _run(provider_factory([FakeResponse(403)] * 4), monkeypatch, later(0), sent)
    assert sent == []
    _run(provider_factory([payload([allu("11:00 AM", "1100", {"5": "3"})])]), monkeypatch, later(10), sent)
    assert len(sent) == 1


# ── 4 · ALLU's sections: identity, names, prices ─────────────────────────
def test_allu_3d_sections_are_bookmyshows_own_with_their_prices():
    cats = {c.area_code.lstrip("0"): c for c in parse_seat_categories({"categories": TWO_GOLDS})}
    assert (cats["2"].name, cats["2"].price) == ("3D PLATINUM", "450.00")
    assert (cats["3"].name, cats["3"].price) == ("3D GOLD", "395.00")
    assert (cats["4"].name, cats["4"].price) == ("3D DIRECTOR CHOICE", "450.00")
    assert (cats["5"].name, cats["5"].price) == ("3D GOLD.", "395.00")
    assert len({c.key for c in cats.values()}) == 4                          # area 3 and area 5 never collide


def test_allu_sections_have_the_same_physical_names_in_2d_and_3d():
    assert section_name("ALUC", "0000000002") == "Platinum · recliners"
    assert section_name("ALUC", "0000000003") == "Gold Area 1 · upper"
    assert section_name("ALUC", "0000000004") == "Director Choice · between the Gold areas"
    assert section_name("ALUC", "0000000005") == "Gold Area 2 · lower"
    # 2D GOLD (₹295) and 3D GOLD (₹395) are the same area, so the same seats
    two_d = SeatCategory("0002", "GOLD", Availability.AVAILABLE, "295.00", "0000000003")
    three_d = SeatCategory("0006", "3D GOLD", Availability.AVAILABLE, "395.00", "0000000003")
    assert section_name("ALUC", two_d.area_code) == section_name("ALUC", three_d.area_code)
    assert section_name("AMBH", "0000000003") == "" and section_name("ALUC", "0000000009") == ""   # nothing invented


def test_a_sections_identity_never_depends_on_its_price():
    cheap = SeatCategory("0006", "3D GOLD", Availability.AVAILABLE, "335.00", "0000000003")
    dear = SeatCategory("0006", "3D GOLD", Availability.AVAILABLE, "395.00", "0000000003")
    assert cheap.key == dear.key == GOLD_1
    watcher = Monitor(movie=MovieRef("bookmyshow", "ET1", "x", "HYD", "hyderabad"), targets=[], categories=[GOLD_1])
    assert watcher.watches_category(cheap) and watcher.watches_category(dear)


def test_a_theatres_category_summary_says_the_price_range_not_the_first_shows_price():
    early = SeatCategory("0006", "3D GOLD", Availability.AVAILABLE, "335.00", "0000000003")
    later_ = SeatCategory("0006", "3D GOLD", Availability.SOLD_OUT, "395.00", "0000000003")
    gold2 = SeatCategory("0008", "3D GOLD.", Availability.AVAILABLE, "395.00", "0000000005")
    merged = {c.key: c for c in merge_categories([early, gold2], [later_])}
    assert merged[GOLD_1].label == "3D GOLD · ₹335–395 · area 3"
    assert merged[GOLD_2].label == "3D GOLD. · ₹395 · area 5"
    assert SeatCategory.from_dict(merged[GOLD_1].to_dict()) == merged[GOLD_1]       # stored and read back


def test_the_provider_records_the_range_across_one_theatres_shows(provider_factory, listing_url):
    early = allu("07:00 AM", "0700", {})
    for c in early["cats"]:
        c["curPrice"] = {"0000000002": "390.00", "0000000003": "335.00"}.get(c["areaCatCode"], c["curPrice"])
    snap = snapshot(provider_factory, listing_url, [early, allu("11:00 AM", "1100", {})])
    venue = next(v for v in snap.venues if v.code == "ALUC")
    labels = {c.key: c.label for c in venue.categories}
    assert labels[GOLD_1] == "3D GOLD · ₹335–395 · area 3"
    assert labels["0000000002|3D PLATINUM"] == "3D PLATINUM · ₹390–450 · area 2"
    for show in snap.showtimes:                                               # each show keeps its own price
        assert all(not c.price_high for c in show.categories)


def test_the_picker_warns_when_only_one_of_two_gold_sections_is_chosen():
    from ui.flow import sibling_sections

    cats = list(parse_seat_categories({"categories": TWO_GOLDS}))
    [warning] = sibling_sections([GOLD_1], cats)
    assert "3D GOLD. · ₹395 · area 5" in warning and "not covered unless you choose it too" in warning
    assert sibling_sections([GOLD_1, GOLD_2], cats) == []


# ── 5 · ALLU's formats ───────────────────────────────────────────────────
def test_allu_offers_its_screens_and_not_3d_as_a_screen():
    from ui.flow import screen_options

    assert screen_options("ALUC", ["Dolby Cinema", "3D", "Dolby Cinema 3D", "Laser 4K Atmos"]) == \
        ["Dolby Cinema", "Laser 4K Atmos"]
    assert screen_options("AMBH", ["HDR By Barco", "3D", "4DX 3D"]) == ["HDR By Barco", "3D", "4DX 3D"]   # unchanged
    assert screen_options("ALUC", ["2D", "3D"]) == ["2D", "3D"]                # nothing left to choose otherwise


def test_dolby_cinema_watches_its_3d_shows_and_laser_its_own(make_monitor, provider_factory, listing_url):
    shows = [allu("11:00 AM", "1100", {"3": "3"}, fmt="DOLBY CINEMA"),
             allu("07:20 AM", "0720", {"3": "3"}, fmt="LASER 4K ATMOS")]
    snap = snapshot(provider_factory, listing_url, shows)
    dolby = monitor(make_monitor, fmt="Dolby Cinema")
    laser = monitor(make_monitor, fmt="Laser 4K Atmos")
    assert [s.time_label for s in evaluate_target(dolby, dolby.targets[0], snap).showtimes] == ["11:00 AM"]
    assert [s.time_label for s in evaluate_target(laser, laser.targets[0], snap).showtimes] == ["07:20 AM"]


def test_an_existing_dolby_cinema_3d_monitor_still_works(make_monitor, provider_factory, listing_url):
    shows = [allu("11:00 AM", "1100", {"3": "3"})]
    shows[0]["fmt"] = "DOLBY CINEMA 3D"
    snap = snapshot(provider_factory, listing_url, shows)
    old = monitor(make_monitor, fmt="Dolby Cinema 3D")
    assert evaluate_target(old, old.targets[0], snap).availability is Availability.AVAILABLE


def test_infinity_vision_certification_is_said_for_marvel_films_only():
    from ui.flow import format_display_name

    assert format_display_name("ALUC", "Dolby Cinema", "Avengers: Doomsday") == "Dolby Cinema (Infinity Vision Certified)"
    assert format_display_name("ALUC", "Dolby Cinema", "Ramayana") == "Dolby Cinema"
    assert format_display_name("ALUC", "Laser 4K Atmos", "Avengers: Doomsday") == "Laser 4K Atmos"
    assert format_display_name("AMBH", "Dolby Cinema", "Avengers: Doomsday") == "Dolby Cinema"


def test_any_format_still_covers_every_screen(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", SOLD), allu("07:20 AM", "0720", {"3": "3"}, fmt="LASER 4K ATMOS")])
    m = monitor(make_monitor, fmt=ANY_FORMAT)
    assert evaluate_target(m, m.targets[0], snap).availability is Availability.AVAILABLE
