"""A failed or cancelled alert must not silently suppress the next one.

The detection mark written with an outbox intent is released only when that
intent provably reached nobody (``failed`` / ``cancelled``) and only while the
mark is still that intent's own. ``uncertain`` is never released.
"""
from datetime import timedelta

from monitor.execution import EXECUTION, OUTBOX, Execution
from monitor.models import CheckOutcome
from monitor.state import STATES, MonitorState
from notifications.outbox import Outbox, Rejected
from tests.test_monitor_execution import ledger  # noqa: F401 - pytest fixture


def _observed(store, monitor):
    return MonitorState.from_dict(store.fields(STATES, monitor.id))


def _check(worker, store, monitor, outcome):
    claim = worker.claim(monitor, _observed(store, monitor))
    assert claim is not None
    worker.finish(monitor, claim, outcome)


def _items(store, status=None):
    return [doc for (collection, doc), (fields, _) in store.rows.items()
            if collection == OUTBOX and (status is None or fields["status"] == status)]


def _again(outcome, at):
    return CheckOutcome(outcome.monitor_id, at, True, outcome.results)


def test_refused_alert_is_detected_again_and_delivered(ledger):
    store, at, monitor, state, outcome = ledger
    sent = []

    def refuse(*_):
        raise Rejected(retryable=False)

    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, outcome)
        [first] = _items(store)
        assert Outbox(worker, refuse).deliver(first, monitor.notify_email) == "failed"
    at[0] += timedelta(minutes=5)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, _again(outcome, at[0]))
        pending = _items(store, "pending")
        assert len(pending) == 1 and pending[0] != first
        assert Outbox(worker, lambda *a: sent.append(a)).deliver(pending[0], monitor.notify_email) == "accepted"
    assert len(sent) == 1


def test_stale_cancelled_alert_is_detected_again(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, outcome)
    at[0] += timedelta(minutes=25)          # beyond the 20-minute freshness limit
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        [first] = _items(store)
        assert Outbox(worker, None).deliver(first, monitor.notify_email) == "skipped"
        assert store.fields(OUTBOX, first)["status"] == "cancelled"
        _check(worker, store, monitor, _again(outcome, at[0]))
        assert len(_items(store, "pending")) == 1


def test_uncertain_alert_is_never_released(ledger):
    store, at, monitor, state, outcome = ledger

    def ambiguous(*_):
        raise OSError("lost final reply")

    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, outcome)
        [first] = _items(store)
        assert Outbox(worker, ambiguous).deliver(first, monitor.notify_email) == "uncertain"
    at[0] += timedelta(minutes=5)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, _again(outcome, at[0]))
    assert _items(store, "pending") == []


def test_pending_alert_is_not_duplicated_by_the_next_check(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, outcome)
    at[0] += timedelta(minutes=5)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, _again(outcome, at[0]))
    assert len(_items(store)) == 1


def test_a_newer_mark_is_left_alone(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        _check(worker, store, monitor, outcome)
        [first] = _items(store)
    execution = store.fields(EXECUTION, monitor.id)
    key = monitor.targets[0].key
    newer = {**execution}
    newer["detection"]["targets"][key]["notified_at"] = "2026-11-24T21:00:00+05:30"
    store.put(EXECUTION, monitor.id, newer)

    def refuse(*_):
        raise Rejected(retryable=False)

    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        assert Outbox(worker, refuse).deliver(first, monitor.notify_email) == "failed"
    assert store.fields(EXECUTION, monitor.id)["detection"]["targets"][key]["notified_at"] \
        == "2026-11-24T21:00:00+05:30"
