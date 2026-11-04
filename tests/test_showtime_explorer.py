"""Showtime Explorer — classification, the time window, the live source and
its safeguards, the catalogue sync's snapshot, and the page itself.

Every BookMyShow answer here is built by ``tests.conftest.build_payload``,
which reproduces the widget shape of the real showtimes-by-event response;
the parser under test is the production one. The suite's clock is pinned at
25 Sep 2026, 10:20 PM IST (``conftest.SUITE_NOW``).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, time as dtime

import pytest

from config import store
from config.timezone import IST
from explorer import catalog, classify as K, query as Q, snapshot as snap, source as S
from monitor import catalogue
from monitor.models import MovieRef
from platforms.bookmyshow import BookMyShowProvider
from tests.conftest import APP_SCRIPT, build_payload

TODAY, TOMORROW = "20260925", "20260926"
BMS = "https://in.bookmyshow.com"


# ──────────────────────────────────────────────────────────────────────────
# A BookMyShow that answers from fixtures
# ──────────────────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, payload=None, status=200, text_body=None):
        self.payload, self.status_code, self._text = payload, status, text_body

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self.payload


class FakeBMS:
    """``answers[event_code]`` is a payload, a status code, or a callable."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def get(self, url, headers=None, params=None, timeout=20):
        code, date = params["eventCode"], params["dateCode"]
        with self.lock:
            self.calls.append((code, date))
        answer = self.answers.get(code, 404)
        if callable(answer):
            answer = answer(date)
        if isinstance(answer, int):
            return Resp(status=answer)
        if answer == "garbage":
            return Resp(text_body="<html>")
        return Resp(answer)


def show(code, name, area, time, tcode, date=TOMORROW, fmt="2D", status="3", sid=None):
    return {"venue_code": code, "venue_name": name, "area": area, "time": time, "time_code": tcode,
            "date": date, "fmt": fmt, "status": status, "session_id": sid or f"{code}{date}{tcode}{fmt}"}


RAMAYANA_SHOWS = [
    show("AMBH", "AMB Cinemas", "Gachibowli", "09:05 AM", "0905"),
    show("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad", "09:30 AM", "0930", fmt="3D"),
    show("ASJY", "Asian Jyothi", "RC Puram", "10:15 AM", "1015"),
    show("SUDA", "Sudarshan 35MM 4k Laser & Dolby Atmos", "RTC X Roads", "06:45 AM", "0645"),
    show("AMBH", "AMB Cinemas", "Gachibowli", "10:40 AM", "1040", fmt="3D"),
    show("PRHN", "Prasads Multiplex", "Hyderabad", "11:00 AM", "1100"),
    show("PRHN", "Prasads Multiplex", "Hyderabad", "11:01 AM", "1101"),
    show("AMBH", "AMB Cinemas", "Gachibowli", "07:30 PM", "1930", status="0"),
]
IMAX_SHOWS = [show("PRHN", "Prasads Multiplex", "Hyderabad", "10:30 AM", "1030", fmt="")]


def ramayana(**kw) -> MovieRef:
    base = dict(platform="bookmyshow", event_code="ET00900001", title="Ramayana", region_code="HYD",
                region_slug="hyderabad", language="Telugu",
                source_url=f"{BMS}/movies/hyderabad/ramayana/buytickets/ET00900001",
                variants=(("ET00900002", "IMAX 3D"),))
    base.update(kw)
    return MovieRef(**base)


def payload(shows, date=TOMORROW):
    return build_payload(shows, bookable_dates=(date,), closed_dates=())


def make_reader(bms: FakeBMS, clock=None, bucket=None) -> S.LiveReader:
    kw = {}
    if clock is not None:
        kw["clock"] = clock
    return S.LiveReader(lambda b, sl: BookMyShowProvider(session=S.GatedSession(bms, b, sl),
                                                         sleeper=lambda s: None),
                        bucket=bucket or S.TokenBucket(capacity=1000, sleeper=lambda s: None), **kw)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def explorer_show(**kw) -> Q.ExplorerShow:
    base = dict(movie_id="bookmyshow:ET1", movie_title="Ramayana", language="Telugu", event_code="ET1",
                venue_code="AMBH", venue_name="AMB Cinemas", area="Gachibowli", locality="Gachibowli",
                operator="AMB Cinemas", theatre_type=K.UNKNOWN, date_code=TOMORROW, time_label="09:05 AM",
                minutes=545, formats=("2D",), session_id="", booking_url=f"{BMS}/x")
    base.update(kw)
    return Q.ExplorerShow(**base)


# ──────────────────────────────────────────────────────────────────────────
# Classification: only what BookMyShow's own text says
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,operator", [
    ("PVR", "PVR"), ("PVR ICON", "PVR"), ("PVR Superplex Inorbit", "PVR"),
    ("INOX", "INOX"), ("INOX GVK One, Banjara Hills", "INOX"),
    ("Cinepolis", "Cinepolis"), ("AMB Cinemas", "AMB Cinemas"),
    ("Asian Mukta A2 Sensation Cinema", "Asian Cinemas"), ("Miraj Cinemas", "Miraj Cinemas"),
    # a familiar word that is not where a brand goes proves nothing
    ("ALLU Cinemas", K.INDEPENDENT), ("Platinum Movietime Cinema", K.INDEPENDENT),
    ("Sudarshan 35MM", K.INDEPENDENT), ("Asiana Talkies", K.INDEPENDENT), ("", K.INDEPENDENT),
])
def test_operator_needs_the_brand_at_the_start_of_the_name(name, operator):
    assert K.operator_of(name) == operator


@pytest.mark.parametrize("name,area,kind", [
    ("PVR", "Nexus Mall Kukatpally, Hyderabad", K.MULTIPLEX),           # a multiplex chain
    ("Prasads Multiplex", "Hyderabad", K.MULTIPLEX),                     # the name says so
    ("Asian Tarakarama Cineplex", "Kachiguda", K.MULTIPLEX),
    ("Devi 70MM 4K Laser & Dolby Atmos", "RTC X Roads", K.SINGLE_SCREEN),  # listed by film gauge
    ("Sandhya 35mm 2k Dolby Atmos", "RTC X Roads", K.SINGLE_SCREEN),
    ("AMB Cinemas", "Gachibowli", K.UNKNOWN),                            # nothing says which
    ("Asian Jyothi", "RC Puram", K.UNKNOWN),                             # Asian runs both kinds
    ("Kumar Theatre", "Kachiguda", K.UNKNOWN),
])
def test_theatre_type_is_only_claimed_with_evidence(name, area, kind):
    assert K.classify(name, area).theatre_type == kind


@pytest.mark.parametrize("name,area,locality", [
    ("PVR", "Atrium Gachibowli, Hyderabad", "Gachibowli"),
    ("PVR ICON", "Hitech, Madhapur, Hyderabad", "Madhapur"),
    ("CONNPLEX Luxuriance Cinemas", "Mpm Mall, Banjara Hil", "Banjara Hills"),   # BookMyShow's truncation
    ("Asian Mukta A2 Sensation Cinema", "Kairathabad", "Khairatabad"),           # its spelling
    ("INOX GVK One, Banjara Hills", "Hyderabad", "Banjara Hills"),                # named in the name
    ("Prasads Multiplex", "Hyderabad", K.UNKNOWN),                                # only the city
    ("PVR", "RK Cineplex, Hyderabad", K.UNKNOWN),                                 # never guessed
    ("Mystery Hall", "", K.UNKNOWN),
    ("Cinepolis", "Mantra Mall, Attapur", "Attapur"),
])
def test_locality_comes_from_the_listing_or_is_unknown(name, area, locality):
    assert K.locality_of(name, area) == locality


def test_the_earliest_place_named_wins():
    assert K.locality_of("X", "Uppal Road near Kukatpally") == "Uppal"


def test_every_theatre_in_the_real_catalogue_classifies_without_error():
    data = json.loads((store.REPO_ROOT / "data" / "catalogue.json").read_text(encoding="utf-8"))
    for entry in data["movies"]:
        for v in entry.get("venues", []):
            facts = K.classify(v["name"], v["area"])
            assert facts.locality in {n for n, _ in K.LOCALITIES} | {K.UNKNOWN}
            assert facts.theatre_type in (K.MULTIPLEX, K.SINGLE_SCREEN, K.UNKNOWN)


# ──────────────────────────────────────────────────────────────────────────
# Times and the Asia/Kolkata window
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,label,minutes", [
    ("1930", "", 19 * 60 + 30), ("0905", "", 545), ("", "07:30 PM", 19 * 60 + 30),
    ("", "12:05 AM", 5), ("", "12:30 PM", 12 * 60 + 30), ("", "9:05 am", 545),
    ("2460", "11:00 PM", 23 * 60),   # a bad code falls back to the label
    ("", "", None), ("", "Tonight", None), ("abcd", "25:00 PM", None),
])
def test_parse_minutes(code, label, minutes):
    assert Q.parse_minutes(code, label) == minutes


def test_window_validation():
    assert Q.validate_window(dtime(7), dtime(11)) == (420, 660)
    for start, end in ((dtime(11), dtime(7)), (dtime(9), dtime(9)), (None, dtime(9)), (dtime(9), None)):
        with pytest.raises(Q.WindowError):
            Q.validate_window(start, end)


def test_a_shows_moment_is_its_own_date_in_ist():
    s = explorer_show(date_code="20261231", minutes=5)
    assert s.starts_at == datetime(2026, 12, 31, 0, 5, tzinfo=IST)
    assert explorer_show(minutes=None).starts_at is None
    assert explorer_show(date_code="").starts_at is None
    assert explorer_show(minutes=0).display_time == ("12:00", "AM")
    assert explorer_show(minutes=13 * 60 + 5).display_time == ("1:05", "PM")


def test_the_window_is_inclusive_at_both_ends_and_sorted_by_time():
    shows = [explorer_show(minutes=m, session_id=str(m), venue_name=n)
             for m, n in ((660, "B"), (419, "A"), (420, "C"), (661, "D"), (545, "A"))]
    out = Q.apply(shows, Q.Filters(TOMORROW, dtime(7), dtime(11)))
    assert [s.minutes for s in out.shows] == [420, 545, 660]
    assert out.outside_window == 2


def test_midnight_shows_stay_on_their_own_date():
    late = explorer_show(date_code="20260927", minutes=15, session_id="late")
    night = explorer_show(minutes=23 * 60 + 45, session_id="night")
    out = Q.apply([late, night], Q.Filters(TOMORROW, dtime(21), dtime(23, 59)))
    assert out.shows == [night] and out.wrong_date == 1
    out = Q.apply([late, night], Q.Filters("20260927", dtime(0), dtime(2)))
    assert out.shows == [late]


def test_shows_already_started_today_are_hidden_and_counted():
    now = datetime(2026, 9, 26, 10, 0, tzinfo=IST)
    shows = [explorer_show(minutes=m, session_id=str(m)) for m in (545, 600, 700)]
    out = Q.apply(shows, Q.Filters(TOMORROW, hide_started_before=now))
    assert [s.minutes for s in out.shows] == [600, 700] and out.already_started == 1


def test_unreadable_times_are_counted_not_placed():
    out = Q.apply([explorer_show(minutes=None, time_label="?")], Q.Filters(TOMORROW))
    assert out.shows == [] and out.unreadable_time == 1


def test_filters_operator_type_locality_language_format():
    shows = [
        explorer_show(session_id="1", operator="PVR", theatre_type=K.MULTIPLEX, locality="Kukatpally", formats=("3D",)),
        explorer_show(session_id="2", operator="INOX", theatre_type=K.MULTIPLEX, locality="Miyapur", language="Hindi"),
        explorer_show(session_id="3", operator=K.INDEPENDENT, theatre_type=K.SINGLE_SCREEN, locality=K.UNKNOWN),
        explorer_show(session_id="4", operator="AMB Cinemas", theatre_type=K.UNKNOWN, formats=("IMAX 3D", "2D")),
    ]
    ids = lambda **f: [s.session_id for s in Q.apply(shows, Q.Filters(TOMORROW, **f)).shows]  # noqa: E731
    assert set(ids(operators=("PVR", "INOX"))) == {"1", "2"}
    assert set(ids(theatre_type=K.MULTIPLEX)) == {"1", "2"}
    assert ids(theatre_type=K.SINGLE_SCREEN) == ["3"]
    assert ids(localities=(K.UNKNOWN,)) == ["3"]
    assert set(ids(localities=("Kukatpally", "Miyapur"))) == {"1", "2"}
    assert ids(languages=("Hindi",)) == ["2"]
    assert ids(formats=("imax 3d",)) == ["4"]           # case-insensitive, any of the show's labels
    assert len(ids()) == 4                              # nothing chosen = everything


def test_duplicates_are_removed_and_their_formats_kept():
    a = explorer_show(session_id="S1", formats=("2D",))
    b = explorer_show(session_id="S1", formats=("IMAX 3D",))
    c = explorer_show(session_id="", minutes=600, formats=("2D",))
    d = explorer_show(session_id="", minutes=600, formats=("2D",))
    out, dropped = Q.dedupe_shows([a, b, c, d])
    assert dropped == 2 and len(out) == 2
    assert out[0].formats == ("2D", "IMAX 3D")


def test_grouping_by_theatre_and_by_movie():
    shows = [explorer_show(session_id="1", venue_code="P", venue_name="PVR", minutes=600),
             explorer_show(session_id="2", venue_code="A", venue_name="AMB", minutes=545),
             explorer_show(session_id="3", venue_code="P", venue_name="PVR", minutes=500),
             explorer_show(session_id="4", venue_code="A", venue_name="AMB", movie_id="m2", movie_title="Jailer",
                           minutes=700)]
    theatres = Q.group_by_theatre(shows)
    assert [g.title for g in theatres] == ["PVR", "AMB"]           # by each theatre's first show
    assert [s.minutes for s in theatres[0].shows] == [500, 600]
    movies = Q.group_by_movie(shows)
    assert [g.title for g in movies] == ["Ramayana", "Jailer"]
    assert [h for h, _ in Q.by_hour(Q.sort_by_time(shows))] == ["8 AM", "9 AM", "10 AM", "11 AM"]


def test_missing_metadata_still_renders_honestly():
    bare = explorer_show(area="", locality=K.UNKNOWN, formats=(), language="", booking_url="")
    from ui import explorer as ux

    html = ux.time_rows_markup([bare], "movie")
    assert "Hyderabad" in html and "No link" in html and "tr-badge" not in html
    assert Q.options([bare])["localities"] == [K.UNKNOWN]


# ──────────────────────────────────────────────────────────────────────────
# The provider read: one film, one date, every sibling, failures kept
# ──────────────────────────────────────────────────────────────────────────
def test_read_day_sweeps_the_siblings_and_keeps_each_events_format():
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS), "ET00900002": payload(IMAX_SHOWS)})
    day = BookMyShowProvider(session=bms, sleeper=lambda s: None).read_day(ramayana(), TOMORROW)
    assert bms.calls == [("ET00900001", TOMORROW), ("ET00900002", TOMORROW)]
    assert day.errors == [] and day.events == 2
    imax = [s for s in day.showtimes if s.time_code == "1030"]
    assert imax and imax[0].event_format == "IMAX 3D"


def test_a_failed_sibling_is_reported_not_swallowed():
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS), "ET00900002": 404})
    day = BookMyShowProvider(session=bms, sleeper=lambda s: None).read_day(ramayana(), TOMORROW)
    assert len(day.showtimes) == len(RAMAYANA_SHOWS) and day.errors and day.errors[0][0] == "ET00900002"
    md = S.read_one(S.Wanted(ramayana(), "bookmyshow:ET00900001"), TOMORROW,
                    rd=make_reader(FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS), "ET00900002": 500})))
    assert md.origin == S.LIVE and md.missing
    assert "premium-format" in ux_freshness([md])[2]


def ux_freshness(days):
    from ui import explorer as ux

    return ux.freshness(days)


def test_a_fallback_date_is_dated_as_bookmyshow_dated_it():
    # Asked for the 27th, BookMyShow answered with the 26th's shows.
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS, date=TOMORROW)})
    md = S.read_one(S.Wanted(ramayana(variants=()), "m"), "20260927", rd=make_reader(bms))
    out = Q.apply(md.shows, Q.Filters("20260927"))
    assert out.shows == [] and out.wrong_date == len(RAMAYANA_SHOWS)


def test_booking_links_are_verified_bookmyshow_pages():
    md = S.read_one(S.Wanted(ramayana(variants=()), "m"), TOMORROW,
                    rd=make_reader(FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})))
    for s in md.shows:
        assert s.booking_url.startswith(f"{BMS}/buytickets/ramayana-hyderabad/cinema-hyde-{s.venue_code}-MT/")
        assert s.booking_url.endswith(TOMORROW)
    # A catalogue row with a link that is not BookMyShow's never becomes a link.
    odd = ramayana(variants=(), source_url="https://evil.example/movies/hyderabad/x/buytickets/ET00900001")
    md = S.read_one(S.Wanted(odd, "m"), TOMORROW, rd=make_reader(FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})))
    assert all(S.is_bookmyshow_url(s.booking_url) or s.booking_url == "" for s in md.shows)
    assert all("evil" not in s.booking_url for s in md.shows)


def test_sold_out_is_only_said_when_every_category_said_it():
    md = S.read_one(S.Wanted(ramayana(variants=()), "m"), TOMORROW,
                    rd=make_reader(FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})))
    sold = [s for s in md.shows if s.sold_out]
    assert [s.time_label for s in sold] == ["07:30 PM"]


# ──────────────────────────────────────────────────────────────────────────
# Caching, single flight, budget, circuit breaker
# ──────────────────────────────────────────────────────────────────────────
def test_the_cache_serves_repeat_reads_and_ages_honestly():
    clock = Clock()
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})
    rd = make_reader(bms, clock=clock)
    w = S.Wanted(ramayana(variants=()), "m")
    assert S.read_one(w, TOMORROW, rd=rd).origin == S.LIVE
    clock.t += 30
    assert S.read_one(w, TOMORROW, rd=rd).origin == S.LIVE        # still fresh
    clock.t += S.FRESH_FOR
    assert S.read_one(w, TOMORROW, rd=rd).origin == S.CACHED      # served from cache, and says so
    assert len(bms.calls) == 1
    clock.t += S.CACHE_TTL
    S.read_one(w, TOMORROW, rd=rd)
    assert len(bms.calls) == 2                                     # expired: read again


def test_refresh_respects_the_cooldown_then_reads():
    clock = Clock()
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})
    rd = make_reader(bms, clock=clock)
    w = S.Wanted(ramayana(variants=()), "m")
    S.read_one(w, TOMORROW, rd=rd)
    S.read_one(w, TOMORROW, rd=rd, force=True)
    assert len(bms.calls) == 1                                     # within COOLDOWN: no new request
    clock.t += S.COOLDOWN + 1
    S.read_one(w, TOMORROW, rd=rd, force=True)
    assert len(bms.calls) == 2


def test_concurrent_sessions_share_one_request():
    gate = threading.Event()

    def slow(date):
        gate.wait(2)
        return payload(RAMAYANA_SHOWS)

    bms = FakeBMS({"ET00900001": slow})
    rd = make_reader(bms)
    w = S.Wanted(ramayana(variants=()), "m")
    results = []
    threads = [threading.Thread(target=lambda: results.append(S.read_one(w, TOMORROW, rd=rd))) for _ in range(5)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert len(results) == 5 and len(bms.calls) == 1


def test_a_refusal_pauses_live_reads_and_falls_back_honestly():
    clock = Clock()
    bms = FakeBMS({"ET00900001": 403})
    rd = make_reader(bms, clock=clock)
    w = S.Wanted(ramayana(variants=()), "m")
    first = S.read_one(w, TOMORROW, rd=rd)
    assert first.origin == S.FAILED and "refused" in first.error and "paused" in first.error
    calls = len(bms.calls)
    assert S.read_one(w, TOMORROW, rd=rd).origin == S.FAILED
    assert len(bms.calls) == calls                                 # circuit open: not asked again
    clock.t += S.BLOCK_PAUSE + 1
    S.read_one(w, TOMORROW, rd=rd)
    assert len(bms.calls) > calls                                  # and it closes again


def test_rate_limits_and_timeouts_are_failures_not_empty_listings():
    w = S.Wanted(ramayana(variants=()), "m")
    md = S.read_one(w, TOMORROW, rd=make_reader(FakeBMS({"ET00900001": 429})))
    assert md.origin == S.FAILED and md.error == "BookMyShow is rate-limiting requests right now"
    md = S.read_one(w, TOMORROW, rd=make_reader(FakeBMS({"ET00900001": "garbage"})))
    assert md.origin == S.FAILED and md.error == "BookMyShow sent a response that couldn't be read"

    class Timeout:
        def get(self, *a, **k):
            import requests

            raise requests.Timeout("timed out")

    md = S.read_one(w, TOMORROW, rd=make_reader(Timeout()))
    assert md.origin == S.FAILED and md.error == "BookMyShow didn't respond in time"


def test_the_request_budget_is_enforced():
    bucket = S.TokenBucket(capacity=2, rate=0.01, sleeper=lambda s: None)
    bucket.acquire()
    bucket.acquire()
    with pytest.raises(S.BudgetExhausted):
        bucket.acquire(max_wait=5)
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS)})
    rd = make_reader(bms, bucket=S.TokenBucket(capacity=0, rate=0.001, sleeper=lambda s: None))
    md = S.read_one(S.Wanted(ramayana(variants=()), "m"), TOMORROW, rd=rd)
    assert md.origin == S.FAILED and "request limit" in md.error and bms.calls == []


def test_a_theatre_query_is_capped_and_says_what_it_skipped():
    films = [S.Wanted(ramayana(event_code=f"ET0090000{i}", variants=()), f"m{i}") for i in range(6)]
    bms = FakeBMS({w.movie.event_code: payload(RAMAYANA_SHOWS) for w in films})
    days = S.read_many(films, TOMORROW, max_events=4, rd=make_reader(bms))
    assert len(bms.calls) == 4 and sum(d.skipped for d in days) == 2
    assert len({c for c, _ in bms.calls}) == 4


# ──────────────────────────────────────────────────────────────────────────
# The catalogue sync's snapshot
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def synced(provider_factory, monkeypatch):
    from tests.conftest import ALLU_LIVE, QUICKBOOK_HYD, hyd_detail

    provider = provider_factory([QUICKBOOK_HYD] + hyd_detail(build_payload(ALLU_LIVE)))
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    result = catalogue.sync_region("hyderabad", mirror=False, detail=True)
    return result


def test_the_sync_keeps_the_times_it_already_read(synced):
    data = snap.load()
    assert data["movies"], "the sync wrote no snapshot"
    for movie_id, rec in data["movies"].items():
        assert rec["region"] == "hyderabad" and rec["read_at"]
        assert all(len(row) == 8 for row in rec["shows"])
    assert synced["ok"]


def test_a_broken_snapshot_never_fails_the_sync(provider_factory, monkeypatch):
    from tests.conftest import ALLU_LIVE, QUICKBOOK_HYD, hyd_detail

    provider = provider_factory([QUICKBOOK_HYD] + hyd_detail(build_payload(ALLU_LIVE)))
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(snap, "record", boom)
    result = catalogue.sync_region("hyderabad", mirror=False, detail=True)
    assert result["ok"] and catalogue.list_entries("hyderabad")
    assert not snap.path().exists()


def test_the_snapshot_drops_past_dates_unlisted_films_and_old_reads():
    from monitor.models import Availability, Showtime, Snapshot, Venue

    def snapshot_for(code, dates):
        movie = ramayana(event_code=code, variants=())
        shows = [Showtime("AMBH", "AMB Cinemas", f"S{d}", d, "09:05 AM", "0905", "2D", Availability.AVAILABLE)
                 for d in dates]
        return Snapshot(movie=movie, venues=[Venue("AMBH", "AMB Cinemas", "Gachibowli")], showtimes=shows,
                        fetched_at=datetime(2026, 9, 25, 20, 0, tzinfo=IST))

    a, b = snapshot_for("ET00900001", ["20260924", TODAY, TOMORROW]), snapshot_for("ET00900009", [TOMORROW])
    snap.record("hyderabad", [a, b], listed_ids={a.movie.id}, at=datetime(2026, 9, 25, 21, 0, tzinfo=IST))
    data = snap.load()
    assert list(data["movies"]) == [a.movie.id]
    assert [row[1] for row in data["movies"][a.movie.id]["shows"]] == [TODAY, TOMORROW]
    assert snap.movie_record(a.movie.id, at=datetime(2026, 9, 26, 12, 0, tzinfo=IST)) is not None
    assert snap.movie_record(a.movie.id, at=datetime(2026, 9, 28, 12, 0, tzinfo=IST)) is None


def test_when_live_fails_the_snapshot_is_offered_and_labelled():
    from monitor.models import Availability, Showtime, Snapshot, Venue

    movie = ramayana(variants=())
    s = Snapshot(movie=movie, venues=[Venue("AMBH", "AMB Cinemas", "Gachibowli")],
                 showtimes=[Showtime("AMBH", "AMB Cinemas", "S1", TOMORROW, "09:05 AM", "0905", "2D",
                                     Availability.AVAILABLE)],
                 fetched_at=datetime(2026, 9, 25, 19, 0, tzinfo=IST))
    snap.record("hyderabad", [s], listed_ids={movie.id})
    md = S.read_one(S.Wanted(movie, movie.id), TOMORROW, rd=make_reader(FakeBMS({movie.event_code: 403})))
    assert md.origin == S.SNAPSHOT and md.shows and md.read_at == datetime(2026, 9, 25, 19, 0, tzinfo=IST)
    kind, head, detail = ux_freshness([md])
    assert kind == "snap" and "catalogue sync" in head and "Not live" in detail and "7:00 PM" in head


def test_freshness_labels():
    now = datetime(2026, 9, 25, 22, 20, tzinfo=IST)
    live = S.MovieDay("m", TOMORROW, [], S.LIVE, now)
    old = S.MovieDay("m", TOMORROW, [], S.CACHED, datetime(2026, 9, 25, 22, 10, tzinfo=IST))
    failed = S.MovieDay("m", TOMORROW, [], S.FAILED, None, error="BookMyShow is rate-limiting requests right now")
    assert ux_freshness([live])[0] == "ok" and "Live" in ux_freshness([live])[1]
    assert ux_freshness([old])[0] == "cached" and "Cached" in ux_freshness([old])[1]
    assert ux_freshness([failed])[0] == "bad" and "rate-limiting" in ux_freshness([failed])[2]
    assert ux_freshness([live, failed])[0] == "warn"


# ──────────────────────────────────────────────────────────────────────────
# What the catalogue tells the Explorer
# ──────────────────────────────────────────────────────────────────────────
def _entry(code, title, language, venues, listings=None, variants=()):
    movie = ramayana(event_code=code, title=title, language=language, variants=variants)
    entry = catalogue.entry_from_movie(movie)
    entry["venues"] = [{"code": c, "name": n, "area": a, "formats": [], "categories": []} for c, n, a in venues]
    entry["listings"] = listings or {}
    entry["resolved_at"] = "2026-09-25T20:00:00+05:30"
    return entry


AMB = ("AMBH", "AMB Cinemas", "Gachibowli")
PVR = ("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad")
ENTRIES = [
    _entry("ET00900001", "Ramayana", "Telugu", [AMB, PVR], {TOMORROW: {"AMBH": [], "PVFS": []}},
           variants=(("ET00900002", "IMAX 3D"),)),
    _entry("ET00900003", "Ramayana", "Hindi", [PVR], {TOMORROW: {"PVFS": []}}),
    _entry("ET00900004", "Jailer 2", "Tamil", [AMB], {TOMORROW: {}}),        # listed, but not at AMB tomorrow
    _entry("ET00900005", "Kantara", "Telugu", [AMB]),                         # no per-date listing: theatre list
]


def test_titles_group_languages_and_theatres():
    titles = catalog.titles(ENTRIES)
    ram = titles[0]
    assert ram.title == "Ramayana" and ram.languages == ("Telugu", "Hindi") and ram.theatre_count == 2
    assert ram.label == "Ramayana · Telugu, Hindi · 2 theatres"
    assert [t.title for t in titles] == ["Ramayana", "Jailer 2", "Kantara"]


def test_the_theatre_directory_and_films_at_a_theatre():
    th = {t.code: t for t in catalog.theatres(ENTRIES)}
    assert th["AMBH"].label == "AMB Cinemas · Gachibowli" and th["AMBH"].facts.locality == "Gachibowli"
    films = catalog.films_at(ENTRIES, "AMBH", TOMORROW)
    # listed there that day first; then a film AMB has played but the last sync
    # did not list for that day (BookMyShow may have added it since)
    assert [(w.movie.event_code, w.listed) for w in films] == [
        ("ET00900001", True), ("ET00900005", True), ("ET00900004", False)]
    # a date beyond the per-date listings falls back to the films' theatre lists
    later = [w.movie.event_code for w in catalog.films_at(ENTRIES, "AMBH", "20261001")]
    assert later == ["ET00900001", "ET00900004", "ET00900005"]


# ──────────────────────────────────────────────────────────────────────────
# The page
# ──────────────────────────────────────────────────────────────────────────
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

JAILER_SHOWS = [show("AMBH", "AMB Cinemas", "Gachibowli", "08:15 AM", "0815", fmt="EPIQ")]


@pytest.fixture
def page_env(monkeypatch):
    catalogue_data = {"movies": ENTRIES, "updated_at": "2026-09-25T20:00:00+05:30",
                      "sync": {"hyderabad": {"status": "OK", "message": "", "at": "2026-09-25T20:00:00+05:30",
                                             "movie_count": len(ENTRIES)}}}
    store.save_catalogue(catalogue_data, mirror=False)
    bms = FakeBMS({"ET00900001": payload(RAMAYANA_SHOWS), "ET00900002": payload(IMAX_SHOWS),
                   "ET00900003": payload([show("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad", "08:00 AM",
                                               "0800")]),
                   "ET00900004": payload(JAILER_SHOWS),
                   "ET00900005": payload([show("AMBH", "AMB Cinemas", "Gachibowli", "09:45 PM", "2145")])})
    rd = make_reader(bms)
    S.install(rd)

    # The Explorer is browsing: anything that writes a monitor is a failure.
    from monitor import state as state_mod

    def forbidden(*a, **k):
        raise AssertionError("the Explorer tried to write monitor data")

    for name in ("upsert_monitor", "save_state", "record_history", "delete_monitor", "stop_monitor",
                 "extend_monitor", "save_monitors"):
        if hasattr(state_mod, name):
            monkeypatch.setattr(state_mod, name, forbidden)
    return bms, rd


def run(**session):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state["page"] = "Showtime Explorer"
    for k, v in session.items():
        app.session_state[k] = v
    return app.run()


def body(app) -> str:
    """The page's markup without the theme's stylesheet."""
    return " ".join(m.value for m in app.markdown if "<style>" not in m.value[:400])


def ok(app):
    assert not app.exception, [str(e) for e in app.exception]
    return app


def pick_movie(app, title="Ramayana"):
    box = app.selectbox(key="sx_movie")
    return ok(box.select(next(o for o in box.options if o.startswith(title))).run())


def test_the_page_opens_inside_the_shell_with_both_modes(page_env):
    app = ok(run())
    assert list(app.radio(key="page").options) == ["Home", "Showtime Explorer", "My Monitors", "History", "Settings"]
    html = body(app)
    assert "Showtime Explorer" in html and "Find your movie. Find your theatre. Find your time." in html
    assert "By movie" in html and "By theatre" in html and "tr-sx-mode selected" in html
    assert "Choose a movie to begin" in html
    assert page_env[0].calls == []                       # nothing read until something is chosen


def test_by_movie_lists_matching_shows_in_time_order(page_env):
    bms, rd = page_env
    app = ok(run(sx_date=TOMORROW))
    app = pick_movie(app)
    html = body(app)
    assert ("ET00900001", TOMORROW) in bms.calls and ("ET00900003", TOMORROW) in bms.calls
    order = [html.index(t) for t in ("6:45", "8:00", "9:05", "9:30", "10:15", "10:30", "10:40", "7:30")]
    assert order == sorted(order)
    assert "Live from BookMyShow" in html and "Gachibowli" in html and "Book" in html
    assert "cinema-hyde-AMBH-MT/20260926" in html
    assert "Sold out" in html


def test_a_custom_window_filters_and_never_reads_again(page_env):
    bms, rd = page_env
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    reads = rd.reads
    app.time_input(key="sx_from").set_value(dtime(7, 0)).run()
    app = ok(app.time_input(key="sx_to").set_value(dtime(11, 0)).run())
    html = body(app)
    assert "6:45" not in html and "7:30" not in html and "11:01" not in html
    assert "9:05" in html and "11:00" in html and "7:00 AM – 11:00 AM" in html
    assert rd.reads == reads                             # a filter is not a request


def test_an_invalid_window_is_refused(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app.time_input(key="sx_from").set_value(dtime(11, 0)).run()
    app = ok(app.time_input(key="sx_to").set_value(dtime(7, 0)).run())
    html = body(app)
    assert "end time must be later" in html and 'class="tr-sx-row"' not in html


def test_operator_type_area_and_format_filters_on_the_page(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.multiselect(key="sx_ops").select("PVR").run())
    html = body(app)
    assert "AMB Cinemas</div>" not in html and "PVR</div>" in html
    app = ok(app.multiselect(key="sx_ops").unselect("PVR").run())
    app = ok(app.selectbox(key="sx_type").select(K.SINGLE_SCREEN).run())
    html = body(app)
    assert "Sudarshan" in html and "PVR</div>" not in html
    app = ok(app.selectbox(key="sx_type").select("All theatres").run())
    app = ok(app.multiselect(key="sx_area").select("Gachibowli").run())
    html = body(app)
    assert "AMB Cinemas</div>" in html and "Sudarshan" not in html
    app = ok(app.multiselect(key="sx_fmt").select("3D").run())
    assert "10:40" in body(app) and "9:05" not in body(app)


def test_the_view_switch_keeps_the_filters(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.multiselect(key="sx_area").select("Gachibowli").run())
    app = ok(app.button(key="sxview_group").click().run())
    html = body(app)
    assert 'class="tr-sx-group"' in html and "AMB Cinemas" in html and "Sudarshan" not in html
    assert app.session_state["sx_area"] == ["Gachibowli"]


def test_switching_mode_keeps_common_filters_and_clears_city_ones(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.multiselect(key="sx_area").select("Gachibowli").run())
    app = ok(app.button(key="sxmode_theatre").click().run())
    assert app.session_state["sx_area"] == [] and app.session_state["sx_date"] == TOMORROW
    assert "Choose a theatre to begin" in body(app)
    box = app.selectbox(key="sx_theatre")
    app = ok(box.select("AMB Cinemas · Gachibowli").run())
    html = body(app)
    # every film the catalogue lists at AMB tomorrow, and only AMB's shows
    # Jailer 2 was not in the catalogue's listing for AMB that day, but BookMyShow
    # lists a show there now — it is read and shown, never silently missed
    assert "Ramayana" in html and "Kantara" in html and "Jailer 2" in html and "8:15" in html
    assert "Asian Jyothi" not in html and "9:45" in html
    assert not any(k.key == "sx_area" for k in app.multiselect)    # city filters aren't offered for one theatre


def test_refresh_reads_again_and_a_failed_refresh_says_so(page_env):
    bms, rd = page_env
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    rd.clear()                                           # past the cooldown
    bms.answers["ET00900001"] = 403
    bms.answers["ET00900003"] = 403
    app = ok(app.button(key="sx_refresh").click().run())
    html = body(app)
    assert "Refresh failed" in html or "Couldn" in html
    assert "Live from BookMyShow" not in html


def test_no_shows_is_an_honest_empty_state(page_env):
    bms, _ = page_env
    app = ok(run(sx_date="20260928"))
    app = pick_movie(app, "Kantara")
    assert "Nothing listed for this date" in body(app)


def test_reruns_do_not_read_again(page_env):
    bms, rd = page_env
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    reads = rd.reads
    for _ in range(3):
        app = ok(app.run())
    assert rd.reads == reads


def test_the_explorer_never_touches_monitors(page_env, tmp_path):
    before = store.MONITORS_FILE.read_text() if store.MONITORS_FILE.exists() else None
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app = ok(app.button(key="sxview_group").click().run())
    app = ok(app.button(key="sx_refresh").click().run())
    after = store.MONITORS_FILE.read_text() if store.MONITORS_FILE.exists() else None
    assert before == after
    # nor the monitor wizard's picks: the Explorer's keys are all its own
    assert app.session_state["movie_id"] == "" and app.session_state["theatres"] == []
    assert app.session_state["step"] == 1


def test_the_theme_carries_the_explorer_responsive_rules():
    from ui.theme import CSS

    for rule in ('[class*="st-key-trgrid_sxdate"] [data-testid="stColumn"] { --tr-cols: 4; }',
                 ".tr-sx-row { grid-template-columns: 72px minmax(0,1fr) auto;",
                 '[class*="st-key-trpair_sxview"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }',
                 ':nth-child(3) label [data-testid="stMarkdownContainer"] p::after'):
        assert rule in CSS, rule
    # no new font: the Explorer's rules use the theme's own two families
    section = CSS.split("── Showtime Explorer")[1].split("── mobile")[0]
    assert "font-family" not in section
