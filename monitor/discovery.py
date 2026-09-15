"""Sibling-event discovery for running monitors.

Why this exists
---------------
A theatre releases a film on BookMyShow as an *event*, and a premium format
("Dolby Cinema 2D", "Barco Laser", "4DX 3D") very often arrives as a brand-new
sibling event of the film rather than as showtimes inside an existing one. The
checker sweeps every sibling it knows about: the ones saved on the monitor when
it was created, plus the ones the catalogue has recorded since
(:func:`monitor.checker.with_current_variants`).

Neither of those sources learns about a sibling BookMyShow created *after* the
monitor was saved. The monitor is a snapshot of the catalogue row, and the
catalogue only re-lists the city when the sync workflow runs — a loose
four-times-a-day schedule, or **Settings → Refresh catalogue** by hand. Between
those, a running monitor could read the same, incomplete family of events for
hours and honestly report the theatre as "not listed yet" while BookMyShow was
already selling it under an event nobody had told the worker about.

So while something is being watched, the worker re-lists the city itself.

* **Targeted.** One QUICKBOOK request per city — the very request the catalogue
  sync starts with — not a full catalogue sync. No per-movie detail reads.
* **Only when it can matter.** It runs when a *due* monitor still has a target
  it hasn't found (``UNRESOLVED``). A target that is already bookable, sold out
  or waiting for booking to open has found its event; a new sibling changes
  nothing for it.
* **Shared and rate-limited.** Every monitor in the city shares the one
  listing, and a city is re-listed at most once per :data:`DISCOVERY_TTL`
  (a failed attempt retries after :data:`DISCOVERY_RETRY`). A catalogue sync
  counts as a listing too, so a manual refresh doesn't trigger a redundant one.
  The clock lives in ``data/discovery.json`` so it survives across worker
  segments, and nothing is listed while nothing is being watched.
* **Never destructive.** Siblings are only ever *added* to a monitor. A listing
  strategy that answers with fewer siblings, a movie missing from the listing,
  or a blocked request leaves every monitor exactly as it was.

New siblings are merged onto the monitor and persisted, so the very next fetch
— in the same tick — sweeps them, and the ordinary state-transition rules in
``monitor.changes`` decide whether that is worth an email. Nothing here writes
the catalogue: the sync notices the changed family on its own
(``catalogue.siblings_changed``) and re-reads the movie's theatre detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from config.store import load_discovery, save_discovery
from config.timezone import now_ist, parse_iso, to_iso
from monitor.models import Availability, Monitor, MovieRef
from monitor.state import MonitorState
from platforms import get_provider
from platforms.base import PlatformBlocked, PlatformError

#: How long one city listing is trusted before a watching worker re-lists it.
#: Shorter than the catalogue's six-hour detail TTL by design: this is the
#: path a release takes to a *running* monitor, and one request every quarter
#: hour — only while something is watched and unresolved — is cheap.
DISCOVERY_TTL = timedelta(minutes=15)

#: Back-off after a failed listing (BookMyShow's bot check is intermittent).
DISCOVERY_RETRY = timedelta(minutes=5)

#: A target in one of these states has not found its theatre+format yet — the
#: only situation a newly created sibling event can change.
UNRESOLVED = frozenset(
    {
        Availability.UNKNOWN,
        Availability.NOT_FOUND,
        Availability.THEATRE_NOT_AVAILABLE,
        Availability.SHOW_NOT_AVAILABLE,
    }
)


@dataclass
class DiscoveryReport:
    """What one tick's discovery did — printed to the Actions log, asserted in tests."""

    listed: list[str] = field(default_factory=list)     # cities re-listed this tick
    skipped: list[str] = field(default_factory=list)    # "hyderabad (listed 6 min ago)"
    failed: list[str] = field(default_factory=list)     # "hyderabad: refused (HTTP 403)"
    #: monitor id -> the sibling events it did not know about until now.
    updated: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return bool(self.listed or self.failed)

    def summary(self) -> str:
        new = sum(len(v) for v in self.updated.values())
        return f"listed={len(self.listed)} failed={len(self.failed)} new_siblings={new}"


# ──────────────────────────────────────────────────────────────────────────
# Deciding whether to look
# ──────────────────────────────────────────────────────────────────────────
def needs_discovery(monitor: Monitor, ms: MonitorState | None) -> str:
    """Why this monitor could benefit from a fresh listing — '' when it can't."""
    for target in monitor.targets:
        ts = ms.targets.get(target.key) if ms is not None else None
        availability = ts.availability if ts is not None else Availability.UNKNOWN
        if availability in UNRESOLVED:
            return f"{target.label} is {availability.value}"
    return ""


def _region_key(monitor: Monitor) -> str:
    return f"{monitor.movie.platform}:{monitor.movie.region_slug}"


def last_listed_at(region_key: str, clock: dict[str, Any]) -> tuple[datetime | None, bool]:
    """``(when, ok)`` for the most recent listing of a city, from either the
    worker's own clock or the catalogue sync — whichever is later."""
    raw = clock.get(region_key)
    raw = raw if isinstance(raw, dict) else {}
    when, ok = parse_iso(raw.get("at")), bool(raw.get("ok", False))

    from monitor import catalogue  # local: catalogue imports platforms too

    _platform, _, slug = region_key.partition(":")
    sync = catalogue.sync_state(slug)
    if sync["at"] is not None and not sync["status"].is_failure:
        if when is None or sync["at"] > when:
            when, ok = sync["at"], True
    return when, ok


def is_fresh(region_key: str, clock: dict[str, Any], at: datetime) -> tuple[bool, str]:
    when, ok = last_listed_at(region_key, clock)
    if when is None:
        return False, "never listed by the worker"
    age = at - when
    limit = DISCOVERY_TTL if ok else DISCOVERY_RETRY
    minutes = int(age.total_seconds() // 60)
    if age < limit:
        left = max(int((limit - age).total_seconds() // 60), 1)
        return True, (f"listed {minutes} min ago — next re-list in {left} min" if ok
                      else f"last attempt failed {minutes} min ago — retry in {left} min")
    return False, (f"last listing {minutes} min ago" if ok
                   else f"last attempt failed {minutes} min ago")


# ──────────────────────────────────────────────────────────────────────────
# Reading the family off a fresh listing
# ──────────────────────────────────────────────────────────────────────────
def family_for(movie: MovieRef, listed: Iterable[MovieRef]) -> tuple[tuple[str, str], ...] | None:
    """The sibling events the listing names for this movie, or None if the
    listing does not mention it at all.

    Matches the movie's own row first. Failing that, the event may have become
    a sibling of a *different* primary — BookMyShow re-picks which child is
    the plain screening — in which case the family is that primary plus its
    other siblings, minus ourselves.
    """
    rows = list(listed)
    row = next((m for m in rows if m.id == movie.id), None)
    if row is not None:
        return row.variants
    for m in rows:
        if m.platform == movie.platform and movie.event_code in m.variant_codes:
            return ((m.event_code, ""), *((c, l) for c, l in m.variants if c != movie.event_code))
    return None


def merge_variants(known: tuple[tuple[str, str], ...], extra: Iterable[tuple[str, str]],
                   *, own_code: str) -> tuple[tuple[str, str], ...]:
    """Union by event code, keeping known labels. Never drops a sibling."""
    merged: dict[str, str] = {}
    for code, label in (*known, *extra):
        if not code or code == own_code:
            continue
        merged.setdefault(code, label)
        if not merged[code] and label:
            merged[code] = label
    return tuple(merged.items())


# ──────────────────────────────────────────────────────────────────────────
# The step the checker runs before checking
# ──────────────────────────────────────────────────────────────────────────
def discover_siblings(monitors: list[Monitor], due: list[Monitor], state: dict[str, MonitorState],
                      *, at: datetime | None = None, mirror: bool = False,
                      provider_for: Callable[[str], Any] | None = None) -> DiscoveryReport:
    """Re-list each city a due, unresolved monitor is watching, and merge any
    sibling events the listing names that the monitors don't know yet.

    ``monitors`` is every monitor (the file as loaded); ``due`` the ones this
    tick will check. New siblings are merged onto *every* running monitor of
    that movie — the listing is shared — and the caller persists ``monitors``
    when anything in ``report.updated`` changed. Never raises.
    """
    at = at or now_ist()
    provider_for = provider_for or get_provider
    report = DiscoveryReport()

    # Which cities need a fresh listing, and the first reason why.
    reasons: dict[str, str] = {}
    for monitor in due:
        why = needs_discovery(monitor, state.get(monitor.id))
        if why:
            reasons.setdefault(_region_key(monitor), f"{monitor.movie.title}: {why}")
    if not reasons:
        return report

    clock = load_discovery()
    dirty = False
    for region_key, why in reasons.items():
        platform, _, slug = region_key.partition(":")
        fresh, age = is_fresh(region_key, clock, at)
        if fresh:
            report.skipped.append(f"{slug} ({age})")
            print(f"[discover] {slug}: {age}; not re-listing ({why})")
            continue

        watched = [m for m in monitors if m.is_running(at) and _region_key(m) == region_key]
        titles = sorted({m.movie.title for m in watched})
        print(f"[discover] {slug}: re-listing the city for {len(watched)} monitor(s) on "
              f"{len(titles)} movie(s) — {why}; {age}")
        try:
            listed = provider_for(platform).list_movies(slug)
        except PlatformBlocked as exc:
            report.failed.append(f"{slug}: {exc}")
            print(f"[discover] {slug}: listing refused ({exc}) — keeping known siblings, "
                  f"retry in {int(DISCOVERY_RETRY.total_seconds() // 60)} min")
            clock[region_key] = {"at": to_iso(at), "ok": False, "message": str(exc)[:300]}
            dirty = True
            continue
        except PlatformError as exc:
            report.failed.append(f"{slug}: {exc}")
            print(f"[discover] {slug}: listing failed ({exc}) — keeping known siblings, "
                  f"retry in {int(DISCOVERY_RETRY.total_seconds() // 60)} min")
            clock[region_key] = {"at": to_iso(at), "ok": False, "message": str(exc)[:300]}
            dirty = True
            continue
        except Exception as exc:  # noqa: BLE001 - a parser bug must not stop the checks
            report.failed.append(f"{slug}: unexpected {type(exc).__name__}: {exc}")
            print(f"[discover] {slug}: unexpected {type(exc).__name__}: {exc} — keeping known siblings")
            clock[region_key] = {"at": to_iso(at), "ok": False,
                                 "message": f"{type(exc).__name__}: {exc}"[:300]}
            dirty = True
            continue

        report.listed.append(slug)
        clock[region_key] = {"at": to_iso(at), "ok": True, "message": "", "movies": len(listed)}
        dirty = True
        print(f"[discover] {slug}: {len(listed)} movie(s) listed")

        for monitor in watched:
            _merge_into(monitor, listed, report)

    if dirty:
        save_discovery(clock, mirror=mirror)
    return report


def _merge_into(monitor: Monitor, listed: list[MovieRef], report: DiscoveryReport) -> None:
    from monitor.checker import with_current_variants  # local: checker imports us

    movie = monitor.movie
    family = family_for(movie, listed)
    label = f"{movie.title} · {movie.language}" if movie.language else movie.title
    if family is None:
        print(f"[discover]   {label} ({movie.event_code}): not in the listing — keeping "
              f"{len(movie.variant_codes)} known sibling(s)")
        return

    known = with_current_variants(movie)  # monitor ∪ catalogue, as the checker sees it
    known_codes = {*known.variant_codes, movie.event_code}
    new = [(code, fmt) for code, fmt in family if code and code not in known_codes]
    if not new:
        print(f"[discover]   {label} ({movie.event_code}): no new sibling events "
              f"({len(known.variant_codes)} known)")
        return

    monitor.movie = replace(movie, variants=merge_variants(movie.variants, family,
                                                           own_code=movie.event_code))
    report.updated[monitor.id] = new
    print(f"[discover]   {label} ({movie.event_code}): +{len(new)} new sibling event(s) — "
          + ", ".join(f"{code} '{fmt or '?'}'" for code, fmt in new)
          + f" — monitor {monitor.id} will sweep them from this check on")


__all__ = [
    "DISCOVERY_RETRY",
    "DISCOVERY_TTL",
    "DiscoveryReport",
    "UNRESOLVED",
    "discover_siblings",
    "family_for",
    "is_fresh",
    "last_listed_at",
    "merge_variants",
    "needs_discovery",
]
