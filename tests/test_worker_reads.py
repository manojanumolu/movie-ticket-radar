"""What the worker reads from Firestore, tick by tick — and what it must not.

Every thirty seconds, for as long as anything is being watched, the worker
read the entire ``monitors`` collection twice and the entire ``monitor_state``
collection twice: once for the tick and once more to decide when to wake.
Every STOPPED and EXPIRED monitor anyone had ever created came back each
time, was billed as a document read, and was thrown away. With half a dozen
people making monitors that never get deleted, that grows without bound.

Two changes, pinned here:

1. The worker's monitors read is filtered to ``status == ACTIVE`` on
   Firestore. Finished monitors are still in the store, still in My
   Monitors, still in History — the worker just never asks for them again.
2. The tick's monitors and state are handed to ``seconds_until_next_due``
   instead of being read a second time. Within one tick only: the next tick
   reads afresh, so nothing here is a cache.

The JSON fallback is deliberately untouched: a file costs the same to read
whatever it holds, and that store writes the list back whole.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config import firestore as fs
from config.timezone import now_ist
from monitor import checker
from monitor import state as state_mod
from monitor import worker
from monitor.discovery import DiscoveryReport
from monitor.models import ANY_FORMAT, CheckOutcome, Monitor, MonitorStatus, MovieRef, TheatreTarget
from monitor.state import MonitorState, Scope
from tests.test_isolation import MemoryFirestore, PROJECT
from tests.test_scheduling import Clock

OWNER = "uid-friend-1"


def _monitor(title: str, *, until: datetime, owner: str = OWNER, interval: int = 10) -> Monitor:
    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=interval, monitor_until=until,
        notify_email="w@example.com", owner_uid=owner)


@pytest.fixture
def firestore_worker(monkeypatch):
    """The Actions worker against the rules-enforcing fake, with a tally of
    what each request cost: documents returned per query, GETs, writes."""
    store = MemoryFirestore()
    tally = {"queries": [], "gets": 0, "writes": 0}

    def counting(method, url, token, body):
        status, payload = store(method, url, token, body)
        if url.endswith(":runQuery"):
            collection = body["structuredQuery"]["from"][0]["collectionId"]
            docs = sum(1 for r in payload if isinstance(r, dict) and r.get("document"))
            tally["queries"].append((collection, docs, body["structuredQuery"].get("where")))
        elif method == "GET":
            tally["gets"] += 1
        elif url.endswith(":commit"):
            tally["writes"] += len(body["writes"])
        return status, payload

    monkeypatch.setattr(state_mod, "_transport", counting)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setattr(state_mod, "_owner_of", {})
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "admin"))
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT",
                       '{"client_email": "worker@test", "type": "service_account"}')
    state_mod.set_scope_provider(None)

    # No BookMyShow and no city listing: the platform answers at once, so the
    # only network in these tests is Firestore's.
    monkeypatch.setattr(checker, "check_monitor",
                        lambda monitor, at=None: CheckOutcome(monitor.id, at or now_ist(), ok=True, results=[]))
    monkeypatch.setattr(checker, "discover_siblings", lambda *a, **k: DiscoveryReport())

    at = now_ist().replace(microsecond=0)

    class Worker:
        docs = store.docs
        now = at

        @staticmethod
        def seed(monitor: Monitor, *, status: str = "ACTIVE", checked: bool = True) -> Monitor:
            """A monitor as the UI left it, and the state doc the worker wrote."""
            if status == "STOPPED":
                monitor.stop(at - timedelta(hours=1))
            elif status == "EXPIRED":
                monitor.expire(at - timedelta(hours=1))
            store.docs.setdefault("monitors", {})[monitor.id] = monitor.to_dict()
            if checked:
                store.docs.setdefault("monitor_state", {})[monitor.id] = {
                    **MonitorState(check_count=1, last_check_at=at - timedelta(hours=1)).to_dict(),
                    fs.OWNER: monitor.owner_uid}
            return monitor

        @staticmethod
        def app_stops(monitor_id: str) -> None:
            """What Stop monitoring does: as the signed-in owner, through the store."""
            state_mod.set_scope_provider(lambda: Scope(PROJECT, OWNER, lambda: f"id.{OWNER}"))
            try:
                state_mod.stop_monitor(monitor_id, mirror=False)
            finally:
                state_mod.set_scope_provider(None)

        @staticmethod
        def app_creates(title: str) -> Monitor:
            state_mod.set_scope_provider(lambda: Scope(PROJECT, OWNER, lambda: f"id.{OWNER}"))
            try:
                monitor = _monitor(title, until=at + timedelta(days=2))
                state_mod.upsert_monitor(monitor, mirror=False)
            finally:
                state_mod.set_scope_provider(None)
            return monitor

        @staticmethod
        def reset() -> None:
            tally["queries"].clear()
            tally["gets"] = 0
            tally["writes"] = 0

        @staticmethod
        def docs_read(collection: str) -> int:
            return sum(n for c, n, _ in tally["queries"] if c == collection)

        @staticmethod
        def queries(collection: str | None = None) -> list:
            return [q for q in tally["queries"] if collection is None or q[0] == collection]

        @staticmethod
        def writes() -> int:
            return tally["writes"]

        @staticmethod
        def gets() -> int:
            return tally["gets"]

    yield Worker
    state_mod.set_scope_provider(None)


def _active_plus_finished(w) -> tuple[Monitor, list[Monitor]]:
    active = w.seed(_monitor("Active one", until=w.now + timedelta(days=2)))
    finished = [w.seed(_monitor(f"Stopped {i}", until=w.now + timedelta(days=2)), status="STOPPED")
                for i in range(4)]
    finished += [w.seed(_monitor(f"Expired {i}", until=w.now + timedelta(days=2)), status="EXPIRED")
                 for i in range(3)]
    return active, finished


# ──────────────────────────────────────────────────────────────────────────
# Change 1: the worker asks Firestore for ACTIVE monitors only
# ──────────────────────────────────────────────────────────────────────────
def test_the_worker_query_filters_on_status_active(firestore_worker):
    w = firestore_worker
    active, finished = _active_plus_finished(w)

    w.reset()
    got = state_mod.load_monitors_for_checking()

    assert [m.id for m in got] == [active.id]
    (collection, docs, where), = w.queries()
    assert collection == "monitors"
    assert docs == 1, "finished monitors came back from the query"
    assert where == {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "EQUAL",
                                     "value": {"stringValue": "ACTIVE"}}}
    # …and it is a single-field equality — nothing Firestore needs an index for.


def test_stopped_and_expired_monitors_never_reach_the_monitoring_path(firestore_worker):
    """They are not read, not skipped, not checked and — this matters just as
    much for cost — not written back."""
    w = firestore_worker
    active, finished = _active_plus_finished(w)
    before = {m.id: dict(w.docs["monitors"][m.id]) for m in finished}

    w.reset()
    report = checker.run_once(at=w.now, force=True, mirror=False, notifier=lambda m, c: None)

    assert report.checked == [active.id]
    assert report.skipped == []                       # nothing finished was even seen
    assert w.docs_read("monitors") == 1
    assert [m.id for m in report.monitors] == [active.id]
    # Still there, byte for byte: nothing is deleted and nothing rewritten.
    assert {m.id: w.docs["monitors"][m.id] for m in finished} == before
    assert all(w.docs["monitors"][m.id]["status"] in ("STOPPED", "EXPIRED") for m in finished)


def test_a_signed_in_persons_variant_keeps_the_owner_filter(firestore_worker, monkeypatch):
    """The app never calls this, but if it did the query would still be one
    the rules accept: owner *and* status, never status alone."""
    w = firestore_worker
    mine = w.seed(_monitor("Mine", until=w.now + timedelta(days=2)))
    w.seed(_monitor("Theirs", until=w.now + timedelta(days=2), owner="uid-friend-2"))
    state_mod.set_scope_provider(lambda: Scope(PROJECT, OWNER, lambda: f"id.{OWNER}"))

    w.reset()
    got = state_mod.load_monitors_for_checking()

    assert [m.id for m in got] == [mine.id]
    (_, _, where), = w.queries("monitors")
    paths = {f["fieldFilter"]["field"]["fieldPath"] for f in where["compositeFilter"]["filters"]}
    assert paths == {"owner_uid", "status"}


def test_expiry_still_happens_on_the_filtered_read(firestore_worker):
    """A monitor past its end time is still ACTIVE *in the store* until a tick
    flips it. The filter is on the stored field, so it is read and flipped."""
    w = firestore_worker
    overdue = w.seed(_monitor("Overdue", until=w.now - timedelta(minutes=5)))

    report = checker.run_once(at=w.now, force=True, mirror=False, notifier=lambda m, c: None)

    assert report.expired == [overdue.id]
    assert w.docs["monitors"][overdue.id]["status"] == "EXPIRED"
    # …and from the next tick on it is never read again.
    w.reset()
    assert state_mod.load_monitors_for_checking() == []
    assert w.docs_read("monitors") == 0


# ──────────────────────────────────────────────────────────────────────────
# Change 2: one read per collection per tick, and none carried across ticks
# ──────────────────────────────────────────────────────────────────────────
def test_one_tick_reads_each_collection_once(firestore_worker):
    """Before: run_once read monitors and state, then seconds_until_next_due
    read both again. 1 ACTIVE + 4 STOPPED + 3 EXPIRED cost 32 document reads
    per tick; it now costs 1 monitor read and the state collection once."""
    w = firestore_worker
    active, _ = _active_plus_finished(w)

    w.reset()
    report = checker.run_once(at=w.now, force=True, mirror=False, notifier=lambda m, c: None)
    wait = worker.seconds_until_next_due(w.now + timedelta(seconds=1), 30,
                                         monitors=report.monitors, state=report.state)

    assert wait is not None
    assert [c for c, _, _ in w.queries()] == ["monitors", "monitor_state"]
    assert w.docs_read("monitors") == 1
    assert w.gets() == 0


def test_the_wake_up_decision_uses_the_state_the_tick_just_wrote(firestore_worker):
    """The reused state is what run_once committed — the check that just
    happened is in it, so the next wake is one interval away, not 'now'."""
    w = firestore_worker
    active = w.seed(_monitor("Active one", until=w.now + timedelta(days=2), interval=10), checked=False)

    report = checker.run_once(at=w.now, force=True, mirror=False, notifier=lambda m, c: None)
    assert report.state[active.id].check_count == 1

    wait = worker.seconds_until_next_due(w.now, 30, monitors=report.monitors, state=report.state)
    assert wait == 30.0                               # capped at the poll; not 1.0 ("due now")
    # Identical to what a fresh read would have decided.
    fresh = worker.seconds_until_next_due(w.now, 30)
    assert fresh == wait


def test_nothing_is_reused_across_ticks(firestore_worker):
    """Two ticks of the real loop: each one issues its own monitors and state
    queries. There is no cache in the worker, before or after this change."""
    w = firestore_worker
    w.seed(_monitor("Active one", until=w.now + timedelta(days=2)), checked=False)
    clock = Clock(w.now)

    w.reset()
    loop = worker.run_loop(max_minutes=1, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=clock.sleep, notifier=lambda m, c: None)

    assert loop.ticks == 2
    monitors_queries = w.queries("monitors")
    # preflight (unfiltered, once per segment) + one filtered read per tick
    assert len(monitors_queries) == 1 + loop.ticks
    assert len(w.queries("monitor_state")) == loop.ticks


def test_a_monitor_the_app_stops_is_gone_from_the_next_tick(firestore_worker, monkeypatch):
    w = firestore_worker
    active = w.seed(_monitor("Active one", until=w.now + timedelta(days=2)), checked=False)
    clock = Clock(w.now)
    ticks = []

    def sleeper(seconds):
        clock.sleep(seconds)
        if len(ticks) == 1:
            w.app_stops(active.id)                    # between tick 1 and tick 2

    real_run_once = checker.run_once

    def spy(**kw):
        report = real_run_once(**kw)
        ticks.append(report)
        return report

    monkeypatch.setattr(worker, "run_once", spy)
    loop = worker.run_loop(max_minutes=5, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=sleeper, notifier=lambda m, c: None)

    assert ticks[0].checked == [active.id]
    assert [m.id for m in ticks[1].monitors] == []    # not read: it is STOPPED now
    assert ticks[1].checked == []
    assert loop.stopped_reason == "nothing running"
    assert loop.handed_over is False


def test_a_monitor_the_app_creates_is_checked_on_the_next_tick(firestore_worker, monkeypatch):
    w = firestore_worker
    first = w.seed(_monitor("First", until=w.now + timedelta(days=2), interval=30), checked=False)
    clock = Clock(w.now)
    ticks = []
    created = []

    def sleeper(seconds):
        clock.sleep(seconds)
        if len(ticks) == 1:
            created.append(w.app_creates("Second"))   # between tick 1 and tick 2

    real_run_once = checker.run_once

    def spy(**kw):
        report = real_run_once(**kw)
        ticks.append(report)
        return report

    monkeypatch.setattr(worker, "run_once", spy)
    worker.run_loop(max_minutes=1, poll_seconds=30, use_git=False, chain=False,
                    clock=clock, sleeper=sleeper, notifier=lambda m, c: None)

    assert ticks[0].checked == [first.id]
    assert created and ticks[1].checked == [created[0].id]
    assert {m.id for m in ticks[1].monitors} == {first.id, created[0].id}


def test_the_reuse_is_this_ticks_data_and_nothing_older(firestore_worker):
    """Belt and braces on the 'not a cache' claim: hand seconds_until_next_due
    a stale list and it believes it — which is exactly why run_loop only ever
    hands it the report of the tick that just ran."""
    w = firestore_worker
    active = w.seed(_monitor("Active one", until=w.now + timedelta(days=2)))
    stale = [active]
    w.app_stops(active.id)

    assert worker.seconds_until_next_due(w.now, 30, monitors=stale, state={}) is not None
    assert worker.seconds_until_next_due(w.now, 30) is None   # a fresh read knows


# ──────────────────────────────────────────────────────────────────────────
# The JSON fallback is exactly as it was
# ──────────────────────────────────────────────────────────────────────────
def test_the_json_worker_still_reads_and_writes_the_whole_file(monkeypatch, isolated_data):
    """That store replaces the file on save, so the worker must keep handing
    it the whole list — a stopped monitor must survive a tick that expires
    another."""
    state_mod.set_scope_provider(None)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    at = now_ist()
    stopped = _monitor("Stopped", until=at + timedelta(days=1), owner="")
    stopped.stop(at - timedelta(hours=1))
    overdue = _monitor("Overdue", until=at - timedelta(minutes=1), owner="")
    state_mod.save_monitors([stopped, overdue], mirror=False)
    monkeypatch.setattr(checker, "check_monitor",
                        lambda monitor, at=None: CheckOutcome(monitor.id, at or now_ist(), ok=True, results=[]))
    monkeypatch.setattr(checker, "discover_siblings", lambda *a, **k: DiscoveryReport())

    assert [m.id for m in state_mod.load_monitors_for_checking()] == [stopped.id, overdue.id]
    report = checker.run_once(at=at, force=True, mirror=False, notifier=lambda m, c: None)

    assert report.expired == [overdue.id]
    assert any("stopped" in s for s in report.skipped)          # seen and skipped, as before
    kept = {m.id: m.status for m in state_mod.load_monitors()}
    assert kept == {stopped.id: MonitorStatus.STOPPED, overdue.id: MonitorStatus.EXPIRED}


# ──────────────────────────────────────────────────────────────────────────
# owner_uid on state documents the worker no longer loads the monitor for
# ──────────────────────────────────────────────────────────────────────────
def test_finished_monitors_state_keeps_its_owner_without_a_second_read(firestore_worker):
    """The worker writes the whole state back each dirty tick, finished
    monitors included. Now that it never loads a finished monitor's record,
    the owner has to come from the state document itself — the field it just
    read — not from a GET of that same document, and never as ''."""
    w = firestore_worker
    active, finished = _active_plus_finished(w)
    owners_before = {m.id: w.docs["monitor_state"][m.id][fs.OWNER] for m in finished}
    assert all(owners_before.values())

    w.reset()
    checker.run_once(at=w.now, force=True, mirror=False, notifier=lambda m, c: None)

    assert w.gets() == 0
    assert w.writes() == 1 + len(finished)            # the whole state, as before
    assert {m.id: w.docs["monitor_state"][m.id][fs.OWNER] for m in finished} == owners_before
    assert w.docs["monitor_state"][active.id][fs.OWNER] == OWNER
    # …and the owner's own live read of a finished monitor's state still works.
    theirs = fs.FirestoreClient(PROJECT, lambda: f"id.{OWNER}", transport=state_mod._transport)
    assert theirs.get("monitor_state", finished[0].id) is not None
