"""Persistent monitoring state — the memory that survives between runs.

Every GitHub Actions run is a cold start. Without this file the checker would
have nothing to compare against and would email on every single run, which is
the failure mode this application exists to avoid.

Two files, two jobs:

* ``data/monitors.json`` — what the user asked for (owned by the UI).
* ``data/state.json``    — what we have observed (owned by the worker).

Keeping them apart means the worker can rewrite observations every ten minutes
without ever touching, or racing, the user's configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from config.store import (
    MONITORS_FILE,
    STATE_FILE,
    load_history,
    read_json,
    save_history,
    write_json,
)
from config.timezone import now_ist, parse_iso, to_iso
from monitor.models import Availability, Monitor, MonitorStatus

#: Never send more than one "new showtime" notice per target inside this
#: window, regardless of how many showtimes appear. Ticket releases arrive in
#: bursts; without this a staggered release becomes an inbox full of mail.
NEW_SHOWTIME_COOLDOWN = timedelta(minutes=45)

#: How early a check may run and still count as "on the interval". Absorbs
#: scheduler jitter; without it a run landing 8 seconds early would skip and
#: the effective interval would drift outwards run after run.
DUE_TOLERANCE_SECONDS = 30


# ──────────────────────────────────────────────────────────────────────────
# Monitors
# ──────────────────────────────────────────────────────────────────────────
def load_monitors() -> list[Monitor]:
    raw = read_json(MONITORS_FILE)
    if not isinstance(raw, list):
        return []
    monitors: list[Monitor] = []
    for item in raw:
        try:
            monitors.append(Monitor.from_dict(item))
        except (KeyError, TypeError, ValueError) as exc:
            print(f"[state] skipping unreadable monitor: {exc}")
    return monitors


def save_monitors(monitors: list[Monitor], *, mirror: bool = True) -> None:
    write_json(
        MONITORS_FILE,
        [m.to_dict() for m in monitors],
        mirror=mirror,
        message="chore: update monitors",
    )


def upsert_monitor(monitor: Monitor, *, mirror: bool = True) -> list[Monitor]:
    monitors = load_monitors()
    for i, existing in enumerate(monitors):
        if existing.id == monitor.id:
            monitors[i] = monitor
            break
    else:
        monitors.insert(0, monitor)
    save_monitors(monitors, mirror=mirror)
    return monitors


def get_monitor(monitor_id: str) -> Monitor | None:
    return next((m for m in load_monitors() if m.id == monitor_id), None)


def expire_due_monitors(monitors: list[Monitor] | None = None, *, at: datetime | None = None,
                        mirror: bool = True) -> tuple[list[Monitor], list[Monitor]]:
    """Flip every past-its-end-time monitor to EXPIRED and persist.

    Called at the start of every worker run *and* every UI render, so a
    forgotten monitor cannot outlive its end time in either place.
    """
    at = at or now_ist()
    monitors = load_monitors() if monitors is None else monitors
    newly_expired = [m for m in monitors if m.status.is_running and m.is_expired(at)]
    for m in newly_expired:
        m.expire(at)
        record_history(m, "EXPIRED", "Monitoring expired — reached the end time you set.")
    if newly_expired:
        save_monitors(monitors, mirror=mirror)
    return monitors, newly_expired


def stop_monitor(monitor_id: str, *, at: datetime | None = None, mirror: bool = True) -> Monitor | None:
    """Mark a monitor stopped *in the persisted file*.

    Hiding it in the UI would not be enough — the worker reads this file, so
    stopping has to change what the worker sees.
    """
    monitors = load_monitors()
    target = next((m for m in monitors if m.id == monitor_id), None)
    if target is None:
        return None
    target.stop(at)
    save_monitors(monitors, mirror=mirror)
    record_history(target, "STOPPED", "Monitoring stopped — you stopped this alert.")
    return target


def extend_monitor(monitor_id: str, hours: int = 24, *, mirror: bool = True) -> Monitor | None:
    monitors = load_monitors()
    target = next((m for m in monitors if m.id == monitor_id), None)
    if target is None:
        return None
    target.extend(hours)
    save_monitors(monitors, mirror=mirror)
    return target


def delete_monitor(monitor_id: str, *, mirror: bool = True) -> None:
    monitors = [m for m in load_monitors() if m.id != monitor_id]
    save_monitors(monitors, mirror=mirror)
    clear_monitor_state(monitor_id, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# Observed state
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class TargetState:
    """What we last *observed* for one theatre+format target.

    ``availability`` is the last real answer. ``notified_availability`` is the
    last answer we successfully emailed about. They are separate so that a
    failed send is retried next run instead of being silently swallowed.
    """

    availability: Availability = Availability.UNKNOWN
    since: datetime | None = None
    showtime_keys: list[str] = field(default_factory=list)
    time_labels: list[str] = field(default_factory=list)
    #: ``[[label, url], …]`` — the per-showtime links behind ``time_labels``.
    time_links: list[list[str]] = field(default_factory=list)
    date_code: str = ""
    #: Every show date behind the current showtimes (``date_code`` is the first).
    date_codes: list[str] = field(default_factory=list)
    booking_url: str = ""
    notified_availability: Availability = Availability.UNKNOWN
    notified_at: datetime | None = None
    notified_showtime_keys: list[str] = field(default_factory=list)
    new_showtime_notice_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "since": to_iso(self.since),
            "showtime_keys": self.showtime_keys,
            "time_labels": self.time_labels,
            "time_links": [list(pair) for pair in self.time_links],
            "date_code": self.date_code,
            "date_codes": list(self.date_codes),
            "booking_url": self.booking_url,
            "notified_availability": self.notified_availability.value,
            "notified_at": to_iso(self.notified_at),
            "notified_showtime_keys": self.notified_showtime_keys[-200:],
            "new_showtime_notice_at": to_iso(self.new_showtime_notice_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TargetState:
        def avail(key: str) -> Availability:
            try:
                return Availability(raw.get(key, "UNKNOWN"))
            except ValueError:
                return Availability.UNKNOWN

        return cls(
            availability=avail("availability"),
            since=parse_iso(raw.get("since")),
            showtime_keys=list(raw.get("showtime_keys", [])),
            time_labels=list(raw.get("time_labels", [])),
            time_links=[
                [str(pair[0]), str(pair[1])]
                for pair in raw.get("time_links", [])
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            ],
            date_code=raw.get("date_code", ""),
            date_codes=[str(c) for c in raw.get("date_codes", []) if c],
            booking_url=raw.get("booking_url", ""),
            notified_availability=avail("notified_availability"),
            notified_at=parse_iso(raw.get("notified_at")),
            notified_showtime_keys=list(raw.get("notified_showtime_keys", [])),
            new_showtime_notice_at=parse_iso(raw.get("new_showtime_notice_at")),
        )

    def may_announce_new_showtimes(self, at: datetime) -> bool:
        if self.new_showtime_notice_at is None:
            return True
        return at - self.new_showtime_notice_at >= NEW_SHOWTIME_COOLDOWN


@dataclass
class MonitorState:
    """Run bookkeeping for one monitor."""

    last_check_at: datetime | None = None
    last_success_at: datetime | None = None
    check_count: int = 0
    success_count: int = 0
    consecutive_errors: int = 0
    last_error: str = ""
    #: "BLOCKED" when the platform refused us, "ERROR" for anything else, ""
    #: when the last check succeeded. Lets the UI say which one happened.
    last_error_kind: str = ""
    #: The last email delivery failure, until a send succeeds. Retried
    #: automatically; surfaced as a problem so it is never silent.
    last_email_error: str = ""
    targets: dict[str, TargetState] = field(default_factory=dict)

    @property
    def is_blocked(self) -> bool:
        return bool(self.consecutive_errors) and self.last_error_kind == "BLOCKED"

    def target(self, key: str) -> TargetState:
        return self.targets.setdefault(key, TargetState())

    def is_due(self, interval_minutes: int, at: datetime | None = None) -> bool:
        """Has enough time passed since the last attempt?

        The workflow ticks more often than any monitor's interval and this is
        what decides whether a tick actually does work. A small tolerance
        absorbs GitHub's scheduling jitter — without it a run that lands 8
        seconds early would skip, and the effective interval would drift
        outwards run after run.
        """
        if self.last_check_at is None:
            return True
        at = at or now_ist()
        elapsed = (at - self.last_check_at).total_seconds()
        return elapsed >= interval_minutes * 60 - DUE_TOLERANCE_SECONDS

    def next_check_at(self, interval_minutes: int) -> datetime | None:
        if self.last_check_at is None:
            return None
        return self.last_check_at + timedelta(minutes=interval_minutes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_check_at": to_iso(self.last_check_at),
            "last_success_at": to_iso(self.last_success_at),
            "check_count": self.check_count,
            "success_count": self.success_count,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "last_error_kind": self.last_error_kind,
            "last_email_error": self.last_email_error,
            "targets": {k: v.to_dict() for k, v in self.targets.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MonitorState:
        if not isinstance(raw, dict):
            return cls()
        targets = raw.get("targets")
        return cls(
            last_check_at=parse_iso(raw.get("last_check_at")),
            last_success_at=parse_iso(raw.get("last_success_at")),
            check_count=int(raw.get("check_count", 0) or 0),
            success_count=int(raw.get("success_count", 0) or 0),
            consecutive_errors=int(raw.get("consecutive_errors", 0) or 0),
            last_error=raw.get("last_error", "") or "",
            last_error_kind=str(raw.get("last_error_kind", "") or ""),
            last_email_error=str(raw.get("last_email_error", "") or ""),
            targets={
                k: TargetState.from_dict(v)
                for k, v in (targets.items() if isinstance(targets, dict) else [])
                if isinstance(v, dict)
            },
        )


def load_state() -> dict[str, MonitorState]:
    raw = read_json(STATE_FILE)
    if not isinstance(raw, dict):
        return {}
    return {k: MonitorState.from_dict(v) for k, v in raw.items()}


def save_state(state: dict[str, MonitorState], *, mirror: bool = True) -> None:
    write_json(
        STATE_FILE,
        {k: v.to_dict() for k, v in state.items()},
        mirror=mirror,
        message="chore: update monitoring state",
    )


def get_monitor_state(monitor_id: str) -> MonitorState:
    return load_state().get(monitor_id, MonitorState())


def clear_monitor_state(monitor_id: str, *, mirror: bool = True) -> None:
    state = load_state()
    if state.pop(monitor_id, None) is not None:
        save_state(state, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# History (the right-rail "Recent history" list)
# ──────────────────────────────────────────────────────────────────────────
def record_history(monitor: Monitor, kind: str, message: str, *, mirror: bool = True,
                   at: datetime | None = None, extra: dict[str, Any] | None = None) -> None:
    """Append one line to the activity log.

    De-duplicated on (monitor, kind, message) within the last hour so a
    flapping check cannot flood the rail.
        """
    at = at or now_ist()
    history = load_history()
    for item in history[:8]:
        if (
            item.get("monitor_id") == monitor.id
            and item.get("kind") == kind
            and item.get("message") == message
        ):
            seen = parse_iso(item.get("at"))
            if seen and (at - seen) < timedelta(hours=1):
                return
    history.insert(
        0,
        {
            "monitor_id": monitor.id,
            "kind": kind,
            "message": message,
            "movie": monitor.movie.title,
            "poster_url": monitor.movie.poster_url,
            "targets": [t.label for t in monitor.targets],
            "at": to_iso(at),
            **(extra or {}),
        },
    )
    save_history(history, mirror=mirror)


__all__ = [
    "DUE_TOLERANCE_SECONDS",
    "MonitorState",
    "NEW_SHOWTIME_COOLDOWN",
    "TargetState",
    "clear_monitor_state",
    "delete_monitor",
    "expire_due_monitors",
    "extend_monitor",
    "get_monitor",
    "get_monitor_state",
    "load_monitors",
    "load_state",
    "record_history",
    "save_monitors",
    "save_state",
    "stop_monitor",
    "upsert_monitor",
]
