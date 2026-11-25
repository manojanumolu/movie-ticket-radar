import smtplib
from datetime import timedelta

import pytest

from monitor.execution import OUTBOX, Execution
from monitor.state import HISTORY, STATES, MonitorState
from notifications import email
from notifications.outbox import Outbox, Rejected
from tests.test_monitor_execution import ledger  # pytest fixture


def enqueue(execution, store, monitor, state, outcome):
    claim = execution.claim(monitor, state)
    execution.finish(monitor, claim, outcome)
    return next(doc for collection, doc in store.rows if collection == OUTBOX)


def test_acceptance_records_history_once(ledger):
    store, at, monitor, state, outcome = ledger
    calls = []
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        item_id = enqueue(worker, store, monitor, state, outcome)
        outbox = Outbox(worker, lambda *args: calls.append(args))
        assert outbox.deliver(item_id, "unrelated@example.com") == "skipped"
        assert outbox.deliver(item_id, monitor.notify_email) == "accepted"
        assert outbox.deliver(item_id, monitor.notify_email) == "skipped"
    assert len(calls) == 1 and calls[0][-1] == item_id
    assert store.fields(HISTORY, item_id)["monitor_id"] == monitor.id
    observed = MonitorState.from_dict(store.fields(STATES, monitor.id))
    assert observed.targets[monitor.targets[0].key].notified_at == at[0]


def test_retry_only_when_rejection_proven(ledger):
    store, at, monitor, state, outcome = ledger
    effects = iter([Rejected(retryable=True), OSError("lost final reply")])

    def send(*args):
        raise next(effects)

    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        item_id = enqueue(worker, store, monitor, state, outcome)
        outbox = Outbox(worker, send)
        assert outbox.deliver(item_id, monitor.notify_email) == "retry"
        assert outbox.deliver(item_id, monitor.notify_email) == "skipped"
    at[0] += timedelta(minutes=3)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        outbox = Outbox(worker, send)
        assert outbox.deliver(item_id, monitor.notify_email) == "uncertain"
        assert outbox.deliver(item_id, monitor.notify_email) == "skipped"
    assert (HISTORY, item_id) not in store.rows


def test_crash_after_sender_claim_is_never_automatically_retried(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        item_id = enqueue(worker, store, monitor, state, outcome)
        assert Outbox(worker, None)._start(item_id, monitor.notify_email)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        assert Outbox(worker, None).deliver(item_id, monitor.notify_email) == "skipped"
    assert store.fields(OUTBOX, item_id)["status"] == "uncertain"


def test_uncertain_item_can_be_reconciled_without_sending(ledger):
    store, at, monitor, state, outcome = ledger

    def ambiguous(*_):
        raise OSError("lost final reply")

    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        item_id = enqueue(worker, store, monitor, state, outcome)
        outbox = Outbox(worker, ambiguous)
        assert outbox.deliver(item_id, monitor.notify_email) == "uncertain"
        assert outbox.reconcile(item_id, accepted=True, evidence_sha256="a" * 64)
        assert not outbox.reconcile(item_id, accepted=True, evidence_sha256="a" * 64)
        assert outbox.deliver(item_id, monitor.notify_email) == "skipped"
    assert store.fields(OUTBOX, item_id)["status"] == "accepted"
    assert store.fields(HISTORY, item_id)["monitor_id"] == monitor.id


@pytest.mark.parametrize("failure,retryable", [
    (smtplib.SMTPDataError(450, b"synthetic"), True),
    (smtplib.SMTPDataError(550, b"synthetic"), False),
])
def test_smtp_explicit_data_errors_and_stable_message_id(monkeypatch, failure, retryable):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic")

    class SMTP:
        def login(self, *_):
            pass

        def send_message(self, msg, **kwargs):
            assert msg["Message-ID"] == "<fixture@ticket-radar.invalid>"
            raise failure

        def close(self):
            pass

    monkeypatch.setattr(email.smtplib, "SMTP_SSL", lambda *args, **kwargs: SMTP())
    with pytest.raises(Rejected) as exc:
        email.send_outbox_email("to@example.com", "Subject", "<b>Hi</b>", "Hi", "fixture")
    assert exc.value.retryable is retryable
