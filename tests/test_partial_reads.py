"""A listing read that came back in part is not an answer (audit REL-01).

One check of a film is several requests: its own event, once per watched
date, and each premium-format sibling event, once per date. Premium formats
— Dolby Cinema, IMAX, 4DX, Infinity Vision — are exactly what BookMyShow
lists as sibling events, and its bot check refuses requests at random
(``platforms/http.py``). Before this, a sibling that was refused was dropped
without a word, the theatre it carried was recorded as *not listed*, the
alert was re-armed, and the next good read sent "tickets are live" a second
time.

Pinned here:

* the provider keeps every failed request on the snapshot;
* a target the missing part could concern is UNKNOWN — not an answer — so
  its last real observation, and what was announced for it, are untouched;
* AVAILABLE is still reported from a partial read, because a bookable show
  that *was* read proves it;
* a sibling event that no longer exists (404) is an answer, not a failure;
* none of this costs a single extra request.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import requests

from monitor.changes import apply_outcome, detect_changes, mark_notified
from monitor.checker import (
    Listing,
    evaluate_target,
    fetch_listing,
    outcome_for,
    run_once,
)
from monitor.models import (
    ANY_FORMAT,
    Availability,
    Monitor,
    MovieRef,
    ReadFailure,
    Snapshot,
    TheatreTarget,
)
from monitor.state import MonitorState, get_monitor_state, load_history, upsert_monitor
from platforms.base import PlatformBlocked, PlatformError
from tests.conftest import FakeResponse, build_payload

BASE_EVENT = "ET00478890"
DOLBY_EVENT = "ET00999001"
URL = f"https://in.bookmyshow.com/movies/hyderabad/a-film/buytickets/{BASE_EVENT}"


def show(venue="ALUC", name="ALLU Cinemas", *, time="07:30 PM", code="1930", fmt="DOLBY CINEMA",
         status="3", date="20260925"):
    return {"venue_code": venue, "venue_name": name, "area": "Kokapet", "time": time,
            "time_code": code, "fmt": fmt, "status": status, "date": date}


#: The film's own event lists an ordinary screen elsewhere; the Dolby Cinema
#: shows at ALLU live in a sibling event — the shape of a real premium release.
BASE = [show("PVRX", "PVR Example", fmt="2D", time="06:00 PM", code="1800")]
DOLBY = [show()]
DOLBY_SOLD = [show(status="0")]

ALLU_DOLBY = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Dolby Cinema")
PVR_ANY = TheatreTarget("PVRX", "PVR Example", "Somewhere", ANY_FORMAT)


def film(*, siblings=((DOLBY_EVENT, "Dolby Cinema 2D"),)) -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=BASE_EVENT, title="A Film", region_code="HYD",
                    region_slug="hyderabad", source_url=URL, variants=tuple(siblings))


def watch(at, *targets, dates=(), **kw) -> Monitor:
    return Monitor(movie=kw.pop("movie", film()), targets=list(targets or [ALLU_DOLBY]),
                   notify_email="w@example.com", owner_uid="uid-test-1",
                   monitor_until=at + timedelta(days=1), date_codes=list(dates), **kw)


REFUSED = [FakeResponse(403)]
SERVER_ERROR = [FakeResponse(500)] * 3            # retried three times, then given up on
TIMED_OUT = [requests.Timeout("read timed out")] * 3


def read(provider_factory, monitor, *responses):
    """One listing read through the real provider, exactly as the worker makes it."""
    flat = []
    for r in responses:
        flat.extend(r if isinstance(r, list) and r and not isinstance(r[0], dict) else [r])
    provider = provider_factory([build_payload(r) if isinstance(r, list) and (not r or isinstance(r[0], dict))
                                 else r for r in flat])
    return provider.fetch(monitor.movie, monitor.date_codes or None), provider


class Run:
    """A monitor's life across several checks, with the real change
    detection and state bookkeeping and an inbox instead of SMTP."""

    def __init__(self, provider_factory, monitor, at):
        self.factory, self.monitor, self.at = provider_factory, monitor, at
        self.state = MonitorState()
        self.inbox: list = []
        self.requests = 0

    def tick(self, *responses):
        self.at += timedelta(minutes=10)
        try:
            snapshot, provider = read(self.factory, self.monitor, *responses)
            listing = Listing(snapshot=snapshot)
            self.requests = len(provider.session.calls)
        except PlatformBlocked as exc:
            listing = Listing(error=str(exc), blocked=True)
        except PlatformError as exc:
            listing = Listing(error=str(exc))
        outcome = outcome_for(self.monitor, listing, at=self.at)
        changes = detect_changes(self.monitor, outcome, self.state)
        apply_outcome(outcome, self.state)
        # Only what this check announced is marked — re-marking an earlier
        # alert would paper over a state that had wrongly been re-armed.
        for change in changes:
            self.inbox.append(change)
            mark_notified(change, self.state, self.at)
        return outcome

    @property
    def target(self):
        return self.state.target(self.monitor.targets[0].key)


# ──────────────────────────────────────────────────────────────────────────
# 1 · The provider keeps what failed
# ──────────────────────────────────────────────────────────────────────────
def test_a_read_whose_every_request_answered_is_complete(provider_factory, at):
    snapshot, provider = read(provider_factory, watch(at), BASE, DOLBY)
    assert snapshot.complete and snapshot.failures == []
    assert {v.code for v in snapshot.venues} == {"PVRX", "ALUC"}
    assert len(provider.session.calls) == 2


@pytest.mark.parametrize("failure,blocked,calls", [
    (REFUSED, True, 2),          # a bot check is not retried
    (SERVER_ERROR, False, 4),    # a 5xx is, three times — exactly as before
    (TIMED_OUT, False, 4),
], ids=["403", "500", "timeout"])
def test_a_sibling_event_that_does_not_answer_is_kept_on_the_snapshot(provider_factory, at, failure,
                                                                    blocked, calls):
    snapshot, provider = read(provider_factory, watch(at), BASE, failure)
    assert not snapshot.complete
    [failed] = snapshot.failures
    assert (failed.event_code, failed.date_code, failed.label) == (DOLBY_EVENT, "", "Dolby Cinema 2D")
    assert failed.blocked is blocked and failed.message
    # What did answer is still there, untouched…
    assert [v.code for v in snapshot.venues] == ["PVRX"]
    # …and the failure cost no request the old code did not already make.
    assert len(provider.session.calls) == calls


def test_one_failed_date_of_several_is_kept_with_its_date(provider_factory, at):
    monitor = watch(at, dates=["20260925", "20260926"], movie=film(siblings=()))
    snapshot, _ = read(provider_factory, monitor, DOLBY, SERVER_ERROR)
    [failed] = snapshot.failures
    assert (failed.event_code, failed.date_code, failed.blocked) == (BASE_EVENT, "20260926", False)
    assert [s.date_code for s in snapshot.showtimes] == ["20260925"]


def test_the_failure_is_logged_without_being_lost(provider_factory, at, capsys):
    read(provider_factory, watch(at), BASE, REFUSED)
    out = capsys.readouterr().out
    assert f"partial read: {DOLBY_EVENT}@default date (Dolby Cinema 2D)" in out
    assert "refused (bot check)" in out


def test_a_sibling_event_that_no_longer_exists_is_an_answer_not_a_failure(provider_factory, at):
    """404 for a sibling means the event is gone, and a gone event holds no
    shows. Counting it as a failure would leave a monitor whose film lost a
    premium event unable to conclude anything, for ever."""
    monitor = watch(at)
    snapshot, _ = read(provider_factory, monitor, BASE, [FakeResponse(404)])
    assert snapshot.complete
    result = evaluate_target(monitor, ALLU_DOLBY, snapshot)
    assert result.availability is Availability.THEATRE_NOT_AVAILABLE


@pytest.mark.parametrize("failure,kind", [(REFUSED, PlatformBlocked), (SERVER_ERROR, PlatformError)],
                         ids=["403", "500"])
def test_the_films_own_event_failing_on_every_date_is_still_no_snapshot_at_all(provider_factory, at,
                                                                             failure, kind):
    """Completely failed, as opposed to partial: nothing was learned, and a
    refusal is still reported as a refusal."""
    with pytest.raises(kind) as exc:
        read(provider_factory, watch(at), failure)
    assert isinstance(exc.value, PlatformBlocked) is (kind is PlatformBlocked)


# ──────────────────────────────────────────────────────────────────────────
# 2 · What a partial read may and may not say
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("failure", [REFUSED, SERVER_ERROR, TIMED_OUT], ids=["403", "500", "timeout"])
def test_a_target_the_missing_part_could_carry_is_unknown_not_not_listed(provider_factory, at, failure):
    monitor = watch(at)
    snapshot, _ = read(provider_factory, monitor, BASE, failure)
    result = evaluate_target(monitor, ALLU_DOLBY, snapshot)
    assert result.availability is Availability.UNKNOWN
    assert not result.availability.is_answer
    assert "Not a 'no tickets' answer" in result.detail


def test_a_date_that_could_not_be_read_is_unknown_not_no_showtimes(provider_factory, at):
    """The audit's reproduction B: the second date's read fails three times."""
    monitor = watch(at, TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", ANY_FORMAT),
                    dates=["20260926"], movie=film(siblings=()))
    provider = provider_factory([build_payload(DOLBY), *SERVER_ERROR])
    snapshot = provider.fetch(monitor.movie, ["20260925", "20260926"])
    result = evaluate_target(monitor, monitor.targets[0], snapshot)
    assert result.availability is Availability.UNKNOWN


def test_available_is_still_reported_from_a_partial_read(provider_factory, at):
    """A bookable show that was read is proof, whatever else was not."""
    monitor = watch(at, PVR_ANY)
    snapshot, _ = read(provider_factory, monitor, BASE, REFUSED)
    result = evaluate_target(monitor, PVR_ANY, snapshot)
    assert result.availability is Availability.AVAILABLE
    assert [s.time_label for s in result.showtimes] == ["06:00 PM"]


def test_an_unrelated_fully_read_target_is_evaluated_while_the_other_waits(provider_factory, at):
    """Two theatres on one monitor: the one whose shows were read is answered
    (and alerted on) while the one a failed sibling could carry is left alone."""
    run = Run(provider_factory, watch(at, PVR_ANY, ALLU_DOLBY), at)
    outcome = run.tick(BASE, REFUSED)
    by_key = {r.target_key: r.availability for r in outcome.results}
    assert outcome.ok and outcome.partial
    assert by_key == {PVR_ANY.key: Availability.AVAILABLE, ALLU_DOLBY.key: Availability.UNKNOWN}
    assert [c.target_key for c in run.inbox] == [PVR_ANY.key]
    assert run.state.target(ALLU_DOLBY.key).availability is Availability.UNKNOWN


def test_a_failure_on_a_date_nobody_watches_changes_no_verdict(at):
    """``ReadFailure.concerns``: a failed read of the 26th says nothing about
    a monitor that watches only the 25th."""
    monitor = watch(at, TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", ANY_FORMAT), dates=["20260925"])
    snapshot = Snapshot(movie=monitor.movie, venues=[], showtimes=[],
                        failures=[ReadFailure(BASE_EVENT, "20260926", "HTTP 500")])
    assert snapshot.failures_for(monitor.date_codes) == []
    assert evaluate_target(monitor, monitor.targets[0], snapshot).availability \
        is Availability.THEATRE_NOT_AVAILABLE
    # …while the default date, or a monitor on every date, is concerned.
    assert ReadFailure(BASE_EVENT, "", "x").concerns(["20260925"])
    assert ReadFailure(BASE_EVENT, "20260926", "x").concerns([])


def test_a_partial_read_that_answers_for_nothing_is_recorded_as_a_failed_check(provider_factory, at):
    """Not a successful check that learned nothing: the page says "couldn't
    check", and a refusal is still worded as one."""
    run = Run(provider_factory, watch(at), at)
    outcome = run.tick(BASE, REFUSED)
    assert outcome.ok is False and outcome.partial and outcome.blocked
    assert "Not a 'no tickets' answer" in outcome.error
    assert (run.state.consecutive_errors, run.state.last_error_kind) == (1, "BLOCKED")
    assert run.state.success_count == 0 and run.inbox == []
    assert run.tick(BASE, SERVER_ERROR).blocked is False


# ──────────────────────────────────────────────────────────────────────────
# 3 · The last real observation survives, and nobody is emailed twice
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("failure", [REFUSED, SERVER_ERROR], ids=["403", "500"])
def test_a_sibling_that_fails_between_two_good_reads_sends_one_email_not_two(provider_factory, at, failure):
    """The audit's reproduction A, which used to end with two emails."""
    run = Run(provider_factory, watch(at), at)

    run.tick(BASE, DOLBY)
    assert run.target.availability is Availability.AVAILABLE and len(run.inbox) == 1
    announced = list(run.target.notified_showtime_keys)
    since = run.target.since
    assert announced

    run.tick(BASE, failure)
    # Nothing about the target moved: not its availability, not when it
    # began, not what was announced for it.
    assert run.target.availability is Availability.AVAILABLE
    assert run.target.notified_availability is Availability.AVAILABLE
    assert run.target.notified_showtime_keys == announced
    assert run.target.since == since

    run.tick(BASE, DOLBY)
    assert len(run.inbox) == 1, "the event becoming readable again is not news"


def test_a_sold_out_observation_survives_a_partial_read(provider_factory, at):
    run = Run(provider_factory, watch(at), at)
    run.tick(BASE, DOLBY_SOLD)
    assert run.target.availability is Availability.SOLD_OUT
    run.tick(BASE, REFUSED)
    assert run.target.availability is Availability.SOLD_OUT
    # …and the genuine return from sold out is still announced, once.
    run.tick(BASE, DOLBY)
    assert [c.previous for c in run.inbox] == [Availability.SOLD_OUT]


def test_a_release_that_arrives_after_a_failed_read_is_announced_once(provider_factory, at):
    """The failed read delays the alert by a check; it never suppresses it."""
    run = Run(provider_factory, watch(at), at)
    run.tick(BASE, REFUSED)
    assert run.inbox == []
    run.tick(BASE, DOLBY)
    run.tick(BASE, DOLBY)
    assert len(run.inbox) == 1


def test_fetch_listing_hands_a_partial_snapshot_through(provider_factory, at, monkeypatch):
    monitor = watch(at)
    provider = provider_factory([build_payload(BASE), *REFUSED])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    listing = fetch_listing(monitor.movie, monitor.date_codes)
    assert listing.ok and not listing.snapshot.complete


# ──────────────────────────────────────────────────────────────────────────
# 4 · The same, through the worker's own tick and the store
# ──────────────────────────────────────────────────────────────────────────
def test_the_worker_tick_keeps_state_and_sends_one_email_across_a_refused_sibling(
        provider_factory, monkeypatch, at):
    monitor = watch(at)
    upsert_monitor(monitor, mirror=False)
    sent: list = []

    def tick(minutes, *payloads):
        provider = provider_factory(list(payloads))
        monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
        return run_once(at=at + timedelta(minutes=minutes), force=True, mirror=False,
                        notifier=lambda m, c: sent.append(c))

    tick(0, build_payload(BASE), build_payload(DOLBY))
    assert len(sent) == 1

    report = tick(10, build_payload(BASE), *REFUSED)
    assert report.failed == [monitor.id] and report.changes == []
    stored = get_monitor_state(monitor.id).targets[ALLU_DOLBY.key]
    assert stored.availability is Availability.AVAILABLE
    assert stored.notified_availability is Availability.AVAILABLE and stored.notified_showtime_keys

    tick(20, build_payload(BASE), build_payload(DOLBY))
    assert len(sent) == 1
    assert [h["kind"] for h in load_history()].count("TICKETS_LIVE") == 1
