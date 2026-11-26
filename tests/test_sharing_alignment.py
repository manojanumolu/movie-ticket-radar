"""One BookMyShow read for monitors on the same listing that are due together,
and never a check before a monitor's own interval.

Monitors on the same listing (film, city, dates) that are due in the same
tick share one listing read. A monitor that is *not* due is not checked,
even when another monitor on its listing is: each keeps its own configured
interval (owner's request, 27 Nov 2026 — a 10-minute monitor was being
checked every 5 minutes because it rode a 5-minute monitor's read).

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
def test_monitor_limits_stay_per_account(users):
    """Riding a shared read still costs the subscriber one of their own slots."""
    from monitor import state as state_mod

    limit = state_mod.ACTIVE_MONITOR_LIMIT
    mine = [users("uid-a", ALLU_DOLBY) for _ in range(limit)]
    for i, m in enumerate(mine):
        m.id = f"aligned-{i}"
    other = users("uid-b", ALLU_DOLBY)
    # A member can no longer create monitors; re-activating a stopped one is
    # the remaining way to gain a running monitor, so that is what is refused.
    stopped = users("uid-a", ALLU_DOLBY)
    stopped.id = "stopped-extra"
    stopped.stop()
    save_monitors([*mine, other, stopped], mirror=False)
    scope = state_mod.Scope("", "uid-a", lambda: "tok", firestore_enabled=False, admin=False)
    state_mod.set_scope_provider(lambda: scope)
    try:
        state_mod.invalidate_cache()
        seen = state_mod.load_monitors()
        assert sorted(m.id for m in seen if m.is_running()) == sorted(m.id for m in mine), \
            "B's monitor is invisible to A"
        assert state_mod.monitor_limit_reached(seen) is True
        with pytest.raises(state_mod.MonitorLimitError):
            state_mod.extend_monitor("stopped-extra", 24, mirror=False)
    finally:
        state_mod.set_scope_provider(None)
        state_mod.invalidate_cache()


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


# ──────────────────────────────────────────────────────────────────────────
# Each monitor keeps its own interval, even on a shared listing
# ──────────────────────────────────────────────────────────────────────────
def _five_and_ten(users):
    fast = users("uid-a", ALLU_DOLBY)
    fast.interval_minutes = 5
    slow = users("uid-b", ALLU_DOLBY)
    slow.interval_minutes = 10
    return fast, slow


def test_five_and_ten_minute_monitors_on_one_listing_keep_their_own_intervals(users, recording):
    fast, slow = _five_and_ten(users)
    seed([fast, slow], {})                          # both new: due at once
    checks: dict[str, list[datetime]] = {fast.id: [], slow.id: []}
    for minute in range(0, 31):
        report = tick(t(minute))
        assert report.sharing.aligned == 0
        for mid in report.checked:
            checks[mid].append(t(minute))
    assert checks[fast.id] == [t(m) for m in range(0, 31, 5)]       # 0,5,…,30
    assert checks[slow.id] == [t(m) for m in range(0, 31, 10)]      # 0,10,20,30
    # One read per tick in which anyone was due: the shared ticks cost one.
    assert recording.count == len(checks[fast.id])


def test_at_the_workers_30_second_poll_no_monitor_is_checked_early(users, recording):
    """The worker polls every 30 seconds and a check may land up to
    ``DUE_TOLERANCE_SECONDS`` (30 s) early, as it always could. Over an hour
    the 10-minute monitor is never checked closer than 9.5 minutes apart,
    i.e. never at the 5-minute monitor's slots."""
    fast, slow = _five_and_ten(users)
    seed([fast, slow], {})
    checks: dict[str, list[datetime]] = {fast.id: [], slow.id: []}
    for minute in range(0, 61):
        for second in (0, 30):
            for mid in tick(t(minute, second)).checked:
                checks[mid].append(t(minute, second))
    for mid, interval in ((fast.id, 5), (slow.id, 10)):
        gaps = [(b - a).total_seconds() for a, b in zip(checks[mid], checks[mid][1:])]
        assert gaps and all(interval * 60 - 30 <= g <= interval * 60 + 30 for g in gaps), (interval, gaps)
    assert 6 <= len(checks[slow.id]) <= 7 and 12 <= len(checks[fast.id]) <= 14


def test_the_ten_minute_monitor_is_not_checked_at_minute_five(users, recording):
    fast, slow = _five_and_ten(users)
    seed([fast, slow], {})
    tick(t(0))
    before = load_state()[slow.id]
    report = tick(t(5))
    assert report.checked == [fast.id]
    after = load_state()[slow.id]
    assert after.check_count == before.check_count == 1
    assert after.last_check_at == before.last_check_at == t(0)
    assert any(sid.startswith(slow.id[:8]) and "not due" in sid for sid in report.skipped)


def test_monitors_due_together_share_one_read(users, recording):
    fast, slow = _five_and_ten(users)
    seed([fast, slow], {})
    report = tick(t(0))
    assert sorted(report.checked) == sorted([fast.id, slow.id])
    assert recording.count == 1 and report.sharing.fetches == 1 and report.sharing.monitors == 2
    tick(t(5))
    report = tick(t(10))
    assert sorted(report.checked) == sorted([fast.id, slow.id])
    assert recording.count == 3                    # 0 (shared), 5 (fast), 10 (shared)


def test_check_counts_only_grow_for_monitors_that_were_due(users, recording):
    fast, slow = _five_and_ten(users)
    seed([fast, slow], {})
    for minute in range(0, 21):
        tick(t(minute))
    state = load_state()
    assert state[fast.id].check_count == 5          # 0,5,10,15,20
    assert state[slow.id].check_count == 3          # 0,10,20


def test_a_new_monitor_is_checked_at_once_without_waiting_for_another(users, recording):
    fast, slow = _five_and_ten(users)
    seed([fast], {})
    tick(t(0))
    save_monitors([fast, slow], mirror=False)       # created at 00:02 (never checked)
    report = tick(t(2))
    assert report.checked == [slow.id], "the new monitor alone; the other is not due"
    assert load_state()[fast.id].check_count == 1

