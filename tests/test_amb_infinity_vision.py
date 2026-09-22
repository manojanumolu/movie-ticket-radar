"""AMB Cinemas · Infinity Vision · Avengers Endgame: Encore · 25 Sep 2026.

The requirement (22 Sep 2026). BookMyShow lists AMB Cinemas for Avengers
Endgame: Encore (English) as "MS - Infinity Vision" — its own bookable
sibling event, ``ET00516224`` — and every one of those showtimes is SOLD
OUT. AMB's ordinary screen for the same film, "Barco Flagship Laser Dolby
Atmos", is on sale in the base event at the same time.

What has to be true:

* a monitor on **AMB + Infinity Vision** stays ACTIVE while the format is
  listed and sold out, and says nothing;
* it emails once, and only once, when an Infinity Vision show becomes
  bookable — or when a *new* Infinity Vision show is released after that;
* AMB's Barco screen going live, or growing showtimes, never reaches it;
* an existing AMB + Barco monitor keeps behaving exactly as it did, and
  neither monitor is touched by the other.

Nothing here is a new mechanism. The composite identity is the existing
``TheatreTarget.key`` ("AMBH::Infinity Vision 2D"), the label comes from the
existing ``clean_format`` canonicalisation of BookMyShow's three spellings,
and the emails come from the existing ``detect_changes`` ladder.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from config.theatre_capabilities import premium_formats
from monitor import catalogue, checker
from monitor.models import Availability, Monitor, MonitorStatus, MovieRef, TheatreTarget
from monitor.state import load_monitors, load_state, upsert_monitor
from platforms.bookmyshow import clean_format
from tests.conftest import APP_SCRIPT, build_payload
from ui import catalogue_view as cv
from ui import flow

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

# ── the film, as BookMyShow lists it ────────────────────────────────────
ENGLISH_2D = "ET00514163"        # the row the user picks
INFINITY = "ET00516224"          # the "MS - Infinity Vision" sibling event
SHOW_DATE = "20260925"           # 25 Sep 2026

# ── the two AMB targets, told apart by nothing but the format ───────────
AMB_IV = TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", "Infinity Vision 2D")
AMB_BARCO = TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", "Barco Flagship Laser Dolby Atmos")


def movie(*, variants=((INFINITY, "Ms - Infinity Vision"),)) -> MovieRef:
    return MovieRef(
        platform="bookmyshow", event_code=ENGLISH_2D, title="Avengers Endgame: Encore",
        region_code="HYD", region_slug="hyderabad", language="English",
        source_url=f"https://in.bookmyshow.com/movies/hyderabad/avengers-endgame-encore/buytickets/{ENGLISH_2D}",
        variants=variants,
    )


def amb(fmt: str, status: str | None, time: str = "07:30 PM", code: str = "1930") -> dict:
    """One AMB show. ``fmt=""`` is how the Infinity Vision sibling's own
    cards look — no screen attribute, the event's own dimension names them —
    which is what the provider's ``default_format`` is for."""
    return {"venue_code": "AMBH", "venue_name": "AMB Cinemas", "area": "Gachibowli",
            "time": time, "time_code": code, "fmt": fmt, "status": status, "date": SHOW_DATE}


# Barco, in the base event, bookable — the noise this monitor must ignore.
BARCO_LIVE = amb("Barco Flagship Laser Dolby Atmos", "3", "06:45 PM", "1845")
BARCO_LATE = amb("Barco Flagship Laser Dolby Atmos", "3", "11:45 PM", "2345")
# Infinity Vision, in its own event: sold out, bookable, and a second show.
IV_SOLD_OUT = amb("", "0")
IV_LIVE = amb("", "3")
IV_SECOND = amb("", "3", "10:45 PM", "2245")


def make_monitor(at, *targets: TheatreTarget) -> Monitor:
    return Monitor(
        movie=movie(),
        targets=list(targets),
        interval_minutes=10,
        monitor_until=at + timedelta(days=3),
        notify_email="watcher@example.com",
        date_codes=[SHOW_DATE],
        owner_uid="uid-test-1",
    )


class Wire:
    """One tick of BookMyShow: the base event's shows, then the Infinity
    Vision sibling's. ``run_once`` sweeps them in that order."""

    def __init__(self, provider_factory, monkeypatch):
        self._factory = provider_factory
        self._mp = monkeypatch

    def tick(self, base: list[dict], infinity: list[dict]):
        self.provider = self._factory([build_payload(base), build_payload(infinity)])
        self._mp.setattr(checker, "get_provider", lambda slug: self.provider)

    @property
    def swept(self) -> list[str]:
        return [c["params"]["eventCode"] for c in self.provider.session.calls]


def check(at, sent, *, force=False):
    return checker.run_once(at=at, force=force, mirror=False, notifier=lambda m, c: sent.append(c))


# ──────────────────────────────────────────────────────────────────────────
# 1 + 2 · What the target matches, and what it must never match
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["MS - Infinity Vision", "Ms - Infinity Vsn", "MS-Infinity Vision",
                                 "ms - infinity vision"])
def test_the_amb_target_matches_bookmyshows_spellings_of_infinity_vision(raw):
    """The target is the canonical label; every spelling BookMyShow uses for
    the 2D screen canonicalises to it (``clean_format``) and matches."""
    assert clean_format(raw) == "Infinity Vision 2D"
    assert AMB_IV.matches_format(clean_format(raw))


@pytest.mark.parametrize("other", [
    "Barco Flagship Laser Dolby Atmos", "HDR By Barco", "Barco Laser 4K Atmos",
    "4K", "4K Dolby Atmos", "Dolby Atmos", "Dolby Cinema 2D", "2D", "MB LUXE", "VIP",
    "Infinity Vision 3D",                     # a different screen dimension, not this target
    "Infinity Vision Dolby Atmos",            # a theatre's own screen attribute, never claimed
])
def test_the_amb_target_matches_no_other_amb_format(other):
    assert not AMB_IV.matches_format(other)
    assert not AMB_IV.matches_format(clean_format(other))


def test_the_two_amb_targets_are_separate_identities_and_amb_gains_no_capability():
    """``AMBH::Infinity Vision 2D`` and ``AMBH::Barco Flagship Laser Dolby
    Atmos`` are different keys at the same venue — independent by
    construction. Infinity Vision is a BookMyShow *listing*, so it is not,
    and must not become, a verified capability of AMB."""
    assert AMB_IV.key == "AMBH::Infinity Vision 2D"
    assert AMB_BARCO.key == "AMBH::Barco Flagship Laser Dolby Atmos"
    assert AMB_IV.key != AMB_BARCO.key and AMB_IV.venue_code == AMB_BARCO.venue_code
    assert premium_formats("AMBH") == ("HDR By Barco", "MB LUXE", "VIP")
    assert not any("infinity" in f.lower() for f in premium_formats("AMBH"))
    assert not AMB_BARCO.matches_format("Infinity Vision 2D")


# ──────────────────────────────────────────────────────────────────────────
# 3 · The baseline: listed, sold out, still ACTIVE, and silent
# ──────────────────────────────────────────────────────────────────────────
def test_sold_out_infinity_vision_is_the_baseline_and_sends_nothing(provider_factory, monkeypatch, at):
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    # AMB is listed in Infinity Vision and every show is sold out, while the
    # theatre's Barco screen is on sale in the base event.
    wire.tick([BARCO_LIVE], [IV_SOLD_OUT])
    report = check(at, sent, force=True)

    assert report.checked == [monitor.id]
    assert wire.swept == [ENGLISH_2D, INFINITY]
    target = load_state()[monitor.id].targets[AMB_IV.key]
    assert target.availability is Availability.SOLD_OUT
    assert target.notified_availability is not Availability.AVAILABLE
    assert sent == []
    # Still running — a sold-out answer is an answer, not an ending.
    stored = load_monitors()[0]
    assert stored.status is MonitorStatus.ACTIVE and stored.is_running(at)


def test_a_barco_release_at_the_same_theatre_never_reaches_the_infinity_vision_monitor(
        provider_factory, monkeypatch, at):
    """Barco is bookable from the first tick and grows a showtime in the
    second. The Infinity Vision target stays SOLD_OUT and stays quiet."""
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    wire.tick([BARCO_LIVE], [IV_SOLD_OUT])
    check(at, sent, force=True)
    wire.tick([BARCO_LIVE, BARCO_LATE], [IV_SOLD_OUT])
    check(at + timedelta(minutes=10), sent)

    assert load_state()[monitor.id].targets[AMB_IV.key].availability is Availability.SOLD_OUT
    assert sent == []


def test_an_infinity_vision_listing_that_never_opens_is_not_a_theatre_not_available(
        provider_factory, monkeypatch, at):
    """Listed but not on sale is NOT_BOOKABLE — a distinct, silent state —
    not "the theatre isn't listed"."""
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)
    wire.tick([BARCO_LIVE], [amb("", None)])
    check(at, sent, force=True)
    assert load_state()[monitor.id].targets[AMB_IV.key].availability is Availability.NOT_BOOKABLE
    assert sent == []


# ──────────────────────────────────────────────────────────────────────────
# 4 · Sold out → available: exactly one email, and no second one
# ──────────────────────────────────────────────────────────────────────────
def test_infinity_vision_coming_back_from_sold_out_sends_exactly_one_email(
        provider_factory, monkeypatch, at):
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    wire.tick([BARCO_LIVE], [IV_SOLD_OUT])
    check(at, sent, force=True)
    assert sent == []

    # A seat is released in the Infinity Vision show.
    wire.tick([BARCO_LIVE], [IV_LIVE])
    check(at + timedelta(minutes=10), sent)

    assert [(c.kind.value, c.venue_name, c.fmt) for c in sent] == [
        ("TICKETS_LIVE", "AMB Cinemas", "Infinity Vision 2D")]
    assert sent[0].previous is Availability.SOLD_OUT
    assert sent[0].current is Availability.AVAILABLE
    assert sent[0].date_codes == [SHOW_DATE] and sent[0].time_labels == ["07:30 PM"]

    # Three more identical checks: still one email.
    for minute in (20, 30, 40):
        wire.tick([BARCO_LIVE], [IV_LIVE])
        check(at + timedelta(minutes=minute), sent)
    assert len(sent) == 1

    target = load_state()[monitor.id].targets[AMB_IV.key]
    assert target.availability is Availability.AVAILABLE
    assert target.notified_availability is Availability.AVAILABLE


def test_sold_out_again_then_available_again_re_arms_and_emails_once_more(
        provider_factory, monkeypatch, at):
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    for minute, infinity in ((0, [IV_SOLD_OUT]), (10, [IV_LIVE]), (20, [IV_SOLD_OUT]), (30, [IV_LIVE])):
        wire.tick([BARCO_LIVE], infinity)
        check(at + timedelta(minutes=minute), sent, force=minute == 0)

    assert [c.kind.value for c in sent] == ["TICKETS_LIVE", "TICKETS_LIVE"]
    assert [c.previous for c in sent] == [Availability.SOLD_OUT, Availability.SOLD_OUT]
    assert all(c.fmt == "Infinity Vision 2D" for c in sent)


# ──────────────────────────────────────────────────────────────────────────
# 5 · A newly released Infinity Vision showtime — and only that
# ──────────────────────────────────────────────────────────────────────────
def test_a_new_infinity_vision_showtime_is_announced_and_a_new_barco_one_is_not(
        provider_factory, monkeypatch, at):
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    wire.tick([BARCO_LIVE], [IV_LIVE])
    check(at, sent, force=True)
    assert [c.kind.value for c in sent] == ["TICKETS_LIVE"]

    # BookMyShow adds a *Barco* show. Nothing: it is not this target.
    wire.tick([BARCO_LIVE, BARCO_LATE], [IV_LIVE])
    check(at + timedelta(minutes=10), sent)
    assert len(sent) == 1

    # BookMyShow adds a second *Infinity Vision* show on the 25th.
    wire.tick([BARCO_LIVE, BARCO_LATE], [IV_LIVE, IV_SECOND])
    check(at + timedelta(minutes=20), sent)

    assert [c.kind.value for c in sent] == ["TICKETS_LIVE", "NEW_SHOWTIME"]
    assert sent[1].fmt == "Infinity Vision 2D" and sent[1].venue_name == "AMB Cinemas"
    assert sent[1].new_time_labels == ["10:45 PM"]
    assert sent[1].date_codes == [SHOW_DATE]


def test_an_infinity_vision_showtime_on_another_date_is_filtered_out(
        provider_factory, monkeypatch, at):
    """The monitor watches 25 Sep. A 26 Sep Infinity Vision show is not it."""
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)
    wire.tick([BARCO_LIVE], [{**IV_LIVE, "date": "20260926"}])
    check(at, sent, force=True)
    assert load_state()[monitor.id].targets[AMB_IV.key].availability is Availability.SHOW_NOT_AVAILABLE
    assert sent == []


# ──────────────────────────────────────────────────────────────────────────
# 6 + 8 · The existing AMB Barco monitor, unchanged and untouched
# ──────────────────────────────────────────────────────────────────────────
def test_an_existing_amb_barco_monitor_keeps_working_and_is_left_untouched(
        provider_factory, monkeypatch, at):
    barco_monitor = make_monitor(at, AMB_BARCO)
    upsert_monitor(barco_monitor, mirror=False)
    before = barco_monitor.to_dict()

    iv_monitor = make_monitor(at, AMB_IV)
    upsert_monitor(iv_monitor, mirror=False)

    # Adding the Infinity Vision monitor changed nothing about the other one.
    stored = {m.id: m for m in load_monitors()}
    assert set(stored) == {barco_monitor.id, iv_monitor.id}
    assert stored[barco_monitor.id].to_dict() == before

    sent: list = []
    wire = Wire(provider_factory, monkeypatch)
    # Barco live, Infinity Vision sold out: the Barco monitor fires, the other doesn't.
    wire.tick([BARCO_LIVE], [IV_SOLD_OUT])
    check(at, sent, force=True)
    assert [(c.monitor_id, c.fmt) for c in sent] == [
        (barco_monitor.id, "Barco Flagship Laser Dolby Atmos")]

    # Infinity Vision opens: the other monitor fires, once, and the Barco
    # monitor — already announced — stays quiet.
    wire.tick([BARCO_LIVE], [IV_LIVE])
    check(at + timedelta(minutes=10), sent)
    assert [(c.monitor_id, c.fmt) for c in sent] == [
        (barco_monitor.id, "Barco Flagship Laser Dolby Atmos"),
        (iv_monitor.id, "Infinity Vision 2D")]

    state = load_state()
    assert state[barco_monitor.id].targets[AMB_BARCO.key].availability is Availability.AVAILABLE
    assert state[iv_monitor.id].targets[AMB_IV.key].availability is Availability.AVAILABLE
    assert AMB_IV.key not in state[barco_monitor.id].targets
    assert AMB_BARCO.key not in state[iv_monitor.id].targets


def test_one_monitor_can_hold_both_amb_targets_without_either_muting_the_other(
        provider_factory, monkeypatch, at):
    """The wizard writes one target per theatre × format. Both AMB targets in
    one monitor stay independent — the same guarantee, one row down."""
    monitor = make_monitor(at, AMB_BARCO, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    wire = Wire(provider_factory, monkeypatch)

    wire.tick([BARCO_LIVE], [IV_SOLD_OUT])
    check(at, sent, force=True)
    assert [c.fmt for c in sent] == ["Barco Flagship Laser Dolby Atmos"]

    wire.tick([BARCO_LIVE], [IV_LIVE])
    check(at + timedelta(minutes=10), sent)
    assert [c.fmt for c in sent] == ["Barco Flagship Laser Dolby Atmos", "Infinity Vision 2D"]

    targets = load_state()[monitor.id].targets
    assert set(targets) == {AMB_BARCO.key, AMB_IV.key}


# ──────────────────────────────────────────────────────────────────────────
# 7 · No duplicate
# ──────────────────────────────────────────────────────────────────────────
def test_saving_the_same_monitor_again_updates_it_rather_than_duplicating_it(at):
    monitor = make_monitor(at, AMB_IV)
    upsert_monitor(monitor, mirror=False)
    upsert_monitor(monitor, mirror=False)
    stored = load_monitors()
    assert len(stored) == 1 and stored[0].id == monitor.id
    assert [t.key for t in stored[0].targets] == [AMB_IV.key]


def test_a_duplicate_target_in_one_monitor_collapses_to_one_key(at):
    """Two identical AMB + Infinity Vision targets are one watched thing —
    the key is the identity, and state is stored against it."""
    monitor = make_monitor(at, AMB_IV, TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", "Infinity Vision 2D"))
    assert len({t.key for t in monitor.targets}) == 1
    assert monitor.target(AMB_IV.key) is not None


# ──────────────────────────────────────────────────────────────────────────
# The wizard: Infinity Vision is offered at AMB because BookMyShow lists it
# ──────────────────────────────────────────────────────────────────────────
def test_step_4_offers_infinity_vision_at_amb_only_when_bookmyshow_lists_it_there(provider_factory):
    """No new UI concept: the Formats step's options are this movie's
    listing at this theatre on the chosen date, plus AMB's verified premium
    screens. "Infinity Vision 2D" is offered because the sibling event lists
    it — and ``unknown_formats`` therefore accepts it."""
    film = movie()
    provider = provider_factory([
        build_payload([BARCO_LIVE]),                 # the base event, 25 Sep
        build_payload([IV_SOLD_OUT]),                # the Infinity Vision sibling, 25 Sep
    ])
    catalogue.store_snapshot(provider.fetch(film, [SHOW_DATE]), mirror=False)

    [venue] = cv.selected_venues(film.id, "hyderabad", ["AMBH"], [SHOW_DATE])
    assert venue.formats == ("Barco Flagship Laser Dolby Atmos", "Infinity Vision 2D")
    assert flow.unknown_formats(venue, ["Infinity Vision 2D"], premium_formats("AMBH")) == []

    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state["page"] = "Home"
    for key, value in {"step": 4, "furthest": 4, "location": "hyderabad", "movie_id": film.id,
                       "theatres": ["AMBH"], "show_dates": [SHOW_DATE]}.items():
        app.session_state[key] = value
    app.run()

    boxes = {c.key: c for c in app.checkbox}
    assert "fmt_AMBH_Infinity Vision 2D" in boxes
    assert boxes["fmt_AMBH_Infinity Vision 2D"].proto.help.startswith("Currently listed")
    assert "fmt_AMBH_Barco Flagship Laser Dolby Atmos" in boxes
    # The Marvel note names AMB, from the listing alone.
    body = " ".join(m.value for m in app.markdown)
    assert "Infinity Vision" in body and "AMB Cinemas (2D)" in body


def test_step_4_refuses_infinity_vision_at_amb_when_bookmyshow_does_not_list_it(provider_factory):
    """AMB has no verified Infinity Vision screen, so with no listing there
    is no basis for the target — and the wizard refuses it rather than
    inferring one from "Marvel" or "AMB"."""
    film = movie(variants=())
    provider = provider_factory([build_payload([BARCO_LIVE])])
    catalogue.store_snapshot(provider.fetch(film, [SHOW_DATE]), mirror=False)

    [venue] = cv.selected_venues(film.id, "hyderabad", ["AMBH"], [SHOW_DATE])
    assert venue.formats == ("Barco Flagship Laser Dolby Atmos",)
    assert flow.unknown_formats(venue, ["Infinity Vision 2D"], premium_formats("AMBH")) == ["Infinity Vision 2D"]
