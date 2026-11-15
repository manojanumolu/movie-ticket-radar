"""A dry run evaluates everything and changes nothing (post-deployment finding).

``run_monitor.py --dry-run`` — the workflow's ``dry_run`` input — used to
swap only the notifier for a print statement. The rest of the tick ran for
real: the "delivered" alert was marked as sent and saved, so the next real
run believed it had already emailed and never did. The tick also wrote
observed state and history, flipped expired monitors, clamped end dates,
stored the discovery clock, committed through git — and the segment held the
workflow's only slot for fifty minutes, in which no real alert could go out.

Pinned here, against the rules-enforcing in-memory Firestore and the JSON
store's throwaway data directory:

1. a dry run sends no email and never reaches a notifier;
2. it writes nothing — not one Firestore commit, not one data file — so no
   "sent" mark, no observed state, no history, no EXPIRED flip, no clamp;
3. it reports exactly the alerts a real run would send;
4. any number of dry runs leave a later real run's outcome unchanged: the
   alert still goes out, once;
5. a real run behaves exactly as before;
6. the guard under it (``config.readonly``) refuses any write that is
   reached anyway;
7. a dry-run segment is one pass, with no git, and hands over to a real
   segment when monitors are running — so it can neither delay real alerts
   nor break the chain.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import Path

import pytest

import run_monitor
from config import firestore as fs
from config import readonly, store
from monitor import checker, worker
from monitor import state as state_mod
from monitor.checker import run_once
from monitor.discovery import DiscoveryReport
from monitor.models import (
    ANY_FORMAT,
    Availability,
    Monitor,
    MovieRef,
    Showtime,
    Snapshot,
    TheatreTarget,
    Venue,
)
from monitor.state import MonitorState
from tests.test_scheduling import Clock
from tests.test_worker_reads import OWNER, firestore_worker  # noqa: F401 - the fixture


def _monitor(code: str, at, *, until=None, owner: str = OWNER) -> Monitor:
    return Monitor(movie=MovieRef(platform="bookmyshow", event_code=code, title=f"Film {code}",
                                  region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
                   targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=10, monitor_until=until or at + timedelta(days=2),
                   notify_email="w@example.com", owner_uid=owner)


def _listing(availability: Availability):
    def fetch(movie, date_codes=None):
        show = Showtime("ALLU", "ALLU Cinemas", "S1", "20260925", "07:30 PM", "1930", "Dolby Cinema",
                        availability)
        return checker.Listing(snapshot=Snapshot(movie=movie, venues=[Venue("ALLU", "ALLU Cinemas")],
                                                 showtimes=[show]))
    return fetch


@pytest.fixture
def cloud(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """The production worker path (Firestore, service account) on a bookable
    listing, with an inbox, a snapshot of everything stored, and a tally of
    every write that reached the store."""
    w = firestore_worker
    monkeypatch.setattr(checker, "fetch_listing", _listing(Availability.AVAILABLE))
    w.inbox = []
    w.notify = lambda monitor, change: w.inbox.append((monitor.id, change.kind.value))
    w.snapshot = lambda: copy.deepcopy(w.docs)
    w.stored = lambda m: MonitorState.from_dict(w.docs["monitor_state"][m.id])

    def no_real_email(monitor, change):
        raise AssertionError("the real email sender was reached")

    monkeypatch.setattr("notifications.email.send_change_email", no_real_email)
    return w


def _target(state: MonitorState):
    return next(iter(state.targets.values()))


def _data_files() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in Path(store.DATA_DIR).glob("*") if p.is_file()}


# ──────────────────────────────────────────────────────────────────────────
# 1–3 · Nothing sent, nothing written, everything reported
# ──────────────────────────────────────────────────────────────────────────
def test_a_dry_run_sends_no_email_and_reports_what_it_would_send(cloud, capsys):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    called = []

    report = run_once(at=cloud.now, force=True, dry_run=True,
                      notifier=lambda monitor, change: called.append(change))

    assert called == [] and cloud.inbox == []
    assert report.emails_sent == 0 and report.email_errors == []
    assert report.dry_run is True
    assert [(c.monitor_id, c.kind.value) for c in report.would_send] == [(m.id, "TICKETS_LIVE")]
    assert report.checked == [m.id]
    out = capsys.readouterr().out
    assert "[dry-run] would email: TICKETS ARE LIVE — Film ET1 (ALLU Cinemas · Any format)" in out
    assert "DRY-RUN would_email=1" in out


def test_a_dry_run_writes_nothing_at_all(cloud):
    """Not one Firestore commit: no sent mark, no state, no history."""
    m = cloud.seed(_monitor("ET1", cloud.now), checked=True)
    before = cloud.snapshot()
    cloud.reset()

    run_once(at=cloud.now, force=True, dry_run=True)

    assert cloud.writes() == 0
    assert cloud.docs == before
    stored = cloud.stored(m)
    assert stored.targets == {} and stored.check_count == 1, "the record is exactly as seeded"


def test_a_dry_run_does_not_mark_an_alert_sent_even_in_memory(cloud):
    """The record the tick hands back is not "announced" either."""
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    report = run_once(at=cloud.now, force=True, dry_run=True)
    ts = _target(report.state[m.id])
    assert ts.availability is Availability.AVAILABLE           # evaluated…
    assert ts.notified_availability is not Availability.AVAILABLE  # …never marked
    assert ts.notified_at is None and ts.notified_showtime_keys == []


def test_a_dry_run_flips_no_monitor_to_expired_and_clamps_nothing(cloud):
    expired = cloud.seed(_monitor("ET1", cloud.now, until=cloud.now - timedelta(minutes=5)))
    too_long = cloud.seed(_monitor("ET2", cloud.now, until=cloud.now + timedelta(days=60)))
    before = cloud.snapshot()
    cloud.reset()

    report = run_once(at=cloud.now, force=True, dry_run=True)

    assert cloud.writes() == 0 and cloud.docs == before
    assert cloud.docs["monitors"][expired.id]["status"] == "ACTIVE"
    assert cloud.docs["monitors"][too_long.id]["monitor_until"] == before["monitors"][too_long.id]["monitor_until"]
    assert report.would_expire == [expired.id] and report.expired == []
    assert "would_expire=1" in report.summary()


def test_a_failed_check_in_a_dry_run_writes_no_error_history(cloud, monkeypatch):
    cloud.seed(_monitor("ET1", cloud.now), checked=False)
    monkeypatch.setattr(checker, "fetch_listing",
                        lambda movie, date_codes=None: checker.Listing(error="HTTP 500"))
    cloud.reset()
    report = run_once(at=cloud.now, force=True, dry_run=True)
    assert len(report.failed) == 1 and cloud.writes() == 0
    assert not cloud.docs.get("history")


def test_a_dry_run_does_not_re_list_the_city(cloud, monkeypatch):
    """Sibling discovery stores what it learns; a dry run stores nothing, so
    it does not run (and makes no city-listing request)."""
    calls = []
    monkeypatch.setattr(checker, "discover_siblings",
                        lambda *a, **k: calls.append(1) or DiscoveryReport())
    cloud.seed(_monitor("ET1", cloud.now), checked=False)
    run_once(at=cloud.now, force=True, dry_run=True)
    assert calls == []
    run_once(at=cloud.now, force=True, notifier=cloud.notify)
    assert calls == [1], "a real run still discovers"


def test_the_json_store_is_untouched_too(make_monitor, provider_factory, monkeypatch, at):
    """The developer's store: no data file changes, the discovery clock included."""
    from tests.conftest import ALLU_LIVE, build_payload

    m = make_monitor()
    state_mod.upsert_monitor(m, mirror=False)
    provider = provider_factory([build_payload(ALLU_LIVE)])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    before = _data_files()

    report = run_once(at=at, force=True, dry_run=True, notifier=lambda *a: None)

    assert _data_files() == before
    assert [c.target_key for c in report.would_send] == [m.targets[0].key]


# ──────────────────────────────────────────────────────────────────────────
# 4–5 · A later real run is exactly what it would have been
# ──────────────────────────────────────────────────────────────────────────
def test_a_real_run_after_dry_runs_still_sends_the_alert_once(cloud):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    for minutes in (0, 10, 20):
        run_once(at=cloud.now + timedelta(minutes=minutes), force=True, dry_run=True)
    assert cloud.inbox == []

    run_once(at=cloud.now + timedelta(minutes=30), force=True, notifier=cloud.notify)
    run_once(at=cloud.now + timedelta(minutes=40), force=True, notifier=cloud.notify)

    assert cloud.inbox == [(m.id, "TICKETS_LIVE")]
    stored = cloud.stored(m)
    assert _target(stored).notified_availability is Availability.AVAILABLE
    assert stored.check_count == 2, "only the real checks were counted"


def test_dry_runs_change_nothing_a_real_run_would_record(cloud):
    """Two identical worlds; one has three dry runs first. After the same
    real run, every stored document is identical."""
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    pristine = cloud.snapshot()
    run_once(at=cloud.now + timedelta(minutes=30), force=True, notifier=cloud.notify)
    without = cloud.snapshot()

    cloud.docs.clear()
    cloud.docs.update(copy.deepcopy(pristine))
    cloud.inbox.clear()
    for minutes in (0, 10, 20):
        run_once(at=cloud.now + timedelta(minutes=minutes), force=True, dry_run=True)
    run_once(at=cloud.now + timedelta(minutes=30), force=True, notifier=cloud.notify)
    with_dry_runs = cloud.snapshot()

    # History ids carry a random suffix; everything else must match exactly.
    def comparable(docs):
        return ({c: d for c, d in docs.items() if c != "history"},
                sorted(h["kind"] for h in docs.get("history", {}).values()))

    assert comparable(with_dry_runs) == comparable(without)
    assert cloud.inbox == [(m.id, "TICKETS_LIVE")]


def test_a_real_run_still_sends_marks_and_persists_as_before(cloud):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    report = run_once(at=cloud.now, force=True, notifier=cloud.notify)
    assert report.dry_run is False and report.would_send == []
    assert report.emails_sent == 1 and cloud.inbox == [(m.id, "TICKETS_LIVE")]
    assert _target(cloud.stored(m)).notified_at is not None
    assert [h["kind"] for h in cloud.docs["history"].values()] == ["TICKETS_LIVE"]
    assert "DRY-RUN" not in report.summary()


def test_a_real_run_still_expires_what_is_past_its_end(cloud):
    expired = cloud.seed(_monitor("ET1", cloud.now, until=cloud.now - timedelta(minutes=5)))
    report = run_once(at=cloud.now, force=True, notifier=cloud.notify)
    assert report.expired == [expired.id] and report.would_expire == []
    assert cloud.docs["monitors"][expired.id]["status"] == "EXPIRED"


# ──────────────────────────────────────────────────────────────────────────
# 6 · The guard underneath
# ──────────────────────────────────────────────────────────────────────────
def test_the_guard_refuses_a_firestore_write():
    client = fs.FirestoreClient("p", lambda: "admin",
                                transport=lambda *a: pytest.fail("the write reached the transport"))
    with readonly.read_only():
        assert readonly.active()
        with pytest.raises(readonly.ReadOnlyViolation):
            client.set("monitor_state", "m1", {"a": 1})
        with pytest.raises(readonly.ReadOnlyViolation):
            client.delete("monitors", "m1")
    assert not readonly.active()


def test_the_guard_refuses_a_data_file_write():
    path = Path(store.DATA_DIR) / "state.json"
    before = path.read_text(encoding="utf-8") if path.exists() else None
    with readonly.read_only(), pytest.raises(readonly.ReadOnlyViolation):
        store.write_json(path, {"x": 1}, mirror=False)
    assert (path.read_text(encoding="utf-8") if path.exists() else None) == before
    store.write_json(path, {"x": 1}, mirror=False)            # and outside it, writes work
    assert path.read_text(encoding="utf-8").strip().startswith("{")


def test_a_write_path_the_dry_run_forgot_would_be_refused_not_performed(cloud, monkeypatch):
    """If a future change made the tick write, the dry run fails that
    monitor loudly instead of changing production data."""
    cloud.seed(_monitor("ET1", cloud.now), checked=False)
    real = checker._check_one

    def careless(monitor, listing, state, report, **kw):
        result = real(monitor, listing, state, report, **kw)
        state_mod.save_state(state)                          # a write nobody guarded
        return result

    monkeypatch.setattr(checker, "_check_one", careless)
    before = cloud.snapshot()
    report = run_once(at=cloud.now, force=True, dry_run=True)
    assert cloud.docs == before
    assert len(report.failed) == 1, "refused, and reported against the monitor"


def test_the_guard_is_off_again_after_a_dry_run_even_one_that_raised(cloud, monkeypatch):
    monkeypatch.setattr(checker, "load_monitors_for_checking",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run_once(at=cloud.now, dry_run=True)
    assert not readonly.active()


# ──────────────────────────────────────────────────────────────────────────
# 7 · The segment and the command line
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def dispatched(monkeypatch):
    calls: list[dict] = []

    def dispatch(workflow, inputs=None, ref=""):
        calls.append(dict(inputs or {}))
        return True, "Workflow started."

    monkeypatch.setattr(worker, "dispatch_workflow", dispatch)
    return calls


def test_a_dry_run_segment_is_one_pass_with_no_git_and_hands_over_to_a_real_one(cloud, dispatched,
                                                                              monkeypatch):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    git = []
    monkeypatch.setattr(worker, "git_pull", lambda: git.append("pull") or True)
    monkeypatch.setattr(worker, "git_commit_and_push", lambda msg: git.append("commit") or (True, True))
    monkeypatch.setattr(worker, "git_configure", lambda: git.append("configure"))
    clock = Clock(cloud.now)
    cloud.reset()

    loop = worker.run_loop(max_minutes=50, poll_seconds=30, use_git=True, chain=True, dry_run=True,
                           clock=clock, sleeper=clock.sleep)

    assert loop.ticks == 1 and loop.stopped_reason == "dry run: one pass"
    assert clock.now == cloud.now, "it did not hold the slot"
    assert git == [] and cloud.writes() == 0 and cloud.inbox == []
    assert [(c.monitor_id, c.kind.value) for c in loop.would_send] == [(m.id, "TICKETS_LIVE")]
    # The successor is a real segment: monitoring carries on as normal.
    assert loop.handed_over is True and dispatched == [{"force": "false", "dry_run": "false",
                                                        "monitor_id": ""}]


def test_a_dry_run_with_nothing_running_ends_quietly(cloud, dispatched):
    clock = Clock(cloud.now)
    loop = worker.run_loop(max_minutes=50, use_git=False, chain=True, dry_run=True,
                           clock=clock, sleeper=clock.sleep)
    assert loop.stopped_reason == "nothing running" and dispatched == []


def test_a_real_segment_is_unchanged(cloud, dispatched):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    clock = Clock(cloud.now)
    loop = worker.run_loop(max_minutes=12, poll_seconds=30, use_git=False, chain=True,
                           clock=clock, sleeper=clock.sleep, notifier=cloud.notify)
    assert loop.ticks > 1 and loop.stopped_reason == "" and loop.would_send == []
    assert cloud.inbox == [(m.id, "TICKETS_LIVE")] and loop.handed_over is True


def test_the_command_line_dry_run_takes_the_dry_run_path(monkeypatch, capsys):
    seen = {}

    def fake_loop(**kw):
        seen.update(kw)
        return worker.LoopReport(started_at=None, stopped_reason="dry run: one pass")

    monkeypatch.setattr(run_monitor, "run_loop", fake_loop)
    assert run_monitor.main(["--loop", "--dry-run", "--no-git", "--no-chain"]) == 0
    assert seen["dry_run"] is True and "notifier" not in seen
    assert "nothing is emailed and nothing is written" in capsys.readouterr().out

    seen.clear()
    run_monitor.main(["--loop", "--no-git", "--no-chain"])
    assert seen["dry_run"] is False


def test_the_single_pass_command_line_dry_run_writes_nothing(cloud, capsys):
    m = cloud.seed(_monitor("ET1", cloud.now), checked=False)
    cloud.reset()
    assert run_monitor.main(["--dry-run", "--force"]) == 0
    out = capsys.readouterr().out
    assert cloud.writes() == 0 and cloud.inbox == []
    assert "would email: 1" in out and "Film ET1" in out
    assert m.id not in cloud.docs.get("monitor_state", {})
