"""A fenced check, intent and delivery never rewrite the monitor document."""
from copy import deepcopy

from monitor.execution import Execution, OUTBOX
from monitor.state import HISTORY, MONITORS, STATES, MonitorState
from notifications.outbox import Outbox
from tests.test_monitor_execution import ledger  # noqa: F401 - pytest fixture


def test_monitor_document_and_identity_survive_a_full_cycle(ledger):
    store, at, monitor, state, outcome = ledger
    before = deepcopy(store.fields(MONITORS, monitor.id))
    with Execution(store, "shadow-test", clock=lambda: at[0]) as worker:
        claim = worker.claim(monitor, MonitorState.from_dict(store.fields(STATES, monitor.id)))
        worker.finish(monitor, claim, outcome)
        [item] = [d for (c, d) in store.rows if c == OUTBOX]
        assert Outbox(worker, lambda *a: None).deliver(item, monitor.notify_email) == "accepted"
    after = store.fields(MONITORS, monitor.id)
    assert after == before
    assert after["id"] == monitor.id if "id" in after else True
    assert after["notify_email"] == monitor.notify_email
    assert after["targets"] == before["targets"]
    assert store.fields(STATES, monitor.id)["owner_uid"] == monitor.owner_uid
    assert store.fields(HISTORY, item)["monitor_id"] == monitor.id
