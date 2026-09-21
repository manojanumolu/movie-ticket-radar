"""The Formats step offers the formats BookMyShow lists *this movie* in at
each theatre, on the chosen dates — a theatre's capability is said apart.

The regression (15 Sep 2026): once the catalogue sync listed ALLU Cinemas for
Avengers Endgame: Encore (English) in Barco Laser 4K Atmos, the Formats step
offered Barco only. The day before — ALLU not yet listed for the film — it
had offered Barco *and* Dolby Cinema. ``selected_venues`` took the movie's
own row for a listed theatre (the formats of its current showtimes) and the
city directory (every format the catalogue has seen the theatre run) only
for a theatre that wasn't listed. The knowledge was there the whole time;
the code stopped looking at it the moment the theatre listed the film.

A release monitor exists to wait for a format the film hasn't opened yet at
a theatre. That format has to stay selectable whether or not the theatre is
already listed in some *other* format.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from monitor import catalogue, checker
from monitor.models import ANY_FORMAT, Availability, MovieRef, TheatreTarget
from monitor.state import load_monitors, load_state, upsert_monitor
from ui import catalogue_view as cv
from tests.conftest import build_payload
from tests.conftest import APP_SCRIPT
from tests.test_discovery import (
    ALLU, ALLU_DOLBY, ALLU_BARCO_LIVE, ALLU_DOLBY_LIVE, AMB_2D, BARCO, DOLBY, ENGLISH_2D, ENGLISH_3D, PVR_3D,
    SHOW_DATE, Wire, english_movie, listing, show,
)

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

AVENGERS = f"bookmyshow:{ENGLISH_2D}"


def other_film(code: str, title: str) -> MovieRef:
    return replace(english_movie(), event_code=code, title=title, variants=(),
                   source_url=f"https://in.bookmyshow.com/movies/hyderabad/x/buytickets/{code}")


def seed(provider_factory, *, avengers_at_allu: list[dict] | None):
    """Avengers (English) at AMB + PVR — and at ALLU only if given — while
    two other films show ALLU runs Dolby Cinema, and PVR runs IMAX + 4DX."""
    base = [AMB_2D] + (avengers_at_allu or [])
    catalogue.store_snapshot(provider_factory([build_payload(base), build_payload([PVR_3D])]).fetch(english_movie()),
                             mirror=False)
    dolby_here = show(ALLU_DOLBY, "DOLBY CINEMA", None)
    catalogue.store_snapshot(provider_factory([build_payload([dolby_here])]).fetch(other_film("ET00514261", "Mandaadi")),
                             mirror=False)
    catalogue.store_snapshot(provider_factory([build_payload([show(ALLU, "BARCO LASER 4K ATMOS", "3")])]).fetch(
        other_film("ET00487933", "Irumudi")), mirror=False)
    pvr = TheatreTarget("PVFS", "PVR", "Kukatpally", ANY_FORMAT)
    catalogue.store_snapshot(
        provider_factory([build_payload([show(pvr, "IMAX", "3"), show(pvr, "4DX 3D", "3", time="09:00 PM", code="2100"),
                                         show(ALLU_DOLBY, "Dolby Cinema", "3", time="10:00 PM", code="2200")])]).fetch(
            other_film("ET00498183", "Resident Evil")),
        mirror=False)


def run(**session):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state["page"] = "Home"
    for k, v in session.items():
        app.session_state[k] = v
    return app.run()


def markup(app) -> str:
    return " ".join(m.value for m in app.markdown)


# ──────────────────────────────────────────────────────────────────────────
# The exact regression
# ──────────────────────────────────────────────────────────────────────────
def test_the_options_are_the_movies_listing_plus_the_theatres_premium_screens_told_apart(provider_factory):
    """ALLU lists Avengers in Barco; ALLU's verified premium screen is Dolby
    Cinema. The picker offers Barco — what BookMyShow lists *this* movie in
    — and says Dolby Cinema is a premium screen here not listed for it; it
    does not offer Dolby as a current format."""
    seed(provider_factory, avengers_at_allu=[ALLU_BARCO_LIVE])
    [allu] = cv.selected_venues(AVENGERS, "hyderabad", ["ALUC"])
    assert allu.formats == ("Barco Laser 4K Atmos",)
    assert cv.listed_formats(AVENGERS, "hyderabad", ["ALUC"]) == {"ALUC": ("Barco Laser 4K Atmos",)}
    assert cv.capabilities("hyderabad", ["ALUC"]) == {"ALUC": ("Dolby Cinema",)}           # HyderabadTheatres
    assert cv.seen_formats("hyderabad", ["ALUC"]) == {"ALUC": ("Barco Laser 4K Atmos", "Dolby Cinema")}
    assert cv.coming_soon_codes(AVENGERS, "hyderabad", ["ALUC"]) == set()

    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["ALUC"])
    assert not app.exception
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {"fmt_ALUC_any", "fmt_ALUC_Dolby Cinema", "fmt_ALUC_Barco Laser 4K Atmos"}
    assert boxes["fmt_ALUC_Barco Laser 4K Atmos"].proto.help.startswith("Currently listed for this movie here on 25 Sep")
    assert boxes["fmt_ALUC_Dolby Cinema"].proto.help.startswith("ALLU Cinemas's Dolby Cinema screen (HyderabadTheatres). Not yet listed")
    assert "Premium screens here: Dolby Cinema — not yet listed for this movie here" in markup(app)
    app.checkbox(key="fmt_ALUC_Barco Laser 4K Atmos").check().run()
    app.checkbox(key="fmt_ALUC_Dolby Cinema").check().run()
    assert app.session_state["formats"] == {"ALUC": ["Dolby Cinema", "Barco Laser 4K Atmos"]}   # both, independently


def test_a_theatre_not_listing_the_film_offers_its_premium_screens_to_wait_for(provider_factory):
    """Coming soon: no format of the movie's — so the theatre's verified
    premium screen (ALLU: Dolby Cinema) is offered to wait for, beside
    every format; BookMyShow's strings for other films are not."""
    seed(provider_factory, avengers_at_allu=None)
    [allu] = cv.selected_venues(AVENGERS, "hyderabad", ["ALUC"])
    assert allu.formats == ()
    assert cv.coming_soon_codes(AVENGERS, "hyderabad", ["ALUC"]) == {"ALUC"}
    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["ALUC"])
    assert not app.exception
    assert {c.key for c in app.checkbox} == {"fmt_ALUC_any", "fmt_ALUC_Dolby Cinema"}
    body = markup(app)
    assert "Coming soon" in body and "Premium screens here: Dolby Cinema." in body
    assert "Barco Laser 4K Atmos" not in body                                  # seen for other films: not a capability
    app.checkbox(key="fmt_ALUC_Dolby Cinema").check().run()
    assert app.session_state["formats"] == {"ALUC": ["Dolby Cinema"]}


def test_every_theatre_gets_the_films_formats_not_its_own(provider_factory):
    seed(provider_factory, avengers_at_allu=[ALLU_BARCO_LIVE])
    by_code = {v.code: v for v in cv.selected_venues(AVENGERS, "hyderabad", ["PVFS", "AMBH", "ALUC"])}
    assert by_code["PVFS"].formats == ("3D",)                     # Avengers lists 3D; IMAX/4DX are seen for other films
    assert by_code["AMBH"].formats == ("2D",)
    assert by_code["ALUC"].formats == ("Barco Laser 4K Atmos",)
    assert cv.capabilities("hyderabad", ["PVFS"])["PVFS"] == ("4DX",)          # PVR Nexus: Audi 5 (4DX)
    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["PVFS", "ALUC"])
    keys = {c.key for c in app.checkbox}
    assert keys == {"fmt_PVFS_any", "fmt_PVFS_4DX", "fmt_PVFS_3D", "fmt_ALUC_any", "fmt_ALUC_Dolby Cinema", "fmt_ALUC_Barco Laser 4K Atmos"}
    assert "Premium screens here: 4DX — not yet listed for this movie here" in markup(app)
    assert "IMAX" not in {c.label for c in app.checkbox}                      # seen for another film, not a verified screen


def test_formats_are_deduplicated_on_the_checkers_own_key():
    assert cv.merge_formats(("Dolby Cinema",), ("DOLBY CINEMA", "Dolby Cinema ", "Laser", "Laser 3D")) == (
        "Dolby Cinema", "Laser", "Laser 3D")
    assert cv.merge_formats((), ("IMAX", "", None)) == ("IMAX",)
    assert cv.merge_formats(("Barco Laser 4K Atmos",), ("Dolby Cinema", "Barco Laser 4K Atmos")) == (
        "Barco Laser 4K Atmos", "Dolby Cinema")


# ──────────────────────────────────────────────────────────────────────────
# A known-but-unreleased format can be monitored, and is found on release
# ──────────────────────────────────────────────────────────────────────────
def test_a_known_unreleased_format_can_be_monitored_and_is_found_when_it_opens(
        provider_factory, monkeypatch):
    from config.timezone import now_ist

    at = now_ist()          # the UI gives a fresh monitor a real end time
    seed(provider_factory, avengers_at_allu=[ALLU_BARCO_LIVE])
    app = run(step=5, furthest=5, location="hyderabad", movie_id=AVENGERS, theatres=["ALUC"],
              formats={"ALUC": ["Dolby Cinema"]}, date_mode="any")
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception
    [monitor] = load_monitors()
    assert [t.key for t in monitor.targets] == ["ALUC::Dolby Cinema"]
    monitor.date_codes = [SHOW_DATE]
    upsert_monitor(monitor, mirror=False)

    sent = []
    wire = Wire(provider_factory, monkeypatch)
    # Dolby not released: ALLU is listed, in Barco — waiting, no email.
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")), shows=[[AMB_2D, ALLU_BARCO_LIVE], [PVR_3D], [ALLU_BARCO_LIVE]])
    report = checker.run_once(at=at, force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.checked == [monitor.id]
    assert load_state()[monitor.id].targets["ALUC::Dolby Cinema"].availability is Availability.SHOW_NOT_AVAILABLE
    assert sent == []

    # BookMyShow creates the Dolby event; discovery finds it; the monitor fires once.
    later = at + timedelta(minutes=20)
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER"), (DOLBY, "DOLBY CINEMA")),
              shows=[[AMB_2D, ALLU_BARCO_LIVE], [PVR_3D], [ALLU_BARCO_LIVE], [ALLU_DOLBY_LIVE]])
    report = checker.run_once(at=later, mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.discovery.updated == {monitor.id: [(DOLBY, "Dolby Cinema")]}
    assert load_state()[monitor.id].targets["ALUC::Dolby Cinema"].availability is Availability.AVAILABLE
    assert [(c.kind.value, c.fmt) for c in sent] == [("TICKETS_LIVE", "Dolby Cinema")]

    wire.tick(shows=[[AMB_2D, ALLU_BARCO_LIVE], [PVR_3D], [ALLU_BARCO_LIVE], [ALLU_DOLBY_LIVE]])
    checker.run_once(at=later + timedelta(minutes=10), mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1


# ──────────────────────────────────────────────────────────────────────────
# Date-aware: this movie, this theatre, this date — never the theatre's capability
# ──────────────────────────────────────────────────────────────────────────
PRASADS = TheatreTarget("PRHN", "Prasads Multiplex", "Hyderabad", ANY_FORMAT)
MARVEL = replace(english_movie(), title="Avengers Endgame: Encore", variants=())
OTHER = replace(english_movie(), event_code="ET00436621", title="The Paradise", variants=(),
                source_url="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00436621")


def dated(venue, fmt, date, status="3", time="07:15 PM", code="1915"):
    return {**show(venue, fmt, status, time=time, code=code), "date": date}


def store(provider_factory, movie, *payloads):
    """A movie detailed the way the sync does it: the default date, then the
    further bookable dates the platform lists (``catalogue.read_detail``)."""
    provider = provider_factory(list(payloads))
    catalogue.store_snapshot(catalogue.read_detail(provider, movie), mirror=False)
    return provider


def test_A_marvel_theatre_capable_and_bms_lists_infinity_vision_here_offers_it(provider_factory):
    # Prasads runs Infinity Vision for another film (capability)…
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "MS - Infinity Vision", "20260925")]))
    # …and BookMyShow lists Avengers at Prasads in Infinity Vision 2D and 3D on the 26th
    store(provider_factory, MARVEL,
          build_payload([dated(PRASADS, "Ms - Infinity Vsn", "20260926"),
                         dated(PRASADS, "Ms-Infinity Vsn 3D", "20260926", time="10:30 PM", code="2230")],
                        bookable_dates=("20260926",), closed_dates=()))
    [prasads] = cv.selected_venues(MARVEL.id, "hyderabad", ["PRHN"], ["20260926"])
    assert prasads.formats == ("Infinity Vision 2D", "Infinity Vision 3D")
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN"], show_dates=["20260926"])
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {"fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco", "fmt_PRHN_Infinity Vision 2D", "fmt_PRHN_Infinity Vision 3D"}
    assert boxes["fmt_PRHN_Infinity Vision 2D"].proto.help.startswith("Currently listed")
    assert boxes["fmt_PRHN_PCX"].proto.help.startswith("Prasads Multiplex's PCX screen")
    assert "Infinity Vision" in markup(app) and "Prasads Multiplex (2D, 3D)" in markup(app)   # the Marvel note


def test_B_marvel_theatre_capable_but_bms_does_not_list_it_for_this_movie_offers_nothing_of_it(provider_factory):
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "MS - Infinity Vision", "20260925")]))
    store(provider_factory, MARVEL, build_payload([dated(PRASADS, "2D", "20260925")]))
    [prasads] = cv.selected_venues(MARVEL.id, "hyderabad", ["PRHN"])
    assert prasads.formats == ("2D",)
    # Infinity Vision is not a verified capability of Prasads (HyderabadTheatres names PCX and HDR by Barco)
    assert cv.capabilities("hyderabad", ["PRHN"])["PRHN"] == ("PCX", "HDR By Barco")
    assert cv.seen_formats("hyderabad", ["PRHN"])["PRHN"] == ("2D", "Infinity Vision 2D")
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN"])
    assert {c.key for c in app.checkbox} == {"fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco", "fmt_PRHN_2D"}
    body = markup(app)
    assert "Premium screens here: PCX · HDR By Barco — not yet listed for this movie here" in body
    assert "Infinity Vision" not in body                                     # neither a listing nor a verified capability


def test_C_same_theatre_no_listing_on_the_25th_infinity_vision_on_the_26th(provider_factory):
    """The catalogue reads the default date and the next bookable dates; the
    wizard answers per date: on the 25th Prasads is coming soon (its
    premium screens to wait for), on the 26th its actual Infinity Vision."""
    default = build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")], bookable_dates=("20260925", "20260926"), closed_dates=())
    twenty_sixth = build_payload([dated(PRASADS, "MS - Infinity Vision", "20260926"),
                                  dated(PRASADS, "MS-Infinity Vision 3D", "20260926", time="10:30 PM", code="2230"),
                                  dated(ALLU, "DOLBY CINEMA", "20260926")],
                                 bookable_dates=("20260925", "20260926"), closed_dates=())
    store(provider_factory, MARVEL, default, twenty_sixth)
    entry = cv.entry(MARVEL.id, "hyderabad")
    assert set(catalogue.listings_from_entry(entry)) == {"20260925", "20260926"}
    assert cv.listed_dates(MARVEL.id, "hyderabad", "PRHN") == {"20260926": ("Infinity Vision 2D", "Infinity Vision 3D")}

    assert cv.coming_soon_codes(MARVEL.id, "hyderabad", ["PRHN", "ALUC"], ["20260925"]) == {"PRHN"}
    assert cv.coming_soon_codes(MARVEL.id, "hyderabad", ["PRHN", "ALUC"], ["20260926"]) == set()
    assert cv.listed_formats(MARVEL.id, "hyderabad", ["PRHN"], ["20260925"]) == {"PRHN": ()}
    assert cv.listed_formats(MARVEL.id, "hyderabad", ["PRHN"], ["20260926"]) == {"PRHN": ("Infinity Vision 2D", "Infinity Vision 3D")}
    assert cv.listed_formats(MARVEL.id, "hyderabad", ["PRHN"]) == {"PRHN": ("Infinity Vision 2D", "Infinity Vision 3D")}

    on_25 = run(step=4, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN", "ALUC"], show_dates=["20260925"])
    assert {c.key for c in on_25.checkbox} == {"fmt_ALUC_any", "fmt_ALUC_Dolby Cinema", "fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco"}
    assert on_25.session_state["formats"]["PRHN"] == []                          # nothing chosen yet, nothing assumed
    body = markup(on_25)
    assert "Not listed for this movie on 25 Sep 2026 yet" in body and "Premium screens here: PCX · HDR By Barco." in body
    assert "Infinity Vision" not in body                                        # no leak from the 26th
    on_26 = run(step=4, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN", "ALUC"], show_dates=["20260926"])
    boxes = {c.key: c for c in on_26.checkbox}
    assert {"fmt_PRHN_Infinity Vision 2D", "fmt_PRHN_Infinity Vision 3D", "fmt_ALUC_Dolby Cinema", "fmt_PRHN_PCX"} <= set(boxes)
    assert boxes["fmt_PRHN_Infinity Vision 2D"].proto.help.startswith("Currently listed for this movie here on 26 Sep")
    assert boxes["fmt_PRHN_PCX"].proto.help.startswith("Prasads Multiplex's PCX screen")   # a watch target, not a listing
    assert "Prasads Multiplex (2D, 3D)" in markup(on_26)
    # the theatre step is date-aware the same way
    theatres_25 = run(step=3, furthest=5, location="hyderabad", movie_id=MARVEL.id, show_dates=["20260925"])
    assert "th_PRHN" not in {b.key for b in theatres_25.button if not (b.key or "").startswith("feat_")}


def test_D_non_marvel_at_a_premium_theatre_lists_only_what_bms_lists(provider_factory):
    store(provider_factory, MARVEL, build_payload([dated(PRASADS, "MS - Infinity Vision", "20260925")]))
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "2D", "20260925"),
                                                  dated(PRASADS, "DOLBY CINEMA 2D", "20260925", time="10:30 PM", code="2230")]))
    [prasads] = cv.selected_venues(OTHER.id, "hyderabad", ["PRHN"])
    assert prasads.formats == ("2D", "Dolby Cinema 2D")
    app = run(step=4, furthest=4, location="hyderabad", movie_id=OTHER.id, theatres=["PRHN"])
    assert {c.key for c in app.checkbox} == {"fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco", "fmt_PRHN_2D", "fmt_PRHN_Dolby Cinema 2D"}
    body = markup(app)
    assert "Premium screens here: PCX · HDR By Barco — not yet listed for this movie here" in body
    assert "Infinity Vision" not in body                                       # seen for Avengers: not this movie, not a capability
    assert "Pick it below to watch that screen" not in body                   # the Marvel note is not drawn for The Paradise


def test_E_non_marvel_current_formats_are_exactly_bms_plus_premium_watch_targets(provider_factory):
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "3D", "20260925"),
                                                  dated(ALLU, "HDR BY BARCO", "20260925"),
                                                  dated(ALLU, "DOLBY CINEMA 3D", "20260925", time="10:30 PM", code="2230")]))
    by_code = {v.code: v for v in cv.selected_venues(OTHER.id, "hyderabad", ["PRHN", "ALUC"])}
    assert by_code["PRHN"].formats == ("3D",) and by_code["ALUC"].formats == ("Dolby Cinema 3D", "HDR By Barco")
    app = run(step=4, furthest=4, location="hyderabad", movie_id=OTHER.id, theatres=["PRHN", "ALUC"])
    boxes = {c.key: c for c in app.checkbox}
    # the movie's own BMS formats, plus each theatre's verified premium screens as watch targets
    assert set(boxes) == {"fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco", "fmt_PRHN_3D",
                          "fmt_ALUC_any", "fmt_ALUC_Dolby Cinema", "fmt_ALUC_Dolby Cinema 3D", "fmt_ALUC_HDR By Barco"}
    assert boxes["fmt_ALUC_Dolby Cinema"].proto.help.startswith("Currently listed")          # listed as "Dolby Cinema 3D": covered
    assert boxes["fmt_PRHN_3D"].proto.help.startswith("Currently listed") and boxes["fmt_PRHN_PCX"].proto.help.startswith("Prasads")
    assert "Infinity Vision" not in markup(app)


def test_F_capability_never_stands_in_for_a_listing_but_still_guards_the_start(provider_factory):
    from ui import flow

    store(provider_factory, MARVEL, build_payload([dated(PRASADS, "MS - Infinity Vision", "20260925")]))
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "2D", "20260925")]))
    [prasads] = cv.selected_venues(OTHER.id, "hyderabad", ["PRHN"])
    capable = (*cv.capabilities("hyderabad", ["PRHN"])["PRHN"], *cv.seen_formats("hyderabad", ["PRHN"])["PRHN"])
    assert "PCX" not in prasads.formats and "PCX" in capable
    # the start guard still refuses a format nobody has ever seen the theatre run, and nothing else
    assert flow.unknown_formats(prasads, ["IMAX"], capable) == ["IMAX"]
    assert flow.unknown_formats(prasads, ["2D", "PCX", "Infinity Vision 2D", ANY_FORMAT], capable) == []


def test_the_monitoring_step_says_what_is_not_listed_on_the_chosen_date(provider_factory):
    default = build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")], bookable_dates=("20260925", "20260926"), closed_dates=())
    twenty_sixth = build_payload([dated(PRASADS, "MS - Infinity Vision", "20260926"), dated(ALLU, "DOLBY CINEMA", "20260926")],
                                 bookable_dates=("20260925", "20260926"), closed_dates=())
    store(provider_factory, MARVEL, default, twenty_sixth)
    app = run(step=5, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN", "ALUC"],
              formats={"PRHN": ["Infinity Vision 2D"], "ALUC": ["Dolby Cinema"]}, date_mode="single")
    from datetime import date
    app.date_input(key="show_date_single").set_value(date(2026, 9, 25)).run()
    body = " ".join(c.value for c in app.caption)
    assert "Not listed on 25 Sep 2026 yet: Prasads Multiplex · Infinity Vision 2D" in body and "ALLU" not in body.split("Not listed")[1]
    app.date_input(key="show_date_single").set_value(date(2026, 9, 26)).run()
    assert "Not listed on" not in " ".join(c.value for c in app.caption)


def test_old_rows_without_listings_fall_back_to_their_undated_formats(provider_factory):
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "2D", "20260925")]))
    entry = cv.entry(OTHER.id, "hyderabad")
    entry.pop("listings", None)                                            # a row from before listings were kept
    catalogue._upsert([entry], region_slug="hyderabad", mirror=False)
    cv.view.cache_clear() if hasattr(cv.view, "cache_clear") else None
    assert cv.listings(OTHER.id, "hyderabad") == {"": {"PRHN": ("2D",)}}
    assert cv.listed_formats(OTHER.id, "hyderabad", ["PRHN"], ["20260926"]) == {"PRHN": ("2D",)}
    assert cv.coming_soon_codes(OTHER.id, "hyderabad", ["PRHN"], ["20260926"]) == set()


# ──────────────────────────────────────────────────────────────────────────
# Premium capability (HyderabadTheatres) vs BookMyShow listing — the PXL class of bug
# ──────────────────────────────────────────────────────────────────────────
LAKESHORE = TheatreTarget("ILKS", "PVR Lakeshore Mall", "Y Junction", ANY_FORMAT)
SUPERPLEX = TheatreTarget("PIIC", "PVR Superplex Inorbit", "Cyberabad", ANY_FORMAT)


def test_1_lakeshore_has_pxl_and_offers_it_before_the_movie_is_listed(provider_factory):
    from config.theatre_capabilities import premium_formats, source_page

    assert premium_formats("ILKS") == ("PXL",) and "pvr-lakeshore-mall-y-junction" in source_page("ILKS")
    store(provider_factory, OTHER, build_payload([dated(LAKESHORE, "PLAYHOUSE", "20260925")]))   # Lakeshore known to BMS via another film
    store(provider_factory, MARVEL, build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")]))
    assert cv.coming_soon_codes(MARVEL.id, "hyderabad", ["ILKS"]) == {"ILKS"}
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["ILKS"])
    assert not app.exception
    assert {c.key for c in app.checkbox} == {"fmt_ILKS_any", "fmt_ILKS_PXL"}
    body = markup(app)
    assert "Coming soon" in body and "Premium screens here: PXL." in body and "Playhouse" not in body
    app.checkbox(key="fmt_ILKS_PXL").check().run()
    assert app.session_state["formats"] == {"ILKS": ["PXL"]}
    # …and the monitor starts with PXL at Lakeshore
    app = run(step=5, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["ILKS"], formats={"ILKS": ["PXL"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    [monitor] = load_monitors()
    assert [t.key for t in monitor.targets] == ["ILKS::PXL"]


def test_2_a_theatre_with_several_premium_screens_offers_them_all_when_not_listed(provider_factory):
    store(provider_factory, MARVEL, build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")]))
    store(provider_factory, OTHER, build_payload([dated(SUPERPLEX, "2D", "20260925")]))
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["PIIC"])
    assert {c.key for c in app.checkbox} == {"fmt_PIIC_any", "fmt_PIIC_PXL", "fmt_PIIC_LUXE", "fmt_PIIC_4DX"}
    assert "Premium screens here: PXL · LUXE · 4DX." in markup(app)


def test_3_listed_only_in_2d_the_premium_screen_is_a_watch_target_not_a_claim(provider_factory):
    store(provider_factory, MARVEL, build_payload([dated(LAKESHORE, "2D", "20260925")]))
    [lakeshore] = cv.selected_venues(MARVEL.id, "hyderabad", ["ILKS"])
    assert lakeshore.formats == ("2D",)
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["ILKS"])
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {"fmt_ILKS_any", "fmt_ILKS_PXL", "fmt_ILKS_2D"}
    assert boxes["fmt_ILKS_2D"].proto.help.startswith("Currently listed")
    assert boxes["fmt_ILKS_PXL"].proto.help.startswith("PVR Lakeshore Mall's PXL screen")    # a watch target, not claimed
    assert "Premium screens here: PXL — not yet listed for this movie here" in markup(app)


def test_4_and_5_pxl_is_a_movie_format_only_when_bms_lists_it(provider_factory):
    default = build_payload([dated(LAKESHORE, "2D", "20260925")], bookable_dates=("20260925", "20260926"), closed_dates=())
    twenty_sixth = build_payload([dated(LAKESHORE, "2D", "20260926"), dated(LAKESHORE, "PXL", "20260926", time="10:30 PM", code="2230")],
                                 bookable_dates=("20260925", "20260926"), closed_dates=())
    store(provider_factory, MARVEL, default, twenty_sixth)
    assert cv.listed_formats(MARVEL.id, "hyderabad", ["ILKS"], ["20260925"]) == {"ILKS": ("2D",)}
    assert cv.listed_formats(MARVEL.id, "hyderabad", ["ILKS"], ["20260926"]) == {"ILKS": ("2D", "PXL")}
    on_26 = run(step=4, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["ILKS"], show_dates=["20260926"])
    b26 = {c.key: c for c in on_26.checkbox}
    assert set(b26) == {"fmt_ILKS_any", "fmt_ILKS_PXL", "fmt_ILKS_2D"}
    assert b26["fmt_ILKS_PXL"].proto.help.startswith("Currently listed for this movie here on 26 Sep")   # 4: actual movie format
    on_25 = run(step=4, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["ILKS"], show_dates=["20260925"])
    b25 = {c.key: c for c in on_25.checkbox}
    assert set(b25) == {"fmt_ILKS_any", "fmt_ILKS_PXL", "fmt_ILKS_2D"}
    assert b25["fmt_ILKS_PXL"].proto.help.startswith("PVR Lakeshore Mall's PXL screen")                # 5: not claimed, still watchable
    assert "Premium screens here: PXL — not yet listed for this movie here on 25 Sep" in markup(on_25)


def test_9_10_11_only_named_premium_formats_are_capabilities():
    from config.theatre_capabilities import NOT_FORMATS, PREMIUM_SCREENS, premium_formats

    every = {f for _, screens in PREMIUM_SCREENS.values() for f in screens}
    for generic in ("4K", "4K Laser", "Dolby Atmos", "Atmos", "DTS:X", "Recliner Seats", "Luxury Seating", "Play House", "Playhouse"):
        assert generic not in every and generic in NOT_FORMATS
    assert {"PCX", "PXL", "EPIQ", "Dolby Cinema", "4DX", "HDR By Barco", "Macro XE", "LUXE"} <= every
    assert premium_formats("PRHN") == ("PCX", "HDR By Barco") and premium_formats("ALUC") == ("Dolby Cinema",)
    assert premium_formats("ACEV") == ("EPIQ",) and premium_formats("ACAS") == ("EPIQ",)
    assert premium_formats("CTNR") == ("Macro XE",) and premium_formats("PVFS") == ("4DX",)
    assert premium_formats("ZZZZ") == ()
    assert not any("Infinity Vision" in f for f in every)      # not verified by the source for any theatre


def test_a_premium_watch_matches_the_screen_under_bookmyshows_own_name():
    from platforms.bookmyshow import clean_format

    # PVR writes its PXL screen "Pxl"; AAA's EPIQ screen is sold as "Led Screen Dolby Atmos"
    assert TheatreTarget("ILKS", "PVR Lakeshore", "", "PXL").matches_format(clean_format("PXL 4K LASER ATMOS"))
    assert TheatreTarget("PIIC", "PVR Superplex", "", "PXL").matches_format(clean_format("pxl"))
    assert TheatreTarget("ACAS", "AAA Cinemas", "", "EPIQ").matches_format(clean_format("LED SCREEN DOLBY ATMOS"))
    assert not TheatreTarget("ACAS", "AAA Cinemas", "", "EPIQ").matches_format(clean_format("LASER DOLBY ATMOS"))
    assert TheatreTarget("AMBH", "AMB", "", "MB LUXE").matches_format(clean_format("M B LUXE"))
    assert TheatreTarget("CTNR", "Cinepolis", "", "Macro XE").matches_format(clean_format("MACRO XE"))
    # ordinary matching is untouched
    assert TheatreTarget("ALUC", "ALLU", "", "Dolby Cinema").matches_format("Dolby Cinema 3D")
    assert not TheatreTarget("ALUC", "ALLU", "", "IMAX").matches_format("Dolby Cinema 3D")


# ──────────────────────────────────────────────────────────────────────────
# The exact bugs: AAA's EPIQ, AMB's two premium screens, both selectable, saved as two targets
# ──────────────────────────────────────────────────────────────────────────
AAA = TheatreTarget("ACAS", "AAA Cinemas", "Ameerpet", ANY_FORMAT)
AMB = TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", ANY_FORMAT)


def test_aaa_exposes_epiq_not_a_generic_led_screen(provider_factory):
    store(provider_factory, OTHER, build_payload([dated(AAA, "LED SCREEN DOLBY ATMOS", "20260925"),
                                                  dated(AAA, "LASER DOLBY ATMOS", "20260925", time="10:30 PM", code="2230")]))
    app = run(step=4, furthest=4, location="hyderabad", movie_id=OTHER.id, theatres=["ACAS"])
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {"fmt_ACAS_any", "fmt_ACAS_EPIQ", "fmt_ACAS_Laser Dolby Atmos"}   # the LED label folds into EPIQ
    assert boxes["fmt_ACAS_EPIQ"].proto.help.startswith("Currently listed")               # it is AAA's EPIQ screen, listed
    # …and unlisted, EPIQ is still the watch target
    store(provider_factory, MARVEL, build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")]))
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["ACAS"])
    assert {c.key for c in app.checkbox} == {"fmt_ACAS_any", "fmt_ACAS_EPIQ"}


def test_amb_offers_hdr_and_mb_luxe_both_selectable_with_states(provider_factory):
    store(provider_factory, MARVEL, build_payload([dated(AMB, "HDR BY BARCO", "20260925"),
                                                   dated(AMB, "BARCO FLAGSHIP LASER DOLBY ATMOS", "20260925", time="10:30 PM", code="2230")]))
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["AMBH"])
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {"fmt_AMBH_any", "fmt_AMBH_HDR By Barco", "fmt_AMBH_MB LUXE", "fmt_AMBH_VIP",
                          "fmt_AMBH_Barco Flagship Laser Dolby Atmos"}
    assert boxes["fmt_AMBH_HDR By Barco"].proto.help.startswith("Currently listed")          # 5: HDR = currently listed
    assert boxes["fmt_AMBH_MB LUXE"].proto.help.startswith("AMB Cinemas's MB LUXE screen")    # 5: MB LUXE = watching, not claimed
    app.checkbox(key="fmt_AMBH_HDR By Barco").check().run()
    app.checkbox(key="fmt_AMBH_MB LUXE").check().run()
    assert app.session_state["formats"] == {"AMBH": ["HDR By Barco", "MB LUXE"]}             # 4: both
    # 11: saved as two independent theatre+format targets
    app = run(step=5, furthest=5, location="hyderabad", movie_id=MARVEL.id, theatres=["AMBH"],
              formats={"AMBH": ["HDR By Barco", "MB LUXE"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    [monitor] = load_monitors()
    assert sorted(t.key for t in monitor.targets) == ["AMBH::HDR By Barco", "AMBH::MB LUXE"]
    assert monitor.target("AMBH::MB LUXE").matches_format("M B LUXE") and not monitor.target("AMBH::MB LUXE").matches_format("HDR By Barco")


def test_prasads_premium_screens_stay_selectable_when_the_movie_is_not_listed(provider_factory):
    store(provider_factory, MARVEL, build_payload([dated(ALLU, "DOLBY CINEMA", "20260925")]))
    store(provider_factory, OTHER, build_payload([dated(PRASADS, "2D", "20260925")]))
    app = run(step=4, furthest=4, location="hyderabad", movie_id=MARVEL.id, theatres=["PRHN"])
    assert {c.key for c in app.checkbox} == {"fmt_PRHN_any", "fmt_PRHN_PCX", "fmt_PRHN_HDR By Barco"}
    app.checkbox(key="fmt_PRHN_PCX").check().run()
    assert app.session_state["formats"] == {"PRHN": ["PCX"]}
