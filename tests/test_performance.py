"""The Firestore read path: how many round trips, and whose data comes back.

Enabling Firestore turned every store read into a network call, and the app
was making four to ten of them per interaction — each one opening a fresh TLS
connection. These tests pin the two things that fixed it and, more
importantly, the thing that must never break because of it: one account's
cached reads must never be handed to another.

Everything runs against the rule-enforcing in-memory Firestore. No
credentials, no network.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import pytest

from config import firestore as fs
from config.timezone import IST
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget
from monitor.state import MonitorState, Scope
from tests.test_isolation import MemoryFirestore, PROJECT

UID_A, UID_B = "uid-perf-a", "uid-perf-b"


def _monitor(uid: str, title: str) -> Monitor:
    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=10, monitor_until=datetime.now(IST) + timedelta(days=2),
        notify_email="w@example.com", owner_uid=uid)


@pytest.fixture
def cloud(monkeypatch):
    """Firestore, a session state to cache into, and helpers to be a person."""
    store = MemoryFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)

    import streamlit as st

    session: dict = {}
    monkeypatch.setattr(st, "session_state", session, raising=False)

    class Cloud:
        docs = store.docs
        calls = store.calls
        state = session

        @staticmethod
        def as_user(uid: str) -> None:
            state_mod.set_scope_provider(
                lambda: Scope(PROJECT, uid, lambda: f"id.{uid}"))

        @staticmethod
        def signed_out() -> None:
            """What auth.session does on sign-out: the session is wiped."""
            session.clear()
            state_mod.set_scope_provider(None)

        @staticmethod
        def requests_since(n: int) -> Counter:
            return Counter(p for _, p, _ in store.calls[n:])

    yield Cloud
    state_mod.set_scope_provider(None)


# ──────────────────────────────────────────────────────────────────────────
# Isolation — the thing caching must not break
# ──────────────────────────────────────────────────────────────────────────
def test_a_cached_read_is_never_served_to_another_account(cloud):
    """A signs in and reads; A signs out; B signs in in the same session.
    B must see B's records, never A's — from the cache or anywhere else."""
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    cloud.as_user(UID_B)
    state_mod.upsert_monitor(_monitor(UID_B, "B's film"), mirror=False)

    cloud.as_user(UID_A)
    assert [m.movie.title for m in state_mod.load_monitors()] == ["A's film"]

    cloud.signed_out()
    cloud.as_user(UID_B)
    titles = [m.movie.title for m in state_mod.load_monitors()]
    assert titles == ["B's film"], f"B saw {titles}"


def test_switching_account_without_a_sign_out_still_cannot_reuse_the_cache(cloud):
    """Even if the session were not wiped, the UID is part of every cache key,
    so one account's entry cannot answer another's read."""
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    cloud.as_user(UID_B)
    state_mod.upsert_monitor(_monitor(UID_B, "B's film"), mirror=False)

    cloud.as_user(UID_A)
    assert [m.movie.title for m in state_mod.load_monitors()] == ["A's film"]
    cloud.as_user(UID_B)                       # no sign-out, no session wipe
    assert [m.movie.title for m in state_mod.load_monitors()] == ["B's film"]

    keys = [k for k in cloud.state.get(state_mod.CACHE_KEY, {})]
    assert any(k.startswith(UID_A) for k in keys) and any(k.startswith(UID_B) for k in keys)


def test_signing_out_clears_the_cache(cloud):
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    state_mod.load_monitors()
    assert cloud.state.get(state_mod.CACHE_KEY)
    cloud.signed_out()
    assert not cloud.state.get(state_mod.CACHE_KEY)


def test_the_worker_never_caches(cloud, monkeypatch):
    """The worker runs for fifty minutes and must see each tick's writes; it
    has no session, so caching is off for it entirely."""
    state_mod.set_scope_provider(None)
    assert state_mod._cache_scope_uid() is None


# ──────────────────────────────────────────────────────────────────────────
# Invalidation — a change the person just made is never hidden
# ──────────────────────────────────────────────────────────────────────────
def test_creating_a_monitor_invalidates_the_cache(cloud):
    cloud.as_user(UID_A)
    assert state_mod.load_monitors() == []          # caches the empty answer
    state_mod.upsert_monitor(_monitor(UID_A, "New"), mirror=False)
    assert [m.movie.title for m in state_mod.load_monitors()] == ["New"]


def test_stopping_a_monitor_invalidates_the_cache(cloud):
    cloud.as_user(UID_A)
    monitor = _monitor(UID_A, "Stop me")
    state_mod.upsert_monitor(monitor, mirror=False)
    assert state_mod.load_monitors()[0].is_running()
    state_mod.stop_monitor(monitor.id, mirror=False)
    assert not state_mod.load_monitors()[0].is_running()


def test_deleting_a_monitor_invalidates_the_cache(cloud):
    cloud.as_user(UID_A)
    monitor = _monitor(UID_A, "Delete me")
    state_mod.upsert_monitor(monitor, mirror=False)
    assert len(state_mod.load_monitors()) == 1
    state_mod.delete_monitor(monitor.id, mirror=False)
    assert state_mod.load_monitors() == []


def test_saving_settings_invalidates_the_cache(cloud):
    cloud.as_user(UID_A)
    state_mod.save_settings({"notify_email": "first@example.com"}, mirror=False)
    assert state_mod.load_settings()["notify_email"] == "first@example.com"
    state_mod.save_settings({"notify_email": "second@example.com"}, mirror=False)
    assert state_mod.load_settings()["notify_email"] == "second@example.com"


def test_recording_history_invalidates_the_cache(cloud):
    cloud.as_user(UID_A)
    monitor = _monitor(UID_A, "Noisy")
    state_mod.upsert_monitor(monitor, mirror=False)
    assert state_mod.load_history() == []
    state_mod.record_history(monitor, "CREATED", "Monitor created.", mirror=False)
    assert [h["message"] for h in state_mod.load_history()] == ["Monitor created."]


def test_the_cache_expires(cloud, monkeypatch):
    """It collapses one interaction's reads, it does not hold data."""
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "First"), mirror=False)
    before = len(cloud.calls)
    state_mod.load_monitors()
    state_mod.load_monitors()
    assert len(cloud.calls) - before == 1                  # second read was free

    monkeypatch.setattr(state_mod, "CACHE_SECONDS", 0.0)
    before = len(cloud.calls)
    state_mod.load_monitors()
    state_mod.load_monitors()
    assert len(cloud.calls) - before == 2                  # expired: both went out


# ──────────────────────────────────────────────────────────────────────────
# Request counts — the regression that started this
# ──────────────────────────────────────────────────────────────────────────
def test_repeated_reads_in_one_interaction_cost_one_request_each(cloud):
    """What a rerun does: monitors, state, history and settings, once apiece,
    however many times the page asks for them."""
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "One"), mirror=False)

    before = len(cloud.calls)
    for _ in range(3):                                     # as the page and the rail both do
        state_mod.load_monitors()
        state_mod.load_state()
        state_mod.load_history()
        state_mod.load_settings()
    kinds = cloud.requests_since(before)
    assert sum(kinds.values()) == 4, kinds                 # not 12


def test_the_status_card_does_not_read_the_store(cloud):
    """It is handed the monitor and its state; it must not fetch them again."""
    import inspect

    import app

    source = inspect.getsource(app.status_card)
    assert "load_monitors" not in source and "load_state" not in source
    params = list(inspect.signature(app.status_card.__wrapped__).parameters)
    assert params == ["monitor", "state"]


# ──────────────────────────────────────────────────────────────────────────
# Connection reuse
# ──────────────────────────────────────────────────────────────────────────
def test_the_http_transport_reuses_one_session_per_thread():
    """A fresh Session per request meant a TLS handshake per request."""
    first, second = fs._session(), fs._session()
    assert first is second

    import threading

    other: list = []
    thread = threading.Thread(target=lambda: other.append(fs._session()))
    thread.start(); thread.join()
    assert other[0] is not first          # not shared across threads


def test_the_transport_still_sends_the_auth_header_and_timeout(monkeypatch):
    """Reusing the connection must not change what is sent."""
    seen: dict = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            seen.update({"method": method, "url": url, **kwargs})

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"ok": True}
            return R()

    monkeypatch.setattr(fs, "_session", lambda: FakeSession())
    status, payload = fs._http("POST", "https://example.invalid/x", "tok", {"a": 1})
    assert (status, payload) == (200, {"ok": True})
    assert seen["headers"] == {"Authorization": "Bearer tok"}
    assert seen["timeout"] == fs.TIMEOUT
    assert seen["json"] == {"a": 1}
