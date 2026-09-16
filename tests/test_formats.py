"""The Formats step offers every format a theatre is known to run.

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
def test_allu_offers_barco_and_dolby_once_it_lists_the_film_in_barco(provider_factory):
    seed(provider_factory, avengers_at_allu=[ALLU_BARCO_LIVE])
    [allu] = cv.selected_venues(AVENGERS, "hyderabad", ["ALUC"])
    assert allu.formats == ("Barco Laser 4K Atmos", "Dolby Cinema")     # listed first, known after
    assert cv.listed_formats(AVENGERS, "hyderabad", ["ALUC"]) == {"ALUC": ("Barco Laser 4K Atmos",)}
    assert cv.coming_soon_codes(AVENGERS, "hyderabad", ["ALUC"]) == set()

    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["ALUC"])
    assert not app.exception
    keys = {c.key for c in app.checkbox}
    assert keys == {"fmt_ALUC_any", "fmt_ALUC_Barco Laser 4K Atmos", "fmt_ALUC_Dolby Cinema"}
    assert "Dolby Cinema: not listed for this movie here yet" in markup(app)
    # …and Dolby is selectable, on its own or beside Barco.
    app.checkbox(key="fmt_ALUC_Dolby Cinema").check().run()
    assert app.session_state["formats"] == {"ALUC": ["Dolby Cinema"]}
    app.checkbox(key="fmt_ALUC_Barco Laser 4K Atmos").check().run()
    assert app.session_state["formats"] == {"ALUC": ["Barco Laser 4K Atmos", "Dolby Cinema"]}
    app.checkbox(key="fmt_ALUC_any").check().run()
    assert app.session_state["formats"] == {"ALUC": [ANY_FORMAT]}


def test_the_same_theatre_before_it_lists_the_film_offers_the_same_formats(provider_factory):
    """Coming soon or listed, the option list is the theatre's, not the day's."""
    seed(provider_factory, avengers_at_allu=None)
    [allu] = cv.selected_venues(AVENGERS, "hyderabad", ["ALUC"])
    assert set(allu.formats) == {"Barco Laser 4K Atmos", "Dolby Cinema"}
    assert cv.coming_soon_codes(AVENGERS, "hyderabad", ["ALUC"]) == {"ALUC"}
    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["ALUC"])
    assert {c.key for c in app.checkbox} == {"fmt_ALUC_any", "fmt_ALUC_Barco Laser 4K Atmos", "fmt_ALUC_Dolby Cinema"}
    assert "Coming soon" in markup(app)


def test_every_theatre_gets_all_its_known_formats_not_just_this_films(provider_factory):
    seed(provider_factory, avengers_at_allu=[ALLU_BARCO_LIVE])
    by_code = {v.code: v for v in cv.selected_venues(AVENGERS, "hyderabad", ["PVFS", "AMBH", "ALUC"])}
    assert by_code["PVFS"].formats == ("3D", "4DX 3D", "IMAX")            # Avengers lists 3D; 4DX/IMAX known
    assert by_code["AMBH"].formats == ("2D",)
    assert by_code["ALUC"].formats == ("Barco Laser 4K Atmos", "Dolby Cinema")
    app = run(step=4, furthest=4, location="hyderabad", movie_id=AVENGERS, theatres=["PVFS", "ALUC"])
    keys = {c.key for c in app.checkbox}
    assert {"fmt_PVFS_IMAX", "fmt_PVFS_4DX 3D", "fmt_PVFS_3D", "fmt_ALUC_Dolby Cinema"} <= keys


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
