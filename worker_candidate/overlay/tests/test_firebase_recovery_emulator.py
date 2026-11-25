"""Integration drill against demo projects in the local Firebase emulator suite."""
import os

import pytest
import requests

from config.firestore import BASE, FirestoreClient, FirestoreError
from monitor.execution import CONTROL, OUTBOX, Execution, LeaseLost
from notifications.outbox import Outbox
from tests.test_monitor_execution import ledger
from tools.export_auth_backup import HEADERS, snapshot
from tools.verify_recovery import verify

FIRESTORE = os.environ.get("FIRESTORE_EMULATOR_HOST")
AUTH = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
pytestmark = pytest.mark.skipif(not FIRESTORE or not AUTH, reason="requires loopback Firebase emulators")


def emulators():
    # Accept the fixed local test ports only. Session never reads proxy settings
    # or follows redirects while using the emulator's admin token.
    assert FIRESTORE == "127.0.0.1:8080" and AUTH == "127.0.0.1:9099"
    session = requests.Session()
    session.trust_env = False

    def transport(method, url, _token, body):
        path = url.removeprefix(BASE)
        assert path.startswith("/projects/demo-ticketradar-")
        reply = session.request(method, f"http://{FIRESTORE}/v1{path}", headers=HEADERS,
                                json=body, timeout=10, allow_redirects=False)
        return reply.status_code, reply.json()

    return session, transport


def test_demo_restore_preserves_ids_state_and_sign_in():
    session, transport = emulators()
    source, target = "demo-ticketradar-source", "demo-ticketradar-restored"
    auth = f"http://{AUTH}/identitytoolkit.googleapis.com/v1"
    rows = [
        ("users", "fixture-alice", {"owner_uid": "fixture-alice", "avatar_key": "violet"}),
        ("monitors", "fixture-watch", {"id": "fixture-watch", "owner_uid": "fixture-alice", "status": "STOPPED"}),
        ("monitor_state", "fixture-watch", {"owner_uid": "fixture-alice", "check_count": 4}),
        ("history", "fixture-event", {"owner_uid": "fixture-alice", "monitor_id": "fixture-watch", "kind": "STOPPED"}),
    ]
    accounts = [
        {"localId": "fixture-alice", "email": "alice@example.test", "emailVerified": True,
         "customAttributes": '{"admin":true}', "password": "emulator-fixture-password"},
        {"localId": "fixture-bob", "email": "bob@example.test", "emailVerified": True,
         "password": "emulator-fixture-password"},
    ]

    def seed(project, users, documents):
        client = FirestoreClient(project, lambda: "owner", transport)
        for user in users:
            response = session.post(f"{auth}/projects/{project}/accounts", headers=HEADERS,
                                    json=user, timeout=8, allow_redirects=False)
            response.raise_for_status()
        client.commit(documents)

    seed(source, accounts, rows)
    captured = snapshot(source, session=session)
    restored_accounts = [{**row, "password": "emulator-fixture-password"} for row in captured["auth"]["users"]]
    restore_rows = [(collection, key, payload) for collection, docs in captured["collections"].items()
                    for key, payload in docs.items()]
    seed(target, restored_accounts, restore_rows)
    restored = snapshot(target, session=session)
    result = verify(captured, restored)
    assert all(not row[k] for row in result.values() for k in
               ("missing", "unexpected", "changed", "new_ownership_issues"))
    password_login = session.post(f"{auth}/accounts:signInWithPassword",
                                  params={"key": target},
                                  json={"email": accounts[0]["email"],
                                        "password": accounts[0]["password"], "returnSecureToken": True},
                                  timeout=8, allow_redirects=False)
    password_login.raise_for_status()
    assert password_login.json()["localId"] == "fixture-alice"


def test_emulator_firestore_transaction_fences_competing_worker():
    _, transport = emulators()
    client = FirestoreClient("demo-ticketradar-lease", lambda: "owner", transport)
    client.set(*CONTROL, {"authority": "isolated-test", "epoch": 1})
    with Execution(client, "isolated-test") as first:
        with pytest.raises(LeaseLost):
            with Execution(client, "isolated-test"):
                pass
        client.set(*CONTROL, {"authority": "isolated-test", "epoch": 2})
        with pytest.raises(LeaseLost):
            first.verify()
    with Execution(client, "isolated-test") as successor:
        assert successor.epoch == 2
    stored = client.get(*CONTROL)
    assert stored["holder"] == "" and stored["epoch"] == 2
    with pytest.raises(FirestoreError):
        with client.transaction() as tx:
            tx.create(*CONTROL, {"epoch": 0})
            tx.commit()
    assert client.get(*CONTROL) == stored


def test_emulator_commits_observation_receipt_and_mail_intent_together(ledger):
    _, transport = emulators()
    store, at, monitor, state, outcome = ledger
    client = FirestoreClient("demo-ticketradar-outbox", lambda: "owner", transport)
    client.commit([(collection, key, fields) for (collection, key), (fields, _) in store.rows.items()])
    calls = []
    with Execution(client, "shadow-test", clock=lambda: at[0]) as execution:
        claim = execution.claim(monitor, state, request_id="fixture-request")
        observed, changes = execution.finish(monitor, claim, outcome)
        assert observed.check_count == len(changes) == 1
        assert execution.already_checked(monitor.id, "fixture-request")
        pending = client.query(OUTBOX, equals={"status": "pending"})
        assert len(pending) == 1
        item_id, = pending
        outbox = Outbox(execution, lambda *args: calls.append(args))
        assert outbox.deliver(item_id, monitor.notify_email) == "accepted"
        assert outbox.deliver(item_id, monitor.notify_email) == "skipped"
    assert len(calls) == 1 and client.get("history", item_id)["owner_uid"] == monitor.owner_uid


def test_emulator_refused_mail_is_detected_again_not_lost(ledger):
    from datetime import timedelta

    from monitor.models import CheckOutcome
    from monitor.state import STATES, MonitorState
    from notifications.outbox import Rejected

    _, transport = emulators()
    store, at, monitor, state, outcome = ledger
    client = FirestoreClient("demo-ticketradar-release", lambda: "owner", transport)
    client.commit([(collection, key, fields) for (collection, key), (fields, _) in store.rows.items()])

    def refuse(*_):
        raise Rejected(retryable=False)

    with Execution(client, "shadow-test", clock=lambda: at[0]) as execution:
        claim = execution.claim(monitor, state)
        execution.finish(monitor, claim, outcome)
        first, = client.query(OUTBOX, equals={"status": "pending"})
        assert Outbox(execution, refuse).deliver(first, monitor.notify_email) == "failed"
    at[0] += timedelta(minutes=5)
    calls = []
    with Execution(client, "shadow-test", clock=lambda: at[0]) as execution:
        observed = MonitorState.from_dict(client.get(STATES, monitor.id))
        claim = execution.claim(monitor, observed)
        execution.finish(monitor, claim, CheckOutcome(monitor.id, at[0], True, outcome.results))
        second, = client.query(OUTBOX, equals={"status": "pending"})
        assert second != first
        assert Outbox(execution, lambda *a: calls.append(a)).deliver(second, monitor.notify_email) == "accepted"
    assert len(calls) == 1
