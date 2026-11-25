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
4. Collapse the running monitors into the listing reads they actually need
   (``monitor.sharing``) and fetch every read that anyone is due for, once.
   Several people waiting on the same film, city, dates and event share one
   read of BookMyShow — the venue and the format never reached the network
   in the first place, they filter the answer — and a monitor whose own
   clock was a few minutes behind the others' is evaluated against that
   read too, so their clocks agree from then on. A failure here becomes an
   ERROR record for every monitor waiting on it, never an availability.
5. Evaluate each theatre+format target independently, per monitor, against
   that read. Sharing the read changes no verdict.
6. Detect changes, send mail, and only then mark as notified — per monitor
   and per owner, so two accounts waiting on one read each get their own
   email, their own state and their own history.
7. Persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from config import readonly
from config.timezone import now_ist
from monitor.changes import Change, apply_outcome, detect_changes, mark_notified
from monitor.discovery import DiscoveryReport, discover_siblings
from monitor.models import (
    Availability,
    CheckOutcome,
    Monitor,
    MovieRef,
    ReadFailure,
    SeatCategory,
    Showtime,
    Snapshot,
    TargetResult,
    TheatreTarget,
)
from monitor import policy
from monitor.sharing import SharingReport, describe, select_for_fetch
from monitor.state import (
    MonitorState,
    backend_name,
    expire_due_monitors,
    load_monitors_for_checking,
    load_state,
    record_history,
    save_monitors,
    save_state,
)
from platforms import get_provider, is_enabled, platform_name
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
    #: How the due monitors collapsed into listing reads (``monitor.sharing``).
    sharing: SharingReport = field(default_factory=SharingReport)
    #: Listing reads this tick actually made — one per unique target, not one
    #: per monitor. ``len(checked) - fetches`` is the duplication avoided.
    fetches: int = 0
    #: The monitors and observed state this tick read and, where it changed
    #: them, wrote back — handed to the segment so deciding when to wake next
    #: does not read the same two collections a second time. Valid for this
    #: tick only; the next tick reads afresh.
    monitors: list[Monitor] = field(default_factory=list)
    state: dict[str, MonitorState] = field(default_factory=dict)
    #: True when this tick decided from ``known_state`` that nothing was due
    #: and read no state document at all (see ``run_once``).
    state_read_skipped: bool = False
    #: Monitor id → why ``monitor.policy`` refused it this tick. A refused
    #: monitor is never checked, never emailed, and never keeps a segment
    #: alive.
    rejected: dict[str, str] = field(default_factory=dict)
    #: Running monitors whose owners could not be looked up this tick: not
    #: checked, retried on the next tick.
    deferred: list[str] = field(default_factory=list)
    #: Listing reads that were due but did not fit this tick's request budget
    #: (``policy.TICK_REQUEST_BUDGET``). Untouched: still due, read first next tick.
    budget_deferred: list = field(default_factory=list)
    #: BookMyShow requests this tick's reads were expected to cost.
    budgeted_requests: int = 0
    #: A dry run (``run_once(dry_run=True)``): everything was evaluated and
    #: nothing was sent or written. ``would_send`` is every alert a real run
    #: would have emailed this tick; ``would_expire`` every monitor it would
    #: have marked EXPIRED.
    dry_run: bool = False
    would_send: list[Change] = field(default_factory=list)
    would_expire: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"checked={len(self.checked)} skipped={len(self.skipped)} "
            f"expired={len(self.expired)} failed={len(self.failed)} "
            f"changes={len(self.changes)} emails={self.emails_sent}"
        )
        if self.fetches:
            text += f" fetches={self.fetches}"
        if self.sharing.saved:
            text += f" saved={self.sharing.saved}"
        if self.discovery.ran:
            text += f" discovery[{self.discovery.summary()}]"
        if self.dry_run:
            text += (f" DRY-RUN would_email={len(self.would_send)}"
                     f" would_expire={len(self.would_expire)} (nothing sent or written)")
        return text


def short_id(monitor_id: str) -> str:
    """A monitor id as it appears in a log line. The repository — and so
    every Actions log — is public; eight characters still tell two monitors
    apart when reading a run, without publishing the whole identifier."""
    return monitor_id[:8]


# ──────────────────────────────────────────────────────────────────────────
# Evaluating one target against one snapshot
# ──────────────────────────────────────────────────────────────────────────
def evaluate_target(monitor: Monitor, target: TheatreTarget, snapshot: Snapshot) -> TargetResult:
    """Turn a snapshot into a verdict for one theatre+format.

    The ladder of "no" answers matters: not knowing the theatre exists, the
    theatre existing without this format, the format existing without an open
    sale, and the sale being sold out are four different situations, and the
    UI says four different things about them.

    Every one of those "no" answers is a claim about the *whole* listing, so
    none of them can be made from a partial read (``Snapshot.failures``). A
    request that did not answer may be the one that carries this theatre —
    a premium-format sibling event is precisely where a Dolby or IMAX screen
    is listed — and which theatres an unread event holds is not knowable
    from its name. So when a failed request could concern the dates this
    monitor watches, the only verdict that survives is the one a partial
    read can still prove: AVAILABLE, from a bookable show that *was* read.
    Anything else becomes UNKNOWN, which is not an answer: the target keeps
    its last real observation, nothing is re-armed and nothing is emailed.
    """
    result = _evaluate(monitor, target, snapshot)
    if result.availability is Availability.AVAILABLE:
        return result
    missing = snapshot.failures_for(monitor.date_codes)
    if not missing:
        return result
    print(f"    [partial] {target.venue_name} · {target.fmt}: {len(missing)} request(s) of this listing "
          f"did not answer ({', '.join(f.where for f in missing[:4])}{'…' if len(missing) > 4 else ''}) — "
          f"not recording '{result.availability.value}'; keeping the last observation")
    return TargetResult(
        target_key=target.key,
        venue_name=target.venue_name,
        fmt=target.fmt,
        availability=Availability.UNKNOWN,
        date_code=result.date_code,
        booking_url=result.booking_url,
        detail=describe_failures(missing),
    )


def describe_failures(failures: list[ReadFailure]) -> str:
    """One sentence for a read that came back in part."""
    refused = sum(1 for f in failures if f.blocked)
    how = ("refused" if refused == len(failures)
           else "did not answer for" if not refused else "refused or did not answer for")
    return (f"BookMyShow {how} {len(failures)} part(s) of this listing, so this check "
            "can't say what is or isn't listed. Not a 'no tickets' answer.")


def _evaluate(monitor: Monitor, target: TheatreTarget, snapshot: Snapshot) -> TargetResult:
    """The verdict the snapshot's contents give, taken at face value.
    :func:`evaluate_target` decides whether a partial read may give it."""
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

    matching = [s for s in shows if target.matches_show(s)]
    if not matching:
        seen = sorted({label for s in shows for label in (s.format_labels or ("(no format)",))})
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

    if monitor.is_category_watch:
        return _evaluate_categories(monitor, target, snapshot, matching)

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

    # SOLD_OUT is a claim about everything this target watches, so every
    # matching show has to say so — and a show only says so when every one
    # of its seat categories does (``BookMyShowProvider._availability``).
    # One show that has not opened for sale, or whose status could not be
    # read, means the data cannot prove "no seats left": that is
    # NOT_BOOKABLE, the same rule the category watch below holds itself to.
    sold_out = [s for s in matching if s.availability is Availability.SOLD_OUT]
    date_code = _earliest_date(matching)
    if sold_out and len(sold_out) == len(matching):
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
        detail=(f"{len(sold_out)} of {len(matching)} showtime(s) are sold out; the rest "
                "haven't opened for booking or sent no status we can read."
                if sold_out else "Showtimes are listed but booking hasn't opened."),
    )


def _evaluate_categories(monitor: Monitor, target: TheatreTarget, snapshot: Snapshot,
                         shows: list[Showtime]) -> TargetResult:
    """The category watch's verdict for shows already narrowed to the
    theatre, the dates and the format: AVAILABLE only when one of the
    watched seat categories is bookable in one of them. Reads nothing the
    ordinary verdict did not — the categories came with the same response."""
    shows = [s for s in shows if monitor.matches_show_time(s)]
    if not shows:
        return TargetResult(
            target_key=target.key, venue_name=target.venue_name, fmt=target.fmt,
            availability=Availability.SHOW_NOT_AVAILABLE,
            detail=f"No show at {monitor.show_time} is listed yet.",
            booking_url=_booking_url(monitor, snapshot, "", target),
        )

    def watched(show: Showtime) -> list[SeatCategory]:
        # every BookMyShow block the watch names at this show — a logical
        # section can be more than one, and is matched with the show itself
        return [c for c in show.categories if monitor.watches_category(c, show)]

    live = [s for s in shows if any(c.availability is Availability.AVAILABLE for c in watched(s))]
    if live:
        date_code = _earliest_date(live)
        names = sorted({c.label for s in live for c in watched(s) if c.availability is Availability.AVAILABLE})
        return TargetResult(
            target_key=target.key, venue_name=target.venue_name, fmt=target.fmt,
            availability=Availability.AVAILABLE, showtimes=live, date_code=date_code,
            booking_url=_booking_url(monitor, snapshot, date_code, target),
            detail=f"{', '.join(names)} bookable at {len(live)} showtime(s).",
        )

    # SOLD_OUT only when every watched block BookMyShow lists says so: one
    # whose status could not be read is "we don't know", never "no seats
    # left" — and it keeps the whole verdict at "we don't know".
    listed = [s for s in shows if watched(s)]
    blocks = [c for s in listed for c in watched(s)]
    date_code = _earliest_date(shows)
    # …and only when no show in scope is still to open: a show that has
    # published no seat categories at all is not on sale yet, and its seats
    # are not "gone". (A show that *has* published its categories and simply
    # does not have this section is not waiting on anything.)
    unopened = [s for s in shows if not s.categories]
    if blocks and not unopened and all(c.availability is Availability.SOLD_OUT for c in blocks):
        return TargetResult(
            target_key=target.key, venue_name=target.venue_name, fmt=target.fmt,
            availability=Availability.SOLD_OUT, showtimes=listed, date_code=date_code,
            booking_url=_booking_url(monitor, snapshot, date_code, target),
            detail=f"{monitor.category_label}: no seats left at the shows listed.",
        )
    seen = sorted({c.label for s in shows for c in s.categories})
    if blocks and unopened and all(c.availability is Availability.SOLD_OUT for c in blocks):
        detail = (f"{monitor.category_label}: no seats left at {len(listed)} showtime(s); "
                  f"{len(unopened)} more haven't opened for booking.")
    elif any(watched(s) for s in shows):
        detail = f"{monitor.category_label} is listed, but BookMyShow sent no status we can read for it."
    elif seen:
        detail = f"{monitor.category_label} not listed for these shows (listed: {', '.join(seen)})."
    else:
        detail = "Shows are listed but no seat categories are published yet."
    return TargetResult(
        target_key=target.key, venue_name=target.venue_name, fmt=target.fmt,
        availability=Availability.NOT_BOOKABLE, showtimes=shows, date_code=date_code,
        booking_url=_booking_url(monitor, snapshot, date_code, target),
        detail=detail,
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
    for entry in catalogue.list_entries(movie.region_slug, platform=movie.platform):
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
# Reading the listing, and reading a monitor out of it
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Listing:
    """One read of a platform listing — or the reason there isn't one.

    Separated from the monitor it was read for so that several monitors
    waiting on the same listing can share it (``monitor.sharing``). A
    failure is carried here rather than raised, so every monitor in a shared
    group records the same honest ERROR instead of one of them swallowing it.
    """

    snapshot: Snapshot | None = None
    error: str = ""
    blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.snapshot is not None


def fetch_listing(movie: MovieRef, date_codes: list[str] | None = None,
                  venue_codes: list[str] | None = None) -> Listing:
    """Read one platform listing. Never raises.

    ``movie`` is taken as given — already resolved through the catalogue by
    the caller — so a shared read is made once with one reference rather than
    re-resolved per subscriber.

    ``venue_codes`` reaches only a provider that reads per cinema
    (``reads_per_venue``, PVR INOX); BookMyShow reads a whole city per event
    and is called exactly as before. A platform that is not enabled is never
    read at all: a monitor on it records an honest ERROR.
    """
    if not is_enabled(movie.platform):
        return Listing(error=f"{platform_name(movie.platform)} is not enabled in TicketRadar; "
                             "nothing was checked.")
    try:
        provider = get_provider(movie.platform)
        if getattr(provider, "reads_per_venue", False):
            snapshot = provider.fetch(movie, date_codes or None, venue_codes=list(venue_codes or []))
        else:
            snapshot = provider.fetch(movie, date_codes or None)
    except PlatformBlocked as exc:
        return Listing(error=str(exc), blocked=True)
    except PlatformError as exc:
        return Listing(error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a parser bug must not kill the run
        return Listing(
            error=f"Unexpected failure while reading the listing: {type(exc).__name__}: {exc}",
        )
    return Listing(snapshot=snapshot)


def reads_per_venue(platform: str) -> bool:
    """Does this platform's provider read per cinema (PVR INOX)? BookMyShow
    does not, and its read is made exactly as it always was."""
    try:
        return bool(getattr(get_provider(platform), "reads_per_venue", False))
    except PlatformError:
        return False


def outcome_for(monitor: Monitor, listing: Listing, *, at: datetime | None = None) -> CheckOutcome:
    """Evaluate one monitor's targets against a listing already read.

    Pure with respect to the network: every monitor sharing a listing is
    evaluated independently here, against its own targets, its own dates and
    its own format rules, so sharing the read changes no verdict.
    """
    at = at or now_ist()
    if not listing.ok:
        return CheckOutcome(monitor.id, at, ok=False, error=listing.error, blocked=listing.blocked)
    snapshot = listing.snapshot
    results = [evaluate_target(monitor, t, snapshot) for t in monitor.targets]
    missing = snapshot.failures_for(monitor.date_codes)
    if missing and results and not any(r.availability.is_answer for r in results):
        # A partial read that could answer for none of this monitor's
        # targets is, for this monitor, a check that did not happen. Recorded
        # as one (state untouched, "couldn't check" on the page) rather than
        # as a successful check that learned nothing.
        return CheckOutcome(monitor.id, at, ok=False, error=describe_failures(missing),
                            blocked=all(f.blocked for f in missing), partial=True)
    return CheckOutcome(monitor.id, at, ok=True, results=results, partial=bool(missing))


def check_monitor(monitor: Monitor, *, at: datetime | None = None) -> CheckOutcome:
    """Read the platform once and evaluate every target. Never raises.

    The single-monitor path, unchanged in behaviour: a theatre often releases
    a film under a *new* premium-format event (a "Dolby Cinema 2D" sibling
    that did not exist when the monitor was saved), so the sweep is the film
    as the catalogue knows it now, not as of the day the monitor was created.
    """
    at = at or now_ist()
    movie = with_current_variants(monitor.movie)
    if reads_per_venue(movie.platform):
        listing = fetch_listing(movie, monitor.date_codes, venue_codes=[t.venue_code for t in monitor.targets])
    else:
        listing = fetch_listing(movie, monitor.date_codes)
    return outcome_for(monitor, listing, at=at)


# ──────────────────────────────────────────────────────────────────────────
# The worker tick
# ──────────────────────────────────────────────────────────────────────────
#: Availabilities that mean "this theatre was in the listing when we looked".
_SEEN_LISTED = frozenset({Availability.AVAILABLE, Availability.SOLD_OUT, Availability.NOT_BOOKABLE,
                          Availability.SHOW_NOT_AVAILABLE})


def _warn_if_listing_vanished(group, listing: Listing, state: dict[str, MonitorState]) -> None:
    """A canary, not a verdict: one warning when a listing that monitors had
    seen theatres in comes back *complete and with no theatres at all*.

    A film leaving cinemas looks like this too, so nothing is held back or
    changed — the read is recorded as it stands. But it is also exactly what
    a response-format change that slipped past validation would look like,
    and that must not be silent. It fires on the transition only: once the
    empty read is recorded the monitors no longer count as having seen the
    theatre, so a film that has genuinely gone warns once, not every tick.
    """
    snapshot = listing.snapshot
    if snapshot is None or snapshot.venues or snapshot.failures:
        return
    had = [m for m in group.monitors
           if any(ts.availability in _SEEN_LISTED
                  for ts in (state.get(m.id) or MonitorState()).targets.values())]
    if had:
        print(f"::warning::{group.key.label} came back with no theatres at all, though "
              f"{len(had)} monitor(s) had seen theatres listed there. If the film is still "
              "showing, BookMyShow's response format may have changed.", flush=True)


def _record_history_safely(monitor: Monitor, kind: str, message: str, **kwargs) -> None:
    """The activity log is a convenience; a check and its alert are not.
    A history write that fails is logged and dropped — it must never undo a
    check, lose the record that an email went out, or end the tick."""
    try:
        record_history(monitor, kind, message, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never worth a tick
        print(f"    history not written for {short_id(monitor.id)} ({type(exc).__name__})", flush=True)


def _persist_now(state: dict[str, MonitorState], carry: dict[str, MonitorState] | None,
                 monitor_id: str, *, mirror: bool) -> bool:
    """Write one monitor's state record now. Never raises.

    Called the moment an alert has gone out, so "this was announced" reaches
    the store before anything else can fail. Only that monitor's record:
    the ones the tick has not reached yet are still blank and are not worth
    a write. If the store refuses, the record is parked in ``carry`` — the
    segment's own memory, handed back to the next tick — so the same process
    does not announce the same thing twice while the store is down.
    """
    try:
        save_state(state, mirror=mirror, only=[monitor_id])
    except Exception as exc:  # noqa: BLE001 - reported, parked, retried
        print(f"::warning::monitoring state could not be saved ({type(exc).__name__}); "
              "kept in memory and retried", flush=True)
        if carry is not None:
            carry[monitor_id] = state[monitor_id]
        return False
    if carry is not None:
        carry.pop(monitor_id, None)
    return True


def _park(state: dict[str, MonitorState], carry: dict[str, MonitorState] | None) -> None:
    if carry is not None:
        carry.update({mid: ms for mid, ms in state.items() if ms.is_dirty()})


def _within_budget(groups: list, state: dict[str, MonitorState]) -> tuple[list, list]:
    """The reads this tick can afford, most overdue first, and the rest.

    Each read costs ``FetchKey.request_count`` BookMyShow requests. Reads
    are taken in order of how long their longest-waiting monitor has gone
    unchecked — never checked first — until :data:`policy.TICK_REQUEST_BUDGET`
    would be exceeded; the first is always taken, so a read can never be
    starved. What is left is not checked and not touched: it is still due,
    and the next tick (thirty seconds on) reaches it first, because it has
    now waited longest. One account's expensive monitors therefore delay
    their own checks, not everybody else's.
    """
    def waited(group) -> float:
        seen = [state.get(m.id).last_check_at for m in group.monitors
                if state.get(m.id) is not None and state.get(m.id).last_check_at is not None]
        if not seen or len(seen) < len(group.monitors):
            return float("inf")              # a member never checked waits longest
        return -min(seen).timestamp()

    ordered = sorted(groups, key=waited, reverse=True)
    taken, left, spent = [], [], 0
    for group in ordered:
        cost = group.key.request_count
        if taken and spent + cost > policy.TICK_REQUEST_BUDGET:
            left.append(group)
            continue
        taken.append(group)
        spent += cost
    if left:
        print(f"[checker] request budget: {len(taken)} read(s) this tick ({spent} requests); "
              f"{len(left)} wait for the next tick", flush=True)
    # Keep the original order among the reads taken, so a tick with room
    # for everything behaves exactly as it always has.
    order = {id(g): i for i, g in enumerate(groups)}
    return sorted(taken, key=lambda g: order[id(g)]), left


def _no_mail_in_a_dry_run(monitor, change):
    raise AssertionError("a dry run must never reach the notifier")


def run_once(*, at: datetime | None = None, force: bool = False, monitor_id: str = "",
             mirror: bool = False, notifier=None,
             known_state: dict[str, MonitorState] | None = None,
             carry: dict[str, MonitorState] | None = None,
             dry_run: bool = False) -> RunReport:
    """Check every monitor that is due (see :func:`_run_once`).

    ``dry_run`` evaluates exactly what a real tick would — the same reads,
    the same verdicts, the same change detection — and then sends nothing
    and writes nothing: no email, no "sent" mark, no observed state, no
    history, no monitor marked EXPIRED, no end date clamped, no sibling
    discovery (a city re-listing whose result would have to be stored). The
    alerts a real tick would send are in ``report.would_send``. The whole
    tick runs under :func:`config.readonly.read_only`, so any write that is
    reached anyway is refused rather than performed. A later real run sees
    the store exactly as it was, and alerts exactly as it would have.
    """
    if not dry_run:
        return _run_once(at=at, force=force, monitor_id=monitor_id, mirror=mirror, notifier=notifier,
                         known_state=known_state, carry=carry, dry_run=False)
    with readonly.read_only():
        return _run_once(at=at, force=force, monitor_id=monitor_id, mirror=False,
                         notifier=_no_mail_in_a_dry_run, known_state=known_state, carry=None,
                         dry_run=True)


def _run_once(*, at: datetime | None, force: bool, monitor_id: str, mirror: bool, notifier,
              known_state: dict[str, MonitorState] | None,
              carry: dict[str, MonitorState] | None, dry_run: bool) -> RunReport:
    """Check every monitor that is due. This is what the workflow calls.

    ``mirror`` is False by default because inside Actions the workflow makes
    one commit of the whole data directory at the end; mirroring each write
    through the API as well would race it.

    ``known_state`` is the segment's own copy of the observed state from
    its previous tick — the records this same process last wrote. The
    worker is the only writer of state, and the workflow runs one segment
    at a time, so for a monitor that was running then and is running now
    that copy *is* what the store holds. When every running monitor is in
    it and none is due, this tick reads no state document: nothing would be
    checked, so nothing would be written. Anything else — a monitor that is
    new, extended, due, or was never checked; a forced tick; a tick for a
    named monitor — reads the collection exactly as before. Monitors are
    read every tick regardless, so a monitor stopped or created in the app
    is seen at once and expiry runs as it always has.

    ``carry`` is the segment's memory of state records a previous tick
    changed but could not save (the store was failing). They are laid over
    whatever this tick reads, still marked as changed, so a monitor already
    emailed is not emailed again and a monitor already checked is not
    checked again every tick for as long as the store is down. This tick
    empties ``carry`` when its own save succeeds and refills it when it does
    not. It lives in the worker process only: a segment that dies with
    unsaved records can still repeat an email — delivery is at-least-once.

    One monitor failing — an exception anywhere in its evaluation, its alert
    or its bookkeeping — is recorded against that monitor and the tick moves
    on to the next. State is saved as soon as an alert has been sent, not
    only at the end of the tick.
    """
    at = at or now_ist()
    report = RunReport(started_at=at, dry_run=dry_run)

    if notifier is None:
        from notifications.email import send_change_email

        notifier = send_change_email

    # Only what can still be checked. On Firestore that is the ACTIVE
    # documents alone — a stopped or expired monitor is never read again by
    # the worker, and never written back by it either.
    if dry_run:
        # Read only. What *would* expire is reported; nothing is flipped —
        # a monitor past its end time is not running either way.
        stored, newly_expired = load_monitors_for_checking(), []
        report.would_expire = [m.id for m in stored if m.status.is_running and m.is_expired(at)]
    else:
        stored, newly_expired = expire_due_monitors(load_monitors_for_checking(), at=at, mirror=mirror)
    report.expired = [m.id for m in newly_expired]

    # The worker is a security boundary of its own: a stored monitor is
    # data, not an instruction. ``monitor.policy`` binds each alert to its
    # owner's verified account address, enforces the intervals, the end-date
    # cap, admin-only features and the active-monitor limit, and refuses
    # anything malformed — whatever the document, the rules or the app said.
    screened = policy.screen(stored, at=at, resolver=policy.worker_resolver(backend_name()))
    report.rejected = dict(screened.rejected)
    report.deferred = [m.id for m in screened.deferred]
    for monitor_ref in screened.deferred:
        report.skipped.append(f"{short_id(monitor_ref.id)} (owner not verified yet)")
    for rejected_id in screened.rejected:
        report.skipped.append(f"{short_id(rejected_id)} (refused)")
    if screened.clamped and not dry_run:
        # The whole list: the JSON store rewrites its file from what it is
        # given, so a subset would drop every other monitor.
        save_monitors(stored, mirror=mirror, changed=screened.clamped)
    monitors = screened.eligible

    if known_state is not None and not force and not monitor_id and all(
        m.id in known_state and not known_state[m.id].is_due(m.interval_minutes, at)
        for m in monitors if m.is_running(at)
    ):
        state = known_state
        report.state_read_skipped = True
    else:
        state = load_state()
    if carry:
        # Records an earlier tick could not save: newer than the store's —
        # for the monitors still being checked. One stopped or deleted in
        # the app meanwhile is dropped, so no state is written for it.
        live_ids = {m.id for m in monitors}
        for stale in [mid for mid in carry if mid not in live_ids]:
            carry.pop(stale)
        state.update(carry)
    dirty = bool(newly_expired) or bool(carry)

    running: list[Monitor] = []
    due: list[Monitor] = []
    for monitor in monitors:
        if not monitor.is_running(at):
            if not monitor_id or monitor.id == monitor_id:
                report.skipped.append(f"{short_id(monitor.id)} ({monitor.status.value.lower()})")
            continue
        ms: MonitorState = state.setdefault(monitor.id, MonitorState())
        running.append(monitor)
        if monitor_id and monitor.id != monitor_id:
            continue
        if force or ms.is_due(monitor.interval_minutes, at):
            due.append(monitor)

    # A theatre that is "not listed yet" may be listed under a sibling event
    # BookMyShow created after the monitor was saved. Learn of such events
    # *before* fetching, so this tick's sweep is the film as it is now.
    # (Not in a dry run: what it learns has to be stored to be of any use,
    # and a dry run stores nothing. It evaluates on the siblings known now.)
    if not dry_run:
        report.discovery = discover_siblings(monitors, due, state, at=at, mirror=mirror)
    if report.discovery.updated:
        save_monitors(stored, mirror=mirror,
                      changed=[m for m in stored if m.id in report.discovery.updated])

    # Several people wanting the same seat want the same answer. Collapse the
    # running monitors into the listing reads they actually need, read every
    # read that someone is due for once, and hand the same snapshot to every
    # monitor waiting on it — including the ones whose own clock was a few
    # minutes behind, which are checked early this once and are due with the
    # others from now on. The grouping is on what reaches the network only
    # (``monitor.sharing``); everything below — evaluation, state,
    # notification, history — stays per monitor and per owner, exactly as
    # it was.
    groups = select_for_fetch(running, (m.id for m in due), resolve=with_current_variants)
    groups, report.budget_deferred = _within_budget(groups, state)
    checked_ids = {m.id for g in groups for m in g.monitors}
    deferred_ids = {m.id for g in report.budget_deferred for m in g.monitors}
    for monitor in running:
        if monitor.id in deferred_ids:
            report.skipped.append(f"{short_id(monitor.id)} (waiting for the next tick: request budget)")
        elif monitor.id not in checked_ids and (not monitor_id or monitor.id == monitor_id):
            report.skipped.append(f"{short_id(monitor.id)} (not due)")
    report.sharing = describe(groups)
    if report.sharing.saved or report.sharing.aligned:
        print(f"[checker] sharing: {report.sharing.summary()}")

    for group in groups:
        if group.shared:
            print(f"[checker] one read for {len(group.monitors)} monitors "
                  f"({len(group.subscribers)} account(s)) — {group.key.label}")
        if group.riders:
            print(f"[checker] aligned {', '.join(short_id(r) for r in group.riders)} "
                  f"to this read; due together from now on")
        if reads_per_venue(group.movie.platform):
            listing = fetch_listing(group.movie, group.date_codes, venue_codes=group.venue_codes)
        else:
            listing = fetch_listing(group.movie, group.date_codes)
        report.fetches += 1
        report.budgeted_requests += group.key.request_count
        _warn_if_listing_vanished(group, listing, state)

        for monitor in group.monitors:
            # One monitor is one unit of failure. Whatever goes wrong inside
            # it — a parser surprise in its evaluation, its alert, its
            # bookkeeping — is this monitor's problem this tick, and the
            # next monitor is still checked.
            try:
                if _check_one(monitor, listing, state, report, at=at, mirror=mirror,
                              notifier=notifier, carry=carry, dry_run=dry_run):
                    dirty = True
            except Exception as exc:  # noqa: BLE001 - isolate, report, move on
                dirty = True
                if monitor.id not in report.failed:
                    report.failed.append(monitor.id)
                print(f"::warning::monitor {short_id(monitor.id)} could not be processed this tick "
                      f"({type(exc).__name__}); the others continue", flush=True)

    if dirty and not dry_run:
        try:
            save_state(state, mirror=mirror)
        except Exception:
            # Nothing this tick learned reached the store. Park it for the
            # next tick (so nobody is emailed or checked twice meanwhile)
            # and let the failure be seen: the segment decides what next.
            _park(state, carry)
            raise
        if carry is not None:
            carry.clear()

    report.monitors = monitors
    report.state = state
    print(f"[checker] {report.summary()}")
    return report


def _check_one(monitor: Monitor, listing: Listing, state: dict[str, MonitorState],
               report: RunReport, *, at: datetime, mirror: bool, notifier,
               carry: dict[str, MonitorState] | None, dry_run: bool = False) -> bool:
    """Evaluate one monitor against a listing already read, alert on what
    changed and record it. Returns True when its state record changed.

    The order is the guarantee: the alert is sent, *then* marked as sent,
    *then* that mark is written to the store before anything else happens.
    A send that fails is never marked (it is retried next check); a mark
    that cannot be saved stays in the segment's memory (``_persist_now``).
    """
    ms = state[monitor.id]
    print(f"[checker] {short_id(monitor.id)} — {monitor.movie.title} "
          f"({len(monitor.targets)} target(s))")
    outcome = outcome_for(monitor, listing, at=at)
    changes = detect_changes(monitor, outcome, ms)
    apply_outcome(outcome, ms)

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
        if not dry_run:
            _record_history_safely(
                monitor, "ERROR",
                "Couldn't check BookMyShow — connection problem, not a 'no tickets' answer.",
                mirror=mirror, at=at,
            )

    for change in changes:
        report.changes.append(change)
        if dry_run:
            # Reported, never sent and never marked: the next real run finds
            # this alert still owed and sends it.
            report.would_send.append(change)
            print(f"    [dry-run] would email: {change.headline} ({change.venue_name} · {change.fmt})")
            continue
        try:
            notifier(monitor, change)
        except Exception as exc:  # noqa: BLE001 - retried on the next tick
            report.email_errors.append(f"{short_id(monitor.id)}: {exc}")
            ms.last_email_error = f"{type(exc).__name__}: {exc}"[:300]
            print(f"    email failed ({exc}) — will retry next check")
            continue
        report.emails_sent += 1
        ms.last_email_error = ""
        mark_notified(change, ms, at)
        # The alert is out. Say so in the store *now* — before the history
        # line, before the next monitor, before the end-of-tick save — so
        # that nothing failing later in this tick can make it go out again.
        _persist_now(state, carry, monitor.id, mirror=mirror)
        _record_history_safely(
            monitor,
            change.kind.value,
            f"{change.venue_name} · {change.fmt} — {change.kind.value.replace('_', ' ').title()}",
            mirror=mirror, at=at,
            extra={"booking_url": change.booking_url},
        )
    return True


__all__ = ["Listing", "RunReport", "check_monitor", "describe_failures", "evaluate_target",
           "fetch_listing", "outcome_for", "reads_per_venue", "run_once", "short_id"]
