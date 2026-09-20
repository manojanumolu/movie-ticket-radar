"""One external check, many waiting users.

Two people who want the same seat want the same answer. Before
``monitor.sharing`` the worker asked BookMyShow for it once per monitor: ten
accounts watching the same film at the same theatre produced ten identical
listing reads every ten minutes.

These tests pin both halves of the fix:

* **the grouping** — what makes two checks the same check, in the project's
  own canonical identifiers (event code, region slug, venue code, the
  squashed format key ``normalise_format``), never in display names;
* **the fan-out** — that sharing a read changes nothing a person sees. Each
  monitor keeps its own owner, its own state, its own history and its own
  email; one account's failed send does not touch another's; and duplicate
  protection stays per person.

The unit of traffic is the *listing read*, not the theatre and not the
format: reading ``platforms.bookmyshow``, one HTTP call is
``_get(event_code, date_code, region)``, and ``evaluate_target`` filters the
answer by venue and format afterwards, in memory. So "ALLU Dolby" and "AMB
HDR" on one film and date were never two requests — a fact these tests state
out loud, because the naive reading of "one check per target" would be a
regression, not a fix.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from monitor import checker, sharing
from monitor.models import Availability, MovieRef, Snapshot, TheatreTarget
from monitor.state import MonitorState, save_monitors, save_state

pytestmark = pytest.mark.usefixtures("signed_in")


@pytest.fixture(autouse=True)
def worker_view():
    """These tests are the worker's view: every owner, no signed-in scope.

    Stated rather than assumed, because a scope left registered by another
    module would make ``save_monitors`` refuse a monitor owned by someone
    else — which is the store doing its job, and nothing to do with sharing.
    The two tests that *do* want a signed-in view register their own.
    """
    from monitor import state as state_mod

    state_mod.set_scope_provider(None)
    state_mod.invalidate_cache()
    yield
    state_mod.set_scope_provider(None)
    state_mod.invalidate_cache()


ALLU_DOLBY = TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")
AMB_HDR = TheatreTarget("AMB", "AMB Cinemas", "Gachibowli, Hyderabad", "HDR by Barco")


# ──────────────────────────────────────────────────────────────────────────
# A recording provider: every listing read is one entry
# ──────────────────────────────────────────────────────────────────────────
class RecordingProvider:
    """Stands in for BookMyShow and counts the reads nobody should repeat.

    ``fetch`` is the whole external surface the checker uses. One call here
    is one listing read — which is itself one HTTP request per date per
    event, as ``FetchKey.request_count`` states.
    """

    slug = "bookmyshow"
    name = "BookMyShow"

    def __init__(self, shows=None):
        self.reads: list[tuple[str, tuple[str, ...]]] = []
        self._shows = shows or []

    def fetch(self, movie: MovieRef, date_codes=None) -> Snapshot:
        self.reads.append((movie.event_code, tuple(date_codes or ())))
        return Snapshot(movie=movie, venues=[], showtimes=list(self._shows))

    def booking_url(self, movie: MovieRef, date_code: str = "") -> str:
        return f"{movie.source_url}/{date_code}" if date_code else movie.source_url

    def venue_booking_url(self, movie: MovieRef, venue_code: str, date_code: str = "") -> str:
        return f"{movie.source_url}/{venue_code}/{date_code}"

    @property
    def count(self) -> int:
        return len(self.reads)


@pytest.fixture
def recording(monkeypatch):
    """Install a provider that records every listing read."""
    provider = RecordingProvider()
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    # The catalogue must not add sibling events behind the grouping's back.
    monkeypatch.setattr(checker, "with_current_variants", lambda movie: movie)
    return provider


@pytest.fixture
def users(make_monitor):
    """Monitors for distinct accounts, all on the same film by default."""

    def make(owner: str, *targets: TheatreTarget, dates=("20260925",), event="ET00478890"):
        monitor = make_monitor(owner_uid=owner, targets=list(targets) or [ALLU_DOLBY])
        monitor.date_codes = list(dates)
        monitor.notify_email = f"{owner}@example.com"
        if event != monitor.movie.event_code:
            monitor.movie = replace(monitor.movie, event_code=event)
        return monitor

    return make


def _run(at, monitors, notifier=None, **kw):
    """One worker tick over exactly these monitors."""
    save_monitors(monitors, mirror=False)
    save_state({m.id: MonitorState() for m in monitors}, mirror=False)
    return checker.run_once(at=at, force=True, mirror=False,
                            notifier=notifier or (lambda m, c: None), **kw)


# ──────────────────────────────────────────────────────────────────────────
# 1–5 · What makes two checks the same check
# ──────────────────────────────────────────────────────────────────────────
def test_two_users_on_an_identical_target_are_one_shared_check(users):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)

    groups = sharing.group_for_fetch([a, b])
    assert len(groups) == 1
    group = groups[0]
    assert [m.id for m in group.monitors] == [a.id, b.id]
    assert group.subscribers == ("uid-a", "uid-b")
    assert group.shared is True
    # …and the two are waiting on one and the same subscription identity
    assert sharing.monitor_targets(a) == sharing.monitor_targets(b)
    assert len(group.targets) == 1


def test_three_users_on_an_identical_target_cost_one_external_check(at, users, recording):
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "abc"]

    report = _run(at, monitors)

    assert recording.count == 1, recording.reads
    assert sorted(report.checked) == sorted(m.id for m in monitors)
    assert report.fetches == 1
    assert report.sharing.monitors == 3 and report.sharing.saved == 2


def test_two_targets_one_of_them_shared_is_still_one_listing_read(at, users, recording):
    """A: ALLU Dolby + AMB HDR. B: ALLU Dolby. C: AMB HDR.

    The theatre and the format never reach the network — they filter the
    answer — so all three are waiting on *one* read of one film on one date,
    not the two a per-venue reading would predict, and certainly not four.
    """
    a = users("uid-a", ALLU_DOLBY, AMB_HDR)
    b = users("uid-b", ALLU_DOLBY)
    c = users("uid-c", AMB_HDR)

    report = _run(at, [a, b, c])

    assert recording.count == 1, recording.reads
    assert report.fetches == 1
    # three monitors, one read, and two distinct things being waited for
    groups = sharing.group_for_fetch([a, b, c])
    assert len(groups) == 1 and len(groups[0].targets) == 2
    assert {t.venue_code for t in groups[0].targets} == {"ALLU", "AMB"}


def test_two_films_are_two_reads(at, users, recording):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY, event="ET00999999")

    report = _run(at, [a, b])

    assert recording.count == 2
    assert report.fetches == 2 and report.sharing.saved == 0
    assert len({g.key for g in sharing.group_for_fetch([a, b])}) == 2


def test_the_same_theatre_in_a_different_format_is_a_different_subscription(users):
    """…but not a different request: the format filters the answer."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", TheatreTarget("ALLU", "Allu Cinemas", "", "IMAX"))

    assert sharing.monitor_targets(a) != sharing.monitor_targets(b)
    assert len(sharing.group_for_fetch([a, b])) == 1        # one read serves both
    assert len(sharing.group_for_fetch([a, b])[0].targets) == 2


def test_a_different_show_date_is_a_different_check(at, users, recording):
    a = users("uid-a", ALLU_DOLBY, dates=("20260925",))
    b = users("uid-b", ALLU_DOLBY, dates=("20260926",))

    report = _run(at, [a, b])

    assert recording.count == 2, recording.reads
    assert report.fetches == 2
    assert {r[1] for r in recording.reads} == {("20260925",), ("20260926",)}
    # …and watching every date is different again from watching one
    every = users("uid-c", ALLU_DOLBY, dates=())
    assert sharing.fetch_identity(every.movie, every.date_codes).date_codes == ()
    assert len(sharing.group_for_fetch([a, b, every])) == 3


def test_the_key_is_built_from_canonical_identifiers_not_display_names(users):
    """BookMyShow writes the same venue and the same format several ways
    ("AMB Cinemas" / "AMB Cinemas: Gachibowli", "Dolby Cinema" /
    "DOLBY CINEMA" / "dolby-cinema") without anything having changed. None of
    that may split a subscription, so the identity is built from the venue
    *code* and from ``normalise_format``'s squashed key — never from what is
    on screen."""
    a = users("uid-a", TheatreTarget("ALLU", "Allu Cinemas", "Attapur", "Dolby Cinema"))
    b = users("uid-b", TheatreTarget("ALLU", "ALLU CINEMAS: Attapur", "", "DOLBY-CINEMA"))

    assert sharing.monitor_targets(a) == sharing.monitor_targets(b)
    assert len(sharing.group_for_fetch([a, b])) == 1
    identity = sharing.monitor_targets(a)[0]
    assert identity.venue_code == "ALLU" and identity.fmt == "dolbycinema"
    assert "Allu Cinemas" not in identity.label and "Attapur" not in identity.label

    # A narrower format really is a different filter, though: the checker
    # matches by containment, so "Dolby Cinema" accepts showtimes that
    # "Dolby Cinema 2D" rejects. Two filters, two subscriptions — and still
    # one listing read, because the format never reached the network.
    narrow = users("uid-c", TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema 2D"))
    assert sharing.monitor_targets(narrow) != sharing.monitor_targets(a)
    assert len(sharing.group_for_fetch([a, narrow])) == 1


def test_sibling_events_and_region_are_part_of_the_read(users):
    """Both change the HTTP request ``_get`` makes, so both split the read."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    b.movie = replace(b.movie, variants=(("ET00111111", "IMAX"),))
    assert len(sharing.group_for_fetch([a, b])) == 2
    assert sharing.fetch_identity(b.movie, b.date_codes).request_count == 2   # 1 date × 2 events

    c = users("uid-c", ALLU_DOLBY)
    c.movie = replace(c.movie, region_slug="bengaluru")
    assert len(sharing.group_for_fetch([a, c])) == 2


def test_the_key_ignores_everything_that_never_reaches_the_network(users):
    """Interval, end time, notification address, owner: none of them changes
    the listing that comes back, so none of them may cost a second read."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    b.interval_minutes = 30
    b.monitor_until = a.monitor_until + timedelta(days=3)
    b.notify_email = "someone-else@example.com"

    assert len(sharing.group_for_fetch([a, b])) == 1


def test_a_read_is_shared_only_when_the_links_would_be_the_same(users):
    """The booking links in an alert are derived from the monitor's own
    listing URL. A monitor carrying a different one keeps its own read rather
    than being handed someone else's links."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    b.movie = replace(b.movie, source_url="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890")

    assert len(sharing.group_for_fetch([a, b])) == 2


# ──────────────────────────────────────────────────────────────────────────
# 6–8 · Joining and leaving
# ──────────────────────────────────────────────────────────────────────────
def test_a_stopped_monitor_leaves_the_shared_check(at, users, recording):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    b.stop(at - timedelta(minutes=1))

    report = _run(at, [a, b])

    assert recording.count == 1
    assert report.checked == [a.id]
    assert any("stopped" in s for s in report.skipped)
    assert sharing.shared_target_counts([a, b], at=at) == {
        sharing.monitor_targets(a)[0].id: 1                   # only the running one counts
    }


def test_when_the_last_subscriber_goes_the_target_is_not_checked_at_all(at, users, recording):
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    a.stop(at - timedelta(minutes=1))
    b.expire(at - timedelta(minutes=1))

    report = _run(at, [a, b])

    assert recording.count == 0, "nobody is waiting; nothing may be read"
    assert report.checked == [] and report.fetches == 0
    assert sharing.shared_target_counts([a, b], at=at) == {}


def test_an_expired_or_future_monitor_never_joins_a_read(at, users, recording):
    live = users("uid-a", ALLU_DOLBY)
    done = users("uid-b", ALLU_DOLBY)
    done.monitor_until = at - timedelta(minutes=1)            # past its end time, still ACTIVE

    report = _run(at, [live, done])

    assert recording.count == 1
    assert report.checked == [live.id] and done.id in report.expired


def test_a_new_subscriber_joins_the_existing_read_instead_of_adding_one(at, users, recording):
    """The acceptance criterion, as a sequence: A alone costs one read; B
    joining costs none; and B is checked on the very tick they join."""
    a = users("uid-a", ALLU_DOLBY)
    first = _run(at, [a])
    assert recording.count == 1 and first.fetches == 1

    b = users("uid-b", ALLU_DOLBY)
    second = _run(at + timedelta(minutes=10), [a, b])

    assert recording.count == 2, "the second tick made one read, not two"
    assert second.fetches == 1 and second.sharing.saved == 1
    assert sorted(second.checked) == sorted([a.id, b.id])


def test_creating_the_same_target_twice_at_once_cannot_create_two_shared_records(users):
    """There is no shared record to duplicate. Deduplication is derived, in
    memory, from the monitors the tick already read — so two people creating
    an identical monitor in the same second simply land in the same group,
    with no document to race over and nothing to reconcile."""
    import monitor.sharing as mod

    source = (mod.__file__ and open(mod.__file__, encoding="utf-8").read()) or ""
    for forbidden in ("save_", "commit(", "upsert", "delete", "client", "firestore"):
        assert forbidden not in source, forbidden

    concurrent = [users(f"uid-{n}", ALLU_DOLBY) for n in range(6)]
    groups = sharing.group_for_fetch(concurrent)
    assert len(groups) == 1 and len(groups[0].monitors) == 6
    # the key is a pure function of the monitor: same input, same key, always
    keys = {sharing.fetch_identity(m.movie, m.date_codes) for m in concurrent}
    assert len(keys) == 1
    assert len({k.id for k in keys}) == 1


# ──────────────────────────────────────────────────────────────────────────
# 9–11 · The fan-out
# ──────────────────────────────────────────────────────────────────────────
def _live_show(date_code="20260925"):
    from monitor.models import Showtime

    return Showtime(
        venue_code="ALLU", venue_name="Allu Cinemas", session_id="S1",
        date_code=date_code, time_label="07:30 PM", time_code="1930",
        format_label="Dolby Cinema 2D", availability=Availability.AVAILABLE,
        booking_url="https://in.bookmyshow.com/x",
    )


def test_one_shared_available_result_emails_every_subscriber_separately(at, users, recording):
    recording._shows = [_live_show()]
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "abc"]

    sent: list[tuple[str, str]] = []
    report = _run(at, monitors, notifier=lambda m, c: sent.append((m.owner_uid, m.notify_email)))

    assert recording.count == 1, "one read of BookMyShow"
    assert report.emails_sent == 3, "…and one email each"
    assert sorted(sent) == [("uid-a", "uid-a@example.com"),
                            ("uid-b", "uid-b@example.com"),
                            ("uid-c", "uid-c@example.com")]
    # nobody is told about anybody else: one recipient per message
    assert len({uid for uid, _ in sent}) == 3
    assert all(len(c.to_dict()["monitor_id"]) > 0 for c in report.changes)
    assert {c.monitor_id for c in report.changes} == {m.id for m in monitors}


def test_duplicate_protection_is_per_person(at, users, recording):
    """The second tick must be silent for everyone — and a person who joins
    later still gets their own first email."""
    recording._shows = [_live_show()]
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)

    sent: list[str] = []
    notify = lambda m, c: sent.append(m.owner_uid)  # noqa: E731

    save_monitors([a, b], mirror=False)
    save_state({a.id: MonitorState(), b.id: MonitorState()}, mirror=False)
    checker.run_once(at=at, force=True, mirror=False, notifier=notify)
    assert sorted(sent) == ["uid-a", "uid-b"]

    sent.clear()
    checker.run_once(at=at + timedelta(minutes=10), force=True, mirror=False, notifier=notify)
    assert sent == [], "already announced to both; silence"

    c = users("uid-c", ALLU_DOLBY)
    save_monitors([a, b, c], mirror=False)
    state = {m.id: MonitorState() for m in (c,)}
    from monitor.state import load_state

    merged = {**load_state(), **state}
    save_state(merged, mirror=False)
    sent.clear()
    checker.run_once(at=at + timedelta(minutes=20), force=True, mirror=False, notifier=notify)
    assert sent == ["uid-c"], "the newcomer is told; the other two stay quiet"


def test_one_persons_email_failure_does_not_silence_the_others(at, users, recording):
    recording._shows = [_live_show()]
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "abc"]

    def flaky(monitor, change):
        if monitor.owner_uid == "uid-b":
            raise RuntimeError("SMTP said no")

    report = _run(at, monitors, notifier=flaky)

    assert report.emails_sent == 2
    assert len(report.email_errors) == 1 and "SMTP said no" in report.email_errors[0]

    from monitor.state import load_state

    state = load_state()
    key = ALLU_DOLBY.key
    sent_to = {m.owner_uid: state[m.id].target(key) for m in monitors}
    assert sent_to["uid-a"].notified_availability is Availability.AVAILABLE
    assert sent_to["uid-c"].notified_availability is Availability.AVAILABLE
    # b was not told, so b is still armed and retries — the others do not
    assert sent_to["uid-b"].notified_availability is not Availability.AVAILABLE
    assert "SMTP said no" in state[[m for m in monitors if m.owner_uid == "uid-b"][0].id].last_email_error

    sent: list[str] = []
    checker.run_once(at=at + timedelta(minutes=10), force=True, mirror=False,
                     notifier=lambda m, c: sent.append(m.owner_uid))
    assert sent == ["uid-b"], "only the failed one is retried"


def test_a_failed_shared_read_is_an_error_for_every_subscriber(at, users, monkeypatch):
    """One refusal must not become one person's ERROR and another's silence."""
    from platforms.base import PlatformBlocked

    class Refusing:
        slug = "bookmyshow"

        def fetch(self, movie, date_codes=None):
            raise PlatformBlocked("bot check")

        def booking_url(self, movie, date_code=""):
            return movie.source_url

    monkeypatch.setattr(checker, "get_provider", lambda slug: Refusing())
    monkeypatch.setattr(checker, "with_current_variants", lambda movie: movie)
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "ab"]

    report = _run(at, monitors)

    assert report.checked == [] and sorted(report.failed) == sorted(m.id for m in monitors)
    from monitor.state import load_state

    state = load_state()
    for m in monitors:
        assert state[m.id].last_error_kind == "BLOCKED"


# ──────────────────────────────────────────────────────────────────────────
# 12–14 · What sharing must not change
# ──────────────────────────────────────────────────────────────────────────
def test_sharing_holds_no_record_one_account_could_read_from(users):
    """The deduplication is derived and in-memory. There is no shared
    document, so there is nothing for the Firestore rules to guard and
    nothing for one account to read another's data out of."""
    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    group = sharing.group_for_fetch([a, b])[0]

    # the group is a view over monitors the caller already holds
    assert group.monitors[0] is a and group.monitors[1] is b
    assert a.owner_uid == "uid-a" and b.owner_uid == "uid-b"     # unchanged by grouping
    # …and nothing about one owner leaks into the other's monitor
    assert a.notify_email != b.notify_email
    assert sharing.__dict__.get("_SHARED") is None

    from pathlib import Path

    rules = Path("firestore.rules").read_text(encoding="utf-8")
    assert "shared" not in rules.lower(), "sharing must add no collection to the rules"
    assert "owner_uid == request.auth.uid" in rules


def test_one_user_still_cannot_see_another_users_monitor(at, users):
    """The store is owner-scoped and sharing does not touch it."""
    from monitor import state as state_mod

    a = users("uid-a", ALLU_DOLBY)
    b = users("uid-b", ALLU_DOLBY)
    save_monitors([a, b], mirror=False)

    scope = state_mod.Scope("", "uid-a", lambda: "tok", firestore_enabled=False)
    state_mod.set_scope_provider(lambda: scope)
    try:
        state_mod.invalidate_cache()
        mine = state_mod.load_monitors()
    finally:
        state_mod.set_scope_provider(None)
        state_mod.invalidate_cache()

    assert [m.id for m in mine] == [a.id]
    assert b.id not in {m.id for m in mine}


def test_a_shared_target_still_costs_the_subscriber_one_of_their_own_slots(at, users):
    """Joining an already-running check is still one active monitor for that
    account — sharing is a backend saving, never a way past the limit."""
    from monitor import state as state_mod

    limit = state_mod.ACTIVE_MONITOR_LIMIT
    mine = [users("uid-a", ALLU_DOLBY) for _ in range(limit)]
    for i, m in enumerate(mine):
        m.id = f"same-target-{i}"
    save_monitors(mine, mirror=False)

    # every one of them is the same shared check…
    assert len(sharing.group_for_fetch(mine)) == 1
    # …and the account is still at its limit
    scope = state_mod.Scope("", "uid-a", lambda: "tok",
                            firestore_enabled=False, admin=False)
    state_mod.set_scope_provider(lambda: scope)
    try:
        state_mod.invalidate_cache()
        assert state_mod.active_monitor_count(mine, at=at) == limit
        assert state_mod.monitor_limit_reached(mine, at=at) is True
        with pytest.raises(state_mod.MonitorLimitError):
            state_mod.upsert_monitor(users("uid-a", ALLU_DOLBY), mirror=False)
    finally:
        state_mod.set_scope_provider(None)
        state_mod.invalidate_cache()


def test_a_lone_monitor_behaves_exactly_as_it_did_before(at, make_monitor, recording):
    """The production monitor: one account, one film, two theatres, two
    formats, ten minutes. It was one listing read before this change and it
    is one now, with the same targets evaluated the same way."""
    monitor = make_monitor(interval=10, targets=[ALLU_DOLBY, AMB_HDR], owner_uid="uid-prod")
    monitor.date_codes = ["20260925"]

    report = _run(at, [monitor])

    assert recording.count == 1 and recording.reads == [("ET00478890", ("20260925",))]
    assert report.checked == [monitor.id]
    assert report.fetches == 1 and report.sharing.saved == 0
    assert report.sharing.shared == []                       # nothing shared, nothing said
    from monitor.state import load_state

    keys = set(load_state()[monitor.id].targets)
    assert keys == {ALLU_DOLBY.key, AMB_HDR.key}             # both targets still evaluated


def test_a_monitor_saved_before_this_change_needs_no_migration(at, make_monitor, recording):
    """Grouping reads only fields the model already had. A document written
    by the old code — no new keys, ``variants`` absent — groups and checks
    exactly as one written today."""
    legacy = make_monitor(targets=[ALLU_DOLBY], owner_uid="")
    raw = legacy.to_dict()
    assert "shared_target" not in raw and "target_key" not in raw

    from monitor.models import Monitor

    restored = Monitor.from_dict(raw)
    assert sharing.fetch_identity(restored.movie, restored.date_codes) == \
        sharing.fetch_identity(legacy.movie, legacy.date_codes)

    report = _run(at, [restored])
    assert recording.count == 1 and report.checked == [restored.id]


# ──────────────────────────────────────────────────────────────────────────
# The measurement (PART D)
# ──────────────────────────────────────────────────────────────────────────
def test_ten_identical_monitors_cost_one_read_and_ten_notifications(at, users, recording):
    """The headline number, measured rather than asserted in prose."""
    recording._shows = [_live_show()]
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in range(10)]

    sent: list[str] = []
    report = _run(at, monitors, notifier=lambda m, c: sent.append(m.owner_uid))

    assert len(monitors) == 10
    assert recording.count == 1                              # …one BookMyShow read
    assert report.fetches == 1
    assert report.sharing.monitors == 10 and report.sharing.saved == 9
    assert len(sent) == 10 and len(set(sent)) == 10          # …ten separate notifications
    assert report.sharing.requests == 1                      # 1 date × 1 event = 1 HTTP call


def test_the_mixed_case_is_measured_the_way_the_traffic_is(at, users, recording):
    """A: ALLU Dolby + AMB HDR · B: ALLU Dolby · C: AMB HDR, all on the same
    film and date. Four monitor-checks before; one listing read now."""
    a = users("uid-a", ALLU_DOLBY, AMB_HDR)
    b = users("uid-b", ALLU_DOLBY)
    c = users("uid-c", AMB_HDR)

    report = _run(at, [a, b, c])

    assert report.sharing.monitors == 3
    assert recording.count == 1 and report.fetches == 1
    # two distinct subscriptions ride that one read
    assert len(sharing.group_for_fetch([a, b, c])[0].targets) == 2


def test_the_report_says_what_was_saved(at, users, recording):
    monitors = [users(f"uid-{n}", ALLU_DOLBY) for n in "abc"]
    report = _run(at, monitors)

    assert "fetches=1" in report.summary() and "saved=2" in report.summary()
    assert report.sharing.summary() == "monitors=3 fetches=1 saved=2 shared=1"
    label, count, accounts = report.sharing.shared[0]
    assert count == 3 and accounts == 3
    assert "ET00478890" in label and "20260925" in label
