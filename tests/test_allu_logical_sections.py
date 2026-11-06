"""ALLU Cinemas' four logical seat sections (6 Nov 2026).

The picker offers Platinum, Gold Area 1, Director Choice and Gold Area 2 for
ALLU's Dolby Cinema auditorium — one option each, in the screening's 2D or
3D prices, never both lists at once. A watch on one stores
``@ALUC|Dolby Cinema|<area>`` and checks every BookMyShow block of that area
on that screen, whatever BookMyShow names it in 2D or 3D, and never a block
of the same area number in the Laser 4K Atmos auditorium (different seats).
SOLD OUT needs every watched block sold; an unreadable one keeps the verdict
unknown. Watches saved before this keep their exact old meaning.

Payloads are fixtures in BookMyShow's real response shape
(``tests.test_allu_false_sold_out``); no live BookMyShow read is possible
from the test environment.
"""

from __future__ import annotations

import copy

from config.seat_sections import entry_for, parse_entry, section_for_entry, sections
from monitor import catalogue
from monitor.checker import evaluate_target
from monitor.models import (
    ANY_FORMAT,
    Availability,
    SeatCategory,
    Venue,
    category_display,
    category_matches,
)
from monitor.state import MonitorState, TargetState
from tests.test_allu_false_sold_out import DAY, GOLD_1, SOLD, allu, monitor, snapshot
from tests.test_category_watch import seeded_with_categories  # noqa: F401 - a fixture
from ui.flow import section_choices

PLATINUM, GOLD_A1, DIRECTOR, GOLD_A2 = (entry_for("ALUC", "Dolby Cinema", s) for s in sections("ALUC", "Dolby Cinema"))

#: ALLU's 2D section names and prices, as BookMyShow prices a 2D show
#: (the user's table; Dolby 2D itself has not been observed live).
TWO_D = [
    {"areaCatCode": "0000000002", "availStatus": "2", "curPrice": "350.00", "priceCode": "0001", "priceDesc": "PLATINUM"},
    {"areaCatCode": "0000000003", "availStatus": "2", "curPrice": "295.00", "priceCode": "0002", "priceDesc": "GOLD"},
    {"areaCatCode": "0000000004", "availStatus": "2", "curPrice": "350.00", "priceCode": "0003", "priceDesc": "DIRECTOR CHOICE"},
    {"areaCatCode": "0000000005", "availStatus": "2", "curPrice": "295.00", "priceCode": "0004", "priceDesc": "GOLD."},
]


def dolby_2d(time, code, status_by_area=None):
    show = allu(time, code, {})
    show["cats"] = copy.deepcopy(TWO_D)
    for c in show["cats"]:
        c["availStatus"] = (status_by_area or {}).get(c["areaCatCode"].lstrip("0"), c["availStatus"])
    return show


def laser_3d(time, code, gold_status):
    """A Laser 4K Atmos 3D show: two sections only, area 3 reused for its Gold."""
    show = allu(time, code, {}, fmt="LASER 4K ATMOS")
    show["cats"] = [
        {"areaCatCode": "0000000002", "availStatus": "0", "curPrice": "390.00", "priceCode": "0005", "priceDesc": "3D PLATINUM"},
        {"areaCatCode": "0000000003", "availStatus": gold_status, "curPrice": "335.00", "priceCode": "0006", "priceDesc": "3D GOLD"},
    ]
    return show


def venue_of(snap) -> Venue:
    return next(v for v in snap.venues if v.code == "ALUC")


def verdict(make_monitor, snap, entries, fmt="Dolby Cinema", show_time=""):
    m = monitor(make_monitor, categories=entries, fmt=fmt, show_time=show_time)
    return evaluate_target(m, m.targets[0], snap)


# ── The picker: four sections, one dimension's prices ────────────────────
def test_the_picker_offers_exactly_the_four_sections_with_3d_prices(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {}), allu("07:00 PM", "1900", {})])
    options, dims, warning = section_choices(venue_of(snap), ["Dolby Cinema"])
    assert warning == "" and dims == ("3D",)
    assert list(options) == ["Platinum · ₹450 · Recliners", "Gold Area 1 · ₹395 · Upper seating",
                             "Director Choice · ₹450 · Between the two Gold areas", "Gold Area 2 · ₹395 · Lower seating"]
    assert list(options.values()) == [PLATINUM, GOLD_A1, DIRECTOR, GOLD_A2]
    assert not any("3D" in label or " · area " in label for label in options)   # BookMyShow's ids stay internal


def test_2d_and_3d_prices_are_never_listed_together(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {}), dolby_2d("07:00 AM", "0700")])
    v = venue_of(snap)
    _o, dims, _w = section_choices(v, ["Dolby Cinema"])
    assert dims == ("2D", "3D")
    two, _d, _w = section_choices(v, ["Dolby Cinema"], "2D")
    three, _d, _w = section_choices(v, ["Dolby Cinema"], "3D")
    assert list(two) == ["Platinum · ₹350 · Recliners", "Gold Area 1 · ₹295 · Upper seating",
                         "Director Choice · ₹350 · Between the two Gold areas", "Gold Area 2 · ₹295 · Lower seating"]
    assert [label.split(" · ")[1] for label in three] == ["₹450", "₹395", "₹450", "₹395"]
    assert list(two.values()) == list(three.values())                         # the same four sections
    undecided, _d, _w = section_choices(v, ["Dolby Cinema"])
    assert all("choose 2D or 3D" in label for label in undecided) and len(undecided) == 4


def test_only_a_2d_listing_shows_2d_prices(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [dolby_2d("07:00 AM", "0700")])
    options, dims, _w = section_choices(venue_of(snap), ["Dolby Cinema"])
    assert dims == ("2D",) and [label.split(" · ")[1] for label in options] == ["₹350", "₹295", "₹350", "₹295"]


def test_the_price_is_the_screens_own_never_the_laser_auditoriums(provider_factory, listing_url):
    # Laser lists "3D GOLD" area 3 at ₹335 — the key Dolby's Gold Area 1 uses
    snap = snapshot(provider_factory, listing_url, [laser_3d("07:20 AM", "0720", "2"), allu("11:00 AM", "1100", {})])
    v = venue_of(snap)
    assert next(c for c in v.categories if c.key == GOLD_1).label == "3D GOLD · ₹335–395 · area 3"   # the old mix-up
    options, _d, _w = section_choices(v, ["Dolby Cinema"])
    assert "Gold Area 1 · ₹395 · Upper seating" in options


def test_a_range_is_shown_when_the_screens_own_shows_differ(provider_factory, listing_url):
    cheap = allu("07:00 AM", "0700", {})
    for c in cheap["cats"]:
        if c["areaCatCode"] == "0000000003":
            c["curPrice"] = "350.00"
    snap = snapshot(provider_factory, listing_url, [cheap, allu("11:00 AM", "1100", {})])
    options, _d, _w = section_choices(venue_of(snap), ["Dolby Cinema"])
    assert "Gold Area 1 · ₹350–395 · Upper seating" in options


def test_laser_any_or_two_screens_get_a_warning_and_no_guessed_sections(provider_factory, listing_url):
    v = venue_of(snapshot(provider_factory, listing_url, [laser_3d("07:20 AM", "0720", "2")]))
    for formats in (["Laser 4K Atmos"], [ANY_FORMAT], ["Dolby Cinema", "Laser 4K Atmos"], []):
        options, _d, warning = section_choices(v, formats)
        assert options == {} and "not verified" in warning and "Dolby Cinema" in warning


def test_a_catalogue_synced_before_screen_prices_says_so():
    old = Venue(code="ALUC", name="ALLU Cinemas", categories=(SeatCategory("0006", "3D GOLD", Availability.UNKNOWN, "395.00", "0000000003"),))
    options, dims, warning = section_choices(old, ["Dolby Cinema"])
    assert warning == "" and dims == () and len(options) == 4
    assert all("price after the next catalogue sync" in label for label in options)


def test_the_older_dolby_cinema_3d_format_maps_to_the_same_auditorium(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {})])
    options, _d, _w = section_choices(venue_of(snap), ["Dolby Cinema 3D"])
    assert list(options.values()) == [PLATINUM, GOLD_A1, DIRECTOR, GOLD_A2]


# ── Mapping: area and screen, every name ─────────────────────────────────
def test_a_section_matches_its_area_on_its_screen_in_2d_and_3d(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {}), dolby_2d("07:00 AM", "0700"),
                                                     laser_3d("07:20 AM", "0720", "2")])
    by_show = {s.time_label: s for s in snap.showtimes}
    gold1 = lambda label: [c.name for c in by_show[label].categories if category_matches(GOLD_A1, c, by_show[label])]
    assert gold1("11:00 AM") == ["3D GOLD"] and gold1("07:00 AM") == ["GOLD"]
    assert gold1("07:20 AM") == []                                            # Laser's area 3 is other seats
    assert not category_matches(GOLD_A1, by_show["11:00 AM"].categories[0])   # no show, no match


def test_laser_gold_open_never_makes_dolby_gold_area_1_available(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [laser_3d("07:20 AM", "0720", "2"), allu("11:00 AM", "1100", SOLD)])
    for fmt in ("Dolby Cinema", ANY_FORMAT):
        assert verdict(make_monitor, snap, [GOLD_A1], fmt=fmt).availability is Availability.SOLD_OUT


def test_gold_area_1_and_gold_area_2_are_never_combined(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {"3": "0", "5": "2"})])
    assert verdict(make_monitor, snap, [GOLD_A1]).availability is Availability.SOLD_OUT
    assert verdict(make_monitor, snap, [GOLD_A2]).availability is Availability.AVAILABLE


# ── Aggregation over every block ─────────────────────────────────────────
def test_any_bookable_block_makes_the_section_available(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", SOLD), dolby_2d("07:00 AM", "0700", {"3": "2"})])
    result = verdict(make_monitor, snap, [GOLD_A1])
    assert result.availability is Availability.AVAILABLE and [s.time_label for s in result.showtimes] == ["07:00 AM"]


def test_sold_out_only_when_every_block_is_sold(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", SOLD), dolby_2d("07:00 AM", "0700", SOLD)])
    assert verdict(make_monitor, snap, [GOLD_A1]).availability is Availability.SOLD_OUT


def test_one_unreadable_block_keeps_the_section_unknown(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", SOLD), dolby_2d("07:00 AM", "0700", {"3": "9"})])
    result = verdict(make_monitor, snap, [GOLD_A1])
    assert result.availability is Availability.NOT_BOOKABLE and "no status we can read" in result.detail


def test_a_section_the_listing_does_not_have_is_not_sold_out(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [laser_3d("07:20 AM", "0720", "0")])
    assert verdict(make_monitor, snap, [GOLD_A1], fmt=ANY_FORMAT).availability is Availability.NOT_BOOKABLE


# ── Old watches keep their meaning ───────────────────────────────────────
def test_watches_saved_before_sections_are_read_exactly_as_before(make_monitor, provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [laser_3d("07:20 AM", "0720", "2"), allu("11:00 AM", "1100", SOLD)])
    # the old key names an exact block, so — as before — Laser's open area 3 counts
    assert verdict(make_monitor, snap, [GOLD_1], fmt=ANY_FORMAT).availability is Availability.AVAILABLE
    assert verdict(make_monitor, snap, [GOLD_1]).availability is Availability.SOLD_OUT
    assert verdict(make_monitor, snap, ["GOLD"], fmt=ANY_FORMAT).availability is Availability.NOT_BOOKABLE   # plain name
    assert category_display(GOLD_1) == "3D GOLD · area 3" and category_display("GOLD") == "GOLD"
    assert category_display(GOLD_A1) == "Gold Area 1 · Dolby Cinema"
    assert parse_entry(GOLD_1) is None and parse_entry("GOLD") is None and section_for_entry(GOLD_A1).name == "Gold Area 1"


def test_the_page_flags_an_old_allu_category_watch_and_not_a_new_one(make_monitor):
    from ui import detail

    state = MonitorState()
    real = detail._other_languages
    detail._other_languages = lambda mon, code: ()
    try:
        old = monitor(make_monitor, categories=[GOLD_1])
        state.targets[old.targets[0].key] = TargetState(availability=Availability.SOLD_OUT)
        assert "predates ALLU Cinemas" in detail.markup(old, state)
        new = monitor(make_monitor, categories=[GOLD_A1])
        assert "predates" not in detail.markup(new, state)
    finally:
        detail._other_languages = real


# ── The catalogue keeps prices per screen and dimension ──────────────────
def test_screen_prices_survive_the_catalogue(provider_factory, listing_url):
    first = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {})])
    second = snapshot(provider_factory, listing_url, [dolby_2d("07:00 AM", "0700"), laser_3d("07:20 AM", "0720", "2")])
    merged = catalogue.merge_snapshots(first, second)
    [v] = [v for v in catalogue.venues_from_entry(catalogue.entry_from_snapshot(merged)) if v.code == "ALUC"]
    assert sorted(k for k, _c in v.screen_categories) == ["dolbycinema|2D", "dolbycinema|3D", "laser4katmos|3D"]
    assert {c.price for c in v.categories_on("Dolby Cinema", "3D")} == {"395.00", "450.00"}
    assert {c.price for c in v.categories_on("Laser 4K Atmos", "3D")} == {"390.00", "335.00"}
    old_entry = {"venues": [{"code": "ALUC", "name": "ALLU Cinemas", "categories": []}]}
    assert catalogue.venues_from_entry(old_entry)[0].screen_categories == ()


def test_a_show_knows_its_dimension_and_screen(provider_factory, listing_url):
    snap = snapshot(provider_factory, listing_url, [allu("11:00 AM", "1100", {}), dolby_2d("07:00 AM", "0700")])
    by_show = {s.time_label: s for s in snap.showtimes}
    assert (by_show["11:00 AM"].dimension, by_show["07:00 AM"].dimension) == ("3D", "2D")
    assert by_show["11:00 AM"].screen_keys == ("dolbycinema|3D",)
    assert DAY == by_show["11:00 AM"].date_code


def test_the_wizards_venues_keep_this_films_screen_prices(seeded_with_categories):  # noqa: F811 - the fixture
    # found in the browser check: selected_venues rebuilt each Venue and
    # dropped its per-screen categories, so the picker never had a price
    from ui import catalogue_view as cv

    [venue] = cv.selected_venues(seeded_with_categories, "hyderabad", ["ALLU"])
    assert venue.screen_categories and all("|" in k for k, _c in venue.screen_categories)
    assert venue.screen_categories == next(v for v in cv.venues(seeded_with_categories, "hyderabad")
                                           if v.code == "ALLU").screen_categories
