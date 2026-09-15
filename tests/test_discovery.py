"""A running monitor must discover events BookMyShow creates *after* it started.

The incident (15 Sep 2026): a monitor on Avengers Endgame: Encore → ALLU
Cinemas, Kokapet → Barco Laser 4K Atmos → 25 Sep ran for three hours and 19
checks while BookMyShow was already selling that exact show — under a
premium-format sibling event. The worker only ever swept the sibling events
it knew when the monitor was saved (plus whatever the catalogue had recorded),
and the catalogue is only re-listed by the sync workflow: a loose four-a-day
cron, or Settings → Refresh catalogue by hand. Nothing in the worker asked
BookMyShow "which events make up this film *now*?".

These tests drive ``run_once`` — the real worker tick — with the city listing
and the showtimes both faked, and never call the catalogue sync. If any of
them fails, a user watching a COMING SOON theatre needs to press Refresh
catalogue by hand again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from config import store
from monitor import catalogue, checker, discovery
from monitor.discovery import (
    DISCOVERY_RETRY,
    DISCOVERY_TTL,
    discover_siblings,
    family_for,
    merge_variants,
)
from monitor.models import ANY_FORMAT, Availability, Monitor, MovieRef, TheatreTarget
from monitor.state import MonitorState, load_monitors, load_state, upsert_monitor
from platforms.base import PlatformBlocked, PlatformError
from tests.conftest import build_payload, build_quickbook

# ── The film as BookMyShow lists it ─────────────────────────────────────
ENGLISH_2D = "ET00514163"       # the row the user picks
ENGLISH_3D = "ET00516731"       # a sibling known when the monitor was saved
BARCO = "ET00517001"            # created by BookMyShow *after* the monitor started
DOLBY = "ET00517002"            # created later still
TELUGU_2D = "ET00514535"        # the film's other row (its own, separate family)

ALLU = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Barco Laser 4K Atmos")
ALLU_DOLBY = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Dolby Cinema")
SHOW_DATE = "20260925"


def listing(*extra_children: tuple[str, str], telugu: bool = True) -> dict:
    """The city QUICKBOOK answer: Avengers in English (2D + 3D + ``extra``)
    and, separately, in Telugu."""
    children = [
        {"code": ENGLISH_2D, "language": "English", "dimension": "2D"},
        {"code": ENGLISH_3D, "language": "English", "dimension": "3D"},
        *({"code": code, "language": "English", "dimension": dim} for code, dim in extra_children),
    ]
    if telugu:
        children.append({"code": TELUGU_2D, "language": "Telugu", "dimension": "2D"})
    return build_quickbook([
        {"title": "Avengers Endgame: Encore", "url": "avengers-endgame-encore", "children": children},
        {"title": "Mandaadi", "url": "mandaadi",
         "children": [{"code": "ET00514261", "language": "Telugu", "dimension": "2D"}]},
    ])


def show(venue: TheatreTarget, fmt: str, status: str | None = "3", time: str = "03:40 PM",
         code: str = "1540") -> dict:
    return {"venue_code": venue.venue_code, "venue_name": venue.venue_name, "area": venue.area,
            "time": time, "time_code": code, "fmt": fmt, "status": status, "date": SHOW_DATE}


AMB_2D = show(TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", ANY_FORMAT), "2D", None)
PVR_3D = show(TheatreTarget("PVFS", "PVR", "Kukatpally", ANY_FORMAT), "3D", "3")
ALLU_BARCO_LIVE = show(ALLU, "BARCO LASER 4K ATMOS", "3")
ALLU_BARCO_LATE = show(ALLU, "BARCO LASER 4K ATMOS", "3", time="11:00 PM", code="2300")
ALLU_DOLBY_LIVE = show(ALLU_DOLBY, "DOLBY CINEMA", "3", time="07:00 PM", code="1900")


def english_movie() -> MovieRef:
    return MovieRef(
        platform="bookmyshow", event_code=ENGLISH_2D, title="Avengers Endgame: Encore",
        region_code="HYD", region_slug="hyderabad", language="English",
        source_url=f"https://in.bookmyshow.com/movies/hyderabad/avengers-endgame-encore/buytickets/{ENGLISH_2D}",
        variants=((ENGLISH_3D, "3D"),),
    )


def make_monitor(at, *targets: TheatreTarget, movie: MovieRef | None = None) -> Monitor:
    return Monitor(
        movie=movie or english_movie(),
        targets=list(targets) or [ALLU],
        interval_minutes=10,
        monitor_until=at + timedelta(days=3),
        notify_email="watcher@example.com",
        date_codes=[SHOW_DATE],
    )


def seed_catalogue(provider_factory) -> None:
    """The catalogue as it stood before the release: the English row with its
    two events, listing AMB and PVR — ALLU nowhere."""
    provider = provider_factory([build_payload([AMB_2D]), build_payload([PVR_3D])])
    catalogue.store_snapshot(provider.fetch(english_movie()), mirror=False)
    entry = catalogue.find_entry(f"bookmyshow:{ENGLISH_2D}")
    assert [v.name for v in catalogue.venues_from_entry(entry)] == ["AMB Cinemas", "PVR"]


class Wire:
    """One tick's worth of fake BookMyShow: the city listing the worker's
    discovery reads, and the showtimes the check reads. Separate sessions so
    each can be asserted on its own."""

    def __init__(self, provider_factory, monkeypatch):
        self._factory = provider_factory
        self._mp = monkeypatch
        self.listing_calls = 0

    def tick(self, *, listing_payload=None, listing_error=None, shows: list[list[dict]]):
        """Arm the next tick. ``listing_calls`` counts that tick's listings only."""
        wire = self
        self.listing_calls = 0

        class ListingProvider:
            slug = "bookmyshow"

            def list_movies(self, region_slug):
                wire.listing_calls += 1
                if listing_error is not None:
                    raise listing_error
                assert listing_payload is not None, "the tick was not expected to list the city"
                return wire._factory([listing_payload]).list_movies(region_slug)

        self._mp.setattr(discovery, "get_provider", lambda slug: ListingProvider())
        self.showtimes = self._factory([build_payload(s) for s in shows])
        self._mp.setattr(checker, "get_provider", lambda slug: self.showtimes)

    @property
    def swept(self) -> list[str]:
        return [c["params"]["eventCode"] for c in self.showtimes.session.calls]


# ──────────────────────────────────────────────────────────────────────────
# The exact incident, end to end, with no manual refresh anywhere
# ──────────────────────────────────────────────────────────────────────────
def test_running_monitor_discovers_a_sibling_event_released_after_it_started(
        provider_factory, monkeypatch, at):
    """T0 catalogue says COMING SOON · T1 monitor starts · T2 BookMyShow
    creates the Barco event · the *same* monitor finds it and emails once."""
    seed_catalogue(provider_factory)
    monitor = make_monitor(at, ALLU)
    upsert_monitor(monitor, mirror=False)
    sent = []
    wire = Wire(provider_factory, monkeypatch)

    # T1 — first check. The listing still knows only 2D + 3D; ALLU is nowhere.
    wire.tick(listing_payload=listing(), shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at, mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.checked == [monitor.id]
    assert report.discovery.listed == ["hyderabad"] and report.discovery.updated == {}
    assert wire.swept == [ENGLISH_2D, ENGLISH_3D]
    assert load_state()[monitor.id].targets[ALLU.key].availability is Availability.THEATRE_NOT_AVAILABLE
    assert sent == []

    # T2 — BookMyShow releases ALLU under a brand-new "Barco Laser" event.
    # Nobody presses Refresh catalogue. The catalogue still says COMING SOON.
    later = at + DISCOVERY_TTL + timedelta(minutes=5)
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")),
              shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE, ALLU_BARCO_LATE]])
    report = checker.run_once(at=later, mirror=False, notifier=lambda m, c: sent.append(c))

    assert report.discovery.listed == ["hyderabad"]
    assert report.discovery.updated == {monitor.id: [(BARCO, "Barco Laser")]}
    assert wire.swept == [ENGLISH_2D, ENGLISH_3D, BARCO]          # the new event was swept this tick
    target = load_state()[monitor.id].targets[ALLU.key]
    assert target.availability is Availability.AVAILABLE
    assert target.date_codes == [SHOW_DATE]
    assert target.time_labels == ["03:40 PM", "11:00 PM"]
    assert [c.kind.value for c in sent] == ["TICKETS_LIVE"]
    assert sent[0].venue_name == "ALLU Cinemas" and sent[0].fmt == "Barco Laser 4K Atmos"
    assert sent[0].previous is Availability.THEATRE_NOT_AVAILABLE

    # The discovery is remembered on the monitor, so the next segment (a cold
    # process) sweeps the Barco event too — and the catalogue was left alone
    # for the sync to re-detail on its own.
    stored = load_monitors()[0]
    assert stored.id == monitor.id
    assert dict(stored.movie.variants) == {ENGLISH_3D: "3D", BARCO: "Barco Laser"}
    assert stored.date_codes == [SHOW_DATE] and stored.interval_minutes == 10
    row = catalogue.movie_from_entry(catalogue.find_entry(stored.movie.id))
    assert row.variants == ((ENGLISH_3D, "3D"),)
    assert store.DISCOVERY_FILE.exists()

    # T3 — the next check: nothing changed, so nothing is sent.
    again = later + timedelta(minutes=10)
    wire.tick(shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE, ALLU_BARCO_LATE]])
    report = checker.run_once(at=again, mirror=False, notifier=lambda m, c: sent.append(c))
    assert report.checked == [monitor.id]
    assert not report.discovery.ran                                 # resolved: nothing to look for
    assert wire.swept == [ENGLISH_2D, ENGLISH_3D, BARCO]
    assert len(sent) == 1


def test_stale_catalogue_is_not_the_worker_s_only_source(provider_factory, monkeypatch, at):
    """The catalogue was read before the release and is never refreshed. The
    worker stays alive across ticks; the next due tick sees the new event."""
    seed_catalogue(provider_factory)
    upsert_monitor(make_monitor(at, ALLU), mirror=False)
    wire = Wire(provider_factory, monkeypatch)

    wire.tick(listing_payload=listing(), shows=[[AMB_2D], [PVR_3D]])
    checker.run_once(at=at, mirror=False, notifier=lambda m, c: None)

    # Inside the TTL the city is not re-listed: the check runs on what is known.
    wire.tick(shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at + timedelta(minutes=10), mirror=False, notifier=lambda m, c: None)
    assert report.checked and not report.discovery.ran
    assert any("next re-list" in s for s in report.discovery.skipped)
    assert wire.listing_calls == 0

    # Past the TTL it is — and the release is found without a catalogue sync.
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")),
              shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE]])
    sent = []
    report = checker.run_once(at=at + timedelta(minutes=20), mirror=False,
                              notifier=lambda m, c: sent.append(c))
    assert wire.listing_calls == 1
    assert report.discovery.listed == ["hyderabad"]
    assert [c.kind.value for c in sent] == ["TICKETS_LIVE"]
    stale_row = catalogue.movie_from_entry(catalogue.find_entry(f"bookmyshow:{ENGLISH_2D}"))
    assert BARCO not in stale_row.variant_codes                    # the catalogue really was stale


# ──────────────────────────────────────────────────────────────────────────
# Format siblings released at different times, watched by different monitors
# ──────────────────────────────────────────────────────────────────────────
def test_barco_and_dolby_siblings_reach_the_right_monitor_in_turn(provider_factory, monkeypatch, at):
    seed_catalogue(provider_factory)
    barco = make_monitor(at, ALLU)
    dolby = make_monitor(at, ALLU_DOLBY)
    upsert_monitor(barco, mirror=False)
    upsert_monitor(dolby, mirror=False)
    sent = []
    wire = Wire(provider_factory, monkeypatch)

    def run(when, **kw):
        wire.tick(**kw)
        return checker.run_once(at=when, mirror=False, notifier=lambda m, c: sent.append(c))

    # Both monitors are due, both unresolved: the city is listed once, shared.
    run(at, listing_payload=listing(), shows=[[AMB_2D], [PVR_3D]] * 2)
    assert wire.listing_calls == 1

    # Barco event appears. The Barco monitor goes live; the Dolby one now sees
    # the theatre listed but not in its format — still waiting, no email.
    t2 = at + timedelta(minutes=20)
    run(t2, listing_payload=listing((BARCO, "BARCO LASER")),
        shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE]] * 2)
    assert set(wire.swept) == {ENGLISH_2D, ENGLISH_3D, BARCO}
    state = load_state()
    assert state[barco.id].targets[ALLU.key].availability is Availability.AVAILABLE
    assert state[dolby.id].targets[ALLU_DOLBY.key].availability is Availability.SHOW_NOT_AVAILABLE
    assert [(c.monitor_id, c.fmt) for c in sent] == [(barco.id, "Barco Laser 4K Atmos")]

    # Dolby event appears later. Only the Dolby monitor has anything to say.
    t3 = t2 + timedelta(minutes=20)
    run(t3, listing_payload=listing((BARCO, "BARCO LASER"), (DOLBY, "DOLBY CINEMA")),
        shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE], [ALLU_DOLBY_LIVE]] * 2)
    assert wire.listing_calls == 1                                   # one listing for both
    state = load_state()
    assert state[dolby.id].targets[ALLU_DOLBY.key].availability is Availability.AVAILABLE
    assert state[barco.id].targets[ALLU.key].availability is Availability.AVAILABLE
    assert [(c.monitor_id, c.fmt) for c in sent] == [
        (barco.id, "Barco Laser 4K Atmos"), (dolby.id, "Dolby Cinema")]
    # Both monitors now carry the whole family.
    for stored in load_monitors():
        assert set(stored.movie.variant_codes) == {ENGLISH_3D, BARCO, DOLBY}

    # And a further tick is silent.
    run(t3 + timedelta(minutes=10), shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE], [ALLU_DOLBY_LIVE]] * 2)
    assert len(sent) == 2


# ──────────────────────────────────────────────────────────────────────────
# Restraint: when discovery looks, when it doesn't, and what a failure does
# ──────────────────────────────────────────────────────────────────────────
def test_a_refused_listing_leaves_the_check_and_the_monitor_untouched(provider_factory, monkeypatch, at):
    seed_catalogue(provider_factory)
    monitor = make_monitor(at, ALLU)
    upsert_monitor(monitor, mirror=False)
    wire = Wire(provider_factory, monkeypatch)

    wire.tick(listing_error=PlatformBlocked("refused (HTTP 403)"), shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at, mirror=False, notifier=lambda m, c: None)
    assert report.discovery.failed == ["hyderabad: refused (HTTP 403)"]
    assert report.checked == [monitor.id] and report.failed == []    # the check itself still ran
    ms = load_state()[monitor.id]
    assert ms.targets[ALLU.key].availability is Availability.THEATRE_NOT_AVAILABLE
    assert ms.consecutive_errors == 0                                 # a failed listing is not a failed check
    assert load_monitors()[0].movie.variants == ((ENGLISH_3D, "3D"),)

    # Backs off (shorter than the TTL), then retries — and a network error is
    # handled the same way.
    wire.tick(shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at + DISCOVERY_RETRY - timedelta(minutes=1), force=True,
                              mirror=False, notifier=lambda m, c: None)
    assert not report.discovery.ran and any("failed" in s for s in report.discovery.skipped)
    assert wire.listing_calls == 0

    wire.tick(listing_error=PlatformError("ConnectionError"), shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at + DISCOVERY_RETRY, force=True,
                              mirror=False, notifier=lambda m, c: None)
    assert report.discovery.failed == ["hyderabad: ConnectionError"]
    assert load_state()[monitor.id].consecutive_errors == 0


def test_a_resolved_monitor_never_lists_the_city(provider_factory, monkeypatch, at):
    """A target that is bookable, sold out or awaiting booking has found its
    event. Nothing a new sibling could add — so no request is made."""
    upsert_monitor(make_monitor(at, ALLU), mirror=False)
    wire = Wire(provider_factory, monkeypatch)
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")),
              shows=[[ALLU_BARCO_LIVE], [PVR_3D], [ALLU_BARCO_LIVE]])
    # First tick: unresolved (never checked) → listed, found, and then live.
    report = checker.run_once(at=at, mirror=False, notifier=lambda m, c: None)
    assert wire.listing_calls == 1 and report.checked
    assert load_state()[load_monitors()[0].id].targets[ALLU.key].availability is Availability.AVAILABLE

    for minutes, shows in ((20, [[ALLU_BARCO_LIVE], [PVR_3D], [ALLU_BARCO_LIVE]]),
                           (40, [[show(ALLU, "BARCO LASER 4K ATMOS", "0")], [PVR_3D], [ALLU_BARCO_LIVE]]),
                           (60, [[show(ALLU, "BARCO LASER 4K ATMOS", None)], [PVR_3D], [ALLU_BARCO_LIVE]])):
        wire.tick(shows=shows)
        report = checker.run_once(at=at + timedelta(minutes=minutes), mirror=False,
                                  notifier=lambda m, c: None)
        assert report.checked and not report.discovery.ran and report.discovery.skipped == []
        assert wire.listing_calls == 0


def test_a_catalogue_sync_counts_as_a_listing(provider_factory, monkeypatch, at):
    """Settings → Refresh catalogue (or the schedule) has just listed the
    city; the worker doesn't ask again inside the TTL."""
    provider = provider_factory([listing()] + [build_payload([AMB_2D])] * 4)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    monkeypatch.setattr("monitor.catalogue.now_ist", lambda: at)
    assert catalogue.sync_region("hyderabad", mirror=False)["ok"]

    upsert_monitor(make_monitor(at, ALLU), mirror=False)
    wire = Wire(provider_factory, monkeypatch)
    wire.tick(shows=[[AMB_2D], [PVR_3D]])
    report = checker.run_once(at=at + timedelta(minutes=5), mirror=False, notifier=lambda m, c: None)
    assert report.checked and not report.discovery.ran
    assert wire.listing_calls == 0


def test_only_due_monitors_trigger_a_listing_and_the_interval_is_honoured(provider_factory, monkeypatch, at):
    seed_catalogue(provider_factory)
    slow = make_monitor(at, ALLU)
    slow.interval_minutes = 30
    upsert_monitor(slow, mirror=False)
    wire = Wire(provider_factory, monkeypatch)

    wire.tick(listing_payload=listing(), shows=[[AMB_2D], [PVR_3D]])
    checker.run_once(at=at, mirror=False, notifier=lambda m, c: None)

    # 20 minutes on: the listing TTL has lapsed, but the monitor isn't due, so
    # neither a listing nor a check happens. The user's interval is the clock.
    wire.tick(shows=[])
    report = checker.run_once(at=at + timedelta(minutes=20), mirror=False, notifier=lambda m, c: None)
    assert report.checked == [] and any("not due" in s for s in report.skipped)
    assert not report.discovery.ran and wire.listing_calls == 0

    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")),
              shows=[[AMB_2D], [PVR_3D], [ALLU_BARCO_LIVE]])
    report = checker.run_once(at=at + timedelta(minutes=30), mirror=False, notifier=lambda m, c: None)
    assert report.checked == [slow.id] and report.discovery.listed == ["hyderabad"]
    assert load_state()[slow.id].targets[ALLU.key].availability is Availability.AVAILABLE


def test_discovery_only_ever_adds_siblings(provider_factory, monkeypatch, at):
    """A listing strategy that knows fewer siblings, or none, must not shrink
    what the monitor sweeps. And a movie missing from the listing is left alone."""
    monitor = make_monitor(at, ALLU)
    monitor.movie = replace(monitor.movie, variants=((ENGLISH_3D, "3D"), (BARCO, "Barco Laser")))
    upsert_monitor(monitor, mirror=False)
    state = {monitor.id: MonitorState()}

    class Sparse:
        def __init__(self, movies):
            self.movies = movies

        def list_movies(self, region_slug):
            return self.movies

    fewer = [replace(english_movie(), variants=())]
    report = discover_siblings([monitor], [monitor], state, at=at, provider_for=lambda s: Sparse(fewer))
    assert report.listed == ["hyderabad"] and report.updated == {}
    assert monitor.movie.variants == ((ENGLISH_3D, "3D"), (BARCO, "Barco Laser"))

    missing = [replace(english_movie(), event_code="ET00000001", variants=())]
    report = discover_siblings([monitor], [monitor], state, at=at + DISCOVERY_TTL,
                               provider_for=lambda s: Sparse(missing))
    assert report.listed == ["hyderabad"] and report.updated == {}
    assert monitor.movie.variants == ((ENGLISH_3D, "3D"), (BARCO, "Barco Laser"))


def test_family_follows_the_event_when_bookmyshow_repicks_the_primary(provider_factory):
    """If our event is now a *sibling* of a different primary, the family is
    that primary plus its other siblings — never ourselves."""
    movie = english_movie()
    repicked = build_quickbook([{
        "title": "Avengers Endgame: Encore", "url": "avengers-endgame-encore", "children": [
            {"code": BARCO, "language": "English", "dimension": "2D"},       # now the plain one
            {"code": ENGLISH_2D, "language": "English", "dimension": "3D"},
            {"code": DOLBY, "language": "English", "dimension": "DOLBY CINEMA"},
        ]}])
    listed = provider_factory([repicked]).list_movies("hyderabad")
    assert [m.event_code for m in listed] == [BARCO]
    assert family_for(movie, listed) == ((BARCO, ""), (DOLBY, "Dolby Cinema"))
    assert family_for(replace(movie, event_code="ET00999999"), listed) is None
    assert merge_variants(movie.variants, family_for(movie, listed), own_code=ENGLISH_2D) == (
        (ENGLISH_3D, "3D"), (BARCO, ""), (DOLBY, "Dolby Cinema"))
    # Labels are kept when known, filled in when not, and a code is never doubled.
    assert merge_variants(((BARCO, "Barco Laser"),), ((BARCO, ""), (BARCO, "x")), own_code=ENGLISH_2D) == (
        (BARCO, "Barco Laser"),)
    assert merge_variants(((BARCO, ""),), ((BARCO, "Barco Laser"),), own_code=ENGLISH_2D) == (
        (BARCO, "Barco Laser"),)


# ──────────────────────────────────────────────────────────────────────────
# What actually happened on 15 Sep: the Telugu row was watched, and the
# theatre was listed under the English one. The log must say so.
# ──────────────────────────────────────────────────────────────────────────
def test_log_points_at_the_other_language_row_when_it_lists_the_theatre(
        provider_factory, monkeypatch, at, capsys):
    # The English row lists ALLU (Barco). The Telugu row lists one theatre.
    title = "Avengers Endgame: Encore"
    english = provider_factory([build_payload([AMB_2D], title=title),
                                build_payload([ALLU_BARCO_LIVE], title=title)]).fetch(
        replace(english_movie(), variants=((BARCO, "Barco Laser"),)))
    catalogue.store_snapshot(english, mirror=False)
    telugu_movie = MovieRef(platform="bookmyshow", event_code=TELUGU_2D, title="Avengers Endgame: Encore",
                            region_code="HYD", region_slug="hyderabad", language="Telugu",
                            source_url=f"https://in.bookmyshow.com/movies/hyderabad/avengers-endgame-encore-telugu/buytickets/{TELUGU_2D}")
    sai_ranga = show(TheatreTarget("SRNG", "Sai Ranga 70MM", "Miyapur", ANY_FORMAT), "2D", None)
    catalogue.store_snapshot(provider_factory([build_payload([sai_ranga], title=title)]).fetch(telugu_movie),
                             mirror=False)

    monitor = make_monitor(at, ALLU, movie=telugu_movie)
    upsert_monitor(monitor, mirror=False)
    wire = Wire(provider_factory, monkeypatch)
    wire.tick(listing_payload=listing((BARCO, "BARCO LASER")), shows=[[sai_ranga]])
    report = checker.run_once(at=at, mirror=False, notifier=lambda m, c: None)

    # Honest for the row that was picked: the Telugu event does not list ALLU,
    # and the Telugu family gained nothing (its siblings live in another row).
    assert report.discovery.updated == {}
    assert load_state()[monitor.id].targets[ALLU.key].availability is Availability.THEATRE_NOT_AVAILABLE
    out = capsys.readouterr().out
    assert "[hint] ALLU Cinemas is listed for 'Avengers Endgame: Encore · English" in out
    assert f"({ENGLISH_2D}; formats: Barco Laser 4K Atmos)" in out
    assert f"watches the Telugu row ({TELUGU_2D})" in out
