"""Lifecycle, change detection and the duplicate-email guarantees."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config.timezone import IST
from monitor.changes import ChangeKind, apply_outcome, detect_changes, mark_notified
from monitor.checker import check_monitor, evaluate_target, run_once
from monitor.models import (
    ANY_FORMAT,
    Availability,
    CheckOutcome,
    MonitorStatus,
    TheatreTarget,
)
from monitor.state import (
    MonitorState,
    expire_due_monitors,
    extend_monitor,
    get_monitor,
    load_monitors,
    load_state,
    save_state,
    stop_monitor,
    upsert_monitor,
)
from tests.conftest import (
    ALLU_LIVE,
    ALLU_LIVE_EXTRA_SHOW,
    NOT_ON_SALE,
    SOLD_OUT,
    FakeResponse,
    build_payload,
)


# ──────────────────────────────────────────────────────────────────────────
# Persisting configuration  (Tests 5, 9)
# ──────────────────────────────────────────────────────────────────────────
def test_monitor_survives_a_save_load_round_trip(make_monitor):
    monitor = make_monitor(interval=15)
    upsert_monitor(monitor, mirror=False)

    loaded = load_monitors()
    assert len(loaded) == 1
    got = loaded[0]
    assert got.id == monitor.id
    assert got.interval_minutes == 15
    assert got.monitor_until == monitor.monitor_until
    assert [t.key for t in got.targets] == [t.key for t in monitor.targets]
    assert got.targets[0].fmt == "Dolby Cinema"
    assert got.notify_email == "watcher@example.com"


def test_multiple_theatres_each_keep_their_own_format(make_monitor):
    monitor = make_monitor(
        targets=[
            TheatreTarget("ALLU", "Allu Cinemas", "Attapur", "Dolby Cinema"),
            TheatreTarget("AMB", "AMB Cinemas", "Gachibowli", "HDR by Barco"),
            TheatreTarget("PVR", "PVR Inorbit", "Madhapur", ANY_FORMAT),
        ]
    )
    upsert_monitor(monitor, mirror=False)
    formats = {t.venue_name: t.fmt for t in load_monitors()[0].targets}
    assert formats == {
        "Allu Cinemas": "Dolby Cinema",
        "AMB Cinemas": "HDR by Barco",
        "PVR Inorbit": ANY_FORMAT,
    }


def test_state_round_trips(make_monitor, at):
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=3, success_count=2)
    state.target("ALLU::Dolby Cinema").availability = Availability.SOLD_OUT
    save_state({"m1": state}, mirror=False)

    restored = load_state()["m1"]
    assert restored.check_count == 3
    assert restored.last_success_at == at
    assert restored.targets["ALLU::Dolby Cinema"].availability is Availability.SOLD_OUT


# ──────────────────────────────────────────────────────────────────────────
# Stopping and expiring  (Tests 7, 8)
# ──────────────────────────────────────────────────────────────────────────
def test_stop_changes_persisted_state_not_just_the_view(make_monitor):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)

    stop_monitor(monitor.id, mirror=False)

    stored = get_monitor(monitor.id)
    assert stored.status is MonitorStatus.STOPPED
    assert stored.stopped_at is not None
    assert stored.is_running() is False


def test_stopped_monitor_is_never_checked(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    stop_monitor(monitor.id, mirror=False)

    checked = []
    report = run_once(at=at, force=True, mirror=False,
                      notifier=lambda m, c: checked.append(c))
    assert report.checked == []
    assert checked == []
    assert any("stopped" in s for s in report.skipped)


def test_expiry_flips_state_without_anyone_asking(make_monitor, at):
    monitor = make_monitor(until=at - timedelta(minutes=1))
    upsert_monitor(monitor, mirror=False)

    _, expired = expire_due_monitors(at=at, mirror=False)

    assert [m.id for m in expired] == [monitor.id]
    assert get_monitor(monitor.id).status is MonitorStatus.EXPIRED


def test_expired_monitor_gets_no_further_checks(make_monitor, at, provider_factory, monkeypatch):
    monitor = make_monitor(until=at - timedelta(seconds=1))
    upsert_monitor(monitor, mirror=False)

    provider = provider_factory([build_payload(ALLU_LIVE)])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)

    report = run_once(at=at, force=True, mirror=False, notifier=lambda m, c: None)

    assert report.checked == []
    assert provider.session.calls == []  # the platform was never contacted


def test_extend_revives_an_expired_monitor(make_monitor, at):
    monitor = make_monitor(until=at - timedelta(hours=1))
    upsert_monitor(monitor, mirror=False)
    expire_due_monitors(at=at, mirror=False)

    extend_monitor(monitor.id, 24, mirror=False)

    revived = get_monitor(monitor.id)
    assert revived.status is MonitorStatus.ACTIVE
    assert revived.is_running() is True


def test_interval_gating(at):
    state = MonitorState(last_check_at=at)
    assert state.is_due(10, at + timedelta(minutes=5)) is False
    assert state.is_due(10, at + timedelta(minutes=10)) is True
    # GitHub fires a little early sometimes; a 30s tolerance stops the
    # effective interval from drifting outwards run after run.
    assert state.is_due(10, at + timedelta(seconds=580)) is True
    assert MonitorState().is_due(30) is True  # never checked


# ──────────────────────────────────────────────────────────────────────────
# Evaluating targets  (Test 10 groundwork)
# ──────────────────────────────────────────────────────────────────────────
def _snapshot(provider_factory, listing_url, shows):
    return provider_factory([build_payload(shows)]).resolve(listing_url)


def test_available_only_for_the_theatre_that_went_live(make_monitor, provider_factory, listing_url):
    monitor = make_monitor()
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE)

    results = {r.venue_name: r for r in (evaluate_target(monitor, t, snap) for t in monitor.targets)}
    assert results["Allu Cinemas"].availability is Availability.AVAILABLE
    assert results["AMB Cinemas"].availability is Availability.NOT_BOOKABLE
    assert results["Allu Cinemas"].time_labels == ["07:30 PM"]


def test_format_filter_is_per_theatre(make_monitor, provider_factory, listing_url):
    monitor = make_monitor(
        targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur", "IMAX")]
    )
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE)
    result = evaluate_target(monitor, monitor.targets[0], snap)
    # Allu is bookable, but not in IMAX — that is SHOW_NOT_AVAILABLE, not AVAILABLE.
    assert result.availability is Availability.SHOW_NOT_AVAILABLE
    assert "IMAX" in result.detail


def test_any_format_matches_everything(make_monitor, provider_factory, listing_url):
    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur", ANY_FORMAT)])
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE)
    assert evaluate_target(monitor, monitor.targets[0], snap).availability is Availability.AVAILABLE


def test_missing_theatre_is_distinct_from_missing_show(make_monitor, provider_factory, listing_url):
    monitor = make_monitor(
        targets=[TheatreTarget("GHOST", "Ghost Cinemas", "Nowhere", ANY_FORMAT)]
    )
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE)
    assert evaluate_target(monitor, monitor.targets[0], snap).availability is (
        Availability.THEATRE_NOT_AVAILABLE
    )


def test_sold_out_is_reported_as_sold_out(make_monitor, provider_factory, listing_url):
    monitor = make_monitor()
    snap = _snapshot(provider_factory, listing_url, SOLD_OUT)
    result = evaluate_target(monitor, monitor.targets[0], snap)
    assert result.availability is Availability.SOLD_OUT


# ──────────────────────────────────────────────────────────────────────────
# Change detection + duplicate suppression  (Tests 10, 11)
# ──────────────────────────────────────────────────────────────────────────
def _run(monitor, provider, monkeypatch, at, sent):
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    return run_once(at=at, force=True, mirror=False,
                    notifier=lambda m, c: sent.append((at, c)))


def test_no_email_while_nothing_changes(make_monitor, provider_factory, monkeypatch, at, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    for step in range(4):
        provider = provider_factory([build_payload(SOLD_OUT)])
        _run(monitor, provider, monkeypatch, later(step * 10), sent)

    assert sent == []


def test_sold_out_to_available_sends_exactly_one_email(make_monitor, provider_factory,
                                                       monkeypatch, at, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(SOLD_OUT)]), monkeypatch, at, sent)
    assert sent == []

    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(10), sent)
    assert len(sent) == 1
    change = sent[0][1]
    assert change.kind is ChangeKind.TICKETS_LIVE
    assert change.venue_name == "Allu Cinemas"
    assert change.previous is Availability.SOLD_OUT
    assert change.time_labels == ["07:30 PM"]

    # Three more identical checks: still one email, total.
    for step in (2, 3, 4):
        _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch,
             later(10 * step), sent)
    assert len(sent) == 1


def test_available_then_sold_out_then_available_emails_twice(make_monitor, provider_factory,
                                                            monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(NOT_ON_SALE)]), monkeypatch, later(0), sent)
    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(10), sent)
    _run(monitor, provider_factory([build_payload(SOLD_OUT)]), monkeypatch, later(20), sent)
    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(30), sent)

    assert [c.kind for _, c in sent] == [ChangeKind.TICKETS_LIVE, ChangeKind.TICKETS_LIVE]


def test_a_new_showtime_is_announced_once(make_monitor, provider_factory, monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(0), sent)
    assert len(sent) == 1  # tickets live

    _run(monitor, provider_factory([build_payload(ALLU_LIVE_EXTRA_SHOW)]), monkeypatch,
         later(60), sent)
    assert len(sent) == 2
    change = sent[1][1]
    assert change.kind is ChangeKind.NEW_SHOWTIME
    assert change.new_time_labels == ["09:45 PM"]

    # Same extra show on later checks: silence.
    _run(monitor, provider_factory([build_payload(ALLU_LIVE_EXTRA_SHOW)]), monkeypatch,
         later(180), sent)
    assert len(sent) == 2


def test_new_showtime_respects_the_cooldown(make_monitor, provider_factory, monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(0), sent)
    _run(monitor, provider_factory([build_payload(ALLU_LIVE_EXTRA_SHOW)]), monkeypatch,
         later(10), sent)
    assert len(sent) == 2

    third = ALLU_LIVE_EXTRA_SHOW + [
        {"venue_code": "ALLU", "venue_name": "Allu Cinemas", "area": "Attapur",
         "time": "11:15 PM", "time_code": "2315", "fmt": "DOLBY CINEMA", "status": "3"},
    ]
    # Inside the 45-minute cooldown -> suppressed.
    _run(monitor, provider_factory([build_payload(third)]), monkeypatch, later(20), sent)
    assert len(sent) == 2
    # Outside it -> allowed.
    _run(monitor, provider_factory([build_payload(third)]), monkeypatch, later(70), sent)
    assert len(sent) == 3


def test_failed_email_is_retried_on_the_next_check(make_monitor, provider_factory,
                                                   monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    attempts = []

    def flaky(m, change):
        attempts.append(change)
        if len(attempts) == 1:
            raise RuntimeError("smtp down")

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    first = run_once(at=later(0), force=True, mirror=False, notifier=flaky)
    assert first.emails_sent == 0 and first.email_errors

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    second = run_once(at=later(10), force=True, mirror=False, notifier=flaky)
    assert second.emails_sent == 1
    assert len(attempts) == 2

    # And no third attempt once it went out.
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    run_once(at=later(20), force=True, mirror=False, notifier=flaky)
    assert len(attempts) == 2


# ──────────────────────────────────────────────────────────────────────────
# Errors are never an answer  (Test 13)
# ──────────────────────────────────────────────────────────────────────────
def test_a_blocked_check_does_not_report_unavailability(make_monitor, provider_factory,
                                                        monkeypatch, at):
    monitor = make_monitor()
    outcome = check_monitor(monitor, at=at)
    assert isinstance(outcome, CheckOutcome)

    provider = provider_factory([FakeResponse(403)])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    outcome = check_monitor(monitor, at=at)

    assert outcome.ok is False
    assert outcome.results == []
    assert "bot check" in outcome.error


def test_an_error_never_overwrites_the_last_known_state(make_monitor, provider_factory,
                                                        monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(0), sent)
    assert len(sent) == 1

    _run(monitor, provider_factory([FakeResponse(403)]), monkeypatch, later(10), sent)

    state = load_state()[monitor.id]
    target = state.targets["ALLU::Dolby Cinema"]
    assert target.availability is Availability.AVAILABLE  # unchanged by the failure
    assert state.consecutive_errors == 1
    assert state.last_success_at is not None
    assert len(sent) == 1  # and definitely no second email


def test_error_then_recovery_does_not_re_email(make_monitor, provider_factory, monkeypatch, later):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    sent = []

    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(0), sent)
    _run(monitor, provider_factory([FakeResponse(403)]), monkeypatch, later(10), sent)
    _run(monitor, provider_factory([build_payload(ALLU_LIVE)]), monkeypatch, later(20), sent)

    assert len(sent) == 1
    assert load_state()[monitor.id].consecutive_errors == 0


def test_detect_changes_ignores_a_failed_outcome(make_monitor, at):
    monitor = make_monitor()
    outcome = CheckOutcome(monitor.id, at, ok=False, error="network down")
    assert detect_changes(monitor, outcome, MonitorState()) == []


def test_apply_outcome_records_failure_without_touching_targets(make_monitor, at):
    monitor = make_monitor()
    state = MonitorState()
    state.target("ALLU::Dolby Cinema").availability = Availability.SOLD_OUT

    apply_outcome(CheckOutcome(monitor.id, at, ok=False, error="boom"), state)

    assert state.check_count == 1
    assert state.success_count == 0
    assert state.consecutive_errors == 1
    assert state.targets["ALLU::Dolby Cinema"].availability is Availability.SOLD_OUT


# ──────────────────────────────────────────────────────────────────────────
# The whole run
# ──────────────────────────────────────────────────────────────────────────
def test_run_once_skips_monitors_that_are_not_due(make_monitor, provider_factory,
                                                  monkeypatch, at, later):
    monitor = make_monitor(interval=30)
    upsert_monitor(monitor, mirror=False)
    sent = []

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(NOT_ON_SALE)]))
    run_once(at=at, mirror=False, notifier=lambda m, c: sent.append(c))

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(NOT_ON_SALE)]))
    report = run_once(at=later(10), mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.checked == [] and any("not due" in s for s in report.skipped)

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(NOT_ON_SALE)]))
    report = run_once(at=later(30), mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.checked == [monitor.id]


def test_one_theatre_going_live_keeps_the_other_watched(make_monitor, provider_factory,
                                                        monkeypatch, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)

    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    run_once(at=at, force=True, mirror=False, notifier=lambda m, c: None)

    stored = get_monitor(monitor.id)
    assert stored.is_running() is True  # still active, not "done"
    state = load_state()[monitor.id]
    assert state.targets["ALLU::Dolby Cinema"].availability is Availability.AVAILABLE
    assert state.targets["AMB::Any format"].availability is Availability.NOT_BOOKABLE
