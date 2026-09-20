"""One BookMyShow read for everyone on a listing — even when their clocks differ.

The production case (21 Sep 2026, run 35529331442): two 10-minute monitors on
Avengers Endgame: Encore · English · Hyderabad · 25 Sep, both ALLU Cinemas ·
Dolby Cinema. ``d9b8e05d`` had been checked at :01, :11, :21…; ``fee5ee79``
was created six minutes later and ran at :07, :17, :27…. ``run_once`` grouped
only the monitors *due in the same tick*, so the two never met and each made
its own listing read — two reads every ten minutes of the very same page.

The fix is not a cache. When a listing read is due for anyone, every running
monitor on that same read is evaluated against it in the same tick — the
others are simply checked a few minutes early, once. From then on their
``last_check_at`` coincide, they are due together, and the existing
same-tick grouping carries them. Nobody is ever handed an answer older than
the tick it was read in, and nobody's gap between checks ever exceeds their
own interval; it can only shorten, once.

These tests run the worker tick by tick on a fixed clock and count reads
through a recording provider — the same instrument ``tests/test_sharing.py``
uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config.timezone import IST
from monitor import checker
from monitor.models import Availability, Showtime, TheatreTarget
from monitor.state import MonitorState, load_state, save_monitors, save_state
from tests.test_sharing import ALLU_DOLBY, AMB_HDR, RecordingProvider, users  # noqa: F401

pytestmark = pytest.mark.usefixtures("signed_in")

MIDNIGHT = datetime(2026, 9, 21, 0, 0, tzinfo=IST)


def t(minute: int, second: int = 0) -> datetime:
    """``t(7)`` is 00:07:00 IST on the night it happened."""
    return MIDNIGHT + timedelta(minutes=minute, seconds=second)


@pytest.fixture(autouse=True)
def worker_view():
    from monitor import state as state_mod

    state_mod.set_scope_provider(None)
    state_mod.invalidate_cache()
    yield
    state_mod.set_scope_provider(None)
    state_mod.invalidate_cache()


@pytest.fixture
def recording(monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    monkeypatch.setattr(checker, "with_current_variants", lambda movie: movie)
    return provider


def seed(monitors, last_checks: dict[str, datetime | None]) -> None:
    """Persist monitors with the ``last_check_at`` each one had in production."""
    save_monitors(monitors, mirror=False)
    save_state({m.id: MonitorState(last_check_at=last_checks.get(m.id)) for m in monitors},
               mirror=False)


def tick(at, notifier=None):
    """One *scheduled* tick — no force: only what is due is checked."""
    return checker.run_once(at=at, force=False, mirror=False,
                            notifier=notifier or (lambda m, c: None))


def _available(venue="ALLU", name="Allu Cinemas", fmt="Dolby Cinema 2D", date_code="20260925"):
    return Showtime(venue_code=venue, venue_name=name, session_id=f"S-{venue}",
                    date_code=date_code, time_label="07:30 PM", time_code="1930",
                    format_label=fmt, availability=Availability.AVAILABLE,
                    booking_url="https://in.bookmyshow.com/x")


# ──────────────────────────────────────────────────────────────────────────
# 8 · The production case: identical target, due six minutes apart
# ──────────────────────────────────────────────────────────────────────────
def test_phase_offset_monitors_share_one_read_and_then_stay_aligned(users, recording):
    a = users("uid-a", ALLU_DOLBY)      # last checked 23:51 → due at 00:01
    b = users("uid-b", ALLU_DOLBY)      # last checked 23:57 → due at 00:07
    seed([a, b], {a.id: t(-9), b.id: t(-3)})

    # 00:01 — A is due. Before the fix this read served A alone.
    report = tick(t(1))
    assert recording.count == 1
    assert sorted(report.checked) == sorted([a.id, b.id]), "B rides the read A needed"
    assert report.sharing.fetches == 1 and report.sharing.monitors == 2
    assert report.sharing.aligned == 1

    # 00:07 — B's old slot. It was served at 00:01, so nothing is due.
    report = tick(t(7))
    assert recording.count == 1, "the phase-offset read no longer happens"
    assert report.checked == []
    assert any(sid.startswith(b.id[:8]) and "not due" in sid for sid in report.skipped)

    # 00:11 — both due together now; one read, both evaluated, no alignment needed.
    report = tick(t(11))
    assert recording.count == 2
    assert sorted(report.checked) == sorted([a.id, b.id])
    assert report.sharing.aligned == 0

    # 00:17 — B's old slot again: silence.
    assert tick(t(17)).checked == []
    assert recording.count == 2

    # 00:21 — and again together.
    tick(t(21))
    assert recording.count == 3
    state = load_state()
    assert state[a.id].last_check_at == state[b.id].last_check_at == t(21)


def test_before_and_after_read_counts_over_twenty_minutes(users, recording):
    """The number the change is for: reads of one listing between 00:00 and
    00:20 for two identical 10-minute monitors six minutes apart."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    for minute in range(0, 21):                    # one scheduled tick a minute
        tick(t(minute))
    # Before: A at :01 and :11, B at :07 and :17 → 4 reads. After: :01, :11 → 2.
    assert recording.count == 2, recording.reads
    assert [r[0] for r in recording.reads] == ["ET00478890", "ET00478890"]


def test_a_monitor_never_waits_longer_than_its_own_interval(users, recording):
    """Alignment only ever pulls a check *earlier*. Whatever the offset, the
    gap between two evaluations of a monitor stays within its interval."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-1)})       # B was checked a minute ago
    checks: dict[str, list[datetime]] = {a.id: [], b.id: []}
    for minute in range(0, 41):
        for second in (0, 30):
            report = tick(t(minute, second))
            for mid in report.checked:
                checks[mid].append(t(minute, second))
    for mid, times in checks.items():
        gaps = [(y - x).total_seconds() for x, y in zip(times, times[1:])]
        assert times, mid
        assert max(gaps, default=0) <= 10 * 60, (mid, gaps)
    assert checks[b.id][0] == checks[a.id][0] < t(2), "B was pulled forward to A's first read"
    assert recording.count == len(checks[a.id]), "one read per aligned tick"


# ──────────────────────────────────────────────────────────────────────────
# 7 · 9 · 10 — same tick, three users, and two targets on one listing
# ──────────────────────────────────────────────────────────────────────────
def test_identical_target_due_at_the_same_time_is_one_read(users, recording):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-10), b.id: t(-10)})
    report = tick(t(0))
    assert recording.count == 1
    assert sorted(report.checked) == sorted([a.id, b.id])
    assert report.sharing.aligned == 0


def test_three_users_offset_by_minutes_cost_one_read(users, recording):
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "abc"]
    seed(monitors, {monitors[0].id: t(-10), monitors[1].id: t(-6), monitors[2].id: t(-2)})
    report = tick(t(0))
    assert recording.count == 1
    assert sorted(report.checked) == sorted(m.id for m in monitors)
    assert report.sharing.aligned == 2
    for minute in (4, 8):                          # the old slots of B and C
        assert tick(t(minute)).checked == []
    assert recording.count == 1


def test_allu_dolby_and_amb_hdr_offset_are_one_listing_read(users, recording):
    """A = ALLU Dolby (due :01), B = ALLU Dolby (due :07), C = AMB HDR (due :09).
    Venue and format never reach the network; one read answers all three."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    c = users("uid-c", AMB_HDR)
    seed([a, b, c], {a.id: t(-9), b.id: t(-3), c.id: t(-1)})
    report = tick(t(1))
    assert recording.count == 1, recording.reads
    assert sorted(report.checked) == sorted([a.id, b.id, c.id])
    assert len(report.sharing.shared) == 1 and report.sharing.shared[0][1:] == (3, 3)
    for minute in (7, 9):
        assert tick(t(minute)).checked == []
    assert recording.count == 1
    tick(t(11))
    assert recording.count == 2


# ──────────────────────────────────────────────────────────────────────────
# 11 · 12 — what must stay separate
# ──────────────────────────────────────────────────────────────────────────
def test_different_show_dates_are_separate_reads_and_are_not_aligned(users, recording):
    a = users("uid-a", ALLU_DOLBY, dates=("20260925",))
    b = users("uid-b", ALLU_DOLBY, dates=("20260926",))
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    report = tick(t(1))
    assert recording.count == 1 and report.checked == [a.id]
    assert report.sharing.aligned == 0
    report = tick(t(7))
    assert recording.count == 2 and report.checked == [b.id]
    assert sorted(recording.reads) == [("ET00478890", ("20260925",)), ("ET00478890", ("20260926",))]


def test_different_events_are_separate_reads_and_are_not_aligned(users, recording):
    a = users("uid-a", ALLU_DOLBY, event="ET00478890")
    b = users("uid-b", ALLU_DOLBY, event="ET00514163")
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    assert tick(t(1)).checked == [a.id]
    assert tick(t(7)).checked == [b.id]
    assert recording.count == 2
    assert [r[0] for r in recording.reads] == ["ET00478890", "ET00514163"]


# ──────────────────────────────────────────────────────────────────────────
# 13 · Nothing is reused across ticks
# ──────────────────────────────────────────────────────────────────────────
def test_a_listing_is_never_reused_after_the_tick_it_was_read_in(users, recording):
    """The reuse window is one tick. When the listing changes between ticks,
    the next tick reads it again — a rider is never served yesterday's page."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    sent: list[tuple[str, str]] = []
    notify = lambda m, c: sent.append((m.owner_uid, c.kind.value))  # noqa: E731

    recording._shows = []                          # 00:01 — no Dolby show yet
    tick(t(1), notify)
    assert recording.count == 1 and sent == []

    recording._shows = [_available()]              # tickets open at 00:03
    assert tick(t(7), notify).checked == [], "not due: no read, and no stale verdict either"
    assert sent == []

    tick(t(11), notify)                            # both due: a fresh read, not the 00:01 one
    assert recording.count == 2
    assert sorted(sent) == [("uid-a", "TICKETS_LIVE"), ("uid-b", "TICKETS_LIVE")]


def test_alignment_reads_no_state_when_nothing_is_due(users, recording):
    """The segment's own skip-the-state-read shortcut still holds."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    first = tick(t(1))
    report = checker.run_once(at=t(4), force=False, mirror=False,
                              notifier=lambda m, c: None, known_state=first.state)
    assert report.state_read_skipped is True and report.checked == []


# ──────────────────────────────────────────────────────────────────────────
# 14 · 15 · 16 · 17 — the fan-out is untouched by alignment
# ──────────────────────────────────────────────────────────────────────────
def test_an_aligned_rider_keeps_its_own_state_and_history(users, recording):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", AMB_HDR)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    recording._shows = [_available(), _available("AMB", "AMB Cinemas", "HDR by Barco 2D")]
    tick(t(1))
    state = load_state()
    assert state[a.id].check_count == 1 and state[b.id].check_count == 1
    assert state[a.id].target(ALLU_DOLBY.key).availability is Availability.AVAILABLE
    assert state[b.id].target(AMB_HDR.key).availability is Availability.AVAILABLE
    assert ALLU_DOLBY.key not in state[b.id].targets, "B's state holds B's targets only"
    assert AMB_HDR.key not in state[a.id].targets


def test_an_aligned_read_emails_each_subscriber_separately(users, recording):
    recording._shows = [_available()]
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    sent: list[tuple[str, str]] = []
    report = tick(t(1), lambda m, c: sent.append((m.owner_uid, m.notify_email)))
    assert recording.count == 1 and report.emails_sent == 2
    assert sorted(sent) == [("uid-a", "uid-a@example.com"), ("uid-b", "uid-b@example.com")]
    # duplicate protection is per person, and the rider's is armed like anyone's
    sent.clear()
    tick(t(11), lambda m, c: sent.append((m.owner_uid, m.notify_email)))
    assert sent == []


def test_a_riders_email_failure_does_not_touch_the_leader(users, recording):
    recording._shows = [_available()]
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})

    def flaky(monitor, change):
        if monitor.owner_uid == "uid-b":
            raise RuntimeError("SMTP said no")

    report = tick(t(1), flaky)
    assert report.emails_sent == 1 and len(report.email_errors) == 1
    state = load_state()
    assert state[a.id].target(ALLU_DOLBY.key).notified_availability is Availability.AVAILABLE
    assert state[b.id].target(ALLU_DOLBY.key).notified_availability is not Availability.AVAILABLE
    sent: list[str] = []
    tick(t(11), lambda m, c: sent.append(m.owner_uid))
    assert sent == ["uid-b"], "only the failed send is retried"


def test_monitor_limits_stay_per_account(users):
    """Riding a shared read still costs the subscriber one of their own slots."""
    from monitor import state as state_mod

    limit = state_mod.ACTIVE_MONITOR_LIMIT
    mine = [users("uid-a", ALLU_DOLBY) for _ in range(limit)]
    for i, m in enumerate(mine):
        m.id = f"aligned-{i}"
    other = users("uid-b", ALLU_DOLBY)
    save_monitors([*mine, other], mirror=False)
    scope = state_mod.Scope("", "uid-a", lambda: "tok", firestore_enabled=False, admin=False)
    state_mod.set_scope_provider(lambda: scope)
    state_mod.invalidate_cache()
    seen = state_mod.load_monitors()
    assert sorted(m.id for m in seen) == sorted(m.id for m in mine), "B's monitor is invisible to A"
    assert state_mod.monitor_limit_reached(seen) is True
    with pytest.raises(state_mod.MonitorLimitError):
        state_mod.upsert_monitor(users("uid-a", ALLU_DOLBY), mirror=False)


# ──────────────────────────────────────────────────────────────────────────
# 12 · 13 · 14 — isolation, no shared record, the production monitor as-is
# ──────────────────────────────────────────────────────────────────────────
def test_alignment_writes_only_each_monitors_own_state(users, recording):
    """Alignment is derived in memory from the monitors the tick already
    read. The state store holds one record per monitor and nothing else —
    no group, no shared document, nothing for the rules to guard."""
    from monitor import sharing
    from pathlib import Path

    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    tick(t(1))
    assert set(load_state()) == {a.id, b.id}
    assert not hasattr(sharing, "_SHARED") and not hasattr(sharing, "CACHE")
    rules = Path("firestore.rules").read_text(encoding="utf-8")
    assert "shared" not in rules.lower() and "owner_uid == request.auth.uid" in rules


def test_one_owner_still_sees_only_their_own_monitor_after_alignment(users, recording):
    from monitor import state as state_mod

    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    seed([a, b], {a.id: t(-9), b.id: t(-3)})
    tick(t(1))                                     # B rode A's read
    scope = state_mod.Scope("", "uid-b", lambda: "tok", firestore_enabled=False, admin=False)
    state_mod.set_scope_provider(lambda: scope)
    state_mod.invalidate_cache()
    mine = state_mod.load_monitors()
    assert [m.id for m in mine] == [b.id]
    assert mine[0].owner_uid == "uid-b" and mine[0].notify_email == "uid-b@example.com"


def test_the_production_monitor_shape_rides_and_leads_unchanged(make_monitor, recording):
    """A document written by earlier code — two targets, no new keys — is
    grouped, led and ridden exactly as one written today; nothing about it
    is rewritten by alignment except its own ``last_check_at``."""
    from dataclasses import replace
    from monitor.models import Monitor

    production = make_monitor(targets=[ALLU_DOLBY, AMB_HDR], owner_uid="uid-owner")
    production.movie = replace(production.movie, event_code="ET00514163")
    production.date_codes = ["20260925"]
    restored = Monitor.from_dict(production.to_dict())      # round-trip, as the store does
    newcomer = make_monitor(targets=[ALLU_DOLBY], owner_uid="uid-other")
    newcomer.movie = replace(newcomer.movie, event_code="ET00514163")
    newcomer.date_codes = ["20260925"]
    newcomer.id = "newcomer-" + newcomer.id
    seed([restored, newcomer], {restored.id: t(-9), newcomer.id: t(-3)})

    before = restored.to_dict()
    report = tick(t(1))
    assert recording.count == 1 and sorted(report.checked) == sorted([restored.id, newcomer.id])
    assert report.sharing.aligned == 1
    from monitor.state import load_monitors_for_checking
    after = next(m for m in load_monitors_for_checking() if m.id == restored.id).to_dict()
    assert after == before, "the monitor document itself is untouched"
    assert len(after["targets"]) == 2 and after["interval_minutes"] == 10
