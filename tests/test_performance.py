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


def test_the_live_panel_does_not_do_a_page_load(cloud):
    """It re-reads the one monitor's state it is showing — that is what keeps
    an open browser current — but never the page's broad reads. Those were
    what made every paint of Home cost four extra round trips."""
    import inspect

    import app

    source = inspect.getsource(app.live_monitor_panel)
    assert "load_monitors()" not in source
    assert "load_state()" not in source
    assert "load_view" not in source
    assert "load_settings" not in source
    assert "invalidate_cache" not in source
    # …and it does ask for the displayed monitor's own state.
    assert "load_states_for([monitor.id])" in source
    params = list(inspect.signature(app.live_monitor_panel.__wrapped__).parameters)
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


# ──────────────────────────────────────────────────────────────────────────
# The connection pool — what has to outlive a Streamlit script thread
# ──────────────────────────────────────────────────────────────────────────
def test_the_connection_pool_is_shared_by_every_thread():
    """Streamlit gives each browser interaction a brand-new script thread, so
    a thread-local *session* starts every click with an empty pool and pays a
    TLS handshake. The pool has to be the part that survives the thread."""
    import threading

    here = fs._session()
    from_threads: list = []
    for _ in range(3):
        thread = threading.Thread(target=lambda: from_threads.append(fs._session()))
        thread.start()
        thread.join()

    for other in from_threads:
        # Still a session of its own: no cookie jar, no default header and no
        # auth is ever shared between threads, and so between users.
        assert other is not here
        # …but the same pool, which is the whole point.
        assert other.get_adapter("https://firestore.googleapis.com") is \
            here.get_adapter("https://firestore.googleapis.com")
        assert other.get_adapter("https://x").poolmanager is fs._ADAPTER.poolmanager


def test_a_session_carries_no_shared_identity():
    """Nothing about who is calling may live on the session: the pool is
    shared between users, so anything sticky on it would leak."""
    session = fs._session()
    assert "Authorization" not in session.headers
    assert not session.cookies
    assert session.auth is None


def test_every_request_carries_its_own_authorization(monkeypatch):
    """Two users, two tokens, in a row over the same shared pool."""
    seen: list = []

    class FakeSession:
        headers: dict = {}

        def request(self, method, url, **kwargs):
            seen.append(kwargs["headers"])

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {}

            return R()

    monkeypatch.setattr(fs, "_session", lambda: FakeSession())
    fs._http("POST", "https://example.invalid/x", "token-for-a", None)
    fs._http("POST", "https://example.invalid/x", "token-for-b", None)
    assert seen == [{"Authorization": "Bearer token-for-a"},
                    {"Authorization": "Bearer token-for-b"}]


# ──────────────────────────────────────────────────────────────────────────
# Concurrent reads — and the boundary that makes them safe
# ──────────────────────────────────────────────────────────────────────────
class ThreadAwareFirestore(MemoryFirestore):
    """Remembers which thread each request came from."""

    def __init__(self):
        super().__init__()
        self.threads: list = []

    def __call__(self, method, url, token, body):
        import threading

        self.threads.append(threading.get_ident())
        return super().__call__(method, url, token, body)


@pytest.fixture
def fanout(monkeypatch):
    """Firestore, plus a tripwire on everything a worker thread must not touch."""
    import threading

    store = ThreadAwareFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)

    import streamlit as st

    session: dict = {}
    monkeypatch.setattr(st, "session_state", session, raising=False)

    caller = threading.get_ident()
    off_thread: list = []

    def guard(name, real):
        def wrapper(*args, **kwargs):
            if threading.get_ident() != caller:
                off_thread.append(name)
                raise AssertionError(f"{name} reached from a worker thread")
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(state_mod, "_cache_store",
                        guard("_cache_store", state_mod._cache_store))
    guarded_backend = guard("_backend", state_mod._backend)
    calls_to_backend: list = []

    def counted_backend():
        calls_to_backend.append(1)
        return guarded_backend()

    monkeypatch.setattr(state_mod, "_backend", counted_backend)

    class Fan:
        docs = store.docs
        calls = store.calls
        threads = store.threads
        state = session
        off_thread_hits = off_thread
        backend_calls = calls_to_backend
        main_thread = caller

        @staticmethod
        def as_user(uid):
            def provider():
                if threading.get_ident() != caller:
                    off_thread.append("scope provider")
                    raise AssertionError("the scope provider reached a worker thread")
                return Scope(PROJECT, uid, lambda: f"id.{uid}")

            state_mod.set_scope_provider(provider)

    yield Fan
    state_mod.set_scope_provider(None)


def test_the_fan_out_never_touches_streamlit_from_a_worker_thread(fanout):
    """The scope provider, the cache and the backend all read
    ``st.session_state``, which a worker thread sees as *empty, with no error
    at all*. Every one of them has to be resolved before a thread starts."""
    fanout.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)

    before = len(fanout.threads)
    data = state_mod.load_many("monitors", "state", "history", "settings")

    assert fanout.off_thread_hits == []
    assert [m.movie.title for m in data["monitors"]] == ["A's film"]
    # The reads really did leave the calling thread — otherwise this would
    # pass merely by being sequential.
    assert {t for t in fanout.threads[before:]} != {fanout.main_thread}


def test_the_backend_is_resolved_once_on_the_calling_thread(fanout):
    """A worker resolving it itself would find nobody signed in and be given
    the unscoped JSON store, which answers with every account's records."""
    fanout.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    fanout.backend_calls.clear()

    state_mod.load_many("monitors", "state", "history", "settings")
    assert len(fanout.backend_calls) == 1


def test_a_worker_thread_is_given_a_uid_bound_firestore_store(fanout):
    """What the threads actually hold: never the JSON store, and never one
    that could answer for somebody else."""
    fanout.as_user(UID_A)
    detached = state_mod._detached(state_mod._backend())

    assert isinstance(detached, state_mod._FirestoreStore)
    assert not isinstance(detached, state_mod._JsonStore)
    assert detached.uid == UID_A
    # The token is already resolved: asking for it must not call back into
    # auth.session, which reads — and near expiry writes — session_state.
    assert detached.client.token() == f"id.{UID_A}"


def test_the_json_store_is_never_fanned_out(monkeypatch):
    """No Firestore: the compatibility path stays sequential, exactly as it was."""
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    state_mod.set_scope_provider(None)
    assert state_mod._detached(state_mod._backend()) is None


def test_user_b_never_receives_user_a_records_through_the_fan_out(fanout):
    """The isolation test, run through the concurrent path."""
    fanout.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    state_mod.save_settings({"notify_email": "a@example.com"}, mirror=False)
    fanout.as_user(UID_B)
    state_mod.upsert_monitor(_monitor(UID_B, "B's film"), mirror=False)
    state_mod.save_settings({"notify_email": "b@example.com"}, mirror=False)

    fanout.as_user(UID_A)
    a = state_mod.load_many("monitors", "state", "history", "settings")
    assert [m.movie.title for m in a["monitors"]] == ["A's film"]
    assert a["settings"]["notify_email"] == "a@example.com"

    fanout.state.clear()                                   # sign out
    fanout.as_user(UID_B)
    b = state_mod.load_many("monitors", "state", "history", "settings")
    assert [m.movie.title for m in b["monitors"]] == ["B's film"], b["monitors"]
    assert b["settings"]["notify_email"] == "b@example.com"
    assert fanout.off_thread_hits == []


def test_the_fan_out_costs_one_round_trip_not_four(cloud):
    """Four reads, four requests — but overlapping, not one after another."""
    import time

    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "One"), mirror=False)

    delay = 0.05
    real = state_mod._transport

    def slow(*args, **kwargs):
        time.sleep(delay)
        return real(*args, **kwargs)

    state_mod._transport = slow
    try:
        state_mod.invalidate_cache()
        before = len(cloud.calls)
        started = time.perf_counter()
        state_mod.load_many("monitors", "state", "history", "settings")
        elapsed = time.perf_counter() - started
    finally:
        state_mod._transport = real

    assert sum(cloud.requests_since(before).values()) == 4
    # Sequentially this is 4 x 50 ms; concurrently it is one of them.
    assert elapsed < delay * 2.5, f"{elapsed:.3f}s — the reads did not overlap"


def test_the_fan_out_reads_only_what_is_not_already_cached(cloud):
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "One"), mirror=False)
    state_mod.load_many("monitors", "state", "history", "settings")

    before = len(cloud.calls)
    data = state_mod.load_many("monitors", "state", "history", "settings")
    assert sum(cloud.requests_since(before).values()) == 0
    assert [m.movie.title for m in data["monitors"]] == ["One"]


def test_a_failed_read_still_raises_on_the_calling_thread(cloud):
    """A worker's exception has to surface where a sequential read's would."""
    real = state_mod._transport

    def broken(method, url, token, body):
        if ":runQuery" in url and "history" in str(body):
            raise fs.FirestoreError("boom", 500)
        return real(method, url, token, body)

    cloud.as_user(UID_A)
    state_mod._transport = broken
    try:
        with pytest.raises(fs.FirestoreError):
            state_mod.load_many("monitors", "state", "history", "settings")
    finally:
        state_mod._transport = real


# ──────────────────────────────────────────────────────────────────────────
# Invalidation, now that it is scoped
# ──────────────────────────────────────────────────────────────────────────
def test_a_write_invalidates_only_what_it_wrote(cloud):
    """Appending a history line is no reason to re-read monitors, state and
    settings over the network — that broad eviction left the rerun after
    every write completely cold."""
    cloud.as_user(UID_A)
    monitor = _monitor(UID_A, "Noisy")
    state_mod.upsert_monitor(monitor, mirror=False)
    state_mod.load_many("monitors", "state", "history", "settings")

    before = len(cloud.calls)
    state_mod.record_history(monitor, "CREATED", "Monitor created.", mirror=False)
    state_mod.load_many("monitors", "state", "history", "settings")

    paths = [p for _, p, _ in cloud.calls[before:]]
    assert not any("users" in p for p in paths), f"a history write re-read settings: {paths}"
    bodies_for_monitors = [p for p in paths if p.endswith(":runQuery")]
    # Only history is re-read; monitors and state are still cached. The
    # history write itself is a commit plus its de-duplication read.
    assert len(bodies_for_monitors) <= 2, paths


def test_each_write_still_shows_the_person_their_own_change(cloud):
    cloud.as_user(UID_A)
    monitor = _monitor(UID_A, "Mine")
    state_mod.upsert_monitor(monitor, mirror=False)

    state_mod.load_many("monitors", "state", "history", "settings")
    state_mod.save_settings({"notify_email": "new@example.com"}, mirror=False)
    assert state_mod.load_many("settings")["settings"]["notify_email"] == "new@example.com"

    state_mod.load_many("monitors")
    state_mod.stop_monitor(monitor.id, mirror=False)
    assert not state_mod.load_many("monitors")["monitors"][0].is_running()

    state_mod.load_many("monitors", "state")
    state_mod.delete_monitor(monitor.id, mirror=False)
    assert state_mod.load_many("monitors")["monitors"] == []


def test_invalidating_everything_still_works(cloud):
    """Deleting an account takes the lot."""
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "Mine"), mirror=False)
    state_mod.load_many("monitors", "state", "history", "settings")
    assert cloud.state.get(state_mod.CACHE_KEY)
    state_mod.invalidate_cache()
    assert cloud.state.get(state_mod.CACHE_KEY) == {}


def test_a_scoped_invalidation_cannot_reach_another_account(cloud):
    cloud.as_user(UID_A)
    state_mod.upsert_monitor(_monitor(UID_A, "A's film"), mirror=False)
    state_mod.load_many("monitors")
    cloud.as_user(UID_B)
    state_mod.upsert_monitor(_monitor(UID_B, "B's film"), mirror=False)
    state_mod.load_many("monitors")

    state_mod.invalidate_cache("monitors")                 # as B
    keys = list(cloud.state.get(state_mod.CACHE_KEY, {}))
    assert f"{UID_A}:monitors" in keys                     # A's entry untouched
    assert f"{UID_B}:monitors" not in keys


# ──────────────────────────────────────────────────────────────────────────
# The page no longer re-reads what it is already holding
# ──────────────────────────────────────────────────────────────────────────
def test_start_monitor_is_handed_the_settings_it_needs():
    """It runs inside the script run that already loaded them; reading them
    again is another round trip for data in hand."""
    import inspect

    import app

    assert "settings" in inspect.signature(app.start_monitor).parameters
    assert "load_settings()" not in inspect.getsource(app.main)
    assert "settings=settings" in inspect.getsource(app.page_home)


def test_load_view_fetches_everything_in_one_go():
    import inspect

    import app

    source = inspect.getsource(app.load_view)
    assert "load_many" in source
    assert "load_state()" not in source and "load_history()" not in source


def test_a_worker_thread_resolving_the_backend_itself_would_leak(cloud):
    """The hazard this design exists to avoid, demonstrated.

    Streamlit's session state is invisible from any thread but the script
    thread — it reads back *empty, with no error*. So a worker that resolved
    the store for itself would find nobody signed in, fall past the
    service-account branch and be handed ``_JsonStore(uid=None)``, whose
    ``_owns`` is True for every record of every account. This test pins that
    it really is what would happen, which is why :func:`_detached` resolves
    the store on the script thread and hands the worker the result.
    """
    import threading

    cloud.as_user(UID_A)

    # What a worker thread sees, with the scope provider's tripwire removed
    # so we can observe the fallback rather than the assertion.
    state_mod.set_scope_provider(lambda: None)
    got: list = []
    thread = threading.Thread(target=lambda: got.append(state_mod._backend()))
    thread.start()
    thread.join()

    leaked = got[0]
    assert isinstance(leaked, state_mod._JsonStore)
    assert leaked.uid is None                    # every owner, no filter
    assert leaked._owns({"owner_uid": "somebody-else"}) is True

    # And what the real path does instead: the store is bound to one UID
    # before any thread starts.
    cloud.as_user(UID_A)
    safe = state_mod._detached(state_mod._backend())
    assert isinstance(safe, state_mod._FirestoreStore)
    assert safe.uid == UID_A
    assert not safe._owned({"owner_uid": "somebody-else"})
