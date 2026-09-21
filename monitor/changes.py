"""Deciding when something actually changed.

The whole value of this module is restraint. BookMyShow gets polled every ten
minutes for hours; almost every one of those reads is identical to the last
one and must produce silence. Exactly three things are worth an email:

1. A target became bookable when it previously was not   -> TICKETS_LIVE
2. A target came back from sold out                      -> TICKETS_LIVE
3. A target that was already live grew new showtimes     -> NEW_SHOWTIME

Everything else — including every failed check — is recorded and stays quiet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from monitor.models import (
    Availability,
    CheckOutcome,
    Monitor,
    NOTIFIABLE_FROM,
    TargetResult,
)
from monitor.state import MonitorState, TargetState


class ChangeKind(str, Enum):
    TICKETS_LIVE = "TICKETS_LIVE"
    NEW_SHOWTIME = "NEW_SHOWTIME"


@dataclass
class Change:
    kind: ChangeKind
    monitor_id: str
    target_key: str
    venue_name: str
    fmt: str
    movie_title: str
    previous: Availability
    current: Availability
    date_code: str = ""
    booking_url: str = ""
    time_labels: list[str] = field(default_factory=list)
    new_time_labels: list[str] = field(default_factory=list)
    new_showtime_keys: list[str] = field(default_factory=list)
    #: ``[[label, url], …]`` for every showtime in ``time_labels``. The email
    #: renders each label as a link to its url.
    time_links: list[list[str]] = field(default_factory=list)
    #: Every show date behind ``time_labels`` (``date_code`` is the earliest).
    date_codes: list[str] = field(default_factory=list)
    detected_at: datetime | None = None
    #: The watched seat categories that became bookable (a category watch);
    #: empty for an ordinary monitor.
    categories: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        if self.kind is ChangeKind.TICKETS_LIVE:
            if self.categories:
                return f"{', '.join(self.categories)} AVAILABLE — {self.movie_title}"
            return f"TICKETS ARE LIVE — {self.movie_title}"
        return f"NEW SHOWTIME — {self.movie_title}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "monitor_id": self.monitor_id,
            "target_key": self.target_key,
            "venue_name": self.venue_name,
            "fmt": self.fmt,
            "movie_title": self.movie_title,
            "previous": self.previous.value,
            "current": self.current.value,
            "date_code": self.date_code,
            "booking_url": self.booking_url,
            "time_labels": self.time_labels,
            "new_time_labels": self.new_time_labels,
            "time_links": [list(pair) for pair in self.time_links],
            "date_codes": list(self.date_codes),
            "categories": list(self.categories),
        }


def detect_changes(monitor: Monitor, outcome: CheckOutcome, state: MonitorState) -> list[Change]:
    """Compare this check against stored state. Pure — mutates nothing.

    State is only advanced by :func:`apply_outcome`, and notification markers
    only by :func:`mark_notified`, so a crash between detecting and sending
    leaves the monitor ready to retry rather than silently "done".
    """
    changes: list[Change] = []
    if not outcome.ok:
        # A failed check tells us nothing about availability. It must not be
        # able to produce, or suppress, a notification.
        return changes

    for result in outcome.results:
        if not result.availability.is_answer:
            continue
        prior = state.targets.get(result.target_key, TargetState())
        change = _change_for(monitor, result, prior, outcome.checked_at)
        if change is not None:
            changes.append(change)
    return changes


def _live_categories(monitor: Monitor, result: TargetResult) -> list[str]:
    """The watched categories bookable in the result's shows, first-seen order."""
    if not monitor.is_category_watch:
        return []
    out: dict[str, None] = {}
    for show in result.showtimes:
        for c in show.categories:
            if monitor.watches_category(c.name) and c.availability is Availability.AVAILABLE:
                out.setdefault(c.name, None)
    return list(out)


def _change_for(monitor: Monitor, result: TargetResult, prior: TargetState,
                at: datetime) -> Change | None:
    if result.availability is not Availability.AVAILABLE:
        return None

    # Compare against what we last *told the user*, not merely what we last
    # saw. If an email failed to send, notified_availability stayed behind and
    # this run retries it — which is the behaviour you want at 2am.
    already_announced = prior.notified_availability is Availability.AVAILABLE

    # Two ways this deserves an email: a genuine transition into bookable, or
    # a previous transition whose email never made it out.
    is_transition = prior.availability in NOTIFIABLE_FROM
    is_retry = prior.availability is Availability.AVAILABLE and not already_announced
    # A category watch (admin) is created to catch a category *opening up*:
    # its first read only establishes where things stand (``settle_baseline``
    # marks it announced). Finding GOLD already bookable on the very first
    # check is not that transition.
    if monitor.is_category_watch and prior.availability is Availability.UNKNOWN:
        return None

    if not already_announced and (is_transition or is_retry):
        return Change(
            kind=ChangeKind.TICKETS_LIVE,
            monitor_id=monitor.id,
            target_key=result.target_key,
            venue_name=result.venue_name,
            fmt=result.fmt,
            movie_title=monitor.movie.title,
            previous=prior.availability,
            current=result.availability,
            date_code=result.date_code,
            booking_url=result.booking_url,
            time_labels=result.time_labels,
            time_links=result.time_links,
            date_codes=result.date_codes,
            detected_at=at,
            categories=_live_categories(monitor, result),
        )

    if not already_announced or monitor.is_category_watch:
        # A category watch is about its categories opening, not about new
        # screenings: once announced it stays quiet until it re-arms.
        return None

    # Already live and already announced: the only remaining news is a
    # screening we have not told the user about.
    #
    # Compared against *notified* keys only, never against merely-observed
    # ones: a showtime held back by the cooldown must still be announced when
    # the cooldown lifts, not quietly absorbed into "already seen".
    known = set(prior.notified_showtime_keys)
    fresh = [k for k in result.showtime_keys if k not in known]
    if not fresh or not prior.may_announce_new_showtimes(at):
        return None

    fresh_set = set(fresh)
    new_labels = [result._label(s) for s in result.showtimes if s.key in fresh_set]
    return Change(
        kind=ChangeKind.NEW_SHOWTIME,
        monitor_id=monitor.id,
        target_key=result.target_key,
        venue_name=result.venue_name,
        fmt=result.fmt,
        movie_title=monitor.movie.title,
        previous=prior.availability,
        current=result.availability,
        date_code=result.date_code,
        booking_url=result.booking_url,
        time_labels=result.time_labels,
        time_links=result.time_links,
        date_codes=result.date_codes,
        new_time_labels=sorted(set(new_labels)),
        new_showtime_keys=fresh,
        detected_at=at,
    )


def settle_baseline(monitor: Monitor, outcome: CheckOutcome, state: MonitorState) -> None:
    """A category watch's first read is its baseline. Called before
    :func:`apply_outcome`: a target read as AVAILABLE with no prior
    observation is marked as already announced, so neither this tick nor a
    later "retry" emails it; the alert arms the first time the category is
    seen unavailable. Ordinary monitors are untouched."""
    if not monitor.is_category_watch or not outcome.ok:
        return
    for result in outcome.results:
        prior = state.targets.get(result.target_key, TargetState())
        if prior.availability is Availability.UNKNOWN and result.availability is Availability.AVAILABLE:
            ts = state.target(result.target_key)
            ts.notified_availability = Availability.AVAILABLE
            ts.notified_showtime_keys = result.showtime_keys


def apply_outcome(outcome: CheckOutcome, state: MonitorState) -> MonitorState:
    """Fold a check result into stored state.

    Failures update the run counters and the error message but deliberately
    leave every target's availability untouched: the last thing we actually
    observed stays the last thing we know.
    """
    state.check_count += 1
    state.last_check_at = outcome.checked_at

    if not outcome.ok:
        state.consecutive_errors += 1
        state.last_error = outcome.error
        state.last_error_kind = "BLOCKED" if outcome.blocked else "ERROR"
        return state

    state.success_count += 1
    state.last_success_at = outcome.checked_at
    state.consecutive_errors = 0
    state.last_error = ""
    state.last_error_kind = ""

    for result in outcome.results:
        if not result.availability.is_answer:
            continue
        ts = state.target(result.target_key)
        if ts.availability is not result.availability:
            ts.since = outcome.checked_at
            ts.availability = result.availability
            if result.availability is not Availability.AVAILABLE:
                # Dropping out of AVAILABLE re-arms the alert: a later return
                # to AVAILABLE is genuine news and should email again.
                ts.notified_availability = result.availability
                ts.notified_showtime_keys = []
        elif ts.since is None:
            ts.since = outcome.checked_at

        ts.showtime_keys = result.showtime_keys
        ts.time_labels = result.time_labels
        ts.time_links = result.time_links
        ts.date_codes = result.date_codes
        ts.date_code = result.date_code or ts.date_code
        ts.booking_url = result.booking_url or ts.booking_url

    return state


def mark_notified(change: Change, state: MonitorState, at: datetime) -> None:
    """Record that an email for this change actually went out.

    Only called after a successful send. This is the single line of defence
    against duplicate mail, and the reason an SMTP outage produces a retry
    rather than a silent drop.
    """
    ts = state.target(change.target_key)
    ts.notified_at = at
    if change.kind is ChangeKind.TICKETS_LIVE:
        ts.notified_availability = Availability.AVAILABLE
        ts.notified_showtime_keys = sorted(set(ts.notified_showtime_keys) | set(ts.showtime_keys))
    else:
        ts.new_showtime_notice_at = at
        ts.notified_showtime_keys = sorted(
            set(ts.notified_showtime_keys) | set(change.new_showtime_keys) | set(ts.showtime_keys)
        )


__all__ = ["Change", "ChangeKind", "apply_outcome", "detect_changes", "mark_notified"]
