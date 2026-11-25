from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from config.firestore import FirestoreClient
from config.timezone import to_iso
from monitor.execution import CONTROL, EXECUTION, OUTBOX, Execution, LeaseLost
from monitor.models import Availability, CheckOutcome, Monitor, MovieRef, TargetResult, TheatreTarget
from monitor.state import HISTORY, MONITORS, STATES, MonitorState, TargetState


class MemoryStore:
    """Transactional fake with document versions and create-only writes."""

    def __init__(self):
        self.rows = {}
        self.version = 0

    def put(self, collection, doc, fields):
        self.version += 1
        self.rows[collection, doc] = deepcopy(fields), str(self.version)

    @contextmanager
    def transaction(self):
        versions = {}
        writes = {}
        store = self

        class Transaction:
            def get(self, collection, doc):
                key = (collection, doc)
                fields, version = deepcopy(store.rows.get(key, (None, None)))
                versions[key] = version
                return fields, version

            def set(self, collection, doc, fields):
                assert (collection, doc) in versions
                writes[collection, doc] = fields

            def create(self, collection, doc, fields):
                assert (collection, doc) not in store.rows
                writes[collection, doc] = fields

            def commit(self):
                for key, version in versions.items():
                    assert store.rows.get(key, (None, None))[1] == version
                for (collection, doc), fields in writes.items():
                    store.put(collection, doc, fields)

        yield Transaction()

    def fields(self, collection, doc):
        return self.rows[collection, doc][0]


@pytest.fixture
def ledger():
    store = MemoryStore()
    now = [datetime(2026, 11, 24, 15, tzinfo=timezone.utc)]
    target = TheatreTarget("ABC", "Theatre", fmt="2D")
    movie = MovieRef("bookmyshow", "ET00000001", "Example", "HYD", "hyderabad")
    monitor = Monitor(movie, [target], id="example", owner_uid="fixture-owner",
                      notify_email="test@example.com", created_at=now[0],
                      monitor_until=now[0] + timedelta(days=1))
    state = MonitorState(targets={target.key: TargetState(availability=Availability.SOLD_OUT)})
    store.put(MONITORS, monitor.id, monitor.to_dict())
    store.put(STATES, monitor.id, {**state.to_dict(), "owner_uid": monitor.owner_uid})
    store.put(*CONTROL, {"authority": "shadow-test", "epoch": 4})
    result = TargetResult(target.key, target.venue_name, target.fmt, Availability.AVAILABLE)
    outcome = CheckOutcome(monitor.id, now[0], True, [result])
    return store, now, monitor, state, outcome


def test_exclusive_authority_and_stale_epoch_cannot_finish(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        with pytest.raises(LeaseLost):
            with Execution(store, "shadow-test", clock=lambda: at[0]):
                pass
        claim = worker.claim(monitor, state)
        assert claim
        assert worker.claim(monitor, state) is None
        store.put(*CONTROL, {"authority": "other", "epoch": 5})
        with pytest.raises(LeaseLost):
            worker.finish(monitor, claim, outcome)
    assert store.fields(*CONTROL) == {"authority": "other", "epoch": 5}
    assert not any(c == OUTBOX for c, _ in store.rows)


def test_expired_lease_allows_new_claim_and_fences_old_holder(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        old_claim = worker.claim(monitor, state)
        at[0] += timedelta(minutes=4)
        with Execution(store, "shadow-test", clock=lambda: at[0]) as successor:
            claim = successor.claim(monitor, state)
            assert claim and claim != old_claim
            with pytest.raises(LeaseLost):
                worker.finish(monitor, old_claim, outcome)
            assert successor.finish(monitor, claim, outcome)


def test_monitor_edit_between_fetch_and_commit_is_fenced(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        claim = worker.claim(monitor, state)
        monitor.stop(at[0])
        store.put(MONITORS, monitor.id, monitor.to_dict())
        assert worker.finish(monitor, claim, outcome) is None
    assert store.fields(STATES, monitor.id)["check_count"] == 0
    assert not any(c == OUTBOX for c, _ in store.rows)


def test_observation_and_intent_commit_together_without_early_mail_marker(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        claim = worker.claim(monitor, state)
        observed, changes = worker.finish(monitor, claim, outcome)
        assert len(changes) == 1 and observed.check_count == 1
        target = observed.targets[monitor.targets[0].key]
        assert target.notified_at is None
        assert worker.finish(monitor, claim, outcome) is None
        claim = worker.claim(monitor, observed)
        observed, changes = worker.finish(monitor, claim, outcome)
        assert not changes and observed.check_count == 2
    assert len([1 for c, _ in store.rows if c == OUTBOX]) == 1
    assert store.fields(EXECUTION, monitor.id)["last_check_id"]
    assert not any(c == HISTORY for c, _ in store.rows)


def test_manual_request_receipt_is_atomic_with_observation(ledger):
    store, at, monitor, state, outcome = ledger
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        claim = worker.claim(monitor, state, request_id="dispatch-1")
        observed, _ = worker.finish(monitor, claim, outcome)
    with Execution(store, "shadow-test", clock=lambda: at[0]) as successor:
        assert successor.claim(monitor, observed, request_id="dispatch-1") is None
        assert successor.claim(monitor, observed, request_id="dispatch-2")


def test_transaction_sends_document_preconditions_and_does_not_retry_commit():
    calls = []

    def transport(method, url, token, body):
        calls.append((method, url, body))
        if url.endswith(":beginTransaction"):
            return 200, {"transaction": "fixture/+"}
        if method == "GET":
            assert url.endswith("?transaction=fixture%2F%2B")
            return 200, {"updateTime": "2026-11-24T15:00:00Z", "fields": {}}
        if url.endswith(":commit"):
            raise OSError("commit response lost")
        assert url.endswith(":rollback")
        return 200, {}

    client = FirestoreClient("demo-ticketradar", lambda: "fixture", transport=transport)
    from config.firestore import FirestoreError

    with pytest.raises(FirestoreError):
        with client.transaction() as tx:
            tx.get("worker_runtime", "control")
            tx.set("worker_runtime", "control", {"epoch": 2})
            tx.create("monitor_outbox", "example", {"status": "pending"})
            tx.commit()
    commits = [body for _, url, body in calls if url.endswith(":commit")]
    assert len(commits) == 1
    assert [w["currentDocument"] for w in commits[0]["writes"]] == [
        {"updateTime": "2026-11-24T15:00:00Z"}, {"exists": False},
    ]
