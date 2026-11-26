"""The worker outlives a failing store, and an alert is recorded the moment
it is sent (audit REL-03).

Before this, one Firestore call that failed — a timeout, a 429, a 503 —
anywhere in a tick raised straight out of the fifty-minute segment. The
segment ended, the handover to the next one was never reached, and
monitoring waited for GitHub's schedule (hours apart, as measured). Emails
were sent during the tick while their "sent" mark was saved only at its
end, so a failure in between sent them again.

Pinned here, layer by layer:

1. ``FirestoreClient`` retries what passes (network, 429, 5xx) a bounded
   number of times with growing, jittered waits — and nothing else.
2. ``run_once`` isolates each monitor, saves state as soon as an alert has
   gone out, and never lets the activity log cost a check.
3. ``run_loop`` survives a tick that raises, backs off, remembers what it
   could not save, stops on a refusal no retry will fix, and attempts the
   handover on every ending that is not "nothing left to do".

Delivery is at-least-once, and the tests say so: a segment that dies with
unsaved state can still repeat an alert.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import requests

import run_monitor
from config import firestore as fs
from monitor import checker, worker
from monitor import state as state_mod
from monitor.checker import RunReport, run_once
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
from tests.test_isolation import PROJECT
from tests.test_scheduling import Clock
from tests.test_worker_reads import OWNER, firestore_worker  # noqa: F401 - the fixture

OK = (200, {})


# ──────────────────────────────────────────────────────────────────────────
# 1 · The client: bounded retries for what passes, none for what does not
# ──────────────────────────────────────────────────────────────────────────
class Script:
    """A transport that answers from a list: a ``(status, payload)``, or an
    exception to raise. The last answer repeats."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, url, token, body):
        self.calls.append((method, url.rsplit("/", 1)[-1], body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(script, token=lambda: "admin") -> fs.FirestoreClient:
    return fs.FirestoreClient(PROJECT, token, transport=script)


def error(status, message="nope"):
    return (status, {"error": {"message": message}})


@pytest.mark.parametrize("failure", [
    error(429, "Quota exceeded"), error(500), error(502), error(503, "unavailable"), error(504),
    requests.ConnectionError("reset"), requests.Timeout("timed out"), OSError("network down"),
], ids=["429", "500", "502", "503", "504", "connection reset", "timeout", "os error"])
def test_a_failure_that_passes_is_retried_and_the_call_succeeds(failure, retry_waits):
    script = Script(failure, failure, OK)
    client(script).set("monitor_state", "m1", {"check_count": 1})
    assert len(script.calls) == 3
    assert len(retry_waits) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 412])
def test_a_refusal_of_the_request_itself_is_not_retried(status, retry_waits):
    script = Script(error(status, "PERMISSION_DENIED"), OK)
    with pytest.raises(fs.FirestoreError) as exc:
        client(script).set("monitor_state", "m1", {"check_count": 1})
    assert exc.value.status == status and exc.value.transient is False
    assert len(script.calls) == 1 and retry_waits == []


def test_retries_are_bounded(retry_waits):
    script = Script(error(503))
    with pytest.raises(fs.FirestoreError) as exc:
        client(script).query("monitors")
    assert exc.value.status == 503 and exc.value.transient is True
    assert len(script.calls) == fs.MAX_ATTEMPTS == 4
    assert len(retry_waits) == fs.MAX_ATTEMPTS - 1


def test_a_network_failure_that_never_clears_is_a_transient_error_without_a_status(retry_waits):
    script = Script(requests.ConnectionError("reset"))
    with pytest.raises(fs.FirestoreError) as exc:
        client(script).query("monitors")
    assert exc.value.status == 0 and exc.value.transient
    assert "ConnectionError" in str(exc.value) and "reset" not in str(exc.value)
    assert len(script.calls) == fs.MAX_ATTEMPTS


def test_the_waits_grow_and_carry_jitter(retry_waits):
    with pytest.raises(fs.FirestoreError):
        client(Script(error(503))).query("monitors")
    for attempt, waited in enumerate(retry_waits, 1):
        floor = fs.BACKOFF_BASE * 2 ** (attempt - 1)
        assert floor <= waited <= floor + fs.BACKOFF_JITTER
    assert retry_waits == sorted(retry_waits)
    # Jitter: two callers failing together do not come back together.
    assert len({fs.backoff_delay(1) for _ in range(20)}) > 1
    # Bounded in time as well as in count: well under ten seconds in all.
    assert sum(retry_waits) < 5


def test_a_retried_write_is_the_same_write(retry_waits):
    """Safe to repeat: whole-document sets with no precondition."""
    script = Script(error(503), OK)
    client(script).commit([("monitor_state", "m1", {"check_count": 2}), ("monitors", "m2", None)])
    assert script.calls[0] == script.calls[1]


def test_a_missing_document_is_still_none_and_never_retried(retry_waits):
    script = Script(error(404))
    assert client(script).get("monitors", "nope") is None
    assert len(script.calls) == 1 and retry_waits == []


def test_a_credential_that_cannot_be_minted_for_a_moment_is_retried_too(retry_waits):
    """The service-account token exchange is a network call of its own."""
    attempts = []

    def token():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("TransportError: secret-looking detail")
        return "admin"

    script = Script(OK)
    client(script, token).set("monitor_state", "m1", {"a": 1})
    assert len(attempts) == 3 and len(script.calls) == 1


def test_a_credential_failure_never_puts_its_text_in_the_error(retry_waits):
    def token():
        raise RuntimeError("secret-looking detail")

    with pytest.raises(fs.FirestoreError) as exc:
        client(Script(OK), token).query("monitors")
    assert "secret-looking" not in str(exc.value) and "RuntimeError" in str(exc.value)


def test_the_retry_is_logged_without_the_message_body(capsys, retry_waits):
    client(Script(error(503, "internal detail"), OK)).query("monitors")
    out = capsys.readouterr().out
    assert "HTTP 503 — retry 1/3" in out


# ──────────────────────────────────────────────────────────────────────────
# Fixtures for the worker: a real tick against the in-memory Firestore
# ──────────────────────────────────────────────────────────────────────────
def _film(code: str) -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=code, title=f"Film {code}", region_code="HYD",
                    region_slug="hyderabad", city="Hyderabad")


def _monitor(code: str, at, *, interval: int = 10) -> Monitor:
    return Monitor(movie=_film(code), targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=interval, monitor_until=at + timedelta(days=2),
                   notify_email="w@example.com", owner_uid=OWNER)


def _bookable(movie, date_codes=None):
    show = Showtime("ALLU", "ALLU Cinemas", "S1", "20260925", "07:30 PM", "1930", "Dolby Cinema",
                    Availability.AVAILABLE)
    return checker.Listing(snapshot=Snapshot(movie=movie, venues=[Venue("ALLU", "ALLU Cinemas")],
                                             showtimes=[show]))


class Outage:
    """Wraps the store's transport: while ``down`` is set, every request it
    matches answers ``status`` — after the real store has *not* been called."""

    def __init__(self, inner):
        self.inner, self.down, self.status = inner, None, 503
        self.refused = 0

    def __call__(self, method, url, token, body):
        if self.down is not None and self.down(method, url, body):
            self.refused += 1
            return self.status, {"error": {"message": "UNAVAILABLE"}}
        return self.inner(method, url, token, body)


def writes_to(collection):
    def match(method, url, body):
        return url.endswith(":commit") and any(
            f"/{collection}/" in (w.get("delete") or w.get("update", {}).get("name", ""))
            for w in body["writes"])
    return match


def any_write(method, url, body):
    return url.endswith(":commit")


def anything(method, url, body):
    return True


@pytest.fixture
def live(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """The Firestore worker with a bookable listing, an inbox and an outage switch."""
    w = firestore_worker
    monkeypatch.setattr(checker, "fetch_listing", _bookable)
    outage = Outage(state_mod._transport)
    monkeypatch.setattr(state_mod, "_transport", outage)
    monkeypatch.setattr(state_mod, "_admin", None)
    w.outage = outage
    w.inbox = []
    w.notify = lambda monitor, change: w.inbox.append((monitor.id, change.kind.value))
    w.stored = lambda monitor: MonitorState.from_dict(w.docs["monitor_state"][monitor.id])
    return w


def _target(state: MonitorState):
    return next(iter(state.targets.values()))


# ──────────────────────────────────────────────────────────────────────────
# 2 · The tick: one monitor's failure is its own, and a sent alert is saved
# ──────────────────────────────────────────────────────────────────────────
def test_one_monitor_failing_does_not_stop_the_others(live, monkeypatch, capsys):
    a = live.seed(_monitor("ET1", live.now), checked=False)
    b = live.seed(_monitor("ET2", live.now), checked=False)
    real = checker.outcome_for

    def explode_for_a(monitor, listing, *, at=None):
        if monitor.id == a.id:
            raise RuntimeError("a parser surprise")
        return real(monitor, listing, at=at)

    monkeypatch.setattr(checker, "outcome_for", explode_for_a)
    report = run_once(at=live.now, force=True, notifier=live.notify)

    assert report.failed == [a.id] and report.checked == [b.id]
    assert live.inbox == [(b.id, "TICKETS_LIVE")]
    assert _target(live.stored(b)).notified_availability is Availability.AVAILABLE
    out = capsys.readouterr().out
    assert f"monitor {a.id[:8]} could not be processed this tick (RuntimeError)" in out
    assert "a parser surprise" not in out


def test_a_failing_notifier_for_one_monitor_does_not_stop_the_next(live):
    a = live.seed(_monitor("ET1", live.now), checked=False)
    b = live.seed(_monitor("ET2", live.now), checked=False)

    def notify(monitor, change):
        if monitor.id == a.id:
            raise RuntimeError("smtp down")
        live.inbox.append((monitor.id, change.kind.value))

    report = run_once(at=live.now, force=True, notifier=notify)
    assert sorted(report.checked) == sorted([a.id, b.id])
    assert live.inbox == [(b.id, "TICKETS_LIVE")] and report.emails_sent == 1


def test_an_email_that_fails_is_not_marked_sent_and_goes_out_once_when_it_can(live):
    m = live.seed(_monitor("ET1", live.now), checked=False)
    mailbox = {"up": False}

    def notify(monitor, change):
        if not mailbox["up"]:
            raise RuntimeError("smtp down")
        live.inbox.append((monitor.id, change.kind.value))

    report = run_once(at=live.now, force=True, notifier=notify)
    assert report.emails_sent == 0 and len(report.email_errors) == 1
    stored = live.stored(m)
    assert _target(stored).availability is Availability.AVAILABLE
    assert _target(stored).notified_availability is not Availability.AVAILABLE
    assert _target(stored).notified_at is None and stored.last_email_error

    mailbox["up"] = True
    for minutes in (10, 20, 30):
        run_once(at=live.now + timedelta(minutes=minutes), force=True, notifier=notify)
    assert live.inbox == [(m.id, "TICKETS_LIVE")], "retried until sent, then never again"
    assert live.stored(m).last_email_error == ""


def test_the_sent_mark_reaches_the_store_before_anything_else_can_fail(live):
    """History is written after the alert. With the history collection
    refusing every write, the tick still finishes and the alert is on record."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.down = writes_to("history")

    report = run_once(at=live.now, force=True, notifier=live.notify)

    assert report.emails_sent == 1 and report.checked == [m.id]
    assert _target(live.stored(m)).notified_at is not None
    assert not live.docs.get("history")

    live.outage.down = None
    run_once(at=live.now + timedelta(minutes=10), force=True, notifier=live.notify)
    assert len(live.inbox) == 1


def test_each_alert_is_recorded_in_the_store_before_it_is_sent(live):
    """Two monitors. The store goes down the moment the second alert has
    been sent — both "sent" marks are already there (write-ahead), so no
    later run, which has no memory of this one, sends either again."""
    a = live.seed(_monitor("ET1", live.now), checked=False)
    b = live.seed(_monitor("ET2", live.now), checked=False)

    def notify(monitor, change):
        live.inbox.append((monitor.id, change.kind.value))
        if len(live.inbox) == 2:
            live.outage.down = any_write

    run_once(at=live.now, force=True, notifier=notify)
    for monitor in (a, b):
        assert _target(live.stored(monitor)).notified_availability is Availability.AVAILABLE

    live.outage.down = None
    run_once(at=live.now + timedelta(minutes=10), force=True, notifier=notify)
    assert len(live.inbox) == 2


def test_a_store_that_cannot_record_an_alert_holds_it_back_and_sends_it_once(live):
    """The store refuses every write: the alert is not sent (it could not be
    recorded first), stays owed, and goes out exactly once when it can be."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    carry: dict[str, MonitorState] = {}
    live.outage.down = any_write

    with pytest.raises(fs.FirestoreError):
        run_once(at=live.now, force=True, notifier=live.notify, carry=carry)
    assert live.inbox == []
    assert m.id not in live.docs.get("monitor_state", {}), "nothing reached the store"
    assert _target(carry[m.id]).notified_availability is not Availability.AVAILABLE

    with pytest.raises(fs.FirestoreError):
        run_once(at=live.now + timedelta(minutes=10), force=True, notifier=live.notify, carry=carry)
    assert live.inbox == []

    live.outage.down = None
    run_once(at=live.now + timedelta(minutes=20), force=True, notifier=live.notify, carry=carry)
    assert live.inbox == [(m.id, "TICKETS_LIVE")] and carry == {}
    assert _target(live.stored(m)).notified_availability is Availability.AVAILABLE


@pytest.mark.parametrize("when", ["before the send", "after the send"])
def test_single_pass_runs_never_repeat_an_alert_when_the_store_fails(live, when):
    """What the five-minute workflow runs: separate processes, no ``carry``.
    Whether the store fails before or just after the email, the alert goes
    out exactly once over the following runs."""
    m = live.seed(_monitor("ET1", live.now), checked=False)

    def notify(monitor, change):
        live.inbox.append((monitor.id, change.kind.value))
        live.outage.down = any_write                 # fails right after sending

    if when == "before the send":
        live.outage.down = any_write
        with pytest.raises(fs.FirestoreError):
            run_once(at=live.now, force=True, notifier=live.notify)
    else:
        try:
            run_once(at=live.now, force=True, notifier=notify)
        except fs.FirestoreError:
            pass
    live.outage.down = None
    for minutes in (10, 20, 30):
        run_once(at=live.now + timedelta(minutes=minutes), force=True, notifier=live.notify)
    assert live.inbox == [(m.id, "TICKETS_LIVE")]


def test_a_failed_save_does_not_corrupt_any_other_monitors_state(live):
    """One already-announced monitor, one new. The save fails; afterwards
    each record is exactly what a clean run would have left."""
    old = live.seed(_monitor("ET1", live.now), checked=False)
    run_once(at=live.now, force=True, notifier=live.notify)
    before = dict(live.docs["monitor_state"][old.id])
    new = live.seed(_monitor("ET2", live.now), checked=False)
    carry: dict[str, MonitorState] = {}

    live.outage.down = any_write
    with pytest.raises(fs.FirestoreError):
        run_once(at=live.now + timedelta(minutes=10), force=True, notifier=live.notify, carry=carry)
    assert live.docs["monitor_state"][old.id] == before, "a failed commit writes nothing"

    live.outage.down = None
    run_once(at=live.now + timedelta(minutes=20), force=True, notifier=live.notify, carry=carry)
    assert live.inbox == [(old.id, "TICKETS_LIVE"), (new.id, "TICKETS_LIVE")]
    for monitor in (old, new):
        stored = live.stored(monitor)
        assert _target(stored).availability is Availability.AVAILABLE
        assert _target(stored).notified_availability is Availability.AVAILABLE
        assert live.docs["monitor_state"][monitor.id][fs.OWNER] == OWNER
    assert live.stored(old).check_count == 3 and live.stored(new).check_count == 2


def test_repeated_ticks_never_send_the_same_alert_twice(live):
    m = live.seed(_monitor("ET1", live.now), checked=False)
    for minutes in range(0, 60, 10):
        run_once(at=live.now + timedelta(minutes=minutes), force=True, notifier=live.notify)
    assert live.inbox == [(m.id, "TICKETS_LIVE")]


def test_a_brief_outage_inside_a_tick_is_absorbed_by_the_retries(live, retry_waits):
    """Three refused requests, then the store answers: the tick never knew."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    budget = {"left": 3}

    def first_three(method, url, body):
        budget["left"] -= 1
        return budget["left"] >= 0

    live.outage.down = first_three
    report = run_once(at=live.now, force=True, notifier=live.notify)
    assert report.checked == [m.id] and live.inbox == [(m.id, "TICKETS_LIVE")]
    assert live.outage.refused == 3 and len(retry_waits) == 3


# ──────────────────────────────────────────────────────────────────────────
# 3 · The segment: a failed tick is not the end of it
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def dispatched(monkeypatch):
    calls: list[dict] = []

    def dispatch(workflow, inputs=None, ref=""):
        calls.append({"workflow": workflow, **(inputs or {})})
        return True, "Workflow started."

    monkeypatch.setattr(worker, "dispatch_workflow", dispatch)
    return calls


def _loop(live, *, minutes=12, chain=True):
    clock = Clock(live.now)
    loop = worker.run_loop(max_minutes=minutes, poll_seconds=30, use_git=False, chain=chain,
                           clock=clock, sleeper=clock.sleep, notifier=live.notify)
    return loop, clock


@pytest.mark.parametrize("status", [429, 500, 503], ids=["429", "500", "503"])
def test_the_segment_survives_a_store_outage_and_hands_over(live, dispatched, status):
    """Firestore answers nothing but errors for the whole segment. It used to
    end on the first one, with nobody dispatched to take over."""
    live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.status = status
    live.outage.down = anything

    loop, _ = _loop(live)

    assert loop.failed_ticks == loop.ticks >= 2
    assert loop.handed_over is True and len(dispatched) == 1
    assert loop.stopped_reason == ""           # it ran its time, like any other segment
    assert live.inbox == []


def test_a_temporary_network_failure_is_survived_the_same_way(live, dispatched, monkeypatch):
    live.seed(_monitor("ET1", live.now), checked=False)

    def unplugged(method, url, token, body):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(state_mod, "_transport", unplugged)
    monkeypatch.setattr(state_mod, "_admin", None)
    loop, _ = _loop(live)
    assert loop.failed_ticks == loop.ticks >= 2 and loop.handed_over is True


def test_the_segment_recovers_when_the_store_does(live, dispatched):
    """Down for the first three minutes, then back: the monitor is checked,
    the alert goes out once, and the segment ends normally."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.down = anything
    clock = Clock(live.now)

    def sleeper(seconds):
        clock.sleep(seconds)
        if clock.now - live.now >= timedelta(minutes=3):
            live.outage.down = None

    loop = worker.run_loop(max_minutes=20, poll_seconds=30, use_git=False, chain=True,
                           clock=clock, sleeper=sleeper, notifier=live.notify)
    assert loop.failed_ticks >= 1 and loop.checks >= 1
    assert live.inbox == [(m.id, "TICKETS_LIVE")]
    assert _target(live.stored(m)).notified_availability is Availability.AVAILABLE
    assert loop.handed_over is True


def test_failed_ticks_back_off_instead_of_hammering_the_store(live, dispatched):
    live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.down = anything
    clock, naps = Clock(live.now), []

    def sleeper(seconds):
        naps.append(seconds)
        clock.sleep(seconds)

    worker.run_loop(max_minutes=12, poll_seconds=30, use_git=False, chain=False,
                    clock=clock, sleeper=sleeper, notifier=live.notify)
    assert naps[:4] == [30, 60, 120, 120], "doubling, capped at four poll intervals"


def test_a_store_down_for_the_whole_segment_holds_the_alert_back(live, dispatched):
    """No write gets through, so nothing is sent: an alert that cannot be
    recorded first is never sent (it would be repeated). The segment says
    what it could not save and hands over; the alert is still owed."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.down = any_write

    loop, _ = _loop(live, minutes=45)

    assert live.inbox == []
    assert loop.failed_ticks == loop.ticks >= 3
    assert m.id not in live.docs.get("monitor_state", {})
    assert loop.handed_over is True


def test_the_parked_state_also_stops_the_listing_being_re_read_every_tick(live, dispatched, monkeypatch):
    """Without it, a monitor whose check could not be saved looks unchecked,
    and a store outage would turn into a BookMyShow request every tick."""
    live.seed(_monitor("ET1", live.now), checked=False)
    reads = []
    monkeypatch.setattr(checker, "fetch_listing", lambda movie, date_codes=None: (
        reads.append(1), _bookable(movie))[1])
    live.outage.down = any_write

    loop, _ = _loop(live, minutes=9)       # shorter than the 10-minute interval
    assert loop.ticks >= 3 and len(reads) == 1


def test_a_save_that_fails_once_is_completed_by_the_next_tick(live, dispatched):
    m = live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.down = any_write
    clock = Clock(live.now)

    def sleeper(seconds):
        clock.sleep(seconds)
        live.outage.down = None            # back after the first failed tick

    # The held-back alert goes out at the monitor's next check (10 minutes).
    loop = worker.run_loop(max_minutes=12, poll_seconds=30, use_git=False, chain=False,
                           clock=clock, sleeper=sleeper, notifier=live.notify)
    assert loop.failed_ticks == 1 and live.inbox == [(m.id, "TICKETS_LIVE")]
    stored = live.stored(m)
    assert _target(stored).notified_availability is Availability.AVAILABLE and stored.check_count == 2


@pytest.mark.parametrize("status", [401, 403], ids=["401", "403"])
def test_a_store_that_refuses_the_worker_stops_the_segment_without_a_successor(live, dispatched, status,
                                                                             capsys):
    """A revoked or wrong credential fails the same way every time. Retrying
    it for fifty minutes and then dispatching another segment to do the same
    would never end."""
    live.seed(_monitor("ET1", live.now), checked=False)
    live.outage.status = status
    live.outage.down = anything

    loop, _ = _loop(live, minutes=50)

    assert loop.failed_ticks == loop.ticks == worker.MAX_PERMANENT_FAILURES
    assert loop.stopped_reason == "store refused the worker"
    assert loop.handed_over is False and dispatched == []
    assert live.outage.refused == loop.ticks + 1, "one request per tick (plus start-up): never retried"
    assert "::error::" in capsys.readouterr().out


def test_that_ending_is_a_red_run(monkeypatch):
    stopped = worker.LoopReport(started_at=None, stopped_reason="store refused the worker")
    monkeypatch.setattr(run_monitor, "run_loop", lambda **kw: stopped)
    assert run_monitor.main(["--loop", "--no-git", "--no-chain"]) == 1
    fine = worker.LoopReport(started_at=None, failed_ticks=2)
    monkeypatch.setattr(run_monitor, "run_loop", lambda **kw: fine)
    assert run_monitor.main(["--loop", "--no-git", "--no-chain"]) == 0


def test_passing_failures_between_refusals_do_not_add_up_to_a_stop(live, dispatched, monkeypatch):
    kinds = iter([403, 503, 403, 503, 403, 503])

    def tick(**kw):
        raise fs.FirestoreError("x", next(kinds, 503))

    monkeypatch.setattr(worker, "run_once", tick)
    loop, _ = _loop(live)
    assert loop.stopped_reason == "" and loop.handed_over is True


def test_an_exception_nobody_foresaw_is_survived_and_logged_by_type_only(live, dispatched, monkeypatch,
                                                                       capsys):
    def tick(**kw):
        raise KeyError("uid-of-somebody@example.com")

    monkeypatch.setattr(worker, "run_once", tick)
    loop, _ = _loop(live)
    out = capsys.readouterr().out
    assert loop.failed_ticks == loop.ticks >= 2 and loop.handed_over is True
    assert "failed (KeyError)" in out and "example.com" not in out


def test_a_good_tick_after_a_bad_one_reads_state_afresh(live, monkeypatch):
    """The in-memory hint is only trusted while the segment's own writes
    are known to have landed."""
    seen = []
    real = worker.run_once

    def tick(**kw):
        seen.append(kw["known_state"])
        if len(seen) == 2:
            raise fs.FirestoreError("x", 503)
        return real(**kw)

    live.seed(_monitor("ET1", live.now), checked=False)
    monkeypatch.setattr(worker, "run_once", tick)
    _loop(live, minutes=3, chain=False)
    assert seen[0] is None and seen[1] is not None and seen[2] is None


def test_a_store_that_is_down_at_start_up_does_not_end_the_segment(live, dispatched):
    """The start-up report reads the monitors once. That read failing used
    to be fatal before a single tick had run."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    budget = {"left": fs.MAX_ATTEMPTS}

    def at_start_up(method, url, body):
        budget["left"] -= 1
        return budget["left"] >= 0

    live.outage.down = at_start_up
    loop, _ = _loop(live, minutes=3)
    assert loop.backend == "firestore" and loop.failed_ticks == 0
    assert live.inbox == [(m.id, "TICKETS_LIVE")]


def test_a_misconfigured_worker_is_still_fatal_and_dispatches_nothing(monkeypatch, dispatched):
    """Refusing to monitor production against the JSON files is a decision,
    not a failure to ride out."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    with pytest.raises(worker.WorkerConfigError):
        worker.run_loop(max_minutes=1, use_git=False, chain=True, notifier=lambda m, c: None)
    assert dispatched == []


def test_a_cancelled_run_does_not_start_its_own_replacement(live, dispatched, monkeypatch):
    def tick(**kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "run_once", tick)
    with pytest.raises(KeyboardInterrupt):
        _loop(live)
    assert dispatched == []


def test_an_idle_segment_still_ends_quietly(live, dispatched):
    loop, _ = _loop(live)
    assert loop.stopped_reason == "nothing running" and dispatched == [] and loop.failed_ticks == 0


# ──────────────────────────────────────────────────────────────────────────
# 4 · The handover itself
# ──────────────────────────────────────────────────────────────────────────
def test_a_handover_that_fails_is_retried(monkeypatch, retry_waits):
    answers = iter([(False, "GitHub answered with an error"), (True, "Workflow started.")])
    calls = []
    monkeypatch.setattr(worker, "dispatch_workflow",
                        lambda wf, inputs=None, ref="": (calls.append(inputs), next(answers))[1])
    assert worker.hand_over() is True
    assert len(calls) == 2 and retry_waits == [worker.HANDOVER_BACKOFF]
    assert calls[0] == {"force": "false", "dry_run": "false", "monitor_id": ""}


def test_a_handover_that_keeps_failing_gives_up_and_says_so(monkeypatch, retry_waits, capsys):
    calls = []
    monkeypatch.setattr(worker, "dispatch_workflow",
                        lambda wf, inputs=None, ref="": (calls.append(1), (False, "No GH_TOKEN configured."))[1])
    assert worker.hand_over() is False
    assert len(calls) == worker.HANDOVER_ATTEMPTS == 3
    assert retry_waits == [worker.HANDOVER_BACKOFF, worker.HANDOVER_BACKOFF * 2]
    out = capsys.readouterr().out
    assert "::warning::next segment NOT dispatched after 3 attempt(s)" in out


def test_a_handover_that_raises_is_contained(monkeypatch, retry_waits):
    def dispatch(wf, inputs=None, ref=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "dispatch_workflow", dispatch)
    assert worker.hand_over() is False


def test_a_successful_handover_is_one_dispatch_exactly(live, dispatched):
    """No duplicate dispatch on the ordinary path."""
    live.seed(_monitor("ET1", live.now, interval=30), checked=False)
    loop, _ = _loop(live, minutes=3)
    assert loop.handed_over is True and len(dispatched) == 1


def test_a_failed_handover_is_reported_on_the_segment(live, monkeypatch, retry_waits):
    live.seed(_monitor("ET1", live.now), checked=False)
    monkeypatch.setattr(worker, "dispatch_workflow", lambda wf, inputs=None, ref="": (False, "nope"))
    loop, _ = _loop(live, minutes=3)
    assert loop.handed_over is False and "handed_over=False" in loop.summary()


# ──────────────────────────────────────────────────────────────────────────
# 5 · What a failure is called
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("exc,permanent", [
    (fs.FirestoreError("x", 403), True), (fs.FirestoreError("x", 401), True),
    (fs.FirestoreError("x", 400), True), (fs.FirestoreError("x", 429), False),
    (fs.FirestoreError("x", 503), False), (fs.FirestoreError("unreachable"), False),
    (worker.WorkerConfigError("x"), True), (RuntimeError("x"), False), (KeyError("x"), False),
])
def test_only_a_failure_that_says_so_is_permanent(exc, permanent):
    assert worker.is_permanent(exc) is permanent


def test_a_failure_is_described_by_type_and_status_only():
    assert worker.describe_failure(fs.FirestoreError("doc projects/p/…/monitors/abc", 503)) \
        == "FirestoreError HTTP 503"
    assert worker.describe_failure(RuntimeError("anything at all")) == "RuntimeError"


def test_run_report_is_unchanged_for_a_clean_tick(live):
    report = run_once(at=live.now, notifier=live.notify)
    assert isinstance(report, RunReport) and report.failed == [] and report.checked == []


# ──────────────────────────────────────────────────────────────────────────
# 6 · Two edges found in review
# ──────────────────────────────────────────────────────────────────────────
class RefreshError(Exception):
    """Stands in for ``google.auth.exceptions.RefreshError`` (matched by name)."""

    def __init__(self, *args, retryable=False):
        super().__init__(*args)
        self.retryable = retryable


class TransportError(Exception):
    """Stands in for ``google.auth.exceptions.TransportError``."""


@pytest.mark.parametrize("raised,status,attempts", [
    (RefreshError("invalid_grant: key revoked"), 401, 1),
    (RefreshError("try later", retryable=True), 0, fs.MAX_ATTEMPTS),
    (TransportError("connection reset"), 0, fs.MAX_ATTEMPTS),
], ids=["revoked key", "retryable refresh", "network during refresh"])
def test_a_revoked_credential_is_permanent_and_a_network_blip_is_not(raised, status, attempts, retry_waits):
    tries = []

    def token():
        tries.append(1)
        raise raised

    with pytest.raises(fs.FirestoreError) as exc:
        client(Script(OK), token).query("monitors")
    assert exc.value.status == status and len(tries) == attempts
    assert "revoked" not in str(exc.value) and "reset" not in str(exc.value)


def test_the_real_google_auth_refusal_is_recognised(retry_waits):
    from google.auth.exceptions import RefreshError as Real
    from google.auth.exceptions import TransportError as RealTransport

    def refused():
        raise Real("invalid_grant", retryable=False)

    def unreachable():
        raise RealTransport("no route")

    with pytest.raises(fs.FirestoreError) as exc:
        client(Script(OK), refused).query("monitors")
    assert exc.value.status == 401 and worker.is_permanent(exc.value)
    with pytest.raises(fs.FirestoreError) as exc:
        client(Script(OK), unreachable).query("monitors")
    assert exc.value.status == 0 and not worker.is_permanent(exc.value)


def test_a_revoked_key_stops_the_segment_without_a_successor(live, dispatched, monkeypatch):
    from google.auth.exceptions import RefreshError as Real

    live.seed(_monitor("ET1", live.now), checked=False)

    def refused():
        raise Real("invalid_grant", retryable=False)

    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: refused)
    monkeypatch.setattr(state_mod, "_admin", None)
    loop, _ = _loop(live, minutes=50)
    assert loop.stopped_reason == "store refused the worker" and dispatched == []


def test_parked_state_for_a_monitor_stopped_meanwhile_is_dropped_not_written(live):
    """Parked while the store was down; the owner stops the monitor before
    it comes back. No state document is resurrected for it."""
    m = live.seed(_monitor("ET1", live.now), checked=False)
    carry: dict[str, MonitorState] = {}
    live.outage.down = any_write
    with pytest.raises(fs.FirestoreError):
        run_once(at=live.now, force=True, notifier=live.notify, carry=carry)
    assert m.id in carry

    live.outage.down = None
    live.app_stops(m.id)
    run_once(at=live.now + timedelta(minutes=10), force=True, notifier=live.notify, carry=carry)
    assert carry == {} and m.id not in live.docs.get("monitor_state", {})
