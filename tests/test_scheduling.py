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
from tests.conftest import APP_SCRIPT

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def run_app(**session):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
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
    from tests.conftest import DETAIL_REQUESTS_HYD, QUICKBOOK_HYD

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * DETAIL_REQUESTS_HYD)
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
    assert monitor.problem is None
    assert "First check is starting now" in body_of(app)
    assert not [b for b in app.button if b.key.startswith("prob_")]


def test_a_failed_dispatch_still_creates_the_monitor_and_shows_a_problem(seeded, monkeypatch):
    """Token can write Contents but the mirror made no commit and dispatch 403s:
    nothing started, and the UI must say PROBLEM — never 'waiting'."""
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    monkeypatch.setattr(store, "sync_from_github", lambda **k: False)
    monkeypatch.setattr(store, "dispatch_workflow",
                        lambda *a, **k: (False, "Could not start workflow: GitHub refused (403): "
                                                "the GH_TOKEN doesn't have permission for this."))
    app = start_from_ui(seeded)
    monitors = load_monitors()
    assert len(monitors) == 1 and monitors[0].is_running()
    monitor = monitors[0]
    assert monitor.first_check_requested_at is None
    assert monitor.problem and monitor.problem["kind"] == "FIRST_CHECK_NOT_STARTED"
    assert "403" in monitor.problem["message"]
    assert any("PROBLEM" in e.value for e in app.error)

    body = body_of(app)
    assert "PROBLEM OCCURRED" in body
    assert "WAITING FOR FIRST CHECK" not in body
    assert "not started" in body
    fresh = run_app()  # a new session sees the persisted problem too
    prob = next(b for b in fresh.button if b.key == f"prob_{monitor.id}")
    opened = body_of(prob.click().run())
    assert "first check could not be started" in opened
    assert "403" in opened and "permission" in opened


def test_a_mirror_commit_counts_as_started_even_when_dispatch_is_forbidden(seeded, monkeypatch):
    """The commit to data/monitors.json triggers the workflow by itself, so a
    PAT without Actions scope still gets an immediate first check."""
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    monkeypatch.setattr(store, "sync_from_github", lambda **k: False)
    monkeypatch.setattr(store, "dispatch_workflow", lambda *a, **k: (False, "403"))

    def fake_push(path, body, message):
        store._last_mirror = {"ok": True, "committed": path.name == "monitors.json",
                              "error": "", "path": path.name}
        return True

    monkeypatch.setattr(store, "push_to_github", fake_push)
    app = start_from_ui(seeded)
    monitor = load_monitors()[0]
    assert monitor.problem is None
    assert monitor.first_check_requested_at is not None
    assert "First check is starting now" in body_of(app)
    assert not [b for b in app.button if b.key.startswith("prob_")]


def test_workflow_is_triggered_by_the_monitors_commit():
    from pathlib import Path
    import yaml

    doc = yaml.safe_load(Path(".github/workflows/bookmyshow-monitor.yml").read_text(encoding="utf-8"))
    on = doc.get(True, doc.get("on"))
    assert on["push"]["paths"] == ["data/monitors.json"]
    assert on["push"]["branches"] == ["main"]


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


# ──────────────────────────────────────────────────────────────────────────
# Show dates: single date, date range, and shows outside them are ignored
# ──────────────────────────────────────────────────────────────────────────
from datetime import date as _date  # noqa: E402

from monitor.checker import check_monitor  # noqa: E402
from monitor.models import date_codes_between, describe_date_codes  # noqa: E402


def test_date_helpers():
    assert date_codes_between(_date(2026, 9, 25), _date(2026, 9, 28)) == [
        "20260925", "20260926", "20260927", "20260928"]
    assert date_codes_between(_date(2026, 9, 25), _date(2026, 9, 25)) == ["20260925"]
    assert describe_date_codes([]) == ""
    assert describe_date_codes(["20260925"]) == "25 Sep 2026"
    assert describe_date_codes(["20260925", "20260926", "20260927", "20260928"]) == "25–28 Sep 2026"
    assert describe_date_codes(["20260930", "20261001"]) == "30 Sep – 1 Oct 2026"
    assert describe_date_codes(["20260925", "20260927"]) == "25 Sep, 27 Sep 2026"


def _two_date_shows():
    return [
        {"venue_code": "ALLU", "venue_name": "Allu Cinemas", "area": "Attapur, Hyderabad",
         "time": "07:30 PM", "time_code": "1930", "fmt": "DOLBY CINEMA", "status": "3", "date": "20260925"},
        {"venue_code": "ALLU", "venue_name": "Allu Cinemas", "area": "Attapur, Hyderabad",
         "time": "09:45 PM", "time_code": "2145", "fmt": "DOLBY CINEMA", "status": "3", "date": "20260929"},
    ]


def test_single_show_date_ignores_other_dates(make_monitor, provider_factory, monkeypatch):
    monitor = make_monitor(targets=[__import__("monitor.models", fromlist=["TheatreTarget"]).TheatreTarget(
        "ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")])
    monitor.date_codes = ["20260929"]
    provider = provider_factory([build_payload(_two_date_shows())])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    outcome = check_monitor(monitor)
    assert outcome.ok
    result = outcome.results[0]
    assert result.availability is Availability.AVAILABLE
    assert result.time_labels == ["09:45 PM"]           # the 25 Sep show is not ours
    assert result.date_codes == ["20260929"]
    # …and the provider was asked for exactly the date we watch.
    assert [c["params"].get("dateCode") for c in provider.session.calls] == ["20260929"]


def test_show_date_range_keeps_only_dates_inside_it(make_monitor, provider_factory, monkeypatch):
    from monitor.models import TheatreTarget

    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")])
    monitor.date_codes = date_codes_between(_date(2026, 9, 25), _date(2026, 9, 28))
    # The fake session answers the same two-date listing for each of the 4 date queries.
    provider = provider_factory([build_payload(_two_date_shows())] * 4)
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    result = check_monitor(monitor).results[0]
    assert result.availability is Availability.AVAILABLE
    assert result.date_codes == ["20260925"]             # 29 Sep is outside 25–28
    assert result.time_labels == ["07:30 PM"]
    assert len(provider.session.calls) == 4


def test_no_shows_inside_the_range_is_not_available(make_monitor, provider_factory, monkeypatch):
    from monitor.models import TheatreTarget

    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")])
    monitor.date_codes = ["20261001"]
    provider = provider_factory([build_payload(_two_date_shows())])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    result = check_monitor(monitor).results[0]
    assert result.availability is Availability.SHOW_NOT_AVAILABLE
    assert "dates you're watching" in result.detail


def test_multi_date_results_label_each_showtime_with_its_date(make_monitor, provider_factory, monkeypatch):
    from monitor.models import TheatreTarget

    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")])
    monitor.date_codes = ["20260925", "20260929"]
    provider = provider_factory([build_payload(_two_date_shows())] * 2)
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    result = check_monitor(monitor).results[0]
    assert result.date_codes == ["20260925", "20260929"]
    assert result.time_labels == ["25 Sep · 07:30 PM", "29 Sep · 09:45 PM"]
    assert [u for _, u in result.time_links] == [
        f"{monitor.movie.source_url}/20260925", f"{monitor.movie.source_url}/20260929"]


def test_email_shows_every_available_date_and_the_watched_range(make_monitor, at):
    from monitor.changes import Change, ChangeKind
    from notifications.email import render_change

    monitor = make_monitor()
    monitor.date_codes = date_codes_between(_date(2026, 9, 25), _date(2026, 9, 28))
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=monitor.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title="X",
                    previous=Availability.NOT_BOOKABLE, current=Availability.AVAILABLE,
                    date_code="20260925", date_codes=["20260925", "20260926"],
                    booking_url=monitor.movie.source_url,
                    time_labels=["25 Sep · 07:30 PM", "26 Sep · 09:45 PM"], detected_at=at)
    _, html, text = render_change(monitor, change)
    assert "25 September 2026 · 26 September 2026" in html
    assert ">Dates<" in html
    assert "Watching shows on 25–28 Sep 2026 only." in html
    assert "Dates: 25 September 2026 · 26 September 2026" in text
    assert "Watching shows on: 25–28 Sep 2026" in text


def test_ui_stores_a_single_show_date_on_the_monitor(seeded, dispatches):
    app = run_app(step=5, location="hyderabad", movie_id=seeded,
                  theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]}, date_mode="single")
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.date_input(key="show_date_single").set_value(_date(2026, 9, 25)).run()
    assert "Watching shows on 25 Sep 2026" in body_of(app)
    app.button(key="start").click().run()
    monitor = load_monitors()[0]
    assert monitor.date_codes == ["20260925"]
    assert monitor.date_range_label == "25 Sep 2026"


def test_ui_stores_a_show_date_range_on_the_monitor(seeded, dispatches):
    app = run_app(step=5, location="hyderabad", movie_id=seeded,
                  theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]}, date_mode="range")
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.date_input(key="show_date_range").set_value((_date(2026, 9, 25), _date(2026, 9, 28))).run()
    assert "Watching shows on 25–28 Sep 2026 (4 days)" in body_of(app)
    app.button(key="start").click().run()
    monitor = load_monitors()[0]
    assert monitor.date_codes == ["20260925", "20260926", "20260927", "20260928"]
    # Monitoring duration is a separate thing and is untouched.
    assert monitor.monitor_until.date() != _date(2026, 9, 28) or monitor.monitor_until.hour == 23
    # The rail shows the dates being watched.
    assert "25–28 Sep 2026" in body_of(run_app())


def test_ui_default_watches_every_date(seeded, dispatches):
    start_from_ui(seeded)
    assert load_monitors()[0].date_codes == []


# ──────────────────────────────────────────────────────────────────────────
# Cards never leak markup; the exact-show path; email failure is a PROBLEM
# ──────────────────────────────────────────────────────────────────────────
def _rendered_markdown(monkeypatch, render):
    """Capture what a component hands to st.markdown."""
    import streamlit as st
    from ui import components as C

    seen: list[str] = []
    monkeypatch.setattr(st, "markdown", lambda body, **kw: seen.append(body))
    render(C)
    return "\n".join(seen)


def test_active_card_has_no_blank_or_indented_lines_that_markdown_would_render_as_code(
        make_monitor, at, monkeypatch):
    monitor = make_monitor()  # no show dates → the optional metric is empty
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=1, success_count=1)
    out = _rendered_markdown(monkeypatch, lambda C: C.active_monitor_card(monitor, state, at=at))
    assert "</div>" in out                      # it is HTML…
    for line in out.splitlines():
        assert line.strip(), "blank line inside an HTML block"
        assert not line.startswith("    "), f"indented line would become a code block: {line!r}"
    # …and the same holds with show dates set (both branches of the optional slot).
    monitor.date_codes = ["20260915"]
    out = _rendered_markdown(monkeypatch, lambda C: C.active_monitor_card(monitor, state, at=at))
    assert "15 Sep 2026" in out and all(l.strip() and not l.startswith("    ") for l in out.splitlines())


def test_raw_markup_is_not_visible_in_the_rail(make_monitor, at):
    """End to end through Streamlit: the card text must not contain literal tags."""
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: MonitorState(last_check_at=at, last_success_at=at, check_count=3,
                                         success_count=3)}, mirror=False)
    app = run_app()
    # A code block is how the leak showed up; Streamlit renders it as a distinct element.
    assert not app.code, [c.value for c in app.code]
    for block in app.markdown:
        assert "&lt;/div&gt;" not in block.value


def test_exact_show_on_the_selected_date_is_detected_and_emailed_on_the_first_tick(
        make_monitor, provider_factory, monkeypatch, at):
    """Movie + theatre + format + a single show date → AVAILABLE and one email, immediately."""
    from monitor.models import TheatreTarget

    monitor = make_monitor(interval=10, until=at + timedelta(hours=6),
                           targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")])
    monitor.date_codes = ["20260925"]
    upsert_monitor(monitor, mirror=False)
    provider = _endless_provider(provider_factory, ALLU_LIVE)
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    sent = []
    clock = Clock(at)
    loop = worker.run_loop(max_minutes=1, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=clock.sleep, notifier=lambda m, c: sent.append(c))
    assert loop.reports[0].checked == [monitor.id]       # first tick, no waiting
    assert loop.reports[0].started_at == at
    assert len(sent) == 1 and sent[0].date_codes == ["20260925"]
    ts = load_state()[monitor.id].targets["ALLU::Dolby Cinema"]
    assert ts.availability is Availability.AVAILABLE and ts.notified_at == at


def test_email_failure_is_persisted_and_shown_as_a_problem(make_monitor, provider_factory, monkeypatch, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))

    def broken(m, change):
        raise RuntimeError("Gmail rejected the login (535)")

    run_once(at=at, mirror=False, notifier=broken)
    state = load_state()[monitor.id]
    assert "Gmail rejected the login" in state.last_email_error
    assert state.targets["ALLU::Dolby Cinema"].notified_at is None   # not marked delivered

    app = run_app()
    body = body_of(app)
    assert "WAITING FOR FIRST CHECK" not in body
    prob = next(b for b in app.button if b.key == f"prob_{monitor.id}")
    opened = body_of(prob.click().run())
    assert "Email could not be sent" in opened and "Gmail rejected the login" in opened

    # A later successful send clears it.
    monkeypatch.setattr("monitor.checker.get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE)]))
    run_once(at=at + timedelta(minutes=10), mirror=False, notifier=lambda m, c: None)
    assert load_state()[monitor.id].last_email_error == ""
    assert not [b for b in run_app().button if b.key.startswith("prob_")]


def test_checker_explains_why_shows_were_filtered_out(make_monitor, provider_factory, monkeypatch, capsys):
    from monitor.models import TheatreTarget

    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "IMAX")])
    monitor.date_codes = ["20260929"]
    provider = provider_factory([build_payload(_two_date_shows())])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    result = check_monitor(monitor).results[0]
    log = capsys.readouterr().out
    assert "ignoring 1 show(s) on ['20260925']" in log          # wrong date, said out loud
    assert "none in 'IMAX'" in log and "Dolby Cinema" in log       # wrong format, with what was listed
    assert result.availability is Availability.SHOW_NOT_AVAILABLE
    assert "none in IMAX" in result.detail
