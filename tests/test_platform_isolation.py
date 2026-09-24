"""PVR INOX ships disabled, and adding it changes nothing about BookMyShow.

* Disabled means disabled: no PVR request from the worker, discovery or the
  catalogue sync; no platform choice in the wizard; no PVR monitor saved.
* BookMyShow is read exactly as before — the production Infinity Vision
  monitor goes live and emails once on the real tick, with a PVR monitor
  in the same tick left untouched.
* The catalogues never mix: PVR's lives in its own file.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
import streamlit as st

import platforms
from monitor import catalogue, checker
from monitor.discovery import discover_siblings
from monitor.models import Availability, Snapshot, TheatreTarget, Venue
from monitor.state import load_monitors, load_state, upsert_monitor
from notifications.email import render_change
from platforms.pvr_inox import PvrInoxProvider, sale_dates
from tests.pvr_payloads import (
    FakePostSession,
    block,
    cinema,
    cinemas_payload,
    city_payload,
    city_row,
    nowshowing_payload,
    sessions_payload,
    show,
)
from tests.test_infinity_vision_live_matching import (
    AMB_3D,
    AMB_IV_2D,
    AMB_IV_SHOWS,
    SWEEP,
    film,  # noqa: F401 - fixture
    production_monitor,
    responses,
)
from tests.test_pvr_inox import COMMON, D1, EN_2D, EN_IMAX, FILM_EN, NOW
from tests.test_scheduling import run_app


def pvr_provider(responses_=()) -> tuple[PvrInoxProvider, FakePostSession]:
    session = FakePostSession(list(responses_))
    return PvrInoxProvider(session=session, sleeper=lambda _s: None, clock=lambda: 0.0, wall=lambda: NOW), session


def pvr_monitor():
    from monitor.models import Monitor

    return Monitor(movie=FILM_EN, targets=[TheatreTarget("101", "PVR 101", fmt="IMAX")], interval_minutes=10,
                   monitor_until=NOW + timedelta(days=2), notify_email="w@example.com", date_codes=[D1],
                   owner_uid="uid-test-1")


# ──────────────────────────────────────────────────────────────────────────
# Disabled
# ──────────────────────────────────────────────────────────────────────────
def test_pvr_inox_is_registered_but_disabled():
    assert platforms.get_provider("pvr_inox").slug == "pvr_inox"
    assert not platforms.is_enabled("pvr_inox")
    assert platforms.is_enabled("bookmyshow")
    assert [p.slug for p in platforms.enabled_platforms()] == ["bookmyshow"]
    assert platforms.platform_name("pvr_inox") == "PVR INOX" and platforms.platform_name("bookmyshow") == "BookMyShow"


def test_the_worker_never_reads_a_disabled_platform(monkeypatch):
    provider, session = pvr_provider()
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    mon = pvr_monitor()
    upsert_monitor(mon, mirror=False)

    report = checker.run_once(at=NOW, force=True, mirror=False, notifier=lambda *a: pytest.fail("no email"))
    assert session.calls == []
    assert report.failed == [mon.id]
    assert "not enabled" in load_state()[mon.id].last_error


def test_discovery_never_lists_a_disabled_platform():
    mon = pvr_monitor()
    asked: list = []
    report = discover_siblings([mon], [mon], {}, at=NOW, provider_for=lambda slug: asked.append(slug))
    assert asked == [] and not report.ran


def test_the_catalogue_sync_never_reads_a_disabled_platform(monkeypatch, isolated_data):
    provider, session = pvr_provider()
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    result = catalogue.sync_region("hyderabad", "pvr_inox", mirror=False)
    assert not result["ok"] and "not enabled" in result["message"]
    assert session.calls == [] and not list(isolated_data.glob("catalogue*.json"))


def test_the_wizard_offers_no_platform_choice_and_a_chosen_pvr_is_ignored():
    app = run_app(step=1, location="hyderabad", platform="pvr_inox")
    assert not [b for b in app.button if str(b.key).startswith("platform_")]

    from ui import catalogue_view as cv

    st.session_state["platform"] = "pvr_inox"
    try:
        assert cv.current_platform() == "bookmyshow"
    finally:
        st.session_state.pop("platform", None)


def test_no_monitor_is_saved_on_a_disabled_platform():
    snapshot = Snapshot(movie=FILM_EN, venues=[Venue("101", "PVR 101", formats=("IMAX 2D",))], showtimes=[])
    catalogue.store_snapshot(snapshot, mirror=False)
    app = run_app(step=5, location="hyderabad", movie_id=FILM_EN.id, theatres=["101"],
                  formats={"101": ["Any format"]}, platform="pvr_inox")
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert load_monitors() == []


# ──────────────────────────────────────────────────────────────────────────
# BookMyShow, exactly as before
# ──────────────────────────────────────────────────────────────────────────
def test_bookmyshow_infinity_vision_goes_live_with_a_disabled_pvr_monitor_beside_it(
        provider_factory, monkeypatch, at, film):
    iv = production_monitor(at, film)
    upsert_monitor(iv, mirror=False)
    pvr = replace(pvr_monitor(), monitor_until=at + timedelta(days=2))
    upsert_monitor(pvr, mirror=False)
    bms = provider_factory(responses(plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS))
    pvr_prov, pvr_session = pvr_provider()
    monkeypatch.setattr(checker, "get_provider", lambda slug: bms if slug == "bookmyshow" else pvr_prov)
    sent: list = []

    report = checker.run_once(at=at, force=True, mirror=False, notifier=lambda m, c: sent.append((m, c)))

    # BookMyShow read exactly as before — the base event and every sibling, once.
    assert [c["params"]["eventCode"] for c in bms.session.calls] == list(SWEEP)
    assert load_state()[iv.id].targets[AMB_IV_2D.key].availability is Availability.AVAILABLE
    [(m, change)] = sent
    assert (m.id, change.fmt) == (iv.id, "Infinity Vision 2D")
    # …and the disabled platform never reached.
    assert pvr_session.calls == [] and pvr.id in report.failed


def test_a_bookmyshow_email_is_worded_and_linked_exactly_as_before(provider_factory, monkeypatch, at, film):
    iv = production_monitor(at, film)
    upsert_monitor(iv, mirror=False)
    bms = provider_factory(responses(plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS))
    monkeypatch.setattr(checker, "get_provider", lambda slug: bms)
    sent: list = []
    checker.run_once(at=at, force=True, mirror=False, notifier=lambda m, c: sent.append((m, c)))
    [(m, change)] = sent
    _, html, text = render_change(m, change)
    assert "BOOK ON BOOKMYSHOW" in html and "Opens AMB Cinemas on BookMyShow." in html
    assert "Tap a showtime to open it on BookMyShow." in html
    assert "in.bookmyshow.com" in html and "pvrcinemas" not in html and "PVR INOX" not in html


def test_a_bookmyshow_monitor_never_carries_a_pvr_link_into_mail(at):
    from monitor.changes import Change, ChangeKind
    from monitor.models import Monitor, MovieRef

    bms_monitor = Monitor(movie=MovieRef("bookmyshow", "ET1", "Film", "HYD", "hyderabad"),
                          targets=[TheatreTarget("AMBH", "AMB", fmt="IMAX")], monitor_until=at + timedelta(days=1),
                          notify_email="w@example.com")
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=bms_monitor.id, target_key="AMBH::IMAX",
                    venue_name="AMB", fmt="IMAX", movie_title="Film", previous=Availability.UNKNOWN,
                    current=Availability.AVAILABLE, booking_url="https://www.pvrcinemas.com/seatlayout/x",
                    time_labels=["10:00 AM"], time_links=[["10:00 AM", "https://www.pvrcinemas.com/seatlayout/x"]])
    _, html, _ = render_change(bms_monitor, change)
    assert "pvrcinemas.com" not in html and "BOOK ON" not in html


# ──────────────────────────────────────────────────────────────────────────
# Catalogues never mix
# ──────────────────────────────────────────────────────────────────────────
def test_a_pvr_catalogue_row_lives_in_its_own_file(isolated_data):
    snapshot = Snapshot(movie=FILM_EN, venues=[Venue("101", "PVR 101", formats=("IMAX 2D",))], showtimes=[])
    catalogue.store_snapshot(snapshot, mirror=False)

    assert (isolated_data / "catalogue_pvr_inox.json").exists()
    assert not (isolated_data / "catalogue.json").exists()
    assert catalogue.list_entries("hyderabad") == []                       # BookMyShow's view: untouched
    assert [catalogue.movie_from_entry(e).id for e in catalogue.list_entries("hyderabad", "pvr_inox")] == [FILM_EN.id]
    assert catalogue.find_entry(FILM_EN.id) is not None
    assert catalogue.platform_of(FILM_EN.id) == "pvr_inox"
    assert catalogue.platform_of("bookmyshow:ET00514163") == "bookmyshow"


def test_an_enabled_pvr_sync_reads_the_city_once_and_writes_only_its_own_file(monkeypatch, isolated_data):
    monkeypatch.setattr(platforms, "PLATFORMS",
                        [replace(p, enabled=True) if p.slug == "pvr_inox" else p for p in platforms.PLATFORMS])
    today = sale_dates(days=1)[0]
    film_block = block(COMMON, [EN_2D, EN_IMAX], {"": [show("101", 1, "38431", today, "2330")]})
    session = FakePostSession([
        nowshowing_payload([EN_2D, EN_IMAX]),                                   # the film list
        city_payload(city_row(count=1)), cinemas_payload(cinema("101", "PVR Test Mall")),   # the cinemas
        sessions_payload(film_block), sessions_payload(), sessions_payload(),   # one cinema × three dates
    ])
    provider = PvrInoxProvider(session=session, sleeper=lambda _s: None, clock=lambda: 0.0)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)

    result = catalogue.sync_region("hyderabad", "pvr_inox", mirror=False)

    assert result["ok"] and result["detailed"] == 1
    assert session.operations() == ["content/nowshowing", "content/city", "content/cinemas",
                                    "content/csessions", "content/csessions", "content/csessions"]
    [entry] = catalogue.list_entries("hyderabad", "pvr_inox")
    assert [(v.code, v.name, v.formats) for v in catalogue.venues_from_entry(entry)] == [
        ("101", "PVR Test Mall", ("2D",))]
    assert not (isolated_data / "catalogue.json").exists()
    assert catalogue.sync_state("hyderabad", "pvr_inox")["status"].value == "OK"
    assert catalogue.sync_state("hyderabad")["status"].value == "NEVER"
