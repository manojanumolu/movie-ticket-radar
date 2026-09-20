"""Deduplicating the external check that several people are waiting on.

Why this exists
---------------
Two people who want the same seat want the same answer. Before this module
the worker asked BookMyShow for it once per *monitor*: ten accounts watching
Avengers at ALLU Cinemas produced ten identical listing reads every ten
minutes, of the same event, on the same date, for the same city. That is ten
times the traffic for one fact, against a site that already refuses us when
we look too eager (``platforms/http.py``).

What is actually one request
----------------------------
The unit of traffic is **not** the theatre and **not** the format. Reading
``platforms.bookmyshow``, one HTTP call is
``_get(event_code, date_code, region_for(region_slug))`` — and
``Provider.fetch`` makes one of those per date, plus one per premium-format
sibling event per date. Nothing about a venue or a format appears in the
request: ``evaluate_target`` filters the *returned* listing by venue and by
format afterwards, in memory.

So "ALLU Dolby" and "AMB HDR by Barco" on the same film and date were never
two requests — they are two readings of one. What was genuinely duplicated
was the listing read itself, once per monitor. :func:`group_for_fetch` is
therefore keyed on exactly the arguments that reach the network:

    platform · region · event code · sibling event codes · show dates

plus the listing URL the monitor was created from, which is not part of the
request but *is* what every booking link in the answer is derived from
(``BookMyShowProvider.booking_url``). Two monitors created from the same
catalogue row share it; a monitor that predates the catalogue and carries a
different URL is kept in its own group rather than silently handed someone
else's links. The key is deliberately conservative: sharing must never
change what a person is told.

What stays private
------------------
Everything. This module groups monitors for the duration of one fetch and
returns them unchanged. It reads no state, writes nothing, and holds no
Firestore document of its own, so there is no shared record for one account
to read another's data out of. The snapshot handed back is a public listing
— the same page anyone can open — and each monitor is evaluated, recorded,
notified and historied on its own, by its own owner, exactly as before.
:data:`SharedFetch.subscribers` exists so a log line can say "3 monitors"
and a test can assert the fan-out; it is never persisted.

:func:`target_identity` names the finer thing a person is actually waiting
for — this venue, in this format, on this listing. The worker does not need
it to save a request, but it is the honest identity of a *subscription*, it
is what the UI counts when it says a check is shared, and it is what tests
assert when they say two people are waiting on the same thing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Sequence

from monitor.models import Monitor, MovieRef, TheatreTarget, normalise_format


def _codes(values: Iterable[str]) -> tuple[str, ...]:
    """Show dates as an order-independent tuple. ``()`` means "every date the
    platform offers", which is a different request from any explicit list."""
    return tuple(sorted({(v or "").strip() for v in values if (v or "").strip()}))


def _slug(value: str) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class FetchKey:
    """What makes two listing reads the same read.

    Every field here changes the bytes that go to BookMyShow — except
    ``source_url``, which changes the links derived from the answer. Venue,
    format, interval, end time, owner and notification address are all
    absent on purpose: none of them reaches the network, and none of them
    changes the listing that comes back.
    """

    platform: str
    region_slug: str
    event_code: str
    variant_codes: tuple[str, ...]
    date_codes: tuple[str, ...]
    source_url: str

    @property
    def id(self) -> str:
        """A short stable name for this read, for logs and the UI.

        Hashed rather than concatenated so a log line in a public repository
        does not spell out a person's listing URL, and so the length does not
        grow with the number of sibling events.
        """
        raw = "|".join((
            self.platform, self.region_slug, self.event_code,
            ",".join(self.variant_codes), ",".join(self.date_codes), self.source_url,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def label(self) -> str:
        """Human form for a log line: the event, its siblings and its dates."""
        events = "+".join((self.event_code, *self.variant_codes))
        dates = ",".join(self.date_codes) or "all dates"
        return f"{self.platform}:{self.region_slug}:{events}@{dates}"

    @property
    def request_count(self) -> int:
        """How many HTTP reads one fetch on this key costs — one per date per
        event, counting the base event and every sibling (``Provider.fetch``)."""
        return max(1, len(self.date_codes)) * (1 + len(self.variant_codes))


@dataclass(frozen=True)
class TargetIdentity:
    """One thing a person is waiting for: a venue, in a format, on a listing.

    Two monitors carrying the same identity are waiting on the same answer,
    whoever owns them. Built from canonical identifiers the models already
    hold — the event code, the venue code, the platform's own region slug —
    never from display names, which BookMyShow rewrites ("AMB Cinemas",
    "AMB Cinemas: Gachibowli") without anything having changed. The format is
    reduced with :func:`monitor.models.normalise_format`, the same squashed
    key the checker matches showtimes on, so "Dolby Cinema" and
    "DOLBY CINEMA 2D" are one identity rather than two.
    """

    fetch: FetchKey
    venue_code: str
    fmt: str

    @property
    def id(self) -> str:
        raw = f"{self.fetch.id}|{self.venue_code}|{self.fmt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def label(self) -> str:
        return f"{self.fetch.label}#{self.venue_code}/{self.fmt or 'any'}"


def fetch_identity(movie: MovieRef, date_codes: Sequence[str]) -> FetchKey:
    """The identity of the listing read a monitor on ``movie`` would make."""
    return FetchKey(
        platform=_slug(movie.platform),
        region_slug=_slug(movie.region_slug),
        event_code=(movie.event_code or "").strip().upper(),
        variant_codes=tuple(sorted({c.strip().upper() for c in movie.variant_codes if c.strip()})),
        date_codes=_codes(date_codes),
        source_url=(movie.source_url or "").strip(),
    )


def target_identity(movie: MovieRef, date_codes: Sequence[str],
                    target: TheatreTarget) -> TargetIdentity:
    """The identity of one watched venue+format on that listing."""
    return TargetIdentity(
        fetch=fetch_identity(movie, date_codes),
        venue_code=(target.venue_code or "").strip().upper(),
        fmt=normalise_format(target.fmt),
    )


def monitor_targets(monitor: Monitor, *, movie: MovieRef | None = None) -> list[TargetIdentity]:
    """Every subscription a monitor holds, as identities."""
    movie = movie if movie is not None else monitor.movie
    return [target_identity(movie, monitor.date_codes, t) for t in monitor.targets]


@dataclass
class SharedFetch:
    """One listing read, and every monitor waiting on it.

    ``movie`` is the reference the read is actually made with: the first
    subscriber's, resolved through the catalogue so the sibling events are
    the family as it is *now* (``checker.with_current_variants``). Every
    monitor in the group resolved to the same key, so any of them would have
    produced the same read.
    """

    key: FetchKey
    movie: MovieRef
    date_codes: list[str]
    monitors: list[Monitor] = field(default_factory=list)

    @property
    def subscribers(self) -> tuple[str, ...]:
        """The distinct accounts waiting on this read, in first-seen order.

        A legacy monitor with no owner (the JSON store predates ownership)
        counts as its own subscriber rather than being folded into one.
        """
        seen: dict[str, None] = {}
        for m in self.monitors:
            seen.setdefault(m.owner_uid or f"<unowned:{m.id}>", None)
        return tuple(seen)

    @property
    def shared(self) -> bool:
        return len(self.monitors) > 1

    @property
    def targets(self) -> tuple[TargetIdentity, ...]:
        """Every distinct venue+format waiting on this one read."""
        seen: dict[TargetIdentity, None] = {}
        for m in self.monitors:
            for identity in monitor_targets(m, movie=self.movie):
                seen.setdefault(identity, None)
        return tuple(seen)

    def summary(self) -> str:
        return (f"{self.key.label} · {len(self.monitors)} monitor(s), "
                f"{len(self.subscribers)} account(s), {len(self.targets)} target(s)")


def group_for_fetch(monitors: Sequence[Monitor], *,
                    resolve: Callable[[MovieRef], MovieRef] | None = None) -> list[SharedFetch]:
    """Collapse monitors into the listing reads they actually need.

    ``resolve`` is applied to each monitor's movie before keying, so two
    monitors that were saved knowing different sibling events — one created
    before a 4DX event existed, one after — still share a read once the
    catalogue has taught them both the same family. Defaults to identity so
    this function can be reasoned about (and tested) without the catalogue.

    Order is first appearance: the groups come back in the order their first
    monitor appeared, and the monitors inside a group keep theirs. A worker
    tick therefore still checks the monitors it always did, in the order it
    always did, when nothing is shared.
    """
    resolve = resolve or (lambda movie: movie)
    groups: dict[FetchKey, SharedFetch] = {}
    for monitor in monitors:
        movie = resolve(monitor.movie)
        key = fetch_identity(movie, monitor.date_codes)
        group = groups.get(key)
        if group is None:
            group = groups[key] = SharedFetch(
                key=key, movie=movie, date_codes=list(monitor.date_codes),
            )
        group.monitors.append(monitor)
    return list(groups.values())


@dataclass
class SharingReport:
    """What one tick saved by sharing. Printed, and asserted in tests."""

    monitors: int = 0
    fetches: int = 0
    requests: int = 0
    shared_fetches: int = 0
    #: ``(label, monitor count, account count)`` for each read that more than
    #: one monitor waited on.
    shared: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def saved(self) -> int:
        """Listing reads this tick did *not* make, against one per monitor."""
        return max(0, self.monitors - self.fetches)

    def summary(self) -> str:
        text = f"monitors={self.monitors} fetches={self.fetches} saved={self.saved}"
        if self.shared_fetches:
            text += f" shared={self.shared_fetches}"
        return text


def describe(groups: Sequence[SharedFetch]) -> SharingReport:
    report = SharingReport(
        monitors=sum(len(g.monitors) for g in groups),
        fetches=len(groups),
        requests=sum(g.key.request_count for g in groups),
        shared_fetches=sum(1 for g in groups if g.shared),
    )
    report.shared = [(g.key.label, len(g.monitors), len(g.subscribers))
                     for g in groups if g.shared]
    return report


def shared_target_counts(monitors: Sequence[Monitor], *,
                         at: datetime | None = None,
                         resolve: Callable[[MovieRef], MovieRef] | None = None,
                         ) -> dict[str, int]:
    """How many *running* monitors wait on each target identity.

    The UI's only use of this module: a monitor whose targets are shared can
    say so. Takes the monitors the caller already has in hand — it reads
    nothing — so a Streamlit rerun costs no extra document reads.
    """
    counts: dict[str, int] = {}
    for monitor in monitors:
        if not monitor.is_running(at):
            continue
        movie = (resolve or (lambda m: m))(monitor.movie)
        for identity in monitor_targets(monitor, movie=movie):
            counts[identity.id] = counts.get(identity.id, 0) + 1
    return counts


__all__ = [
    "FetchKey",
    "SharedFetch",
    "SharingReport",
    "TargetIdentity",
    "describe",
    "fetch_identity",
    "group_for_fetch",
    "monitor_targets",
    "shared_target_counts",
    "target_identity",
]
