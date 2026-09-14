"""Latency: the first check is immediate, the interval is honoured, nothing overlaps.

Background — the failure this guards against. A monitor created at 02:55 was
first checked at 03:57, because the ``*/5`` schedule is throttled by GitHub
to hours apart and nothing asked the worker to run. Two things fix that and
both are pinned here: the UI dispatches the workflow the moment a monitor is
saved, and the worker runs as a segment that ticks on its own clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config import store
from config.timezone import fmt_datetime, fmt_time, to_iso
from monitor import worker
from monitor.checker import run_once
from monitor.models import Availability, Monitor
from monitor.state import MonitorState, get_monitor, load_monitors, load_state, save_state, upsert_monitor
from tests.conftest import ALLU_LIVE, NOT_ON_SALE, SOLD_OUT, FakeResponse, build_payload

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def run_app(**session):
    app = AppTest.from_file("app.py", default_timeout=60)
    app.session_state["page"] = "Home"
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def body_of(app) -> str:
    return " ".join([m.value for m in app.markdown] + [c.value for c in app.caption]
                    + [w.value for w in app.warning])


@pytest.fixture
def seeded(provider_factory, monkeypatch):
    from monitor import catalogue
    from tests.conftest import QUICKBOOK_HYD

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * 3)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad")
                 if catalogue.movie_from_entry(e).title == "Mandaadi")
    return catalogue.movie_from_entry(entry).id


@pytest.fixture
def dispatches(monkeypatch):
    """Capture every workflow dispatch the app attempts; pretend the token exists."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    monkeypatch.setattr(store, "sync_from_github", lambda **k: False)

    def fake_dispatch(workflow, inputs=None, ref=""):
        calls.append((workflow, dict(inputs or {})))
        return True, "Workflow started."

    monkeypatch.setattr(store, "dispatch_workflow", fake_dispatch)
    return calls


def start_from_ui(seeded):
    app = run_app(step=5, location="hyderabad", movie_id=seeded,
                  theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    return app


# ──────────────────────────────────────────────────────────────────────────
# 1–3: Start Monitoring persists, dispatches, and survives a failed dispatch
# ──────────────────────────────────────────────────────────────────────────
def test_start_monitoring_persists_the_monitor(seeded, dispatches):
    start_from_ui(seeded)
    monitors = load_monitors()
    assert len(monitors) == 1
    assert monitors[0].is_running()
    assert monitors[0].notify_email == "me@example.com"


def test_start_monitoring_dispatches_an_immediate_check(seeded, dispatches):
    app = start_from_ui(seeded)
    monitor = load_monitors()[0]
    assert dispatches == [
        (store.MONITOR_WORKFLOW, {"force": "true", "dry_run": "false", "monitor_id": monitor.id}),
    ]
    # ...and the monitor remembers it asked, so the rail says "any moment now"
    # rather than "on next scheduled run".
    assert monitor.first_check_requested_at is not None
    assert "First check is running now" in body_of(app)


def test_a_failed_dispatch_still_creates_the_monitor(seeded, monkeypatch):
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    monkeypatch.setattr(store, "sync_from_github", lambda **k: False)
    monkeypatch.setattr(store, "dispatch_workflow",
                        lambda *a, **k: (False, "Could not start workflow: 403 Forbidden"))
    app = start_from_ui(seeded)
    monitors = load_monitors()
    assert len(monitors) == 1 and monitors[0].is_running()
    assert monitors[0].first_check_requested_at is None
    assert any("scheduled worker will pick it up" in w.value for w in app.warning)


def test_dispatch_is_skipped_when_start_now_is_off(seeded, dispatches):
    app = run_app(step=5, location="hyderabad", movie_id=seeded,
                  theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.toggle(key="start_now_toggle").set_value(False).run()
    app.button(key="start").click().run()
    assert load_monitors()[0].is_running()
    assert dispatches == []


def test_request_check_now_sends_string_inputs(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "dispatch_workflow",
                        lambda wf, inputs=None, ref="": seen.update(wf=wf, inputs=inputs) or (True, "ok"))
    assert store.request_check_now("abc123") == (True, "ok")
    assert seen["wf"] == "bookmyshow-monitor.yml"
    assert seen["inputs"] == {"force": "true", "dry_run": "false", "monitor_id": "abc123"}
    assert all(isinstance(v, str) for v in seen["inputs"].values())


# ──────────────────────────────────────────────────────────────────────────
# 4–5: the schedule remains a fallback and the first check never waits
# ──────────────────────────────────────────────────────────────────────────
def test_scheduled_run_checks_a_never_checked_monitor_without_force(make_monitor, provider_factory,
                                                                    monkeypatch, at):
    """A cron tick (no --force) must still check a brand-new monitor at once."""
    monitor = make_monitor(interval=30)
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(NOT_ON_SALE)]))
    report = run_once(at=at, force=False, mirror=False, notifier=lambda m, c: None)
    assert report.checked == [monitor.id]
    assert load_state()[monitor.id].check_count == 1


def test_first_check_does_not_wait_for_the_interval(at):
    for interval in (10, 15, 30):
        assert MonitorState().is_due(interval, at) is True


def test_workflow_yaml_keeps_schedule_dispatch_loop_and_concurrency():
    from pathlib import Path

    yaml = Path(".github/workflows/bookmyshow-monitor.yml").read_text(encoding="utf-8")
    assert "schedule:" in yaml and "cron:" in yaml           # safety net stays
    assert "workflow_dispatch:" in yaml and "monitor_id:" in yaml
    assert "--loop" in yaml                                  # the segment worker
    assert "group: bookmyshow-monitor" in yaml and "cancel-in-progress: false" in yaml
    assert "actions: write" in yaml                          # can hand over to the next segment


# ──────────────────────────────────────────────────────────────────────────
# 6–9: 10 / 15 / 30 minute cadence inside one segment, and no overlap
# ──────────────────────────────────────────────────────────────────────────
class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _endless_provider(provider_factory, shows):
    """A provider whose queue never runs dry — the loop makes many requests."""
    provider = provider_factory([])
    provider.session.responses = None
    provider.session.get = lambda url, headers=None, params=None, timeout=None: FakeResponse(200, build_payload(shows))
    return provider


@pytest.mark.parametrize("interval", [10, 15, 30])
def test_segment_checks_at_the_configured_interval(interval, make_monitor, provider_factory,
                                                   monkeypatch, at):
    monitor = make_monitor(interval=interval, until=at + timedelta(hours=6))
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: _endless_provider(provider_factory, NOT_ON_SALE))

    clock = Clock(at)
    loop = worker.run_loop(max_minutes=61, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=clock.sleep, notifier=lambda m, c: None)

    checked_at = [r.started_at for r in loop.reports if r.checked]
    # First check immediately, then one per interval for the rest of the hour.
    assert checked_at[0] == at
    expected = 1 + 60 // interval
    assert len(checked_at) in (expected, expected + 1)
    gaps = [(b - a).total_seconds() for a, b in zip(checked_at, checked_at[1:])]
    assert all(g == interval * 60 for g in gaps), gaps
    assert load_state()[monitor.id].check_count == len(checked_at)


def test_segment_serves_each_monitor_at_its_own_interval(make_monitor, provider_factory,
                                                         monkeypatch, at):
    fast = make_monitor(interval=10, until=at + timedelta(hours=6))
    slow = make_monitor(interval=30, until=at + timedelta(hours=6))
    slow.id = "slow" + slow.id
    upsert_monitor(fast, mirror=False)
    upsert_monitor(slow, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: _endless_provider(provider_factory, NOT_ON_SALE))

    clock = Clock(at)
    worker.run_loop(max_minutes=61, poll_seconds=30, use_git=False, chain=False,
                    clock=clock, sleeper=clock.sleep, notifier=lambda m, c: None)
    state = load_state()
    assert state[fast.id].check_count == 7   # 0,10,…,60
    assert state[slow.id].check_count == 3   # 0,30,60


def test_force_and_monitor_filter_apply_to_the_first_tick_only(make_monitor, provider_factory,
                                                               monkeypatch, at):
    """A dispatch for one new monitor must not starve the others for an hour."""
    new = make_monitor(interval=10, until=at + timedelta(hours=6))
    old = make_monitor(interval=10, until=at + timedelta(hours=6))
    old.id = "old" + old.id
    upsert_monitor(new, mirror=False)
    upsert_monitor(old, mirror=False)
    save_state({old.id: MonitorState(last_check_at=at - timedelta(minutes=2), check_count=1,
                                     success_count=1)}, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: _endless_provider(provider_factory, NOT_ON_SALE))

    clock = Clock(at)
    loop = worker.run_loop(max_minutes=25, poll_seconds=30, force=True, monitor_id=new.id,
                           use_git=False, chain=False, clock=clock, sleeper=clock.sleep,
                           notifier=lambda m, c: None)
    first = loop.reports[0]
    assert first.checked == [new.id]                 # the dispatch's monitor, right away
    state = load_state()
    assert state[old.id].check_count == 3            # 1 before + at +8 and +18 min
    assert state[new.id].check_count == 3            # 0, +10, +20


def test_segment_stops_when_nothing_is_running(make_monitor, at):
    monitor = make_monitor(until=at - timedelta(minutes=1))
    upsert_monitor(monitor, mirror=False)
    clock = Clock(at)
    loop = worker.run_loop(max_minutes=50, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=clock.sleep)
    assert loop.ticks == 1
    assert loop.stopped_reason == "nothing running"
    assert loop.handed_over is False
    assert get_monitor(monitor.id).status.value == "EXPIRED"


def test_segment_hands_over_when_monitors_remain(make_monitor, provider_factory, monkeypatch, at):
    monitor = make_monitor(interval=10, until=at + timedelta(hours=6))
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: _endless_provider(provider_factory, NOT_ON_SALE))
    handed = []
    monkeypatch.setattr(worker, "dispatch_workflow",
                        lambda wf, inputs=None, ref="": handed.append((wf, inputs)) or (True, "ok"))
    clock = Clock(at)
    loop = worker.run_loop(max_minutes=5, poll_seconds=30, use_git=False, chain=True,
                           clock=clock, sleeper=clock.sleep, notifier=lambda m, c: None)
    assert loop.handed_over is True
    assert handed == [("bookmyshow-monitor.yml", {"force": "false", "dry_run": "false", "monitor_id": ""})]


def test_a_check_is_never_repeated_inside_the_interval(make_monitor, provider_factory, monkeypatch, at):
    """Two ticks 20 seconds apart — the second must be a no-op (no overlap, no double check)."""
    monitor = make_monitor(interval=10)
    upsert_monitor(monitor, mirror=False)
    calls = []

    def provider_for(slug):
        calls.append(1)
        return provider_factory([build_payload(NOT_ON_SALE)])

    monkeypatch.setattr("monitor.checker.get_provider", provider_for)
    run_once(at=at, mirror=False, notifier=lambda m, c: None)
    # The fake provider has one response queued; a second fetch would fail
    # loudly and show up as a failed check.
    report = run_once(at=at + timedelta(seconds=20), mirror=False, notifier=lambda m, c: None)
    assert report.checked == [] and report.failed == []
    assert any("not due" in s for s in report.skipped)
    assert load_state()[monitor.id].check_count == 1


def test_next_wakeup_is_capped_by_the_poll_so_new_monitors_are_noticed(make_monitor, at):
    monitor = make_monitor(interval=30)
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: MonitorState(last_check_at=at)}, mirror=False)
    assert worker.seconds_until_next_due(at, poll_seconds=30) == 30
    # ...and it wakes exactly when the interval is up, never early.
    assert worker.seconds_until_next_due(at + timedelta(minutes=29), 30) == pytest.approx(60)
    assert worker.seconds_until_next_due(at + timedelta(minutes=29, seconds=40), 30) == pytest.approx(20)
    assert worker.seconds_until_next_due(at + timedelta(minutes=31), 30) == 1.0


# ──────────────────────────────────────────────────────────────────────────
# 10–12: transitions, duplicate protection and retry — through the segment
# ──────────────────────────────────────────────────────────────────────────
def test_transition_in_a_segment_emails_once_and_only_once(make_monitor, provider_factory,
                                                           monkeypatch, at):
    monitor = make_monitor(interval=10, until=at + timedelta(hours=6))
    upsert_monitor(monitor, mirror=False)
    sequence = [SOLD_OUT, SOLD_OUT, ALLU_LIVE, ALLU_LIVE, ALLU_LIVE, ALLU_LIVE]
    served = []

    def provider_for(slug):
        shows = sequence[min(len(served), len(sequence) - 1)]
        served.append(shows)
        return provider_factory([build_payload(shows)])

    monkeypatch.setattr("monitor.checker.get_provider", provider_for)
    sent = []
    clock = Clock(at)
    worker.run_loop(max_minutes=55, poll_seconds=30, use_git=False, chain=False,
                    clock=clock, sleeper=clock.sleep, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1
    assert sent[0].previous is Availability.SOLD_OUT and sent[0].current is Availability.AVAILABLE
    assert sent[0].time_links == [["07:30 PM",
                                   "https://in.bookmyshow.com/movies/hyderabad/avengers-endgame/buytickets/ET00478890/20260925"]]


def test_email_failure_in_a_segment_is_retried_next_tick(make_monitor, provider_factory,
                                                         monkeypatch, at):
    monitor = make_monitor(interval=10, until=at + timedelta(hours=6))
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: _endless_provider(provider_factory, ALLU_LIVE))
    attempts = []

    def flaky(m, change):
        attempts.append(change)
        if len(attempts) == 1:
            raise RuntimeError("smtp down")

    clock = Clock(at)
    worker.run_loop(max_minutes=25, poll_seconds=30, use_git=False, chain=False,
                    clock=clock, sleeper=clock.sleep, notifier=flaky)
    # tick 1 failed, tick 2 succeeded, tick 3 stayed silent.
    assert len(attempts) == 2
    ts = load_state()[monitor.id].targets["ALLU::Dolby Cinema"]
    assert ts.notified_availability is Availability.AVAILABLE
    assert ts.notified_at == at + timedelta(minutes=10)


# ──────────────────────────────────────────────────────────────────────────
# 16: Asia/Kolkata everywhere
# ──────────────────────────────────────────────────────────────────────────
def test_worker_timestamps_are_ist_even_on_a_utc_host(make_monitor, provider_factory, monkeypatch, at):
    from datetime import timezone

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    sent = []
    utc_at = at.astimezone(timezone.utc)
    run_once(at=utc_at, mirror=False, notifier=lambda m, c: sent.append(c))

    raw = store.read_json(store.STATE_FILE)[monitor.id]
    assert raw["last_check_at"].endswith("+05:30")
    assert raw["targets"]["ALLU::Dolby Cinema"]["since"].endswith("+05:30")
    assert fmt_time(sent[0].detected_at) == fmt_time(at)
    assert to_iso(sent[0].detected_at).endswith("+05:30")


def test_email_says_ist_explicitly(make_monitor, at):
    from monitor.changes import Change, ChangeKind
    from notifications.email import render_change

    monitor = make_monitor()
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=monitor.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title="X",
                    previous=Availability.NOT_BOOKABLE, current=Availability.AVAILABLE,
                    date_code="20260925", booking_url=monitor.movie.source_url,
                    time_labels=["07:30 PM"], detected_at=at)
    _, html, text = render_change(monitor, change)
    assert f"Detected at {fmt_time(at)} IST" in html
    assert f"{fmt_datetime(monitor.monitor_until)} IST" in html
    assert f"Detected at {fmt_time(at)} IST." in text


# ──────────────────────────────────────────────────────────────────────────
# The rail tells the truth about the first check
# ──────────────────────────────────────────────────────────────────────────
def test_rail_shows_waiting_for_first_check_after_dispatch(make_monitor, monkeypatch):
    from config.timezone import now_ist

    monitor = make_monitor()
    monitor.first_check_requested_at = now_ist()
    upsert_monitor(monitor, mirror=False)
    body = body_of(run_app())
    assert "WAITING FOR FIRST CHECK" in body
    assert "any moment now" in body
    assert "on next run" not in body


def test_rail_stops_promising_when_the_dispatch_never_landed(make_monitor):
    from config.timezone import now_ist

    monitor = make_monitor()
    monitor.first_check_requested_at = now_ist() - timedelta(minutes=20)
    upsert_monitor(monitor, mirror=False)
    body = body_of(run_app())
    assert "waiting on schedule" in body
    assert "scheduled worker will catch it" in body


def test_rail_distinguishes_blocked_from_error(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: MonitorState(
        last_check_at=at, last_success_at=at - timedelta(minutes=10), check_count=3,
        success_count=2, consecutive_errors=1, last_error="refused", last_error_kind="BLOCKED",
    )}, mirror=False)
    body = body_of(run_app())
    assert "BookMyShow refused the check" in body
    assert "BLOCKED" in body


def test_rail_shows_sold_out_state(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=2, success_count=2)
    for key in ("ALLU::Dolby Cinema", "AMB::Any format"):
        state.target(key).availability = Availability.SOLD_OUT
    save_state({monitor.id: state}, mirror=False)
    body = body_of(run_app())
    assert "SOLD OUT" in body and "watching for a reopen" in body


def test_live_card_showtimes_are_links(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=1, success_count=1)
    ts = state.target("ALLU::Dolby Cinema")
    ts.availability = Availability.AVAILABLE
    ts.since = at
    ts.time_labels = ["07:30 PM"]
    ts.time_links = [["07:30 PM", "https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"]]
    ts.booking_url = "https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"
    ts.date_code = "20260925"
    save_state({monitor.id: state}, mirror=False)
    body = body_of(run_app())
    assert 'class="tr-chip" href="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"' in body
    assert 'class="tr-book" href="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"' in body


def test_monitor_round_trips_first_check_requested_at(make_monitor, at):
    monitor = make_monitor()
    monitor.first_check_requested_at = at
    upsert_monitor(monitor, mirror=False)
    assert get_monitor(monitor.id).first_check_requested_at == at
    assert Monitor.from_dict({**monitor.to_dict(), "first_check_requested_at": None}).first_check_requested_at is None
