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


def replay_tick(intent: Intent, at: datetime, deadline: float) -> dict:
    """Exercise due calculation and the state machine on synthetic data."""
    from monitor.changes import apply_outcome, detect_changes
    from monitor.models import CheckOutcome, Monitor, MovieRef, TheatreTarget
    from monitor.state import MonitorState

    movie = MovieRef("bookmyshow", "ET00000001", "Fixture", "HYD", "hyderabad")
    monitor = Monitor(movie=movie, targets=[TheatreTarget("Fixture", "FIX", "2D")],
                      id="fixture-monitor", created_at=at, monitor_until=at + timedelta(days=1))
    state = MonitorState()
    selected = not intent.monitor_id or intent.monitor_id == monitor.id
    if not selected or not (intent.force or state.is_due(monitor.interval_minutes, at)):
        return {"checked": 0, "changes": 0}
    outcome = CheckOutcome(monitor.id, at, True)
    changes = detect_changes(monitor, outcome, state)
    apply_outcome(outcome, state)
    return {"checked": 1, "changes": len(changes)}
