"""The monitoring engine.

One entry point, :func:`run_once`, is everything the GitHub Actions workflow
does. It is deliberately platform-agnostic: it asks ``platforms.get_provider``
for a snapshot and reasons entirely in ``monitor.models`` terms, so adding a
second ticketing site later means adding a provider, not editing this file.

Order of operations, and why:

1. Expire anything past its end time — *before* checking, so an expired
   monitor never gets one last check it shouldn't have had.
2. Skip monitors that aren't due yet (the workflow ticks faster than the
   slowest interval).
3. Re-list the city for any due monitor that still hasn't found its theatre
   or format (``monitor.discovery``) — rate-limited, one request per city —
   so a sibling event BookMyShow created *after* the monitor was saved is
   swept in this very tick, not after the next catalogue sync.
4. Fetch. A failure here becomes an ERROR record, never an availability.
5. Evaluate each theatre+format target independently.
6. Detect changes, send mail, and only then mark as notified.
7. Persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from config.timezone import now_ist
from monitor.changes import Change, apply_outcome, detect_changes, mark_notified
from monitor.discovery import DiscoveryReport, discover_siblings
from monitor.models import (
    Availability,
    CheckOutcome,
    Monitor,
    MovieRef,
    Showtime,
    Snapshot,
    TargetResult,
    TheatreTarget,
)
from monitor.state import (
    MonitorState,
    expire_due_monitors,
    load_monitors_for_checking,
    load_state,
    record_history,
    save_monitors,
    save_state,
)
from platforms import get_provider
from platforms.base import PlatformBlocked, PlatformError


@dataclass
class RunReport:
    """What one worker tick did — printed to the Actions log and asserted in tests."""

    started_at: datetime
    checked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    emails_sent: int = 0
    email_errors: list[str] = field(default_factory=list)
    #: What sibling-event discovery did before the checks (see ``monitor.discovery``).
    discovery: DiscoveryReport = field(default_factory=DiscoveryReport)
    #: The monitors and observed state this tick read and, where it changed
    #: them, wrote back — handed to the segment so deciding when to wake next
    #: does not read the same two collections a second time. Valid for this
    #: tick only; the next tick reads afresh.
    monitors: list[Monitor] = field(default_factory=list)
    state: dict[str, MonitorState] = field(default_factory=dict)

    def summary(self) -> str:
        text = (
            f"checked={len(self.checked)} skipped={len(self.skipped)} "
            f"expired={len(self.expired)} failed={len(self.failed)} "
            f"changes={len(self.changes)} emails={self.emails_sent}"
        )
        if self.discovery.ran:
            text += f" discovery[{self.discovery.summary()}]"
        return text


# ──────────────────────────────────────────────────────────────────────────
# Evaluating one target against one snapshot
# ──────────────────────────────────────────────────────────────────────────
def evaluate_target(monitor: Monitor, target: TheatreTarget, snapshot: Snapshot) -> TargetResult:
    """Turn a snapshot into a verdict for one theatre+format.

    The ladder of "no" answers matters: not knowing the theatre exists, the
    theatre existing without this format, the format existing without an open
    sale, and the sale being sold out are four different situations, and the
    UI says four different things about them.
    """
    shows = [s for s in snapshot.showtimes if _same_venue(s, target)]
    wanted_dates = set(monitor.date_codes)

    if not shows:
        known_venue = any(_same_venue_code(v.code, v.name, target) for v in snapshot.venues)
        return TargetResult(
            target_key=target.key,
            venue_name=target.venue_name,
            fmt=target.fmt,
            availability=(
                Availability.THEATRE_NOT_AVAILABLE
                if not known_venue
                else Availability.SHOW_NOT_AVAILABLE
            ),
            detail=(
                "This theatre isn't listed for the movie yet."
                if not known_venue
                else "Theatre is listed, but no showtimes are published yet."
            ),
            booking_url=_booking_url(monitor, snapshot, "", target),
        )

    if wanted_dates:
        on_other_dates = [s for s in shows if s.date_code not in wanted_dates]
        shows = [s for s in shows if s.date_code in wanted_dates]
        if on_other_dates:
            print(f"    [filter] {target.venue_name}: ignoring {len(on_other_dates)} show(s) on "
                  f"{sorted({s.date_code for s in on_other_dates})} — watching {sorted(wanted_dates)} only")
        if not shows:
            return TargetResult(
                target_key=target.key,
                venue_name=target.venue_name,
                fmt=target.fmt,
                availability=Availability.SHOW_NOT_AVAILABLE,
                detail=f"No showtimes on the dates you're watching ({', '.join(sorted(wanted_dates))}).",
                booking_url=_booking_url(monitor, snapshot, sorted(wanted_dates)[0], target),
            )

    matching = [s for s in shows if target.matches_format(s.format_label)]
    if not matching:
        seen = sorted({s.format_label or "(no format)" for s in shows})
        print(f"    [filter] {target.venue_name}: {len(shows)} show(s) but none in '{target.fmt}' "
              f"— formats listed: {seen}")
        return TargetResult(
            target_key=target.key,
            venue_name=target.venue_name,
            fmt=target.fmt,
            availability=Availability.SHOW_NOT_AVAILABLE,
            detail=f"Shows are listed, but none in {target.fmt} (listed: {', '.join(seen)}).",
            date_code=shows[0].date_code,
            booking_url=_booking_url(monitor, snapshot, shows[0].date_code, target),
        )

    bookable = [s for s in matching if s.availability is Availability.AVAILABLE]
    if bookable:
        date_code = _earliest_date(bookable)
        return TargetResult(
            target_key=target.key,
            venue_name=target.venue_name,
            fmt=target.fmt,
            availability=Availability.AVAILABLE,
            showtimes=bookable,
            date_code=date_code,
            booking_url=_booking_url(monitor, snapshot, date_code, target),
            detail=f"{len(bookable)} showtime(s) bookable.",
        )

    sold_out = [s for s in matching if s.availability is Availability.SOLD_OUT]
    date_code = _earliest_date(matching)
    if sold_out:
        return TargetResult(
            target_key=target.key,
            venue_name=target.venue_name,
            fmt=target.fmt,
            availability=Availability.SOLD_OUT,
            showtimes=sold_out,
            date_code=date_code,
            booking_url=_booking_url(monitor, snapshot, date_code, target),
            detail="Every seat category is sold out.",
        )

    return TargetResult(
        target_key=target.key,
        venue_name=target.venue_name,
        fmt=target.fmt,
        availability=Availability.NOT_BOOKABLE,
        showtimes=matching,
        date_code=date_code,
        booking_url=_booking_url(monitor, snapshot, date_code, target),
        detail="Showtimes are listed but booking hasn't opened.",
    )


def with_current_variants(movie: MovieRef) -> MovieRef:
    """The movie with its sibling events as the catalogue knows them today,
    unioned with the ones saved on the monitor. Never removes a sibling."""
    from dataclasses import replace

    from monitor import catalogue

    entry = catalogue.find_entry(movie.id)
    if not entry:
        return movie
    current = catalogue.movie_from_entry(entry).variants
    merged = tuple(dict.fromkeys((*movie.variants, *current)))
    return movie if merged == movie.variants else replace(movie, variants=merged)


def _hint_other_rows(monitor: Monitor, target: TheatreTarget | None) -> None:
    """Say so when the theatre *is* listed for this film — under another language.

    A film is one row per language, and each row sweeps only its own
    language's events. A Telugu monitor can therefore watch a theatre for
    hours that BookMyShow is already selling under the English row. That is
    the correct answer for the row, but it is the first thing to check when
    a "not listed yet" looks wrong, so the log says it outright.
    """
    if target is None:
        return
    from monitor import catalogue

    movie = monitor.movie
    mine = catalogue.find_entry(movie.id)
    title = (catalogue.movie_from_entry(mine).title if mine else movie.title).strip().lower()
    for entry in catalogue.list_entries(movie.region_slug):
        other = catalogue.movie_from_entry(entry)
        if other.id == movie.id or other.title.strip().lower() != title:
            continue
        venue = next((v for v in catalogue.venues_from_entry(entry)
                      if _same_venue_code(v.code, v.name, target)), None)
        if venue is None:
            continue
        print(f"    [hint] {target.venue_name} is listed for '{other.title} · {other.language}' "
              f"({other.event_code}; formats: {', '.join(venue.formats) or 'unknown'}) — this "
              f"monitor watches the {movie.language or 'other'} row ({movie.event_code}) and "
              f"only sweeps that language's events")


def _same_venue(show: Showtime, target: TheatreTarget) -> bool:
    return _same_venue_code(show.venue_code, show.venue_name, target)


def _same_venue_code(code: str, name: str, target: TheatreTarget) -> bool:
    """Match on venue code, falling back to the name.

    Venue codes are stable, but a monitor saved months ago can outlive one, so
    a name match is kept as a safety net rather than dropping the theatre.
    """
    if code and target.venue_code and code == target.venue_code:
        return True
    return bool(name) and name.strip().lower() == target.venue_name.strip().lower()


def _earliest_date(shows: list[Showtime]) -> str:
    codes = sorted({s.date_code for s in shows if s.date_code})
    return codes[0] if codes else ""


def _booking_url(monitor: Monitor, snapshot: Snapshot, date_code: str,
                 target: TheatreTarget | None = None) -> str:
    """The link an alert carries: the *theatre's* booking page when the
    provider has one for this venue, else the movie's date page."""
    try:
        provider = get_provider(monitor.movie.platform)
    except PlatformError:
        return monitor.movie.source_url
    venue_page = getattr(provider, "venue_booking_url", None)
    if target is not None and target.venue_code and venue_page is not None:
        return venue_page(snapshot.movie, target.venue_code, date_code)
    return provider.booking_url(snapshot.movie, date_code)


# ──────────────────────────────────────────────────────────────────────────
# Checking one monitor
# ──────────────────────────────────────────────────────────────────────────
def check_monitor(monitor: Monitor, *, at: datetime | None = None) -> CheckOutcome:
    """Read the platform once and evaluate every target. Never raises."""
    at = at or now_ist()
    try:
        provider = get_provider(monitor.movie.platform)
        # A theatre often releases a film under a *new* premium-format event
        # (a "Dolby Cinema 2D" sibling that did not exist when the monitor
        # was saved). The catalogue sync learns of such siblings; take them
        # from there so the sweep is the whole film as of now, not as of the
        # day the monitor was created.
        snapshot = provider.fetch(with_current_variants(monitor.movie), monitor.date_codes or None)
    except PlatformBlocked as exc:
        return CheckOutcome(monitor.id, at, ok=False, error=str(exc), blocked=True)
    except PlatformError as exc:
        return CheckOutcome(monitor.id, at, ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a parser bug must not kill the run
        return CheckOutcome(
            monitor.id, at, ok=False,
            error=f"Unexpected failure while reading the listing: {type(exc).__name__}: {exc}",
        )

    results = [evaluate_target(monitor, t, snapshot) for t in monitor.targets]
    return CheckOutcome(monitor.id, at, ok=True, results=results)


# ──────────────────────────────────────────────────────────────────────────
# The worker tick
# ──────────────────────────────────────────────────────────────────────────
def run_once(*, at: datetime | None = None, force: bool = False, monitor_id: str = "",
             mirror: bool = False, notifier=None) -> RunReport:
    """Check every monitor that is due. This is what the workflow calls.

    ``mirror`` is False by default because inside Actions the workflow makes
    one commit of the whole data directory at the end; mirroring each write
    through the API as well would race it.
    """
    at = at or now_ist()
    report = RunReport(started_at=at)

    if notifier is None:
        from notifications.email import send_change_email

        notifier = send_change_email

    # Only what can still be checked. On Firestore that is the ACTIVE
    # documents alone — a stopped or expired monitor is never read again by
    # the worker, and never written back by it either.
    monitors, newly_expired = expire_due_monitors(load_monitors_for_checking(), at=at, mirror=mirror)
    report.expired = [m.id for m in newly_expired]

    state = load_state()
    dirty = bool(newly_expired)

    due: list[Monitor] = []
    for monitor in monitors:
        if monitor_id and monitor.id != monitor_id:
            continue
        if not monitor.is_running(at):
            report.skipped.append(f"{monitor.id} ({monitor.status.value.lower()})")
            continue
        ms: MonitorState = state.setdefault(monitor.id, MonitorState())
        if not force and not ms.is_due(monitor.interval_minutes, at):
            report.skipped.append(f"{monitor.id} (not due)")
            continue
        due.append(monitor)

    # A theatre that is "not listed yet" may be listed under a sibling event
    # BookMyShow created after the monitor was saved. Learn of such events
    # *before* fetching, so this tick's sweep is the film as it is now.
    report.discovery = discover_siblings(monitors, due, state, at=at, mirror=mirror)
    if report.discovery.updated:
        save_monitors(monitors, mirror=mirror)

    for monitor in due:
        ms = state[monitor.id]
        print(f"[checker] {monitor.id} — {monitor.movie.title} ({len(monitor.targets)} target(s))")
        outcome = check_monitor(monitor, at=at)
        changes = detect_changes(monitor, outcome, ms)
        apply_outcome(outcome, ms)
        dirty = True

        if outcome.ok:
            report.checked.append(monitor.id)
            for result in outcome.results:
                print(f"    {result.venue_name} · {result.fmt} -> {result.availability.value}"
                      f"{' — ' + result.detail if result.detail else ''}")
                if result.availability is Availability.THEATRE_NOT_AVAILABLE:
                    _hint_other_rows(monitor, monitor.target(result.target_key))
        else:
            report.failed.append(monitor.id)
            print(f"    check failed: {outcome.error}")
            record_history(
                monitor, "ERROR",
                "Couldn't check BookMyShow — connection problem, not a 'no tickets' answer.",
                mirror=mirror, at=at,
            )

        for change in changes:
            report.changes.append(change)
            try:
                notifier(monitor, change)
            except Exception as exc:  # noqa: BLE001 - retried on the next tick
                report.email_errors.append(f"{monitor.id}: {exc}")
                ms.last_email_error = f"{type(exc).__name__}: {exc}"[:300]
                print(f"    email failed ({exc}) — will retry next check")
                continue
            report.emails_sent += 1
            ms.last_email_error = ""
            mark_notified(change, ms, at)
            record_history(
                monitor,
                change.kind.value,
                f"{change.venue_name} · {change.fmt} — {change.kind.value.replace('_', ' ').title()}",
                mirror=mirror, at=at,
                extra={"booking_url": change.booking_url},
            )

    if dirty:
        save_state(state, mirror=mirror)

    report.monitors = monitors
    report.state = state
    print(f"[checker] {report.summary()}")
    return report


__all__ = ["RunReport", "check_monitor", "evaluate_target", "run_once"]
