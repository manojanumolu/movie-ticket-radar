"""Bounded polling with fixture-only shadow execution."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from config.timezone import now_ist


@dataclass(frozen=True)
class Intent:
    monitor_id: str = ""
    force: bool = False
    request_id: str = ""


class Runtime:
    def __init__(self, tick, *, clock=time.monotonic, sleep=time.sleep, now=now_ist):
        self.tick = tick
        self.clock = clock
        self.sleep = sleep
        self.now = now

    def run(self, intent: Intent, *, scheduled: bool) -> list[dict]:
        start = self.clock()
        deadline = start + 45
        results = [self.tick(intent, self.now(), deadline)]
        if scheduled and self.clock() <= start + 35:
            self.sleep(max(0, start + 30 - self.clock()))
            if self.clock() < deadline:
                results.append(self.tick(intent, self.now(), deadline))
        return results


class FencedTick:
    def __init__(self, client, authority, *, sender, catalogue, clock=time.monotonic, now=now_ist):
        self.client = client
        self.authority = authority
        self.sender = sender
        self.catalogue = catalogue
        self.clock = clock
        self.now = now

    def __call__(self, intent, at, deadline):
        from config.store import catalogue_source
        from monitor.checker import run_once
        from monitor.execution import Execution, OUTBOX
        from notifications.outbox import Outbox

        with Execution(self.client, self.authority, clock=self.now) as execution, catalogue_source(self.catalogue):
            if intent.request_id and execution.already_checked(intent.monitor_id, intent.request_id):
                return {"duplicate_request": 1}
            report = run_once(
                at=at, monitor_id=intent.monitor_id, force=intent.force, execution=execution,
                request_id=intent.request_id, within_deadline=lambda: self.clock() < deadline,
            )
            eligible = {m.id: m.notify_email for m in report.monitors if m.is_running(at)}
            counts = {"checked": len(report.checked), "changes": len(report.changes),
                      "failed_checks": len(report.failed), "fetches": report.fetches}
            sender = Outbox(execution, self.sender)
            for status in ("sending", "retry", "pending"):
                if self.clock() >= deadline:
                    break
                where = {"status": status}
                if intent.monitor_id:
                    where["monitor_id"] = intent.monitor_id
                rows = self.client.query(OUTBOX, equals=where, limit=50)
                for item_id, item in rows.items():
                    if self.clock() >= deadline:
                        break
                    mid = item["monitor_id"]
                    if not intent.monitor_id or mid == intent.monitor_id:
                        result = sender.deliver(item_id, eligible.get(mid, ""))
                        name = f"mail_{result}"
                        counts[name] = counts.get(name, 0) + 1
            return counts


def replay_tick(intent: Intent, at: datetime, deadline: float) -> dict:
    """Exercise due calculation and the state machine on synthetic data."""
    from monitor.changes import apply_outcome, detect_changes
    from monitor.models import CheckOutcome, Monitor, MovieRef, TheatreTarget
    from monitor.state import MonitorState

    movie = MovieRef("bookmyshow", "ET00000001", "Fixture", "HYD", "hyderabad")
    monitor = Monitor(movie=movie, targets=[TheatreTarget("FIX", "Fixture", fmt="2D")],
                      id="fixture-monitor", created_at=at, monitor_until=at + timedelta(days=1))
    state = MonitorState()
    selected = not intent.monitor_id or intent.monitor_id == monitor.id
    if not selected or not (intent.force or state.is_due(monitor.interval_minutes, at)):
        return {"checked": 0, "changes": 0}
    outcome = CheckOutcome(monitor.id, at, True)
    changes = detect_changes(monitor, outcome, state)
    apply_outcome(outcome, state)
    return {"checked": 1, "changes": len(changes)}
