"""PVR INOX through the real worker tick — with the platform switched on for
the test only (production ships it disabled; see ``test_platform_isolation``).

Everything below the provider runs for real: sharing, evaluation, state,
change detection, notification and history. The provider answers from
production-shaped payloads (``tests/pvr_payloads.py``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import platforms
from monitor import checker
from monitor.discovery import DiscoveryReport
from monitor.models import Availability, Monitor, TheatreTarget
from monitor.sharing import fetch_identity, select_for_fetch
from monitor.state import load_state, upsert_monitor
from notifications.email import render_change
from platforms.pvr_inox import PvrInoxProvider
from tests.pvr_payloads import FakePostSession, block, film_print, sessions_payload, show
from tests.test_pvr_inox import COMMON, D1, EN_2D, EN_IMAX, FILM_EN, NOW

IMAX_3D_PRINT = film_print("38470", COMMON, "English", "IMAX 3D")
PRINTS = [EN_2D, EN_IMAX, IMAX_3D_PRINT]


@pytest.fixture
def pvr_enabled(monkeypatch):
    """PVR INOX switched on for this test only."""
    monkeypatch.setattr(platforms, "PLATFORMS",
                        [replace(p, enabled=True) if p.slug == "pvr_inox" else p for p in platforms.PLATFORMS])


@pytest.fixture
def no_discovery(monkeypatch):
    monkeypatch.setattr(checker, "discover_siblings", lambda *a, **k: DiscoveryReport())


def imax3d(theatre: str, session: int, hhmm: str = "1900", **kw) -> dict:
    return show(theatre, session, "38470", D1, hhmm, movie_format="IMAX 3D", screen_type="IMAX", **kw)


def listing(*shows: dict) -> dict:
    return sessions_payload(block(COMMON, PRINTS, {"IMAX": list(shows)})) if shows else sessions_payload()


def monitor(*cinemas: str, fmt: str = "IMAX 3D", owner: str = "uid-test-1") -> Monitor:
    return Monitor(movie=FILM_EN, targets=[TheatreTarget(c, f"PVR {c}", fmt=fmt) for c in cinemas],
                   interval_minutes=10, monitor_until=NOW + timedelta(days=2),
                   notify_email="watcher@example.com", date_codes=[D1], owner_uid=owner)


def tick(monkeypatch, responses: list, sent: list, when=NOW) -> tuple[checker.RunReport, FakePostSession]:
    session = FakePostSession(responses)
    provider = PvrInoxProvider(session=session, sleeper=lambda _s: None, clock=lambda: 0.0, wall=lambda: when)
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    report = checker.run_once(at=when, force=True, mirror=False, notifier=lambda m, c: sent.append((m, c)))
    return report, session


def test_a_pvr_monitor_goes_live_once_and_links_to_the_show(pvr_enabled, no_discovery, monkeypatch):
    mon = monitor("101")
    upsert_monitor(mon, mirror=False)
    sent: list = []

    # Not listed yet at the cinema: waiting, silent.
    report, session = tick(monkeypatch, [listing()], sent)
    assert report.checked == [mon.id] and sent == []
    assert session.cinema_dates() == [("101", "2026-09-25")]          # only the watched cinema, only the date
    assert load_state()[mon.id].targets[mon.targets[0].key].availability is Availability.THEATRE_NOT_AVAILABLE

    # PVR lists IMAX 3D there: one email, with the show's own PVR link.
    tick(monkeypatch, [listing(imax3d("101", 501))], sent, NOW + timedelta(minutes=10))
    [(m, change)] = sent
    assert (change.kind.value, change.fmt, change.time_labels) == ("TICKETS_LIVE", "IMAX 3D", ["07:00 PM"])
    subject, html, text = render_change(m, change)
    assert "https://www.pvrcinemas.com/seatlayout/" in html and "BOOK ON PVR INOX" in html
    assert "Opens PVR 101 on PVR INOX." in html and "bookmyshow" not in html.lower()

    # The same listing again: nothing more.
    tick(monkeypatch, [listing(imax3d("101", 501))], sent, NOW + timedelta(minutes=20))
    assert len(sent) == 1


def test_a_show_in_another_format_does_not_fire_an_imax_3d_watch(pvr_enabled, no_discovery, monkeypatch):
    mon = monitor("101")
    upsert_monitor(mon, mirror=False)
    sent: list = []
    regular_on_imax_screen = show("101", 7, "38431", D1, "1500", movie_format="", screen_type="IMAX")
    tick(monkeypatch, [sessions_payload(block(COMMON, PRINTS, {"IMAX": [regular_on_imax_screen]}))], sent)
    assert sent == []
    assert load_state()[mon.id].targets[mon.targets[0].key].availability is Availability.SHOW_NOT_AVAILABLE


def test_a_failed_cinema_read_is_an_error_never_a_re_arm_or_a_second_email(pvr_enabled, no_discovery, monkeypatch):
    mon = monitor("101", "202")
    upsert_monitor(mon, mirror=False)
    sent: list = []
    tick(monkeypatch, [listing(imax3d("101", 501)), listing()], sent)
    assert len(sent) == 1                                               # 101 went live

    # 202's read fails: the whole check is an ERROR; nothing is re-armed.
    report, _ = tick(monkeypatch, [listing(imax3d("101", 501)), ConnectionError("reset")], sent,
                     NOW + timedelta(minutes=10))
    assert report.failed == [mon.id] and len(sent) == 1
    state = load_state()[mon.id]
    assert state.consecutive_errors == 1
    assert state.targets[mon.targets[0].key].availability is Availability.AVAILABLE

    # Back to normal: still announced, so still silent.
    tick(monkeypatch, [listing(imax3d("101", 501)), listing()], sent, NOW + timedelta(minutes=20))
    assert len(sent) == 1


def test_a_refusal_is_recorded_as_blocked_not_as_no_tickets(pvr_enabled, no_discovery, monkeypatch):
    from tests.conftest import FakeResponse

    mon = monitor("101")
    upsert_monitor(mon, mirror=False)
    report, session = tick(monkeypatch, [FakeResponse(403, {})], [])
    state = load_state()[mon.id]
    assert report.failed == [mon.id] and state.last_error_kind == "BLOCKED" and len(session.calls) == 1
    assert state.targets == {} or all(t.availability is Availability.UNKNOWN for t in state.targets.values())


def test_monitors_on_the_same_film_share_one_read_of_every_watched_cinema(pvr_enabled, no_discovery, monkeypatch):
    # Two accounts' monitors, saved the way the worker sees the store: with no
    # signed-in scope (an earlier UI test may have left one registered).
    from monitor import state as state_mod

    monkeypatch.setattr(state_mod, "_scope_provider", None)
    a, b = monitor("101", owner="uid-a"), monitor("202", owner="uid-b")
    [group] = select_for_fetch([a, b], [a.id, b.id])
    assert group.venue_codes == ["101", "202"]

    for m in (a, b):
        upsert_monitor(m, mirror=False)
    sent: list = []
    report, session = tick(monkeypatch, [listing(imax3d("101", 501)), listing(imax3d("202", 601))], sent)
    assert report.fetches == 1 and sorted(report.checked) == sorted([a.id, b.id])
    assert sorted(session.cinema_dates()) == [("101", "2026-09-25"), ("202", "2026-09-25")]
    assert sorted(m.owner_uid for m, _ in sent) == ["uid-a", "uid-b"]            # each owner, their own email


def test_the_platform_is_part_of_the_shared_read(pvr_enabled):
    bms = replace(FILM_EN, platform="bookmyshow", event_code="ET00514163")
    assert fetch_identity(bms, [D1]) != fetch_identity(FILM_EN, [D1])
    pvr_mon = monitor("101")
    bms_mon = replace(monitor("101"), movie=bms, id="bmsmon000001")
    groups = select_for_fetch([pvr_mon, bms_mon], [pvr_mon.id, bms_mon.id])
    assert len(groups) == 2 and {g.movie.platform for g in groups} == {"pvr_inox", "bookmyshow"}
