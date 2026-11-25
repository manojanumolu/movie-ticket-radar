"""Firestore fencing for a worker invocation and its per-monitor observations."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from datetime import timedelta
from uuid import uuid4

from config.firestore import OWNER
from config.timezone import now_ist, parse_iso, to_iso
from monitor.changes import apply_outcome, detect_changes, mark_notified
from monitor.models import Monitor
from monitor.state import HISTORY, MONITORS, STATES, MonitorState

CONTROL = ("worker_runtime", "control")
EXECUTION = "monitor_execution"
OUTBOX = "monitor_outbox"
LEASE_DURATION = timedelta(minutes=3)


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    monitor_version: str
    state_version: str | None
    check_id: str


class Execution:
    """All mutations use transactions. Network checks and SMTP run outside them."""

    def __init__(self, client, authority: str, *, clock=now_ist):
        self.client = client
        self.authority = authority
        self.clock = clock
        self.epoch = None
        self.holder = uuid4().hex

    def __enter__(self):
        with self.client.transaction() as tx:
            control, _ = tx.get(*CONTROL)
            if not control or not self.authority or control.get("authority") != self.authority:
                raise LeaseLost("worker authority is disabled")
            until = parse_iso(control.get("lease_until"))
            if until and until > self.clock():
                raise LeaseLost("worker already has an active lease")
            epoch = control.get("epoch")
            if type(epoch) is not int or epoch < 1:
                raise LeaseLost("worker epoch is invalid")
            tx.set(*CONTROL, {**control, "holder": self.holder,
                              "lease_until": to_iso(self.clock() + LEASE_DURATION)})
            tx.commit()
            self.epoch = epoch
        return self

    def __exit__(self, *exc):
        with self.client.transaction() as tx:
            control, _ = tx.get(*CONTROL)
            if (control and control.get("holder") == self.holder
                    and control.get("epoch") == self.epoch):
                tx.set(*CONTROL, {**control, "holder": "", "lease_until": ""})
                tx.commit()

    def assert_lease(self, tx):
        control, _ = tx.get(*CONTROL)
        until = parse_iso((control or {}).get("lease_until"))
        if (not control or control.get("authority") != self.authority
                or control.get("holder") != self.holder or control.get("epoch") != self.epoch
                or not until or until <= self.clock()):
            raise LeaseLost("worker lease lost")

    def verify(self):
        with self.client.transaction() as tx:
            self.assert_lease(tx)

    def discovery_clock(self):
        return (self.client.get("worker_runtime", "discovery") or {}).get("clock", {})

    def save_discovery_clock(self, clock, **_kwargs):
        with self.client.transaction() as tx:
            self.assert_lease(tx)
            tx.get("worker_runtime", "discovery")
            tx.set("worker_runtime", "discovery", {"clock": clock})
            tx.commit()

    def update_monitor(self, original, monitor, *, expired=False):
        mid = original.id
        with self.client.transaction() as tx:
            self.assert_lease(tx)
            current, _ = tx.get(MONITORS, mid)
            if (not current or not original.owner_uid or current.get(OWNER) != original.owner_uid
                    or Monitor.from_dict({**current, "id": mid}).to_dict() != original.to_dict()):
                return False
            tx.set(MONITORS, mid, monitor.to_dict())
            if expired:
                tx.create(HISTORY, uuid4().hex, history(
                    monitor, "EXPIRED", "Monitoring expired — reached the end time you set.", self.clock(),
                ))
            tx.commit()
            return True

    def claim(self, original: Monitor, observed: MonitorState) -> Claim | None:
        mid = original.id
        with self.client.transaction() as tx:
            self.assert_lease(tx)
            monitor, version = tx.get(MONITORS, mid)
            state, state_version = tx.get(STATES, mid)
            execution, _ = tx.get(EXECUTION, mid)
            if (not monitor or not version or not original.owner_uid
                    or monitor.get(OWNER) != original.owner_uid
                    or Monitor.from_dict({**monitor, "id": mid}).to_dict() != original.to_dict()
                    or not original.is_running(self.clock())
                    or (state is not None and state.get(OWNER) != original.owner_uid)
                    or MonitorState.from_dict(state or {}).to_dict() != observed.to_dict()):
                return None
            if execution and execution.get("holder") == self.holder and execution.get("claim"):
                return None
            if execution and execution.get(OWNER) != original.owner_uid:
                return None
            claim = Claim(version, state_version, uuid4().hex)
            tx.set(EXECUTION, mid, {**(execution or {}), OWNER: original.owner_uid,
                                    "claim": claim.check_id, "holder": self.holder})
            tx.commit()
            return claim

    def finish(self, monitor: Monitor, claim: Claim, outcome) -> tuple | None:
        from notifications.outbox import make_item

        mid = monitor.id
        with self.client.transaction() as tx:
            self.assert_lease(tx)
            current, version = tx.get(MONITORS, mid)
            state, state_version = tx.get(STATES, mid)
            execution, _ = tx.get(EXECUTION, mid)
            if (not current or version != claim.monitor_version or state_version != claim.state_version
                    or not execution or execution.get("holder") != self.holder
                    or execution.get("claim") != claim.check_id
                    or not Monitor.from_dict({**current, "id": mid}).is_running(self.clock())):
                return None
            actual = MonitorState.from_dict(state or {})
            detection = MonitorState.from_dict(execution.get("detection") or actual.to_dict())
            changes = detect_changes(monitor, outcome, detection)
            apply_outcome(outcome, actual)
            apply_outcome(outcome, detection)
            for change in changes:
                mark_notified(change, detection, outcome.checked_at)
                item_id, item = make_item(
                    monitor, change, claim.check_id, version, actual.targets[change.target_key],
                )
                tx.create(OUTBOX, item_id, item)
            if not outcome.ok:
                hour = to_iso(outcome.checked_at)[:13]
                key = sha256(f"error:{monitor.owner_uid}:{mid}:{hour}".encode()).hexdigest()
                entry, _ = tx.get(HISTORY, key)
                if entry is None:
                    tx.create(HISTORY, key, history(
                        monitor, "ERROR", "The platform check failed. Availability is unchanged.",
                        outcome.checked_at,
                    ))
            tx.set(STATES, mid, {**actual.to_dict(), OWNER: monitor.owner_uid})
            tx.set(EXECUTION, mid, {**execution, "detection": detection.to_dict(),
                                    "claim": "", "last_check_id": claim.check_id})
            tx.commit()
            return actual, changes


def history(monitor, kind, message, at, **extra):
    return {
        OWNER: monitor.owner_uid, "monitor_id": monitor.id, "kind": kind, "message": message,
        "movie": monitor.movie.title, "language": monitor.movie.language,
        "poster_url": monitor.movie.poster_url, "targets": [t.label for t in monitor.targets],
        "at": to_iso(at), **extra,
    }
