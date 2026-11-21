"""Showtime Explorer premium upgrade: listed ticket prices, the grouped format
filter, and the performance work — regressions for each.

Prices are BookMyShow's own ``curPrice`` per seat category, from the same
showtimes response the Explorer already reads (``explorer.prices``). Every
price below is a fixture, never a captured BookMyShow price.
"""

from __future__ import annotations

import json
import re
from datetime import time as dtime
from pathlib import Path

import pytest

from config import store
from config.timezone import now_ist, to_iso
from explorer import classify as K, formats as F, prices as P, query as Q, snapshot as snap, source as S
from monitor.models import Availability, SeatCategory
from tests.test_showtime_explorer import (
    BMS, ENTRIES, IMAX_SHOWS, JAILER_SHOWS, RAMAYANA_SHOWS, TOMORROW, Clock, FakeBMS, body, explorer_show,
    make_reader, ok, payload, pick_movie, ramayana, run, show,
)
from ui import explorer as X, theme

ROOT = Path(__file__).resolve().parent.parent
DAY_AFTER = "20260927"

THREE = [{"priceDesc": "EXECUTIVE", "priceCode": "0001", "curPrice": "150.00", "availStatus": "3"},
         {"priceDesc": "GOLD", "priceCode": "0002", "curPrice": "295.00", "availStatus": "2"},
         {"priceDesc": "RECLINER", "priceCode": "0003", "curPrice": "450.00", "availStatus": "0"}]
ONE = [{"priceDesc": "STANDARD", "priceCode": "0001", "curPrice": "200", "availStatus": "3"}]
PARTIAL = [{"priceDesc": "EXECUTIVE", "priceCode": "0001", "curPrice": "180.00", "availStatus": "3"},
           {"priceDesc": "RECLINER", "priceCode": "0003", "curPrice": "", "availStatus": "3"}]


def priced(shows, cats: dict[str, list] | None = None, default=THREE, date=TOMORROW):
    """A showtimes payload whose shows carry ``cats[time_code]`` (else
    ``default``) as their seat categories; ``None`` means no categories."""
    p = payload(shows, date=date)
    for w in p["data"]["showtimeWidgets"]:
        for g in w.get("data", []) or []:
            for card in (g.get("data") or []) if isinstance(g, dict) else []:
                for st_ in card.get("showtimes", []) if isinstance(card, dict) else []:
                    ad = st_["additionalData"]
                    chosen = (cats or {}).get(ad["showTimeCode"], default)
                    ad["categories"] = [] if chosen is None else [dict(c) for c in chosen]
    return p


#: Ramayana tomorrow, priced: 09:05 AMB three tiers; 09:30 PVR 3D one price;
#: 10:15 Asian Jyothi partial; 06:45 Sudarshan no categories; the rest three tiers.
PRICES = {"0930": ONE, "1015": PARTIAL, "0645": None}

FORMAT_SHOWS = [
    show("AMBH", "AMB Cinemas", "Gachibowli", "09:05 AM", "0905", fmt="Barco Flagship Laser Dolby Atmos"),
    show("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad", "09:30 AM", "0930", fmt="Laser 4K Atmos"),
    show("ASJY", "Asian Jyothi", "RC Puram", "10:15 AM", "1015", fmt="4K Laser Dolby 7.1"),
    show("SUDA", "Sudarshan 35MM 4k Laser & Dolby Atmos", "RTC X Roads", "06:45 AM", "0645", fmt=""),
    show("PRHN", "Prasads Multiplex", "Hyderabad", "11:00 AM", "1100", fmt="Dolby Cinema"),
    show("PRHN", "Prasads Multiplex", "Hyderabad", "11:30 AM", "1130", fmt="Screen 3"),
    show("INOX", "INOX", "GVK One, Banjara Hills", "12:00 PM", "1200", fmt="2K Dolby"),
]


@pytest.fixture
def env(monkeypatch):
    store.save_catalogue({"movies": ENTRIES, "updated_at": "2026-09-25T20:00:00+05:30",
                          "sync": {"hyderabad": {"status": "OK", "message": "", "at": "2026-09-25T20:00:00+05:30",
                                                 "movie_count": len(ENTRIES)}}}, mirror=False)
    answers = {"ET00900001": priced(RAMAYANA_SHOWS, PRICES), "ET00900002": priced(IMAX_SHOWS),
               "ET00900003": priced([show("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad", "08:00 AM", "0800")]),
               "ET00900004": priced(JAILER_SHOWS),
               "ET00900005": priced([show("AMBH", "AMB Cinemas", "Gachibowli", "09:45 PM", "2145")])}
    bms = FakeBMS(answers)
    rd = make_reader(bms)
    S.install(rd)
    return bms, rd, answers


def rows(html: str) -> list[str]:
    return re.findall(r'<div class="tr-sx-row">.*?</a></div>|<div class="tr-sx-row">.*?No link</span></div>', html)


def row_for(html: str, clock: str) -> str:
    return next(r for r in rows(html) if f"<b>{clock}</b>" in r)


# ──────────────────────────────────────────────────────────────────────────
# 1–6 · Prices
# ──────────────────────────────────────────────────────────────────────────
def test_1_a_price_present_is_shown_in_its_row(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    row = row_for(body(app), "9:05")
    assert "₹150 – ₹450" in row and "3 seat types" in row
    # the tooltip names each category in BookMyShow's own words
    assert "EXECUTIVE ₹150 · GOLD ₹295 · RECLINER ₹450 (sold out)" in row
    # the price sits before the Book link and outside the facts line
    assert row.index('class="p range"') > row.index('class="f"') and row.index('class="p range"') < row.index("tr-sx-book")


def test_2_no_price_says_price_unavailable(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    row = row_for(body(app), "6:45")                     # Sudarshan: no seat categories listed
    assert "Price unavailable" in row and "₹" not in row


@pytest.mark.parametrize("raw", ["", None, "0", "0.00", "-50", "abc", "nan", "inf", True])
def test_2_an_unusable_listed_price_is_unknown_never_zero(raw):
    assert P.parse_amount(raw) is None
    rng = P.of_show([P.SeatPrice("GOLD", P.parse_amount(raw))])
    assert rng.label == P.UNAVAILABLE and rng.kind == "none"


def test_3_only_the_minimum_known_says_onwards(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    row = row_for(body(app), "10:15")                    # one tier priced, one not
    assert "₹180</b> <span class=\"on\">onwards</span>" in row and "1 of 2 seat types priced" in row
    assert "– ₹" not in row                               # the top of the range is never invented
    rng = P.of_show([P.SeatPrice("EXECUTIVE", 180.0), P.SeatPrice("RECLINER", None)])
    assert rng.label == "₹180 onwards" and rng.kind == "from" and rng.high == 180.0 and not rng.complete


def test_4_a_range_and_a_single_price():
    assert P.of_show([P.SeatPrice("A", 150.0), P.SeatPrice("B", 450.0)]).label == "₹150 – ₹450"
    assert P.of_show([P.SeatPrice("A", 295.0), P.SeatPrice("B", 295.0)]).label == "₹295"
    assert P.of_show([P.SeatPrice("A", 200.0)]).kind == "single"
    assert P.of_show([]).label == "Price unavailable"


def test_4_the_summary_above_the_results(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    html = body(app)
    # 6:45 has no price, 10:15 only a minimum: the summary is "onwards", with its coverage said
    m = re.search(r'<div class="tr-sx-pricesum (\w+)">(.*?)</div>', html)
    assert m and m.group(1) == "from" and "₹150 onwards" in m.group(2)
    assert re.search(r"Listed for \d+ of \d+ shows\.", m.group(2))
    assert "final amount at seat selection" in m.group(2)


def test_4_the_summary_is_a_range_only_when_every_show_is_fully_priced():
    a = explorer_show(session_id="1", seats=(P.SeatPrice("A", 150.0), P.SeatPrice("B", 450.0)))
    b = explorer_show(session_id="2", seats=(P.SeatPrice("A", 200.0),))
    c = explorer_show(session_id="3")
    rng, priced_n, total = Q.price_summary([a, b])
    assert (rng.label, priced_n, total) == ("₹150 – ₹450", 2, 2)
    rng, priced_n, total = Q.price_summary([a, b, c])
    assert (rng.label, priced_n, total) == ("₹150 onwards", 2, 3)
    assert Q.price_summary([c])[0].label == "Price unavailable"


def test_5_a_snapshot_never_supplies_a_price(env, monkeypatch):
    """Stale prices: the catalogue sync's snapshot (up to 30 hours old) is
    never a source of prices — not even if a record carried some."""
    bms, rd, answers = env
    rec = {"region": "hyderabad", "read_at": "2026-09-25T20:00:00+05:30",
           "venues": {"AMBH": ["AMB Cinemas", "Gachibowli"]},
           "shows": [["AMBH", TOMORROW, "0905", "09:05 AM", "2D", "", "s1", 0, {"price": "999"}]]}
    shows = S.shows_from_snapshot(rec, ramayana(), "bookmyshow:ET00900001")
    assert shows and all(s.seats == () and s.price.label == "Price unavailable" for s in shows)


def test_5_prices_are_never_carried_from_an_earlier_read(env):
    """A live read with prices, then (cache expired) BookMyShow refuses: the
    page falls back to the snapshot and says Price unavailable — the earlier
    read's prices do not reappear beside the fallback's showtimes."""
    clock = Clock()
    bms = FakeBMS({"ET00900001": priced(RAMAYANA_SHOWS), "ET00900002": 404})
    rd = make_reader(bms, clock=clock)
    w = S.Wanted(movie=ramayana(), movie_id="bookmyshow:ET00900001", title="Ramayana")
    first = S.read_one(w, TOMORROW, rd=rd)
    assert first.origin == S.LIVE and any(s.price.known for s in first.shows)
    snap.record("hyderabad", [], listed_ids={"bookmyshow:ET00900001"})
    data = snap.load()
    data["movies"]["bookmyshow:ET00900001"] = {
        "region": "hyderabad", "read_at": to_iso(now_ist()),
        "venues": {"AMBH": ["AMB Cinemas", "Gachibowli"]},
        "shows": [["AMBH", TOMORROW, "0905", "09:05 AM", "2D", "", "AMBH202609260905", 0]]}
    store.write_json(snap.path(), data, mirror=False)
    clock.t += S.CACHE_TTL + 1
    bms.answers["ET00900001"] = 403
    second = S.read_one(w, TOMORROW, rd=rd)
    assert second.origin == S.SNAPSHOT
    assert second.shows and not any(s.price.known for s in second.shows)
    assert "Prices appear only with a live BookMyShow read." in X.price_summary_markup(second.shows, [second])


def test_5_a_duplicate_keeps_the_copy_that_listed_prices():
    a = explorer_show(session_id="x", formats=("2D",))
    b = explorer_show(session_id="x", formats=("Dolby Atmos",), seats=(P.SeatPrice("GOLD", 295.0),))
    (kept,), dropped = Q.dedupe_shows([a, b])
    assert dropped == 1 and kept.seats == b.seats and kept.formats == ("2D", "Dolby Atmos")


def test_6_multiple_seat_categories_from_the_provider():
    """The provider's own parse, end to end: every category, its price and its
    sold-out flag; decimals kept, thousands grouped, nothing rounded."""
    cats = (SeatCategory("0001", "EXECUTIVE", Availability.AVAILABLE, price="150.00"),
            SeatCategory("0002", "GOLD", Availability.AVAILABLE, price="295.50"),
            SeatCategory("0003", "RECLINER", Availability.SOLD_OUT, price="1250.00"),
            SeatCategory("0004", "BOX", Availability.AVAILABLE, price=""))
    seats = S.seat_prices(cats)
    assert [(s.name, s.amount, s.sold_out) for s in seats] == [
        ("EXECUTIVE", 150.0, False), ("GOLD", 295.5, False), ("RECLINER", 1250.0, True), ("BOX", None, False)]
    rng = P.of_show(seats)
    assert rng.label == "₹150 onwards" and rng.categories == 4 and rng.priced == 3
    assert P.detail(seats) == "EXECUTIVE ₹150 · GOLD ₹295.50 · RECLINER ₹1,250 (sold out) · BOX no price listed"
    full = P.of_show(seats[:3])
    assert full.label == "₹150 – ₹1,250"


def test_6_prices_need_no_extra_request(env):
    bms, rd, _ = env
    pick_movie(ok(run(sx_date=TOMORROW)))
    # the base event and its premium sibling, once each — the same reads as before prices
    assert sorted(set(bms.calls)) == [("ET00900001", TOMORROW), ("ET00900002", TOMORROW),
                                      ("ET00900003", TOMORROW)]
    assert len(bms.calls) == 3


# ──────────────────────────────────────────────────────────────────────────
# 7–12 · Formats
# ──────────────────────────────────────────────────────────────────────────
def fshow(sid, *formats):
    return explorer_show(session_id=sid, formats=tuple(formats))


ATMOS_SET = [fshow("atmos", "Atmos"), fshow("dolbyatmos", "2D", "Dolby Atmos"),
             fshow("4katmos", "2D", "4K Dolby Atmos"), fshow("barco", "2D", "Barco Flagship Laser Dolby Atmos"),
             fshow("iv", "Infinity Vision Dolby Atmos"), fshow("laser4k", "Laser 4K Atmos"),
             fshow("cinema", "Dolby Cinema 2D"), fshow("d71", "2D", "Dolby 7.1"), fshow("plain", "2D"),
             fshow("none")]


def ids(shows, *formats):
    return sorted(s.session_id for s in Q.apply(shows, Q.Filters(TOMORROW, formats=formats)).shows)


def test_7_dolby_atmos_finds_every_show_that_names_it():
    assert ids(ATMOS_SET, "Dolby Atmos") == sorted(["atmos", "dolbyatmos", "4katmos", "barco", "iv", "laser4k"])


def test_7_dolby_cinema_and_dolby_7_1_are_not_atmos():
    assert "Dolby Atmos" not in F.features_of(("Dolby Cinema 2D",))
    assert "Dolby Atmos" not in F.features_of(("Dolby 7.1",))
    assert ids(ATMOS_SET, "Dolby Cinema") == ["cinema"]
    assert ids(ATMOS_SET, "Dolby 7.1") == ["d71"]


def test_8_atmos_with_4k_laser_is_found_by_either():
    s = fshow("both", "2D", "Laser 4K Atmos")
    assert {"Dolby Atmos", "4K Laser", "4K", "Laser projection", "2D"} <= s.features
    for wanted in ("Dolby Atmos", "4K Laser", "4K", "Laser projection"):
        assert ids([s], wanted) == ["both"], wanted


def test_9_several_formats_match_any_of_them():
    shows = [fshow("imax", "IMAX 3D"), fshow("pxl", "PXL"), fshow("epiq", "EPIQ"), fshow("2d", "2D")]
    assert ids(shows, "IMAX", "PXL") == ["imax", "pxl"]
    assert ids(shows, "IMAX", "PXL", "EPIQ") == ["epiq", "imax", "pxl"]
    # case-insensitive whole BookMyShow strings still work, as they always did
    assert ids(shows, "imax 3d") == ["imax"]


@pytest.mark.parametrize("text,features", [
    ("2K Air Cooled", ("2K", "Air cooled")),
    ("4K Laser", ("4K", "4K Laser", "Laser projection")),
    ("2K Laser Dolby 7.1", ("2K", "Laser projection", "Dolby 7.1")),
    ("Dolby Atmos Rgb Laser", ("Laser projection", "RGB Laser", "Dolby Atmos")),
    ("HDR By Barco", ("HDR", "Barco projection")),
    ("Laser & 7.1", ("Laser projection", "7.1 surround")),      # no "Dolby": never Dolby 7.1
    ("2K Dolby", ("2K", "Dolby sound")),
    ("7.1 Dolby", ("Dolby 7.1",)),
    ("Ms - Inf Vsn 4DX 3D", ("3D", "4DX", "Infinity Vision")),
    ("Lalitha Air Conditioned", ("Air conditioned",)),
    ("PXL", ("PXL",)),
    ("Screen 3", ("Screen 3",)),                                 # names nothing known: kept whole
])
def test_formats_are_read_from_bookmyshows_own_text(text, features):
    assert F.features_of_text(text) == features


def test_distinct_technologies_are_never_folded_together():
    for a, b in [("2K", "4K"), ("4K", "HDR"), ("PXL", "IMAX"), ("Dolby Cinema", "Dolby Atmos"), ("HDR", "4K")]:
        fa = F.features_of_text(a if a != "Dolby Cinema" else "Dolby Cinema")
        assert b not in fa, (a, b)
    # 4K alone is not a laser, a laser alone is not 4K
    assert "4K Laser" not in F.features_of_text("4K Dolby Atmos")
    assert "4K" not in F.features_of_text("Laser Dolby Atmos")


def test_every_real_bookmyshow_format_string_has_a_group():
    data = json.loads((ROOT / "data" / "showtimes.json").read_text(encoding="utf-8"))
    strings = {row[i] for rec in data["movies"].values() for row in rec["shows"] for i in (4, 5)}
    for text in strings:
        for feature in F.features_of_text(text):
            assert F.group_of(feature) in F.GROUPS
    # the dimension and the big three are where the picker promises them
    assert F.group_of("3D") == F.STANDARD and F.group_of("4K Laser") == F.SCREEN
    assert F.group_of("Dolby Atmos") == F.AUDIO and F.group_of("Air cooled") == F.MORE


def test_10_clear_shows_everything_again(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    before = len(rows(body(app)))
    app = ok(app.button_group(key="sx_fmt_std").select("3D").run())
    assert 0 < len(rows(body(app))) < before
    assert 'class="tr-sx-fmton"' in body(app)
    app = ok(app.button(key="sx_fmt_clear").click().run())
    assert len(rows(body(app))) == before and app.session_state["sx_fmt"] == []
    assert 'class="tr-sx-fmton"' not in body(app)
    # and the picker's own Clear
    app = ok(app.button_group(key="sx_fmt_std").select("3D").run())
    app = ok(app.button(key="sx_fmt_none").click().run())
    assert len(rows(body(app))) == before


def test_11_select_all_is_all_formats(env):
    """Every offered format ticked must not hide a show that lists no format
    (Sudarshan's 6:45 here, whose show names none)."""
    bms, rd, answers = env
    answers["ET00900001"] = priced(FORMAT_SHOWS)
    answers["ET00900002"] = 404
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    before = len(rows(body(app)))
    assert "<b>6:45</b>" in body(app)
    sudarshan = row_for(body(app), "6:45")
    assert '<div class="f"></div>' in sudarshan or 'class="tr-badge dim"' not in sudarshan
    app = ok(app.button(key="sx_fmt_all").click().run())
    assert len(rows(body(app))) == before and "<b>6:45</b>" in body(app)
    assert set(app.session_state["sx_fmt"]) == set(app.session_state["sx_fmt_avail"])
    assert X.format_summary(app.session_state["sx_fmt"], app.session_state["sx_fmt_avail"]) == "All formats"
    assert F.effective(["3D", "2D"], ["2D", "3D"]) == () and F.effective(["3D"], ["2D", "3D"]) == ("3D",)


def test_12_missing_and_unknown_formats():
    assert F.features_of(()) == frozenset() and F.features_of(("",)) == frozenset()
    shows = [fshow("none"), fshow("screen3", "Screen 3"), fshow("2d", "2D")]
    assert ids(shows) == ["2d", "none", "screen3"]                    # nothing chosen: everything
    assert ids(shows, "2D") == ["2d"]                                  # no format text: not claimed
    assert ids(shows, "Screen 3") == ["screen3"]
    assert F.grouped(["Screen 3", "Dolby Atmos", "2D"]) == [
        (F.STANDARD, ["2D"]), (F.AUDIO, ["Dolby Atmos"]), (F.MORE, ["Screen 3"])]


def test_the_picker_groups_what_the_shows_offer(env):
    bms, rd, answers = env
    answers["ET00900001"] = priced(FORMAT_SHOWS)
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    groups = {g.key: list(g.options) for g in app.button_group if g.key.startswith("sx_fmt_")}
    assert groups["sx_fmt_audio"] == ["Dolby Atmos", "Dolby 7.1", "Dolby sound"]
    assert {"Dolby Cinema", "4K", "4K Laser", "Laser projection", "Barco projection"} <= set(groups["sx_fmt_screen"])
    assert "Screen 3" in groups["sx_fmt_more"]
    # the closed field is one compact line
    app = ok(app.button_group(key="sx_fmt_audio").select("Dolby Atmos").run())
    app = ok(app.button_group(key="sx_fmt_screen").select("4K Laser").run())
    html = body(app)
    clocks = re.findall(r'<div class="t"><b>([\d:]+)</b>', html)
    # Atmos: AMB 9:05, PVR 9:30 (with 4K Laser); 4K Laser: Asian Jyothi 10:15 — and not Sudarshan,
    # whose *theatre name* says "4k Laser & Dolby Atmos" but whose show names no format
    assert clocks == ["9:05", "9:30", "10:15"]
    assert X.format_summary(app.session_state["sx_fmt"], app.session_state["sx_fmt_avail"]) == "4K Laser, Dolby Atmos"
    assert X.format_summary(["A", "B", "C", "D"], ["A", "B", "C", "D", "E"]) == "A, B +2"


def test_format_options_are_only_the_chosen_theatres_and_dates(env):
    """By theatre reads city-wide; the Format picker offers only what this
    theatre lists on this date — never another theatre's IMAX."""
    app = ok(run(sx_date=TOMORROW, sx_mode="theatre"))
    app = ok(app.selectbox(key="sx_theatre").select("AMB Cinemas: Gachibowli").run())
    offered = {o for g in app.button_group if g.key.startswith("sx_fmt_") for o in g.options}
    assert "IMAX" not in offered and "EPIQ" in offered      # IMAX is at Prasads; EPIQ at AMB
    shows = [explorer_show(session_id="a", venue_code="AMBH", formats=("EPIQ",)),
             explorer_show(session_id="b", venue_code="PRHN", formats=("IMAX 3D",)),
             explorer_show(session_id="c", venue_code="AMBH", formats=("PXL",), date_code=DAY_AFTER)]
    assert Q.options(shows, date_code=TOMORROW, venue_code="AMBH")["formats"] == ["EPIQ"]
    assert Q.options(shows, date_code=TOMORROW)["formats"] == ["3D", "IMAX", "EPIQ"]


# ──────────────────────────────────────────────────────────────────────────
# 13–18 · The row, and the page's state
# ──────────────────────────────────────────────────────────────────────────
def test_13_14_15_full_name_locality_and_booking_link_survive_the_price(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    html = body(app)
    pvr = row_for(html, "9:30")
    assert '<div class="n">PVR: Nexus Mall</div>' in pvr and '<div class="s">Kukatpally</div>' in pvr
    assert "₹200" in pvr and "1 seat type" in pvr
    # each row keeps its own theatre-and-date booking link
    assert f'href="{BMS}/buytickets/ramayana-hyderabad/cinema-hyde-PVFS-MT/{TOMORROW}"' in pvr
    amb = row_for(html, "9:05")
    assert f"cinema-hyde-AMBH-MT/{TOMORROW}" in amb and "cinema-hyde-PVFS" not in amb
    # the longest real names render whole
    sudarshan = row_for(html, "6:45")
    assert '<div class="n">Sudarshan 35MM 4k Laser &amp; Dolby Atmos: RTC X Roads</div>' in sudarshan
    assert '<div class="s">RTC X Roads</div>' in sudarshan


def test_16_a_new_date_reads_again_and_keeps_the_format_choice(env):
    bms, rd, answers = env
    answers["ET00900001"] = lambda date: priced([dict(s, date=date) for s in RAMAYANA_SHOWS], PRICES, date=date)
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.button_group(key="sx_fmt_std").select("3D").run())
    calls = len(bms.calls)
    app = ok(app.button(key=f"sxdate_{DAY_AFTER}").click().run())
    assert len(bms.calls) > calls and ("ET00900001", DAY_AFTER) in bms.calls
    assert app.session_state["sx_fmt"] == ["3D"]
    html = body(app)
    assert "10:40" in html and "9:05" not in html and "₹150 – ₹450" in html


def test_17_the_time_window_survives_format_and_price_work(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.time_input(key="sx_from").set_value(dtime(9, 0)).run())
    app = ok(app.time_input(key="sx_to").set_value(dtime(10, 30)).run())
    app = ok(app.button_group(key="sx_fmt_std").select("2D").run())
    app = ok(app.button(key="sxview_group").click().run())
    app = ok(app.button(key="sxview_time").click().run())
    assert app.session_state["sx_from"] == dtime(9, 0) and app.session_state["sx_to"] == dtime(10, 30)
    html = body(app)
    assert "9:00 AM – 10:30 AM" in html and "6:45" not in html and "11:00" not in html


def test_18_session_state_is_the_pages_own_and_consistent(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.button_group(key="sx_fmt_std").select("3D").run())
    app = ok(app.button_group(key="sx_fmt_std").select("2D").run())
    assert app.session_state["sx_fmt"] == ["2D", "3D"]
    assert app.button_group(key="sx_fmt_std").value == ["2D", "3D"]
    # the view switch and a mode round trip keep the choice
    app = ok(app.button(key="sxview_group").click().run())
    assert app.session_state["sx_fmt"] == ["2D", "3D"]
    new_keys = {"sx_fmt_avail", "sx_fmt_std", "sx_fmt_screen", "sx_fmt_audio", "sx_fmt_more", "sx_fmt_pop"}
    for key in new_keys:
        assert key.startswith("sx_")
    assert set(X.FMT_KEYS.values()) <= new_keys


def test_18_a_group_view_carries_prices(env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.button(key="sxview_group").click().run())
    html = body(app)
    assert '<span class="pr">₹150 – ₹450</span>' in html
    assert 'title="₹150 – ₹450"' in html                      # each time chip says its price on hover


# ──────────────────────────────────────────────────────────────────────────
# 19 · Cache and performance
# ──────────────────────────────────────────────────────────────────────────
def test_19_format_and_price_work_never_reads_bookmyshow(env):
    bms, rd, _ = env
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    reads = rd.reads
    app = ok(app.button_group(key="sx_fmt_std").select("3D").run())
    app = ok(app.button(key="sx_fmt_all").click().run())
    app = ok(app.button(key="sx_fmt_none").click().run())
    app = ok(app.button(key="sxview_group").click().run())
    assert rd.reads == reads


def test_19_the_snapshot_file_is_not_read_when_live_reads_succeed(env, monkeypatch):
    loads = []
    real = snap.load
    monkeypatch.setattr(snap, "load", lambda: loads.append(1) or real())
    w = S.Wanted(movie=ramayana(), movie_id="bookmyshow:ET00900001", title="Ramayana")
    days = S.read_many([w, w], TOMORROW)
    assert all(d.origin == S.LIVE for d in days) and loads == []
    # and a fallback still reads it — once for the whole query
    env[0].answers["ET00900001"] = 403
    S.install(make_reader(env[0]))
    days = S.read_many([w, S.Wanted(movie=ramayana(event_code="ET00900003"), movie_id="bookmyshow:ET00900003")],
                       TOMORROW)
    assert len(loads) == 1


def test_19_theatre_classification_is_cached():
    K.classify.cache_clear()
    for _ in range(50):
        K.classify("AMB Cinemas", "Gachibowli")
    info = K.classify.cache_info()
    assert info.misses == 1 and info.hits == 49
    assert K.classify("AMB Cinemas", "Gachibowli") == K.TheatreFacts("AMB Cinemas", K.UNKNOWN, "Gachibowli")


def test_19_one_read_builds_each_theatres_link_once(env, monkeypatch):
    calls = []
    real = S.BookMyShowProvider.venue_booking_url
    monkeypatch.setattr(S.BookMyShowProvider, "venue_booking_url",
                        lambda self, *a, **k: calls.append(a) or real(self, *a, **k))
    entry, _ = S.reader().read(ramayana(), TOMORROW)
    shows = S.shows_from_day(entry.day, ramayana(), "bookmyshow:ET00900001")
    venues = {(s.venue_code, s.date_code) for s in shows}
    assert len(shows) > len(venues) and len(calls) == len(venues)


# ──────────────────────────────────────────────────────────────────────────
# 20 · Layout: desktop and phone
# ──────────────────────────────────────────────────────────────────────────
def test_20_the_row_keeps_price_and_book_in_their_own_columns():
    css = theme.CSS
    desk = re.search(r"\.tr-sx-row \{ display:grid; grid-template-columns:([^;]+);", css).group(1)
    assert desk.strip() == "104px minmax(0,1fr) auto auto"            # time · theatre · price · Book
    assert ".tr-sx-row .f { display:flex; gap:5px; flex-wrap:wrap; justify-content:flex-start;" in css
    phone = css[css.index("@media (max-width: 768px)"):]
    phone = phone[:phone.index("@media (max-width: 480px)")]
    assert ".tr-sx-row .p { grid-column: 2; grid-row: 2;" in phone
    assert ".tr-sx-row .tr-sx-book { grid-column: 3; grid-row: 2; }" in phone
    assert ".tr-sx-row .m { grid-column: 2 / span 2; }" in phone


def test_20_the_format_picker_is_short_and_the_page_never_jumps():
    css = theme.CSS
    assert re.search(r'\[data-testid="stPopoverBody"\]:has\(\.tr-sx-fmtpop\) \{ width:min\(420px, calc\(100vw - 32px\)\)'
                     r' !important;\s+max-height:min\(72vh, 560px\); overflow-y:auto;', css)
    assert '[data-testid="stMain"]:has(.tr-sx-ambience) { overflow-anchor:none; }' in X.AMBIENCE_CSS


def test_20_price_markup_never_overlaps_book_in_markup_order():
    s = explorer_show(seats=(P.SeatPrice("A", 150.0), P.SeatPrice("B", 450.0)))
    html = X.time_rows_markup([s], "movie")
    assert re.search(r'<div class="m">.*<div class="f">.*</div></div><div class="p range"[^>]*>.*</div>'
                     r'<a class="tr-sx-book"', html)
    none = X.time_rows_markup([explorer_show()], "movie")
    assert '<div class="p none"' in none and "Price unavailable" in none
