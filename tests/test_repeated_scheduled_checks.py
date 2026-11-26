"""Repeated checks of a five-minute monitor by scheduled-style passes.

Each pass below is one ``run_once`` without ``force`` — what a scheduled
workflow run executes — against the Firestore worker fixture, with the
clock *advanced* between passes (no real waiting) and BookMyShow and the
mailer replaced by test doubles. This proves the worker's due logic and
bookkeeping; it says nothing about whether GitHub delivers the schedule.
"""
from __future__ import annotations

from datetime import timedelta

from monitor.checker import run_once
from tests.test_worker_reads import firestore_worker  # noqa: F401 - the fixture
from tests.test_worker_resilience import _monitor, live  # noqa: F401 - the fixture


def _five_minute(live):
    monitor = _monitor("ET1", live.now)
    monitor.interval_minutes = 5
    return live.seed(monitor, checked=False)


def test_every_pass_five_minutes_apart_checks_the_monitor_again(live):
    m = _five_minute(live)
    counts = []
    for minutes in range(0, 60, 5):                  # twelve passes, one hour
        run_once(at=live.now + timedelta(minutes=minutes), notifier=live.notify)
        counts.append(live.stored(m).check_count)
    assert counts == list(range(1, 13))
    assert live.inbox == [(m.id, "TICKETS_LIVE")]   # one alert, never repeated


def test_a_pass_inside_the_interval_does_not_check_again(live):
    m = _five_minute(live)
    run_once(at=live.now, notifier=live.notify)
    run_once(at=live.now + timedelta(minutes=2), notifier=live.notify)
    assert live.stored(m).check_count == 1
    run_once(at=live.now + timedelta(minutes=5), notifier=live.notify)
    assert live.stored(m).check_count == 2


def test_a_run_that_arrives_hours_late_checks_once_and_replays_nothing(live):
    """GitHub delivers this repository's schedule hours late. A late run
    checks the overdue monitor once; the missed slots are not replayed."""
    m = _five_minute(live)
    run_once(at=live.now, notifier=live.notify)
    run_once(at=live.now + timedelta(hours=4), notifier=live.notify)
    assert live.stored(m).check_count == 2
    run_once(at=live.now + timedelta(hours=4, minutes=1), notifier=live.notify)
    assert live.stored(m).check_count == 2


def test_a_monitor_past_its_end_time_is_expired_and_not_checked(live):
    m = _five_minute(live)
    run_once(at=live.now, notifier=live.notify)
    late = m.monitor_until + timedelta(minutes=5)
    report = run_once(at=late, notifier=live.notify)
    assert m.id in report.expired and m.id not in report.checked
    assert live.stored(m).check_count == 1
