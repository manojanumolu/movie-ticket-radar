"""The open browser has to show what the worker just found.

The worker writes a monitor's observed state minutes after the page was
drawn: the first check lands, a theatre goes AVAILABLE, "last checked"
moves. Nobody is going to sit on a booking page pressing refresh — that is
the entire reason this application exists — so the rail's live panel has to
pick that up on its own.

It very nearly did not. ``st.fragment(run_every=30)`` replays a fragment
with the arguments it was *first* called with, so a panel handed a
``MonitorState`` re-rendered that same snapshot every thirty seconds for as
long as the tab stayed open. The countdown ticked, which made it look alive,
while the availability underneath it never moved. These tests pin that the
panel asks the store instead — and that asking stayed cheap.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from config import firestore as fs
from config.timezone import IST, now_ist
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Availability, Monitor, MovieRef, TheatreTarget
from monitor.state import MonitorState, Scope, TargetState
from tests.test_isolation import MemoryFirestore, PROJECT

UID_A, UID_B = "uid-live-a", "uid-live-b"


def _monitor(uid: str, title: str = "Hanuman Ansh") -> Monitor:
    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=10, monitor_until=datetime.now(IST) + timedelta(days=2),
        notify_email="w@example.com", owner_uid=uid)


@pytest.fixture
def live(monkeypatch):
    """Firestore, a session to cache into, and a way to be the worker."""
    store = MemoryFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setattr(state_mod, "_owner_of", {})
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)

    import streamlit as st

    session: dict = {}
    monkeypatch.setattr(st, "session_state", session, raising=False)

    class Live:
        docs = store.docs
        calls = store.calls
        state = session

        @staticmethod
        def as_user(uid: str) -> None:
            state_mod.set_scope_provider(
                lambda: Scope(PROJECT, uid, lambda: f"id.{uid}"))

        @staticmethod
        def worker_writes(monitor_id: str, owner: str, target_state: TargetState,
                          *, checked_at=None, checks: int = 1) -> None:
            """What the GitHub Actions worker commits after a check. Written
            straight into the store, because the app is not allowed to."""
            observed = MonitorState(
                last_check_at=checked_at or now_ist(),
                last_success_at=checked_at or now_ist(),
                check_count=checks, success_count=checks,
                targets={"ALLU|Any format": target_state})
            store.docs.setdefault("monitor_state", {})[monitor_id] = {
                **observed.to_dict(), "owner_uid": owner}

        @staticmethod
        def requests_since(n: int) -> list[str]:
            return [f"{m} {p}" for m, p, _ in store.calls[n:]]

    yield Live
    state_mod.set_scope_provider(None)


AVAILABLE = TargetState(availability=Availability.AVAILABLE,
                        showtime_keys=["s1", "s2"],
                        time_labels=["10:30 AM", "02:15 PM"])


# ──────────────────────────────────────────────────────────────────────────
# The production scenario, exactly as it happened
# ──────────────────────────────────────────────────────────────────────────
def test_the_worker_going_available_reaches_an_open_browser(live):
    """ACTIVE + UNKNOWN on screen; the worker finds two bookable showtimes;
    the next fragment tick must show AVAILABLE without a page reload."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)

    # What the page loaded and handed to the fragment: nothing observed yet.
    on_screen = state_mod.load_state().get(monitor.id, MonitorState())
    assert on_screen.check_count == 0
    assert on_screen.targets == {}

    live.worker_writes(monitor.id, UID_A, AVAILABLE, checks=1)

    # The fragment's own read, thirty seconds later. Same arguments as the
    # first paint — the store is what has changed.
    fresh = state_mod.load_states_for([monitor.id])[monitor.id]

    assert fresh.check_count == 1
    assert fresh.last_check_at is not None
    target = fresh.targets["ALLU|Any format"]
    assert target.availability is Availability.AVAILABLE
    assert len(target.showtime_keys) == 2
    assert target.time_labels == ["10:30 AM", "02:15 PM"]


def test_a_stale_snapshot_is_superseded_not_re_rendered(live):
    """The bug itself: the panel held an old object and kept drawing it."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)
    stale = MonitorState(check_count=0)

    live.worker_writes(monitor.id, UID_A, AVAILABLE, checks=3)
    fresh = state_mod.load_states_for([monitor.id]).get(monitor.id, stale)

    assert fresh is not stale
    assert fresh.check_count == 3


@pytest.mark.parametrize("availability", [
    Availability.AVAILABLE,
    Availability.SOLD_OUT,
    Availability.THEATRE_NOT_AVAILABLE,
    Availability.SHOW_NOT_AVAILABLE,
    Availability.NOT_FOUND,
    Availability.ERROR,
])
def test_every_phase_the_worker_can_report_reaches_the_browser(live, availability):
    """None of these may need a manual refresh to appear."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)

    live.worker_writes(monitor.id, UID_A, TargetState(availability=availability))
    fresh = state_mod.load_states_for([monitor.id])[monitor.id]

    assert fresh.targets["ALLU|Any format"].availability is availability


def test_the_panel_re_reads_rather_than_trusting_its_argument():
    """Pins the shape of the fix: the fragment asks the store for the
    displayed monitor's state, and falls back to the argument only if the
    read gives nothing."""
    import app

    source = inspect.getsource(app.live_monitor_panel)
    assert "load_states_for([monitor.id])" in source
    assert "C.active_monitor_card(monitor, fresh)" in source
    assert "C.target_rows(monitor, fresh)" in source
    assert app.live_monitor_panel.__wrapped__ is not None      # still a fragment


# ──────────────────────────────────────────────────────────────────────────
# …and it stayed cheap
# ──────────────────────────────────────────────────────────────────────────
def test_the_live_read_is_one_request_for_one_monitor(live):
    """Thirty seconds apart, for as long as a tab is open. It has to be the
    smallest read in the application, not a page load."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)
    live.worker_writes(monitor.id, UID_A, AVAILABLE)

    before = len(live.calls)
    state_mod.load_states_for([monitor.id])
    requests = live.requests_since(before)

    assert len(requests) == 1, requests
    assert requests[0].startswith("GET /monitor_state/"), requests
    assert not any(":runQuery" in r for r in requests)


def test_the_live_read_is_scoped_to_the_displayed_monitors(live):
    """Three monitors exist; the rail shows one; one document is read."""
    live.as_user(UID_A)
    shown = _monitor(UID_A, "On screen")
    state_mod.upsert_monitor(shown, mirror=False)
    for i in range(2):
        other = _monitor(UID_A, f"Elsewhere {i}")
        state_mod.upsert_monitor(other, mirror=False)
        live.worker_writes(other.id, UID_A, AVAILABLE)
    live.worker_writes(shown.id, UID_A, AVAILABLE)

    before = len(live.calls)
    got = state_mod.load_states_for([shown.id])

    assert list(got) == [shown.id]
    assert len(live.requests_since(before)) == 1


def test_the_live_read_does_not_disturb_the_page_cache(live):
    """It must not evict monitors, history or settings — that would make the
    next interaction pay for four reads it did not need."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)
    state_mod.load_many("monitors", "state", "history", "settings")
    cached = dict(live.state[state_mod.CACHE_KEY])
    live.worker_writes(monitor.id, UID_A, AVAILABLE)

    before = len(live.calls)
    state_mod.load_states_for([monitor.id])

    assert set(live.state[state_mod.CACHE_KEY]) == set(cached)
    # …and the page's own reads are still served from cache.
    state_mod.load_many("monitors", "state", "history", "settings")
    assert len(live.requests_since(before)) == 1


def test_no_monitors_means_no_request(live):
    live.as_user(UID_A)
    before = len(live.calls)
    assert state_mod.load_states_for([]) == {}
    assert state_mod.load_states_for(["", ""]) == {}
    assert live.requests_since(before) == []


def test_repeated_ids_are_read_once(live):
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)
    live.worker_writes(monitor.id, UID_A, AVAILABLE)

    before = len(live.calls)
    state_mod.load_states_for([monitor.id, monitor.id, monitor.id])
    assert len(live.requests_since(before)) == 1


# ──────────────────────────────────────────────────────────────────────────
# Isolation
# ──────────────────────────────────────────────────────────────────────────
def test_the_live_read_cannot_fetch_another_accounts_state(live):
    """Asking for somebody else's monitor id returns nothing. The rules
    refuse it before we see it, which is indistinguishable from absent."""
    live.as_user(UID_B)
    theirs = _monitor(UID_B)
    state_mod.upsert_monitor(theirs, mirror=False)
    live.worker_writes(theirs.id, UID_B, AVAILABLE)

    live.as_user(UID_A)
    assert state_mod.load_states_for([theirs.id]) == {}


def test_an_unowned_state_document_is_never_returned(live, monkeypatch):
    """Belt and braces: even if the rules let a document through, ownership
    is re-checked here, as it is on every other read."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)
    # A document under this id that belongs to somebody else.
    live.worker_writes(monitor.id, UID_B, AVAILABLE)

    store = state_mod._backend()
    monkeypatch.setattr(store.client, "get",
                        lambda c, d: {**MonitorState(check_count=9).to_dict(),
                                      "owner_uid": UID_B})
    assert store.states_for([monitor.id]) == {}


# ──────────────────────────────────────────────────────────────────────────
# The JSON compatibility store
# ──────────────────────────────────────────────────────────────────────────
def test_the_json_store_supports_the_same_live_read(monkeypatch, isolated_data):
    """A machine without Firebase keeps working, scoped the same way."""
    state_mod.set_scope_provider(None)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monitor = _monitor("")
    state_mod.upsert_monitor(monitor, mirror=False)
    state_mod.save_state({monitor.id: MonitorState(check_count=4),
                          "other": MonitorState(check_count=1)}, mirror=False)

    got = state_mod.load_states_for([monitor.id])
    assert list(got) == [monitor.id]
    assert got[monitor.id].check_count == 4


def test_a_monitor_with_no_state_yet_returns_nothing(live):
    """Before the worker's first check there is no document. The panel keeps
    showing the waiting state it was given rather than inventing one."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)

    assert state_mod.load_states_for([monitor.id]) == {}


def test_a_failed_live_read_surfaces(live, monkeypatch):
    """It must not quietly return an empty dict and look like 'no check yet'."""
    live.as_user(UID_A)
    monitor = _monitor(UID_A)
    state_mod.upsert_monitor(monitor, mirror=False)

    def broken(method, url, token, body):
        raise fs.FirestoreError("boom", 500)

    monkeypatch.setattr(state_mod, "_transport", broken)
    with pytest.raises(fs.FirestoreError):
        state_mod.load_states_for([monitor.id])
