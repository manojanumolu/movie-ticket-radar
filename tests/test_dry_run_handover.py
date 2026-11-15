"""A dry run never starts a real worker run — on any branch.

The workflow's ``dry_run`` input runs ``run_monitor.py --loop --dry-run``.
Before this fix, that segment ended like any other: ``hand_over()``
dispatched the next segment with ``dry_run: "false"``, on the ref the dry
run itself was running on (``GITHUB_REF_NAME``). Dispatched on a feature
branch, a "safe" dry run therefore started a *real* worker run of that
branch's unreviewed code, with the production secrets — free to email and
to write the store — which then kept handing over to itself on that branch.

Pinned here, with the real ``config.store.dispatch_workflow`` and GitHub's
client replaced by a recorder (nothing leaves the machine):

* a dry run dispatches nothing, on a feature branch and on ``main``;
* a real segment on ``main`` still hands over exactly as before — one
  dispatch, on ``main``, with ``dry_run: "false"``.
"""

from __future__ import annotations

import sys
import types
from datetime import timedelta

import pytest

from monitor import checker, worker
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
from tests.test_scheduling import Clock
from tests.test_worker_reads import OWNER, firestore_worker  # noqa: F401 - a fixture


class _Recorder:
    """Stands in for PyGithub: records every workflow dispatch."""

    def __init__(self):
        self.dispatches: list[dict] = []

    def __call__(self, token):                       # Github(token)
        recorder = self

        class Repo:
            def get_workflow(self, name):
                class Workflow:
                    def create_dispatch(self, ref, inputs):
                        recorder.dispatches.append({"workflow": name, "ref": ref, **inputs})
                        return True
                return Workflow()

        return types.SimpleNamespace(get_repo=lambda full_name: Repo())


@pytest.fixture
def github(monkeypatch):
    """Inside GitHub Actions, with a token, and GitHub replaced by a recorder."""
    recorder = _Recorder()
    monkeypatch.setitem(sys.modules, "github", types.SimpleNamespace(Github=recorder))
    monkeypatch.setattr("config.store.github_token", lambda: "test-token")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    return recorder


@pytest.fixture
def running(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """One running monitor on a bookable listing (no BookMyShow, no SMTP)."""
    w = firestore_worker

    def bookable(movie, date_codes=None):
        show = Showtime("ALLU", "ALLU Cinemas", "S1", "20261201", "07:30 PM", "1930", "Dolby Cinema",
                        Availability.AVAILABLE)
        return checker.Listing(snapshot=Snapshot(movie=movie, venues=[Venue("ALLU", "ALLU Cinemas")],
                                                 showtimes=[show]))

    monkeypatch.setattr(checker, "fetch_listing", bookable)
    w.seed(Monitor(movie=MovieRef("bookmyshow", "ET00512345", "Film", "HYD", "hyderabad"),
                   targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   monitor_until=w.now + timedelta(days=2), notify_email="w@example.com",
                   owner_uid=OWNER), checked=False)
    w.inbox = []
    return w


def _segment(w, *, dry_run: bool, minutes: int = 3):
    clock = Clock(w.now)
    # chain and use_git left to their defaults: inside Actions, as the
    # workflow runs it (git is stubbed out below).
    return worker.run_loop(max_minutes=minutes, poll_seconds=30, dry_run=dry_run, clock=clock,
                           sleeper=clock.sleep, notifier=lambda m, c: w.inbox.append(c))


@pytest.fixture(autouse=True)
def no_git(monkeypatch):
    monkeypatch.setattr(worker, "git_configure", lambda: None)
    monkeypatch.setattr(worker, "git_pull", lambda: True)
    monkeypatch.setattr(worker, "git_commit_and_push", lambda message: (False, True))


@pytest.mark.parametrize("branch", ["claude/some-feature", "main"])
def test_a_dry_run_never_dispatches_a_worker_run(running, github, monkeypatch, branch):
    monkeypatch.setenv("GITHUB_REF_NAME", branch)
    loop = _segment(running, dry_run=True)
    assert loop.stopped_reason == "dry run: one pass"
    assert github.dispatches == [], f"a dry run on {branch} dispatched {github.dispatches}"
    assert loop.handed_over is False
    assert running.inbox == [] and len(loop.would_send) == 1


def test_a_real_segment_on_main_still_hands_over_exactly_as_before(running, github, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    loop = _segment(running, dry_run=False)
    assert loop.handed_over is True
    assert github.dispatches == [{"workflow": "bookmyshow-monitor.yml", "ref": "main",
                                  "force": "false", "dry_run": "false", "monitor_id": ""}]
    assert len(running.inbox) == 1


def test_a_real_segment_with_nothing_running_still_ends_quietly(firestore_worker, github, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    clock = Clock(firestore_worker.now)
    loop = worker.run_loop(max_minutes=3, clock=clock, sleeper=clock.sleep, notifier=lambda m, c: None)
    assert loop.stopped_reason == "nothing running" and github.dispatches == []
