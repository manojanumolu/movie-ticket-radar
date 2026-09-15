"""Persistent monitoring state — the memory that survives between runs.

Every GitHub Actions run is a cold start. Without this the checker would
have nothing to compare against and would email on every single run, which
is the failure mode this application exists to avoid.

Three kinds of record, two owners:

* monitors      — what a person asked for (owned by the UI)
* monitor state — what the worker observed (owned by the worker)
* history       — the activity log both of them append to

…and two places they can live, behind one set of functions so the checker
and the worker never know which:

* **Firestore**, keyed on the Firebase UID that created each monitor. The
  app reads and writes as the signed-in person (their ID token; the rules
  in ``firestore.rules`` allow only ``owner_uid == uid``), and the worker
  reads everything with a service account. This is what a deployment uses.
* **``data/*.json``** in the repository — the original store, still what the
  tests and a machine without Firebase use. It has no notion of owner and
  is global; that is exactly why it is no longer the store for people.

The app registers a *scope provider* (:func:`set_scope_provider`) that
answers "who is this call for?" from Streamlit's own session, so nothing in
this module ever holds a user in a module-level variable. With no provider
answer and a service account in the environment, calls are the worker's;
with neither, they hit the JSON files.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from config import firestore as fs
from config.store import (
    DEFAULTS,
    MONITORS_FILE,
    STATE_FILE,
    load_history as _json_load_history,
    load_settings as _json_load_settings,
    read_json,
    save_history as _json_save_history,
    save_settings as _json_save_settings,
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
# Which store, for whom
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Scope:
    """One signed-in person's view of Firestore: the project, the UID that
    owns what they may see, and a way to get their current ID token."""

    project_id: str
    uid: str
    token: Callable[[], str]


ScopeProvider = Callable[[], "Scope | None"]
_scope_provider: ScopeProvider | None = None
#: The worker's client, built once from the environment.
_admin: "_FirestoreStore | None" = None
#: Injected by tests to stand in for the service.
_transport: Callable[..., tuple[int, Any]] | None = None


def set_scope_provider(provider: ScopeProvider | None) -> None:
    """The app calls this once at import with a function that reads the
    signed-in user out of Streamlit's session (thread-correct by Streamlit's
    own design). ``None`` — from the provider or as the provider — means
    "nobody", and the JSON files are used."""
    global _scope_provider
    _scope_provider = provider


def backend_name() -> str:
    """``"firestore"`` or ``"json"`` — what the *next* call would use."""
    return "firestore" if isinstance(_backend(), _FirestoreStore) else "json"


def _backend() -> "_Store":
    global _admin
    if _scope_provider is not None:
        scope = _scope_provider()
        if scope is not None and scope.project_id and scope.uid:
            client = fs.FirestoreClient(scope.project_id, scope.token, transport=_transport or fs._http)
            return _FirestoreStore(client, uid=scope.uid)
    info = fs.service_account_from_env()
    project = fs.project_from_env()
    if info and project:
        if _admin is None or _admin.client.project_id != project:
            client = fs.FirestoreClient(project, fs.service_account_token_getter(info),
                                        transport=_transport or fs._http)
            _admin = _FirestoreStore(client, uid=None)
        return _admin
    return _JSON


# ──────────────────────────────────────────────────────────────────────────
# The JSON store — the original, unchanged in behaviour
# ──────────────────────────────────────────────────────────────────────────
class _JsonStore:
    name = "json"

    def load_monitors(self) -> list[Monitor]:
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

    def save_monitors(self, monitors: list[Monitor], *, mirror: bool = True) -> None:
        write_json(MONITORS_FILE, [m.to_dict() for m in monitors], mirror=mirror,
                   message="chore: update monitors")

    def delete_monitor(self, monitor_id: str, *, mirror: bool = True) -> None:
        self.save_monitors([m for m in self.load_monitors() if m.id != monitor_id], mirror=mirror)
        self.clear_monitor_state(monitor_id, mirror=mirror)

    def load_state(self) -> dict[str, MonitorState]:
        raw = read_json(STATE_FILE)
        if not isinstance(raw, dict):
            return {}
        return {k: MonitorState.from_dict(v) for k, v in raw.items()}

    def save_state(self, state: dict[str, MonitorState], *, mirror: bool = True) -> None:
        write_json(STATE_FILE, {k: v.to_dict() for k, v in state.items()}, mirror=mirror,
                   message="chore: update monitoring state")

    def clear_monitor_state(self, monitor_id: str, *, mirror: bool = True) -> None:
        state = self.load_state()
        if state.pop(monitor_id, None) is not None:
            self.save_state(state, mirror=mirror)

    def load_history(self) -> list[dict[str, Any]]:
        return _json_load_history()

    def append_history(self, monitor: Monitor, item: dict[str, Any], *, mirror: bool = True) -> None:
        history = self.load_history()
        history.insert(0, item)
        _json_save_history(history, mirror=mirror)

    def recent_history_for(self, monitor: Monitor) -> list[dict[str, Any]]:
        return self.load_history()[:8]

    def load_settings(self) -> dict[str, Any]:
        return _json_load_settings()

    def save_settings(self, settings: dict[str, Any], *, mirror: bool = True) -> None:
        _json_save_settings(settings, mirror=mirror)


_JSON = _JsonStore()


# ──────────────────────────────────────────────────────────────────────────
# The Firestore store — every record carries owner_uid
# ──────────────────────────────────────────────────────────────────────────
MONITORS, STATES, HISTORY, USERS = "monitors", "monitor_state", "history", "users"
HISTORY_LIMIT = 50
#: monitor id → owner, remembered from the last load so the worker (which
#: has no UID of its own) can write each state document to its owner.
_owner_of: dict[str, str] = {}


class _FirestoreStore:
    """``uid`` set: the signed-in person's store — queries are filtered on
    their UID *and* every document that comes back is re-checked, so even a
    misdeployed rule set could not show one person another's data through
    this code. ``uid`` None: the worker, every owner."""

    name = "firestore"

    def __init__(self, client: fs.FirestoreClient, uid: str | None):
        self.client = client
        self.uid = uid

    # -- helpers ----------------------------------------------------------
    def _mine(self) -> dict[str, Any] | None:
        return {fs.OWNER: self.uid} if self.uid else None

    def _owned(self, doc: dict[str, Any]) -> bool:
        return self.uid is None or doc.get(fs.OWNER) == self.uid

    def _stamp(self, monitor: Monitor) -> str:
        """The owner a monitor is written under. A person can only ever
        write their own; the worker keeps whatever owner a monitor has."""
        if self.uid:
            if monitor.owner_uid and monitor.owner_uid != self.uid:
                raise PermissionError("a monitor cannot be written under another account")
            monitor.owner_uid = self.uid
        return monitor.owner_uid

    # -- monitors ---------------------------------------------------------
    def load_monitors(self) -> list[Monitor]:
        monitors: list[Monitor] = []
        for doc_id, doc in self.client.query(MONITORS, equals=self._mine()).items():
            if not self._owned(doc):
                continue
            try:
                monitor = Monitor.from_dict({**doc, "id": doc_id})
            except (KeyError, TypeError, ValueError) as exc:
                print(f"[state] skipping unreadable monitor {doc_id}: {exc}")
                continue
            _owner_of[monitor.id] = monitor.owner_uid
            monitors.append(monitor)
        monitors.sort(key=lambda m: m.created_at, reverse=True)
        return monitors

    def save_monitors(self, monitors: list[Monitor], *, mirror: bool = True) -> None:
        writes = []
        for monitor in monitors:
            owner = self._stamp(monitor)
            _owner_of[monitor.id] = owner
            writes.append((MONITORS, monitor.id, monitor.to_dict()))
        self.client.commit(writes)

    def delete_monitor(self, monitor_id: str, *, mirror: bool = True) -> None:
        # A direct document read of somebody else's monitor is rightly denied
        # by Firestore before we can inspect ``owner_uid``.  From the UI this
        # is indistinguishable from an id that does not exist in this user's
        # collection, so make deletion the same harmless no-op as stop/extend
        # instead of surfacing a permission error.
        try:
            doc = self.client.get(MONITORS, monitor_id)
        except fs.FirestoreError as exc:
            if self.uid is not None and exc.status in (401, 403):
                return
            raise
        if doc is None or not self._owned(doc):
            return
        self.client.commit([(MONITORS, monitor_id, None), (STATES, monitor_id, None)])
        _owner_of.pop(monitor_id, None)

    # -- observed state ---------------------------------------------------
    def load_state(self) -> dict[str, MonitorState]:
        out: dict[str, MonitorState] = {}
        for doc_id, doc in self.client.query(STATES, equals=self._mine()).items():
            if self._owned(doc):
                out[doc_id] = MonitorState.from_dict(doc)
        return out

    def save_state(self, state: dict[str, MonitorState], *, mirror: bool = True) -> None:
        writes = []
        for monitor_id, ms in state.items():
            owner = self.uid or _owner_of.get(monitor_id, "")
            if not owner:
                existing = self.client.get(STATES, monitor_id) or self.client.get(MONITORS, monitor_id) or {}
                owner = str(existing.get(fs.OWNER, ""))
            writes.append((STATES, monitor_id, {**ms.to_dict(), fs.OWNER: owner}))
        self.client.commit(writes)

    def clear_monitor_state(self, monitor_id: str, *, mirror: bool = True) -> None:
        doc = self.client.get(STATES, monitor_id)
        if doc is not None and self._owned(doc):
            self.client.delete(STATES, monitor_id)

    # -- history ----------------------------------------------------------
    def _history_docs(self, owner: str | None) -> list[dict[str, Any]]:
        equals = {fs.OWNER: owner} if owner else None
        docs = [d for d in self.client.query(HISTORY, equals=equals).values() if self._owned(d)]
        docs.sort(key=lambda d: str(d.get("at", "")), reverse=True)
        return docs

    def load_history(self) -> list[dict[str, Any]]:
        return self._history_docs(self.uid)[:HISTORY_LIMIT]

    def recent_history_for(self, monitor: Monitor) -> list[dict[str, Any]]:
        return self._history_docs(self.uid or monitor.owner_uid or None)[:8]

    def append_history(self, monitor: Monitor, item: dict[str, Any], *, mirror: bool = True) -> None:
        owner = self.uid or monitor.owner_uid
        if not owner:
            print(f"[state] history for {monitor.id} has no owner; not written", flush=True)
            return
        stamp = str(item.get("at", "")).replace(":", "").replace("-", "")[:15] or "0"
        self.client.set(HISTORY, f"{stamp}-{monitor.id[:8]}-{secrets.token_hex(3)}",
                        {**item, fs.OWNER: owner})

    # -- settings (users/{uid}) -------------------------------------------
    def load_settings(self) -> dict[str, Any]:
        if not self.uid:
            return dict(DEFAULTS["settings.json"])
        doc = self.client.get(USERS, self.uid) or {}
        return {**DEFAULTS["settings.json"], **{k: v for k, v in doc.items() if k != fs.OWNER}}

    def save_settings(self, settings: dict[str, Any], *, mirror: bool = True) -> None:
        if not self.uid:
            return
        self.client.set(USERS, self.uid, {**settings, fs.OWNER: self.uid})


_Store = _JsonStore  # the protocol both stores follow


# ──────────────────────────────────────────────────────────────────────────
# Monitors — the functions the UI, the checker and the worker call
# ──────────────────────────────────────────────────────────────────────────
def load_monitors() -> list[Monitor]:
    return _backend().load_monitors()


def save_monitors(monitors: list[Monitor], *, mirror: bool = True) -> None:
    _backend().save_monitors(monitors, mirror=mirror)


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
    """Mark a monitor stopped *in the store*.

    Hiding it in the UI would not be enough — the worker reads the store, so
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
    _backend().delete_monitor(monitor_id, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# Settings — per person in Firestore, the one file in JSON
# ──────────────────────────────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    return _backend().load_settings()


def save_settings(settings: dict[str, Any], *, mirror: bool = True) -> None:
    _backend().save_settings(settings, mirror=mirror)


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
    return _backend().load_state()


def save_state(state: dict[str, MonitorState], *, mirror: bool = True) -> None:
    _backend().save_state(state, mirror=mirror)


def get_monitor_state(monitor_id: str) -> MonitorState:
    return load_state().get(monitor_id, MonitorState())


def clear_monitor_state(monitor_id: str, *, mirror: bool = True) -> None:
    _backend().clear_monitor_state(monitor_id, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# History (the right-rail "Recent history" list)
# ──────────────────────────────────────────────────────────────────────────
def load_history() -> list[dict[str, Any]]:
    """The signed-in person's history (Firestore), or the one global list (JSON)."""
    return _backend().load_history()


def record_history(monitor: Monitor, kind: str, message: str, *, mirror: bool = True,
                   at: datetime | None = None, extra: dict[str, Any] | None = None) -> None:
    """Append one line to the activity log — the monitor's owner's log.

    De-duplicated on (monitor, kind, message) within the last hour so a
    flapping check cannot flood the rail.
    """
    at = at or now_ist()
    store = _backend()
    for item in store.recent_history_for(monitor):
        if (
            item.get("monitor_id") == monitor.id
            and item.get("kind") == kind
            and item.get("message") == message
        ):
            seen = parse_iso(item.get("at"))
            if seen and (at - seen) < timedelta(hours=1):
                return
    store.append_history(monitor, {
        "monitor_id": monitor.id,
        "kind": kind,
        "message": message,
        "movie": monitor.movie.title,
        "language": monitor.movie.language,
        "poster_url": monitor.movie.poster_url,
        "targets": [t.label for t in monitor.targets],
        "at": to_iso(at),
        **(extra or {}),
    }, mirror=mirror)


__all__ = [
    "DUE_TOLERANCE_SECONDS",
    "HISTORY_LIMIT",
    "MonitorState",
    "NEW_SHOWTIME_COOLDOWN",
    "Scope",
    "TargetState",
    "backend_name",
    "clear_monitor_state",
    "delete_monitor",
    "expire_due_monitors",
    "extend_monitor",
    "get_monitor",
    "get_monitor_state",
    "load_history",
    "load_monitors",
    "load_settings",
    "load_state",
    "record_history",
    "save_monitors",
    "save_settings",
    "save_state",
    "set_scope_provider",
    "stop_monitor",
    "upsert_monitor",
]
