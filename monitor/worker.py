"""The long-running worker segment.

Why this exists
---------------
GitHub does not run a ``*/5`` cron every five minutes. Measured on this
repository (Sept 2026): scheduled runs landed at 03:57, 05:51, 10:42 and
16:14 — one to five *hours* apart. A monitor created at 02:55 was first
checked at 03:57. No amount of tuning the cron string changes that; it is
GitHub's scheduler, not ours.

So the schedule is demoted to a safety net and two things carry the real
cadence:

1. **Dispatch on start.** The UI dispatches this workflow the moment a monitor
   is saved, so the first check happens within about a minute of clicking
   START MONITORING rather than at the next time GitHub feels like it.
2. **A segment loop.** One run stays alive for up to ~50 minutes, ticking
   :func:`monitor.checker.run_once` every ``poll_seconds``. ``run_once``
   already skips monitors that aren't due, so a 10-minute monitor is checked
   about every 10 minutes and a 30-minute one about every 30 — the intervals
   the user picked actually mean something. Between ticks the segment pulls
   ``main`` so a monitor created or stopped in the UI mid-segment is picked
   up on the next tick, and it commits every observation immediately so the
   UI's "Last checked" is never an hour stale.

When the segment's time is up and something is still being watched, it
dispatches the next segment before exiting. The workflow's concurrency group
guarantees segments never overlap, so a monitor can never be checked twice at
once. When nothing is running the segment exits at once and dispatches
nothing — an idle repository costs nothing and stays quiet.

Everything git-related is best effort and never raises: a failed push leaves
the commit in place and is retried on the next tick, and the workflow's final
"commit state" step is a second net under that.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from config.store import MONITOR_WORKFLOW, REPO_ROOT, dispatch_workflow, running_in_actions
from config.timezone import fmt_datetime, now_ist
from monitor.checker import RunReport, run_once
from monitor.models import Monitor
from monitor.state import (
    DUE_TOLERANCE_SECONDS,
    MonitorState,
    backend_report,
    load_monitors,
    load_monitors_for_checking,
    load_state,
)

DEFAULT_MAX_MINUTES = 50
DEFAULT_POLL_SECONDS = 30
GIT_TIMEOUT = 90

#: A tick that fails for a reason no retry will fix — the store refusing the
#: worker's credential, a request it calls invalid — fails the same way next
#: time. After this many in a row the segment stops and does *not* dispatch
#: a successor: a chain of segments that can only fail would otherwise
#: re-dispatch itself for ever. The schedule remains as the way back in once
#: the cause is fixed. Failures that pass (a timeout, a 429, a 5xx) never
#: count towards it: those are waited out, and the segment hands over as usual.
MAX_PERMANENT_FAILURES = 3
#: After a failed tick the next one waits the poll interval times this, per
#: consecutive failure, up to the cap — a store that is down is not asked
#: every thirty seconds.
FAILURE_BACKOFF_CAP = 4
#: The handover is one API call, and the only thing that starts the next
#: segment promptly. It is tried this many times before the schedule is left
#: to do it.
HANDOVER_ATTEMPTS = 3
HANDOVER_BACKOFF = 5.0   # seconds, doubled per attempt

#: Replaced by the tests so a retried handover costs no wall-clock time.
_sleep: Callable[[float], None] = time.sleep

#: Set to "1" to let a run inside GitHub Actions use the legacy JSON store on
#: purpose. Without it, Actions refuses to monitor production against JSON —
#: see :func:`preflight`. This exists so the legacy worker is still reachable
#: if it is ever needed, not as a routine setting.
ALLOW_JSON_WORKER = "TICKETRADAR_ALLOW_JSON_WORKER"


class WorkerConfigError(RuntimeError):
    """The worker is running in production but cannot reach the store that
    production's monitors actually live in."""


def _yn(value: bool) -> str:
    return "true" if value else "false"


def preflight(*, in_actions: bool, monitor_id: str = "",
              at: datetime | None = None) -> tuple[str, list]:
    """Say which store this segment will use, and what it can see in it.

    Once the monitors moved to Firestore, a worker whose credentials do not
    work fell back to the legacy ``data/*.json`` files, found every monitor
    there long since stopped, reported "nothing running" and exited — in
    about half a minute, green, having checked nothing. That is the worst
    kind of failure: it looks exactly like an idle repository. So the
    selection is stated out loud before any work happens, and inside Actions
    a fallback to JSON is fatal rather than quiet.

    Nothing printed here is secret. The project id and the service account's
    contents never appear — only whether each one was usable.

    Returns ``(backend_name, monitors)`` so the caller need not read twice.
    """
    report = backend_report()
    print(f"[worker] backend={report.name}")
    print(f"[worker] firebase_project_configured={_yn(report.project_configured)}")
    print(f"[worker] service_account_configured={_yn(report.service_account_configured)}")

    if report.name != "firestore" and in_actions and os.environ.get(ALLOW_JSON_WORKER) != "1":
        if report.problem:
            print(f"[worker] reason: {report.problem}")
        print("[worker] FATAL: Firestore worker credentials are not configured "
              "correctly; refusing to fall back to JSON.")
        raise WorkerConfigError(
            f"the worker selected the {report.name} store inside GitHub Actions"
            + (f": {report.problem}" if report.problem else "")
        )

    # One read, so the segment's first decision is visible rather than
    # inferred from the fact that it exited. Deliberately not cached: the
    # worker has no Streamlit session and must see every tick's writes.
    try:
        monitors = load_monitors()
    except Exception as exc:  # noqa: BLE001 - a report, not a gate
        # This read only feeds the lines below. The store being unreachable
        # for a moment is the first tick's to deal with (it retries, and the
        # segment carries on) — not a reason to end the segment before it
        # has started, with nobody dispatched to take over.
        print(f"::warning::the store could not be read at start-up ({describe_failure(exc)}); "
              "the first tick will try again", flush=True)
        return report.name, []
    at = at or now_ist()
    running = [m for m in monitors if m.is_running(at)]
    print(f"[worker] monitor_count={len(monitors)}")
    print(f"[worker] running_monitor_count={len(running)}")
    print(f"[worker] requested_monitor_id={monitor_id[:8] if monitor_id else ''}")

    requested = next((m for m in monitors if m.id == monitor_id), None) if monitor_id else None
    print(f"[worker] requested_monitor_found={_yn(requested is not None)}")
    print(f"[worker] requested_monitor_status="
          f"{requested.status.value if requested is not None else 'N/A'}")
    if monitor_id and requested is None:
        # Distinguishable from "nothing running": the UI asked for a specific
        # monitor and this store does not have it.
        print(f"::warning::the dispatched monitor was not found in the "
              f"{report.name} store — it may belong to a different project")
    return report.name, monitors


@dataclass
class LoopReport:
    """What one segment did, tick by tick."""

    started_at: datetime
    ticks: int = 0
    reports: list[RunReport] = field(default_factory=list)
    commits: int = 0
    push_failures: int = 0
    handed_over: bool = False
    stopped_reason: str = ""
    #: Ticks that raised instead of finishing. The segment carried on.
    failed_ticks: int = 0
    #: Which store this segment actually used. In the summary so that a run
    #: which checked nothing can be told apart from one that was looking in
    #: the wrong place.
    backend: str = ""

    @property
    def emails_sent(self) -> int:
        return sum(r.emails_sent for r in self.reports)

    @property
    def would_send(self) -> list:
        """A dry run's alerts that a real run would have sent."""
        return [c for r in self.reports for c in r.would_send]

    @property
    def checks(self) -> int:
        return sum(len(r.checked) + len(r.failed) for r in self.reports)

    def summary(self) -> str:
        return (
            f"backend={self.backend or 'unknown'} "
            f"ticks={self.ticks} checks={self.checks} emails={self.emails_sent} "
            f"commits={self.commits} push_failures={self.push_failures} "
            f"failed_ticks={self.failed_ticks} "
            f"handed_over={self.handed_over} stopped={self.stopped_reason or 'time'}"
        )


def report_did_work(report: RunReport) -> bool:
    """Did this tick change anything on disk?"""
    return bool(report.checked or report.failed or report.expired)


def seconds_until_next_due(at: datetime, poll_seconds: int, *,
                           monitors: list[Monitor] | None = None,
                           state: dict[str, MonitorState] | None = None) -> float | None:
    """How long to sleep: until the earliest due monitor, capped at the poll.

    Returns None when nothing is running — the caller stops. Polling is
    capped so a monitor created in the UI is noticed within ``poll_seconds``
    even while every existing monitor is minutes away from being due.

    ``monitors`` and ``state`` are what the tick that just ran read (and, if
    it changed them, wrote back), so this need not read the same two
    collections again seconds later. They are this tick's and nobody
    else's: the next tick starts from a fresh read, so a monitor stopped or
    created meanwhile is seen exactly as soon as it was before.
    """
    if monitors is None:
        monitors = load_monitors_for_checking()
    monitors = [m for m in monitors if m.is_running(at)]
    if not monitors:
        return None
    if state is None:
        state = load_state()
    soonest = None
    for m in monitors:
        ms = state.get(m.id)
        nxt = ms.next_check_at(m.interval_minutes) if ms is not None else None
        due_in = 0.0 if nxt is None else (nxt - at).total_seconds()
        soonest = due_in if soonest is None else min(soonest, due_in)
    soonest = soonest if soonest is not None else 0.0
    # Wake exactly on the interval when it is close. is_due()'s tolerance
    # exists for scheduler jitter; a segment has none, so it must not land a
    # poll inside the tolerance window and run 30s early every time.
    if soonest <= poll_seconds + DUE_TOLERANCE_SECONDS:
        return max(1.0, soonest)
    return float(poll_seconds)


# ──────────────────────────────────────────────────────────────────────────
# git plumbing — only used inside Actions
# ──────────────────────────────────────────────────────────────────────────
def _git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, check=check,
    )


def _branch() -> str:
    return os.environ.get("GITHUB_REF_NAME", "") or "main"


def git_configure() -> None:
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")


def git_pull() -> bool:
    """Bring in anything the UI committed since the last tick. Never raises."""
    try:
        result = _git("pull", "--rebase", "-X", "ours", "--quiet", "origin", _branch())
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[worker] git pull failed: {exc}")
        return False
    if result.returncode != 0:
        print(f"[worker] git pull failed: {(result.stderr or result.stdout).strip()[:300]}")
        _git("rebase", "--abort")
        return False
    return True


def git_commit_and_push(message: str, attempts: int = 3) -> tuple[bool, bool]:
    """Commit data/ and push. Returns (committed, pushed). Never raises."""
    try:
        _git("add", "data/")
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return False, True  # nothing new on disk
        commit = _git("commit", "--quiet", "-m", message)
        if commit.returncode != 0:
            print(f"[worker] git commit failed: {(commit.stderr or commit.stdout).strip()[:300]}")
            return False, False
        for attempt in range(1, attempts + 1):
            if git_pull() and _git("push", "--quiet", "origin", f"HEAD:{_branch()}").returncode == 0:
                return True, True
            time.sleep(attempt * 3)
        print("[worker] could not push state; the commit stays local and is retried next tick")
        return True, False
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[worker] git failed: {exc}")
        return False, False


def hand_over(attempts: int = HANDOVER_ATTEMPTS) -> bool:
    """Dispatch the next segment. Only meaningful inside Actions. Never raises.

    Tried a few times, because a segment that ends without a successor
    leaves every monitor waiting on GitHub's schedule — hours, as measured.
    A dispatch that did reach GitHub but whose answer was lost may be sent
    twice; the workflow's concurrency group keeps one run and at most one
    waiting, so two segments still never run at once.
    """
    message = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            ok, message = dispatch_workflow(
                MONITOR_WORKFLOW, {"force": "false", "dry_run": "false", "monitor_id": ""})
        except Exception as exc:  # noqa: BLE001 - dispatch_workflow never raises; nor may this
            ok, message = False, f"unexpected {type(exc).__name__}"
        if ok:
            print("[worker] next segment: dispatched"
                  + (f" (attempt {attempt})" if attempt > 1 else ""))
            return True
        if attempt < attempts:
            print(f"[worker] next segment: dispatch attempt {attempt} failed — {message}; retrying")
            _sleep(HANDOVER_BACKOFF * (2 ** (attempt - 1)))
    print(f"::warning::next segment NOT dispatched after {max(1, attempts)} attempt(s) — {message}. "
          "Monitoring resumes on the schedule, or when a monitor is next saved.")
    return False


def describe_failure(exc: BaseException) -> str:
    """A failure for a public log: what kind it was, and an HTTP status when
    it has one. Never the exception's own text — a store's error message can
    name a document, and a credential error can quote one."""
    status = getattr(exc, "status", 0)
    return f"{type(exc).__name__}{f' HTTP {status}' if status else ''}"


def is_permanent(exc: BaseException) -> bool:
    """Will the same tick fail the same way if simply run again?

    Only a store error that says so about itself (``FirestoreError`` with a
    non-retryable status: a refused credential, an invalid request) and a
    configuration error count. Anything else — including an exception this
    module has never seen — is treated as passing, because stopping a
    segment is the more expensive mistake.
    """
    if isinstance(exc, WorkerConfigError):
        return True
    transient = getattr(exc, "transient", None)
    return transient is False


# ──────────────────────────────────────────────────────────────────────────
# The segment
# ──────────────────────────────────────────────────────────────────────────
def run_loop(
    *,
    max_minutes: int = DEFAULT_MAX_MINUTES,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    force: bool = False,
    monitor_id: str = "",
    notifier=None,
    use_git: bool | None = None,
    chain: bool | None = None,
    clock: Callable[[], datetime] = now_ist,
    sleeper: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
) -> LoopReport:
    """Tick until the segment's time is up or nothing is left to watch.

    ``force`` and ``monitor_id`` apply to the *first* tick only: they are how
    a dispatch from the UI gets a brand-new monitor checked immediately. Every
    later tick serves all monitors at their own intervals — a segment must
    never spend fifty minutes watching one alert because it was started for
    it.

    ``dry_run`` makes the segment a single read-only pass
    (``run_once(dry_run=True)``: nothing sent, nothing written, no git), and
    then ends the way any segment does — handing over to a real one when
    monitors are running. It is one pass because a segment holds the
    workflow's only slot: a dry run that ticked for fifty minutes would be
    fifty minutes in which no real alert could go out, and a dry run that
    ended without handing over could leave the real chain broken (a dispatch
    can replace the one waiting behind it in the concurrency group).
    """
    in_actions = running_in_actions()
    # A dry run writes nothing, so there is never anything to commit — and it
    # does not pull or push either.
    use_git = False if dry_run else (in_actions if use_git is None else use_git)
    chain = in_actions if chain is None else chain

    started = clock()
    deadline = started + timedelta(minutes=max_minutes)
    loop = LoopReport(started_at=started)
    print(f"[worker] segment start {fmt_datetime(started)} IST · up to {max_minutes} min · "
          f"poll {poll_seconds}s · git={'on' if use_git else 'off'} · chain={'on' if chain else 'off'}")

    # Before any work: which store, and what is in it. Raises inside Actions
    # rather than monitoring production against the legacy JSON files.
    loop.backend = preflight(in_actions=in_actions, monitor_id=monitor_id, at=started)[0]

    if use_git:
        git_configure()

    first = True
    known_state: dict[str, MonitorState] | None = None
    #: State records a tick changed but could not save (``run_once``): held
    #: here, in this process, and handed to the next tick so that nobody is
    #: emailed or checked twice while the store is failing.
    carry: dict[str, MonitorState] = {}
    failures_in_a_row = 0
    permanent_in_a_row = 0
    # Whatever goes wrong inside the loop, the segment's last act is to decide
    # about its successor. Before this, an exception in any tick left the
    # function by the nearest exit and the handover below was never reached.
    try:
        while True:
            at = clock()
            try:
                if not first and use_git:
                    git_pull()

                report = run_once(
                    at=at,
                    force=force if first else False,
                    monitor_id=monitor_id if first else "",
                    mirror=False,
                    notifier=notifier,
                    # This segment's own last-written state: lets a tick where
                    # nothing is due skip the state read. Never carried across
                    # segments.
                    known_state=known_state,
                    carry=carry,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - a tick is a unit of failure
                # The tick did not finish: the store was unreachable, or
                # something nobody foresaw. This tick is lost; the segment is
                # not. What it had learned and could not save is in ``carry``.
                loop.ticks += 1
                loop.failed_ticks += 1
                failures_in_a_row += 1
                permanent_in_a_row = permanent_in_a_row + 1 if is_permanent(exc) else 0
                first = False
                # The in-memory copy can no longer be assumed to be what the
                # store holds: the next tick reads state afresh.
                known_state = None
                print(f"::warning::worker tick {loop.ticks} failed ({describe_failure(exc)}); "
                      f"{len(carry)} unsaved state record(s) kept in memory — the segment continues",
                      flush=True)
                if dry_run:
                    loop.stopped_reason = "dry run: one pass"
                    break
                if permanent_in_a_row >= MAX_PERMANENT_FAILURES:
                    loop.stopped_reason = "store refused the worker"
                    print(f"::error::{permanent_in_a_row} ticks in a row failed for a reason a retry "
                          f"cannot fix ({describe_failure(exc)}). Stopping this segment without a "
                          "successor; fix the cause and the schedule starts monitoring again.",
                          flush=True)
                    break
                wait = float(poll_seconds) * min(2 ** (failures_in_a_row - 1), FAILURE_BACKOFF_CAP)
                if clock() + timedelta(seconds=wait) >= deadline:
                    break
                sleeper(wait)
                continue

            failures_in_a_row = permanent_in_a_row = 0
            known_state = report.state
            loop.ticks += 1
            loop.reports.append(report)
            first = False

            if use_git and report_did_work(report):
                committed, pushed = git_commit_and_push(
                    f"chore: monitoring state {at.strftime('%Y-%m-%dT%H:%M')} IST [skip ci]"
                )
                loop.commits += int(committed)
                loop.push_failures += int(committed and not pushed)

            wait = seconds_until_next_due(clock(), poll_seconds,
                                          monitors=report.monitors, state=report.state)
            if wait is None and report.deferred:
                # Monitors whose owners could not be looked up this tick are not
                # refused, only postponed: keep polling at the ordinary pace (never
                # faster — a failing lookup is not retried in a tight loop).
                wait = float(poll_seconds)
            if wait is None:
                loop.stopped_reason = "nothing running"
                break
            if dry_run:
                loop.stopped_reason = "dry run: one pass"
                break
            if clock() + timedelta(seconds=wait) >= deadline:
                break
            sleeper(wait)
    except Exception as exc:  # noqa: BLE001 - outside a tick: the clock, the sleeper, git
        loop.stopped_reason = "segment error"
        print(f"::error::worker segment stopped early ({describe_failure(exc)}); "
              "handing over to a new segment", flush=True)

    # Reached on every way out of the loop that is a *failure or a finish*:
    # the deadline, nothing left to watch, a tick that raised, an error
    # outside a tick. Only the two endings that mean "there is nothing for a
    # successor to do" skip the dispatch: nothing is running, or the store is
    # refusing the worker outright. A cancelled job (KeyboardInterrupt /
    # SystemExit — not ``Exception``) deliberately passes all of this by: a
    # run somebody stopped must not start its own replacement.
    if chain and loop.stopped_reason not in ("nothing running", "store refused the worker"):
        loop.handed_over = hand_over()
    if carry:
        print(f"::warning::{len(carry)} state record(s) were never saved this segment; "
              "an alert already sent for them may be sent again", flush=True)
    print(f"[worker] segment end · {loop.summary()}")
    return loop


__all__ = [
    "DEFAULT_MAX_MINUTES",
    "DEFAULT_POLL_SECONDS",
    "MAX_PERMANENT_FAILURES",
    "LoopReport",
    "describe_failure",
    "git_commit_and_push",
    "git_pull",
    "hand_over",
    "is_permanent",
    "report_did_work",
    "run_loop",
    "seconds_until_next_due",
]
