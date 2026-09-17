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
* **``data/*.json``** in the repository — the original compatibility store,
  still what tests and a machine without Firebase use. Command-line use sees
  the legacy global view; a signed-in browser is restricted to records stamped
  with its UID.

The app registers a *scope provider* (:func:`set_scope_provider`) that
answers "who is this call for?" from Streamlit's own session, so nothing in
this module ever holds a user in a module-level variable. With no provider
answer and a service account in the environment, calls are the worker's;
with neither, they hit the JSON files.
"""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from config import firestore as fs
from config.store import (
    DEFAULTS,
    MONITORS_FILE,
    SETTINGS_FILE,
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
    #: Firestore is only used after the deployment opts in. Before then the
    #: legacy JSON compatibility store is still scoped to this UID, never
    #: shared with every signed-in person.
    firestore_enabled: bool = True


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


@dataclass(frozen=True)
class BackendReport:
    """Which store was selected and, when it is not the expected one, why.

    Every field here is safe to print. ``project_configured`` says only
    whether ``FIREBASE_PROJECT_ID`` holds something, never what;
    ``service_account_configured`` says only whether the credential was
    structurally usable, never any part of it.
    """

    name: str
    project_configured: bool
    service_account_configured: bool
    problem: str = ""


def backend_report() -> BackendReport:
    """What :func:`_backend` decided, and what to say about it.

    The worker falls back to the legacy JSON files when its credentials are
    unusable. That is right for a laptop and wrong for production, and from
    the outside the two are identical — a green run that checked nothing. So
    the reason is worked out here, in terms that can be logged.
    """
    info, problem = fs.service_account_status()
    project = fs.project_from_env()
    if not project:
        problem = problem or "FIREBASE_PROJECT_ID is not set"
    try:
        name = backend_name()
    except Exception as exc:  # noqa: BLE001 - a key Google itself refuses
        # Structurally complete but unusable: a truncated private key, say.
        # Report it as configuration rather than a traceback; the worker's
        # preflight still turns it into a hard failure.
        name = "json"
        problem = problem or f"the service account was rejected ({type(exc).__name__})"
    return BackendReport(
        name=name,
        project_configured=bool(project),
        service_account_configured=info is not None,
        problem="" if name == "firestore" else problem,
    )


def _backend() -> "_Store":
    global _admin
    if _scope_provider is not None:
        scope = _scope_provider()
        if scope is not None and scope.uid:
            if scope.firestore_enabled and scope.project_id:
                client = fs.FirestoreClient(scope.project_id, scope.token, transport=_transport or fs._http)
                return _FirestoreStore(client, uid=scope.uid)
            return _JsonStore(uid=scope.uid)
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

    def __init__(self, uid: str | None = None):
        # ``None`` is the worker / legacy command-line view (all monitors).
        # A UID is the browser fallback view: old ownerless records and every
        # other account's records are deliberately invisible.
        self.uid = uid

    def _owns(self, item: dict[str, Any]) -> bool:
        return self.uid is None or str(item.get("owner_uid", "")) == self.uid

    def load_monitors(self) -> list[Monitor]:
        raw = read_json(MONITORS_FILE)
        if not isinstance(raw, list):
            return []
        monitors: list[Monitor] = []
        for item in raw:
            try:
                if self._owns(item):
                    monitors.append(Monitor.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                print(f"[state] skipping unreadable monitor: {exc}")
        return monitors

    def save_monitors(self, monitors: list[Monitor], *, mirror: bool = True) -> None:
        if self.uid is None:
            payload = [m.to_dict() for m in monitors]
        else:
            raw = read_json(MONITORS_FILE)
            others = [item for item in raw if isinstance(item, dict) and not self._owns(item)] if isinstance(raw, list) else []
            for monitor in monitors:
                if monitor.owner_uid and monitor.owner_uid != self.uid:
                    raise PermissionError("a monitor cannot be written under another account")
                monitor.owner_uid = self.uid
            payload = others + [m.to_dict() for m in monitors]
        write_json(MONITORS_FILE, payload, mirror=mirror,
                   message="chore: update monitors")

    def delete_monitor(self, monitor_id: str, *, mirror: bool = True) -> None:
        # Clear state while the monitor is still visible to this UID.  Once the
        # monitor is removed, ``load_state`` quite properly cannot establish
        # that the now-orphaned state document belonged to this browser.
        self.clear_monitor_state(monitor_id, mirror=mirror)
        self.save_monitors([m for m in self.load_monitors() if m.id != monitor_id], mirror=mirror)

    def load_state(self) -> dict[str, MonitorState]:
        raw = read_json(STATE_FILE)
        if not isinstance(raw, dict):
            return {}
        owned_ids = {m.id for m in self.load_monitors()} if self.uid else None
        return {k: MonitorState.from_dict(v) for k, v in raw.items()
                if (owned_ids is None or k in owned_ids) and isinstance(v, dict)}

    def save_state(self, state: dict[str, MonitorState], *, mirror: bool = True) -> None:
        if self.uid is None:
            payload = {k: v.to_dict() for k, v in state.items()}
        else:
            raw = read_json(STATE_FILE)
            payload = raw if isinstance(raw, dict) else {}
            owned_ids = {m.id for m in self.load_monitors()}
            payload = {k: v for k, v in payload.items() if k not in owned_ids}
            payload.update({k: v.to_dict() for k, v in state.items() if k in owned_ids})
        write_json(STATE_FILE, payload, mirror=mirror,
                   message="chore: update monitoring state")

    def clear_monitor_state(self, monitor_id: str, *, mirror: bool = True) -> None:
        state = self.load_state()
        if state.pop(monitor_id, None) is not None:
            self.save_state(state, mirror=mirror)

    def states_for(self, monitor_ids: list[str]) -> dict[str, MonitorState]:
        wanted = set(monitor_ids)
        return {k: v for k, v in self.load_state().items() if k in wanted}

    def load_history(self) -> list[dict[str, Any]]:
        history = _json_load_history()
        return [item for item in history if isinstance(item, dict) and self._owns(item)]

    def append_history(self, monitor: Monitor, item: dict[str, Any], *, mirror: bool = True) -> None:
        history = _json_load_history()
        # Stamp the owner so the record is visible to exactly that account.
        # The worker (``self.uid is None``) processes every owner's monitors,
        # so it takes the owner from the monitor it is recording for — without
        # this, worker-written history was invisible to the person who owns it.
        owner = self.uid or monitor.owner_uid
        history.insert(0, {**item, "owner_uid": owner} if owner else item)
        _json_save_history(history, mirror=mirror)

    def recent_history_for(self, monitor: Monitor) -> list[dict[str, Any]]:
        return self.load_history()[:8]

    def load_settings(self) -> dict[str, Any]:
        if self.uid is None:
            return _json_load_settings()
        raw = read_json(SETTINGS_FILE)
        users = raw.get("users", {}) if isinstance(raw, dict) else {}
        mine = users.get(self.uid, {}) if isinstance(users, dict) else {}
        return {**DEFAULTS["settings.json"], **(mine if isinstance(mine, dict) else {})}

    def save_settings(self, settings: dict[str, Any], *, mirror: bool = True) -> None:
        if self.uid is None:
            _json_save_settings(settings, mirror=mirror)
            return
        raw = read_json(SETTINGS_FILE)
        payload = raw if isinstance(raw, dict) else {}
        users = payload.get("users") if isinstance(payload.get("users"), dict) else {}
        payload = {"users": {**users, self.uid: dict(settings)}}
        write_json(SETTINGS_FILE, payload, mirror=mirror, message="chore: update settings")

    def purge_user_data(self, *, mirror: bool = True) -> dict[str, int]:
        """Remove everything this UID owns: monitors, their observed state,
        history and settings. Other owners' records, and the shared catalogue,
        are left exactly as they were."""
        if not self.uid:
            return {"monitors": 0, "history": 0, "settings": 0}
        mine = {m.id for m in self.load_monitors()}

        raw = read_json(MONITORS_FILE)
        keep = [i for i in raw if isinstance(i, dict) and not self._owns(i)] if isinstance(raw, list) else []
        write_json(MONITORS_FILE, keep, mirror=mirror, message="chore: update monitors")

        raw_state = read_json(STATE_FILE)
        if isinstance(raw_state, dict):
            write_json(STATE_FILE, {k: v for k, v in raw_state.items() if k not in mine},
                       mirror=mirror, message="chore: update monitoring state")

        history = _json_load_history()
        kept_history = [i for i in history if isinstance(i, dict) and not self._owns(i)]
        removed_history = len(history) - len(kept_history)
        _json_save_history(kept_history, mirror=mirror)

        raw_settings = read_json(SETTINGS_FILE)
        users = raw_settings.get("users") if isinstance(raw_settings, dict) else None
        had_settings = isinstance(users, dict) and self.uid in users
        if had_settings:
            write_json(SETTINGS_FILE, {"users": {k: v for k, v in users.items() if k != self.uid}},
                       mirror=mirror, message="chore: update settings")
        return {"monitors": len(mine), "history": removed_history, "settings": int(had_settings)}


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
            doc = self.client.get(MONITORS, monitor_id, absent_if_denied=self.uid is not None)
            if doc is None or not self._owned(doc):
                return
            writes: list[tuple[str, str, dict[str, Any] | None]] = [(MONITORS, monitor_id, None)]
            # Only delete the state document if there *is* one. ``allow
            # delete: if ownsExisting()`` reads the existing document, so
            # deleting one that was never written is refused — and a commit is
            # atomic, which would take the monitor's own deletion down with
            # it. A monitor stopped before its first check ever landed has no
            # state document, and that is exactly when people delete one.
            if self.client.get(STATES, monitor_id, absent_if_denied=self.uid is not None) is not None:
                writes.append((STATES, monitor_id, None))
            self.client.commit(writes)
        except fs.FirestoreError as exc:
            if self.uid is not None and exc.status in (401, 403):
                return
            raise
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
        # Absent and not-ours are one answer here (see ``states_for``), and
        # both mean there is nothing of this person's to clear.
        doc = self.client.get(STATES, monitor_id, absent_if_denied=self.uid is not None)
        if doc is not None and self._owned(doc):
            self.client.delete(STATES, monitor_id)

    def states_for(self, monitor_ids: list[str]) -> dict[str, MonitorState]:
        """The observed state of named monitors, one document read each.

        A direct read rather than the collection query :meth:`load_state`
        makes, because the caller is the live status panel showing one
        monitor: it wants that monitor's document and nothing else.
        Ownership is re-checked here as everywhere, and a document belonging
        to somebody else is refused by the rules before we ever see it —
        which from here is indistinguishable from one that does not exist,
        and is treated the same way.

        So is a document that has *never been written*, and that is the common
        case rather than the odd one: between saving a monitor and its first
        check landing, this panel ticks every thirty seconds against a
        document id that has nothing behind it yet. ``allow read: if
        ownsExisting()`` dereferences ``resource.data`` on a null ``resource``
        and Firestore answers 403, not 404 — hence ``absent_if_denied``, which
        is the client saying it knows that and means None either way. Without
        it every one of those ticks logged a permission error against the
        owner's own monitor.
        """
        out: dict[str, MonitorState] = {}
        for monitor_id in monitor_ids:
            try:
                doc = self.client.get(STATES, monitor_id, absent_if_denied=self.uid is not None)
            except fs.FirestoreError as exc:
                # 403 is folded into "absent" above; this is a token that has
                # stopped being accepted, which the next full rerun re-reads.
                if self.uid is not None and exc.status == 401:
                    continue
                raise
            if doc is not None and self._owned(doc):
                out[monitor_id] = MonitorState.from_dict(doc)
        return out

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

    def purge_user_data(self, *, mirror: bool = True) -> dict[str, int]:
        """Delete every document this UID owns — monitors, their state, history
        and the user record. Each is re-checked against the owner before it is
        deleted, so nothing belonging to anybody else can be caught up in it.
        The shared catalogue lives outside these collections and is untouched."""
        if not self.uid:
            return {"monitors": 0, "history": 0, "settings": 0}
        writes: list[tuple[str, str, dict[str, Any] | None]] = []
        monitors = 0
        for collection in (MONITORS, STATES):
            for doc_id, doc in self.client.query(collection, equals=self._mine()).items():
                if self._owned(doc):
                    writes.append((collection, doc_id, None))
                    monitors += int(collection == MONITORS)
                    _owner_of.pop(doc_id, None)
        history = 0
        for doc_id, doc in self.client.query(HISTORY, equals=self._mine()).items():
            if self._owned(doc):
                writes.append((HISTORY, doc_id, None))
                history += 1
        writes.append((USERS, self.uid, None))
        self.client.commit(writes)
        return {"monitors": monitors, "history": history, "settings": 1}


_Store = _JsonStore  # the protocol both stores follow



# ──────────────────────────────────────────────────────────────────────────
# A short-lived read cache, per signed-in account
# ──────────────────────────────────────────────────────────────────────────
#: Where the cache lives. ``st.session_state`` is per browser session, so one
#: person's cache is not even in the same mapping as another's; the UID is
#: part of every key as well, so switching accounts inside one session cannot
#: reuse the previous person's entries either. ``auth.session`` wipes every
#: key outside its keep-list on sign-in and sign-out, which takes this with
#: it — there is no path by which a cached read outlives its owner.
CACHE_KEY = "_tr_read_cache"

#: How long a read may be reused. It exists to collapse the reads of a single
#: interaction — a wizard click is two script runs milliseconds apart, because
#: ``flow.goto`` has to rerun for the new step to render — not to hold data.
#: Monitors and state are written by the worker every ten minutes at the
#: fastest, and anything the person themselves changes invalidates the cache
#: outright, so a few seconds cannot show a stale answer to the person who
#: caused the change.
CACHE_SECONDS = 5.0


def _cache_scope_uid() -> str | None:
    """The UID a cached read belongs to, or None when caching is not allowed.

    The worker has no Streamlit session and must never cache: it runs for
    fifty minutes and has to see each tick's writes.
    """
    if _scope_provider is None:
        return None
    scope = _scope_provider()
    return scope.uid if scope is not None and scope.uid else None


def _cache_store() -> dict[str, Any] | None:
    try:
        import streamlit as st

        cache = st.session_state.get(CACHE_KEY)
        if not isinstance(cache, dict):
            cache = {}
            st.session_state[CACHE_KEY] = cache
        return cache
    except Exception:  # noqa: BLE001 - no Streamlit session: the worker, or a test
        return None


#: Tells "nothing cached" apart from a cached empty answer.
_MISS = object()


def _cache_peek(cache: dict[str, Any] | None, uid: str | None, kind: str) -> Any:
    if cache is None or uid is None:
        return _MISS
    entry = cache.get(f"{uid}:{kind}")
    if entry is not None and (now_ist().timestamp() - entry[0]) < CACHE_SECONDS:
        return entry[1]
    return _MISS


def _cache_put(cache: dict[str, Any] | None, uid: str | None, kind: str, value: Any) -> None:
    if cache is not None and uid is not None:
        cache[f"{uid}:{kind}"] = (now_ist().timestamp(), value)


def _cached(kind: str, load: Callable[[], Any]) -> Any:
    """``load()``, reused for :data:`CACHE_SECONDS` within one account."""
    uid = _cache_scope_uid()
    cache = _cache_store() if uid else None
    if cache is None or uid is None:
        return load()
    hit = _cache_peek(cache, uid, kind)
    if hit is not _MISS:
        return hit
    value = load()
    _cache_put(cache, uid, kind, value)
    return value


def invalidate_cache(*kinds: str) -> None:
    """Forget cached reads, so a change the person just made is never hidden.

    With no arguments every kind goes, which is what erasing an account wants.
    Naming kinds forgets only those: appending one history line is no reason
    for the next rerun to re-read monitors, state *and* settings over the
    network, and that broad eviction was most of what made saving a monitor
    expensive — it left the rerun after every write completely cold.
    """
    cache = _cache_store()
    if cache is None:
        return
    if not kinds:
        cache.clear()
        return
    uid = _cache_scope_uid()
    for kind in kinds:
        cache.pop(f"{uid}:{kind}", None)


# ──────────────────────────────────────────────────────────────────────────
# Reading several kinds at once
# ──────────────────────────────────────────────────────────────────────────
#: The independent reads a page makes, by name. No one of them depends on
#: another, which is what makes the fan-out below possible at all.
READERS: dict[str, Callable[[Any], Any]] = {
    "monitors": lambda store: store.load_monitors(),
    "state": lambda store: store.load_state(),
    "history": lambda store: store.load_history(),
    "settings": lambda store: store.load_settings(),
}


def _detached(store: Any) -> "_FirestoreStore | None":
    """A copy of a Firestore store that a worker thread may safely use.

    The live store's client asks ``auth.session.id_token()`` for a token, and
    that reads — and, near expiry, writes — ``st.session_state``. A thread
    other than Streamlit's script thread sees that state as **empty, with no
    error at all**: the scope provider would find nobody signed in, and
    :func:`_backend` would quietly fall through to the unscoped JSON store,
    which answers with *every* account's records. Resolving the token here, on
    the script thread, and handing the worker a client that can only hand that
    one token back, is what keeps the fan-out from ever reaching Streamlit.

    Returns None for the JSON store: file reads gain nothing from threads, so
    the compatibility path stays sequential and exactly as it was.
    """
    if not isinstance(store, _FirestoreStore):
        return None
    token = store.client.token()                      # script thread only
    client = fs.FirestoreClient(store.client.project_id, lambda: token,
                                transport=store.client.transport)
    return _FirestoreStore(client, uid=store.uid)


def load_many(*kinds: str) -> dict[str, Any]:
    """``{kind: records}`` for several kinds at once, in one round trip.

    A page needs monitors, observed state, history and settings, and asking
    for them one after another cost four sequential round trips for data with
    no order between it. Everything that decides *whose* data this is — the
    scope, the backend, the ID token — is resolved here, before any thread
    starts; the workers only run the reads, against a store already bound to
    one UID. Firestore's rules still apply to every request, and every
    document that comes back is re-checked against that UID as before.
    """
    uid = _cache_scope_uid()
    cache = _cache_store() if uid else None
    out: dict[str, Any] = {}
    missing: list[str] = []
    for kind in kinds:
        hit = _cache_peek(cache, uid, kind)
        if hit is _MISS:
            missing.append(kind)
        else:
            out[kind] = hit
    if not missing:
        return out

    store = _backend()                                # script thread only
    detached = _detached(store) if len(missing) > 1 else None
    if detached is None:
        for kind in missing:
            out[kind] = READERS[kind](store)
    else:
        with ThreadPoolExecutor(max_workers=len(missing),
                                thread_name_prefix="tr-read") as pool:
            futures = {kind: pool.submit(READERS[kind], detached) for kind in missing}
            for kind, future in futures.items():
                # Raises here, on the script thread, exactly as the sequential
                # read it replaces would have.
                out[kind] = future.result()
    for kind in missing:
        _cache_put(cache, uid, kind, out[kind])
    return out


# ──────────────────────────────────────────────────────────────────────────
# Monitors — the functions the UI, the checker and the worker call
# ──────────────────────────────────────────────────────────────────────────
def load_monitors() -> list[Monitor]:
    return _cached("monitors", lambda: _backend().load_monitors())


def save_monitors(monitors: list[Monitor], *, mirror: bool = True) -> None:
    invalidate_cache("monitors")
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
    # The monitor and its observed state both go.
    invalidate_cache("monitors", "state")
    _backend().delete_monitor(monitor_id, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# Settings — per person in Firestore, the one file in JSON
# ──────────────────────────────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    return _cached("settings", lambda: _backend().load_settings())


def save_settings(settings: dict[str, Any], *, mirror: bool = True) -> None:
    invalidate_cache("settings")
    _backend().save_settings(settings, mirror=mirror)


def purge_user_data(*, mirror: bool = True) -> dict[str, int]:
    """Erase everything the *currently scoped* account owns.

    Whose data this is comes from the scope provider — the signed-in Firebase
    UID — never from an argument, so no caller can point it at another
    account. Returns counts of what was removed.
    """
    invalidate_cache()          # every kind: all of it is going
    return _backend().purge_user_data(mirror=mirror)


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
    return _cached("state", lambda: _backend().load_state())


def save_state(state: dict[str, MonitorState], *, mirror: bool = True) -> None:
    invalidate_cache("state")
    _backend().save_state(state, mirror=mirror)


def get_monitor_state(monitor_id: str) -> MonitorState:
    return load_state().get(monitor_id, MonitorState())


def load_states_for(monitor_ids: list[str]) -> dict[str, MonitorState]:
    """The freshest observed state of just these monitors.

    This is the live status panel's read, and it is deliberately none of the
    things a page load is. The worker writes a monitor's state minutes after
    the page was drawn — the first check lands, a theatre goes AVAILABLE —
    and an open browser has to show that without the person pressing refresh.
    A Streamlit fragment replays with the arguments it was *first* given, so
    re-rendering alone would show the same state forever; it has to ask.

    It is therefore not cached, and it does not disturb the cache: nothing
    here evicts monitors, history or settings, and the next full rerun reads
    what it always did. On Firestore this is one document read per displayed
    monitor — one request every thirty seconds for the rail's single active
    monitor, against the same pooled connection as everything else.
    """
    ids = [i for i in dict.fromkeys(monitor_ids) if i]
    if not ids:
        return {}
    return _backend().states_for(ids)


def clear_monitor_state(monitor_id: str, *, mirror: bool = True) -> None:
    invalidate_cache("state")
    _backend().clear_monitor_state(monitor_id, mirror=mirror)


# ──────────────────────────────────────────────────────────────────────────
# History (the right-rail "Recent history" list)
# ──────────────────────────────────────────────────────────────────────────
def load_history() -> list[dict[str, Any]]:
    """The signed-in person's history (Firestore), or the one global list (JSON)."""
    return _cached("history", lambda: _backend().load_history())


def record_history(monitor: Monitor, kind: str, message: str, *, mirror: bool = True,
                   at: datetime | None = None, extra: dict[str, Any] | None = None) -> None:
    """Append one line to the activity log — the monitor's owner's log.

    De-duplicated on (monitor, kind, message) within the last hour so a
    flapping check cannot flood the rail.
    """
    at = at or now_ist()
    invalidate_cache("history")
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
    "BackendReport",
    "MonitorState",
    "NEW_SHOWTIME_COOLDOWN",
    "READERS",
    "Scope",
    "TargetState",
    "backend_name",
    "backend_report",
    "clear_monitor_state",
    "delete_monitor",
    "expire_due_monitors",
    "extend_monitor",
    "get_monitor",
    "get_monitor_state",
    "load_history",
    "load_many",
    "load_monitors",
    "load_settings",
    "load_state",
    "load_states_for",
    "purge_user_data",
    "invalidate_cache",
    "record_history",
    "save_monitors",
    "save_settings",
    "save_state",
    "set_scope_provider",
    "stop_monitor",
    "upsert_monitor",
]
