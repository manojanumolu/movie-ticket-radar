"""Durable notification intents and conservative SMTP retry semantics."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from config.firestore import OWNER
from config.timezone import parse_iso, to_iso
from monitor.changes import Change, ChangeKind, mark_notified
from monitor.execution import OUTBOX, history
from monitor.models import Availability, Monitor
from monitor.state import HISTORY, MONITORS, STATES, MonitorState
from notifications.email import render_change

MAX_ATTEMPTS = 3
MAX_AGE = timedelta(minutes=20)


class Rejected(RuntimeError):
    """The SMTP transaction ended before acceptance or with an explicit rejection."""

    def __init__(self, *, retryable: bool):
        self.retryable = retryable


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def make_item(monitor, change, check_id, version, target):
    item_id = digest(f"{check_id}:{change.kind.value}:{change.target_key}")
    subject, html, plain = render_change(monitor, change)
    return item_id, {
        OWNER: monitor.owner_uid, "monitor_id": monitor.id, "monitor_version": version,
        "target_key": change.target_key, "since": to_iso(target.since),
        "showtime_keys": target.showtime_keys,
        "recipient_sha256": digest(monitor.notify_email), "status": "pending", "attempts": 0,
        "created_at": to_iso(change.detected_at), "subject": subject, "html": html, "plain": plain,
        "change": {**change.to_dict(), "new_showtime_keys": change.new_showtime_keys},
    }


class Outbox:
    def __init__(self, execution, sender):
        self.execution = execution
        self.sender = sender

    def _start(self, item_id, address):
        now = self.execution.clock()
        with self.execution.client.transaction() as tx:
            self.execution.assert_lease(tx)
            item, _ = tx.get(OUTBOX, item_id)
            if not item:
                return None
            status = item.get("status")
            if status == "sending" and item.get("sender") != self.execution.holder:
                tx.set(OUTBOX, item_id, {**item, "status": "uncertain"})
                tx.commit()
                return None
            if status not in {"pending", "retry"}:
                return None
            retry_at = parse_iso(item.get("retry_at"))
            if retry_at and retry_at > now:
                return None
            monitor, version = tx.get(MONITORS, item["monitor_id"])
            state, _ = tx.get(STATES, item["monitor_id"])
            target = MonitorState.from_dict(state or {}).targets.get(item["target_key"])
            created = parse_iso(item.get("created_at"))
            if (not created or now - created > MAX_AGE or not monitor
                    or version != item["monitor_version"] or monitor.get(OWNER) != item[OWNER]
                    or not Monitor.from_dict({**monitor, "id": item["monitor_id"]}).is_running(now) or target is None
                    or (state or {}).get(OWNER) != item[OWNER]
                    or target.availability is not Availability.AVAILABLE
                    or to_iso(target.since) != item["since"]):
                tx.set(OUTBOX, item_id, {**item, "status": "cancelled"})
                tx.commit()
                return None
            if not address or digest(address) != item["recipient_sha256"]:
                return None
            attempt = {**item, "status": "sending", "sender": self.execution.holder,
                       "attempts": item["attempts"] + 1}
            tx.set(OUTBOX, item_id, attempt)
            tx.commit()
            return attempt

    def deliver(self, item_id: str, address: str) -> str:
        attempt = self._start(item_id, address)
        if attempt is None:
            return "skipped"
        status = "accepted"
        try:
            self.sender(address, attempt["subject"], attempt["html"], attempt["plain"], item_id)
        except Rejected as error:
            status = "retry" if error.retryable and attempt["attempts"] < MAX_ATTEMPTS else "failed"
        except Exception:
            status = "uncertain"
        self._finish(item_id, attempt, status)
        return status

    def _finish(self, item_id, attempt, status):
        now = self.execution.clock()
        with self.execution.client.transaction() as tx:
            self.execution.assert_lease(tx)
            item, _ = tx.get(OUTBOX, item_id)
            if not item or item.get("status") != "sending" or item.get("sender") != attempt["sender"]:
                return
            final = {**item, "status": status, "settled_at": to_iso(now)}
            if status == "retry":
                final["retry_at"] = to_iso(now + timedelta(minutes=2 ** attempt["attempts"]))
            if status == "accepted":
                self._record_acceptance(tx, item_id, item, now)
            tx.set(OUTBOX, item_id, final)
            tx.commit()

    @staticmethod
    def _record_acceptance(tx, item_id, item, now):
        mid = item["monitor_id"]
        current, version = tx.get(MONITORS, mid)
        state, _ = tx.get(STATES, mid)
        if not current or current.get(OWNER) != item[OWNER]:
            return
        monitor = Monitor.from_dict({**current, "id": mid})
        observed = MonitorState.from_dict(state or {})
        target = observed.targets.get(item["target_key"])
        raw = item["change"]
        kind = ChangeKind(raw["kind"])
        if (target and state.get(OWNER) == item[OWNER] and version == item["monitor_version"]
                and to_iso(target.since) == item["since"] and target.availability is Availability.AVAILABLE):
            change = Change(kind, mid, raw["target_key"], raw["venue_name"], raw["fmt"],
                            raw["movie_title"], Availability(raw["previous"]),
                            Availability(raw["current"]), new_showtime_keys=raw["new_showtime_keys"])
            current_keys = target.showtime_keys
            target.showtime_keys = item["showtime_keys"]
            mark_notified(change, observed, now)
            target.showtime_keys = current_keys
            tx.set(STATES, mid, {**observed.to_dict(), OWNER: item[OWNER]})
        tx.create(HISTORY, item_id, history(
            monitor, kind.value, f"{raw['venue_name']} · {raw['fmt']} — {kind.value.replace('_', ' ').title()}",
            now, booking_url=raw["booking_url"],
        ))
