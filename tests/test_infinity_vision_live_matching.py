"""The live worker must see an Infinity Vision show that BookMyShow lists.

The incident (24 Sep 2026). A production monitor watched AMB Cinemas for
Avengers Endgame: Encore (English), 25 Sep, in "Infinity Vision 3D" and
"Infinity Vision 2D". BookMyShow was selling a 09:05 AM "MS - Infinity
Vision" show at AMB; every check logged::

    [filter] AMB Cinemas: 5 show(s) but none in 'Infinity Vision 2D'
             — formats listed: ['Barco Flagship Laser Dolby Atmos']

What BookMyShow really sends (bms-diagnose, 24 Sep 2026):

* the film's child events include ``ET00516224`` with ``EventDimension``
  ``"MS - Infinity Vision"`` and ``ET00516728`` with ``"MS-Infinity Vsn 3d"``
  — Infinity Vision is named by the *event*, and the event is what tells
  2D from 3D;
* every show inside ``ET00516224`` at AMBH (09:05 AM session 119140, 03:45 PM
  118437, 10:40 PM 118435) carries ``screenAttr`` ``"BARCO FLAGSHIP LASER DOLBY
  ATMOS"`` — the *screen's* attribute, the same string as AMB's ordinary
  shows. ALLU's shows in the 3D event likewise say "BARCO LASER 4K ATMOS".

The parser used the screen attribute when present and the event's format
only when it was absent, so the event's format was discarded for exactly
the shows that needed it. These tests use that shape, not a show that says
"Infinity Vision" on itself.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from monitor import checker
from monitor.models import Availability, Monitor, TheatreTarget, is_infinity_vision
from monitor.state import load_state, upsert_monitor
from platforms.bookmyshow import clean_format
from tests.conftest import build_payload, build_quickbook

SHOW_DATE = "20260925"

# ── the film's child events, exactly as BookMyShow's quickbook lists them ──
BASE_2D = "ET00514163"
PLAIN_3D = "ET00516731"
IV_3D = "ET00516728"
IV_2D = "ET00516224"
HDR = "ET00518791"
QUICKBOOK = build_quickbook([{
    "title": "Avengers Endgame: Encore", "url": "avengers-endgame-encore", "children": [
        {"code": PLAIN_3D, "language": "English", "dimension": "3D"},
        {"code": BASE_2D, "language": "English", "dimension": "2D"},
        {"code": IV_3D, "language": "English", "dimension": "MS-Infinity Vsn 3d"},
        {"code": IV_2D, "language": "English", "dimension": "MS - Infinity Vision"},
        {"code": HDR, "language": "English", "dimension": "HDR By Barco"},
    ],
}])
#: The order ``fetch`` reads them in: the base event, then each sibling.
SWEEP = (BASE_2D, PLAIN_3D, IV_3D, IV_2D, HDR)

# ── the targets ──────────────────────────────────────────────────────────
AMB_IV_3D = TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", "Infinity Vision 3D")
AMB_IV_2D = TheatreTarget("AMBH", "AMB Cinemas", "Gachibowli", "Infinity Vision 2D")
ALLU_IV_3D = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Infinity Vision 3D")
ALLU_IV_2D = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", "Infinity Vision 2D")


def show(venue: str, name: str, area: str, attr: str, time: str, code: str, session: str,
         status: str | None) -> dict:
    return {"venue_code": venue, "venue_name": name, "area": area, "fmt": attr, "time": time,
            "time_code": code, "session_id": session, "status": status, "date": SHOW_DATE}


def amb(time: str, code: str, session: str, status: str | None = "3") -> dict:
    return show("AMBH", "AMB Cinemas", "Gachibowli", "BARCO FLAGSHIP LASER DOLBY ATMOS", time, code, session, status)


# AMB's shows in the plain 3D event — on sale all along, and not Infinity Vision.
AMB_3D = [amb("12:30 PM", "1230", "118431"), amb("07:10 PM", "1910", "118433")]
# AMB's shows in the "MS - Infinity Vision" event, as captured.
AMB_IV_0905 = amb("09:05 AM", "0905", "119140")
AMB_IV_SHOWS = [AMB_IV_0905, amb("03:45 PM", "1545", "118437"), amb("10:40 PM", "2240", "118435")]
# ALLU's show in the "MS-Infinity Vsn 3d" event, on its Barco Laser screen.
ALLU_IV3D = show("ALUC", "ALLU Cinemas", "Kokapet", "BARCO LASER 4K ATMOS", "06:00 PM", "1800", "77001", "3")


def responses(*, base=(), plain_3d=(), iv_3d=(), iv_2d=(), hdr=()) -> list[dict]:
    return [build_payload(list(x), title="Avengers Endgame: Encore") for x in (base, plain_3d, iv_3d, iv_2d, hdr)]


@pytest.fixture
def film(provider_factory):
    [english] = [m for m in provider_factory([QUICKBOOK]).list_movies("hyderabad") if m.language == "English"]
    return english


def snapshot(provider_factory, film, **events):
    provider = provider_factory(responses(**events))
    snap = provider.fetch(film, [SHOW_DATE])
    assert [c["params"]["eventCode"] for c in provider.session.calls] == list(SWEEP)
    return snap


# ──────────────────────────────────────────────────────────────────────────
# A · Parsing: the show keeps its screen *and* the event it is sold under
# ──────────────────────────────────────────────────────────────────────────
def test_the_listing_names_both_infinity_vision_events_by_their_own_dimension(film):
    assert dict(film.variants) == {PLAIN_3D: "3D", IV_3D: "Infinity Vision 3D", IV_2D: "Infinity Vision 2D",
                                   HDR: "HDR By Barco"}


def test_a_live_ms_infinity_vision_show_is_parsed_with_its_event_format(provider_factory, film):
    snap = snapshot(provider_factory, film, plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS)
    s = next(s for s in snap.showtimes if s.session_id == "119140")

    assert (s.venue_code, s.date_code, s.time_label) == ("AMBH", SHOW_DATE, "09:05 AM")
    assert s.availability is Availability.AVAILABLE
    # The raw screen attribute is kept as BookMyShow sent it …
    assert s.format_raw == "BARCO FLAGSHIP LASER DOLBY ATMOS"
    assert s.format_label == "Barco Flagship Laser Dolby Atmos"
    # … and the event's "MS - Infinity Vision" is no longer thrown away.
    assert s.event_format == "Infinity Vision 2D"
    assert s.format_labels == ("Barco Flagship Laser Dolby Atmos", "Infinity Vision 2D")
    # Its booking page is the Infinity Vision event's own.
    assert f"/buytickets/{IV_2D}/{SHOW_DATE}" in s.booking_url


def test_base_event_shows_carry_no_event_format(provider_factory, film):
    """Only a premium sibling contributes an event format; AMB's shows in the
    base 2D event are just their screen."""
    snap = snapshot(provider_factory, film, base=[amb("11:00 AM", "1100", "118001")])
    [s] = snap.showtimes
    assert s.event_format == "" and s.format_labels == ("Barco Flagship Laser Dolby Atmos",)


def test_the_catalogue_lists_infinity_vision_at_amb_from_the_same_parse(provider_factory, film):
    """The catalogue (Step 4, the Marvel note) reads the same labels, so AMB
    now shows Infinity Vision 2D — and 3D, which AMB is sold in from the
    plain 3D event — beside its screen."""
    from monitor.catalogue import listings_from_showtimes

    snap = snapshot(provider_factory, film, plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS)
    assert snap.venue("AMBH").formats == ("3D", "Barco Flagship Laser Dolby Atmos", "Infinity Vision 2D")
    assert listings_from_showtimes(snap.showtimes)[SHOW_DATE]["AMBH"] == [
        "Barco Flagship Laser Dolby Atmos", "3D", "Infinity Vision 2D"]
    assert [f for f in snap.venue("AMBH").formats if is_infinity_vision(f)] == ["Infinity Vision 2D"]


# ──────────────────────────────────────────────────────────────────────────
# B + G · Matching: Infinity Vision by the event, 2D and 3D kept apart
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("dimension, canonical", [
    ("MS - Infinity Vision", "Infinity Vision 2D"),     # ET00516224, verbatim
    ("MS-Infinity Vsn 3d", "Infinity Vision 3D"),       # ET00516728, verbatim
])
def test_bookmyshows_event_dimensions_canonicalise_to_one_label_each(dimension, canonical):
    assert clean_format(dimension) == canonical and is_infinity_vision(canonical)


def test_the_2d_event_matches_the_2d_target_only(provider_factory, film):
    snap = snapshot(provider_factory, film, plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS)
    premium = [s for s in snap.showtimes if s.event_format]
    matched_2d = [s for s in snap.showtimes if AMB_IV_2D.matches_show(s)]
    assert sorted(s.session_id for s in matched_2d) == ["118435", "118437", "119140"]
    # "MS - Infinity Vision" is not a 3D show, and AMB's plain 3D shows are
    # not Infinity Vision.
    assert not [s for s in snap.showtimes if AMB_IV_3D.matches_show(s)]
    assert len(premium) == 5            # 2 plain 3D + 3 Infinity Vision 2D, each told apart by its event
    assert {s.event_format for s in premium} == {"3D", "Infinity Vision 2D"}


def test_the_3d_event_matches_the_3d_target_only(provider_factory, film):
    """ALLU is listed in "MS-Infinity Vsn 3d" on its "BARCO LASER 4K ATMOS"
    screen — the same bug hid it from the ALLU Infinity Vision 3D monitor."""
    snap = snapshot(provider_factory, film, iv_3d=[ALLU_IV3D])
    [s] = snap.showtimes
    assert s.format_labels == ("Barco Laser 4K Atmos", "Infinity Vision 3D")
    assert ALLU_IV_3D.matches_show(s)
    assert not ALLU_IV_2D.matches_show(s)


def test_a_future_infinity_vision_release_anywhere_matches_the_same_way(provider_factory, film):
    """Nothing is AMB's or Avengers': PVR Lakeshore's PXL screen listed in
    the 2D Infinity Vision event matches an Infinity Vision 2D target there,
    and its PXL watch keeps matching its own screen."""
    lakeshore = show("ILKS", "PVR Lakeshore", "Y Junction", "PXL 4K LASER ATMOS", "01:00 PM", "1300", "5501", "3")
    snap = snapshot(provider_factory, film, iv_2d=[lakeshore])
    [s] = snap.showtimes
    assert TheatreTarget("ILKS", "PVR Lakeshore", "", "Infinity Vision 2D").matches_show(s)
    assert TheatreTarget("ILKS", "PVR Lakeshore", "", "PXL").matches_show(s)
    assert not TheatreTarget("ILKS", "PVR Lakeshore", "", "Infinity Vision 3D").matches_show(s)


# ──────────────────────────────────────────────────────────────────────────
# F · No false positives: other formats never become Infinity Vision
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("venue, name, attr, event", [
    ("AMBH", "AMB Cinemas", "BARCO FLAGSHIP LASER DOLBY ATMOS", "base"),     # AMB's ordinary screen
    ("AMBH", "AMB Cinemas", "BARCO FLAGSHIP LASER DOLBY ATMOS", "plain_3d"),
    ("PRHN", "Prasads Multiplex", "", "hdr"),                                # HDR By Barco event
    ("AMBH", "AMB Cinemas", "M B LUXE", "base"),                             # MB LUXE
    ("AMBH", "AMB Cinemas", "VIP SCREEN", "base"),                           # VIP
    ("ILKS", "PVR Lakeshore", "PXL 4K LASER ATMOS", "plain_3d"),             # PXL
    ("ALUC", "ALLU Cinemas", "DOLBY CINEMA", "base"),                        # Dolby Cinema
    ("AACN", "Aparna Cinemas", "INFINITY VISION DOLBY ATMOS", "base"),       # a screen's own name, not the event
    ("DVRR", "Devi 70MM", "4K DOLBY ATMOS", "base"),                         # generic technology
])
def test_no_other_format_triggers_an_infinity_vision_target(provider_factory, film, venue, name, attr, event):
    snap = snapshot(provider_factory, film, **{event: [show(venue, name, "", attr, "02:00 PM", "1400", "1", "3")]})
    [s] = snap.showtimes
    for fmt in ("Infinity Vision 2D", "Infinity Vision 3D"):
        assert not TheatreTarget(venue, name, "", fmt).matches_show(s), (attr, event, s.format_labels)
    assert not any(is_infinity_vision(f) for f in s.format_labels)


@pytest.mark.parametrize("fmt", ["HDR By Barco", "MB LUXE", "VIP", "PXL", "Dolby Cinema"])
def test_an_infinity_vision_show_triggers_no_other_premium_target(provider_factory, film, fmt):
    snap = snapshot(provider_factory, film, iv_2d=[AMB_IV_0905])
    [s] = snap.showtimes
    assert not TheatreTarget("AMBH", "AMB Cinemas", "", fmt).matches_show(s)


def test_existing_premium_targets_still_match_their_own_shows(provider_factory, film):
    snap = snapshot(provider_factory, film,
                    base=[show("AMBH", "AMB Cinemas", "", "M B LUXE", "01:00 PM", "1300", "1", "3"),
                          show("AMBH", "AMB Cinemas", "", "VIP SCREEN", "02:00 PM", "1400", "2", "3"),
                          show("ALUC", "ALLU Cinemas", "", "DOLBY CINEMA", "03:00 PM", "1500", "3", "3")],
                    plain_3d=[show("ILKS", "PVR Lakeshore", "", "PXL 4K LASER ATMOS", "04:00 PM", "1600", "4", "3")],
                    hdr=[show("PRHN", "Prasads Multiplex", "", "", "05:00 PM", "1700", "5", "3")])
    by_session = {s.session_id: s for s in snap.showtimes}
    assert TheatreTarget("AMBH", "AMB Cinemas", "", "MB LUXE").matches_show(by_session["1"])
    assert TheatreTarget("AMBH", "AMB Cinemas", "", "VIP").matches_show(by_session["2"])
    assert TheatreTarget("ALUC", "ALLU Cinemas", "", "Dolby Cinema").matches_show(by_session["3"])
    assert TheatreTarget("ILKS", "PVR Lakeshore", "", "PXL").matches_show(by_session["4"])
    assert TheatreTarget("PRHN", "Prasads Multiplex", "", "HDR By Barco").matches_show(by_session["5"])


# ──────────────────────────────────────────────────────────────────────────
# C + D + E · The production monitor, on the real worker tick
# ──────────────────────────────────────────────────────────────────────────
def production_monitor(at, film) -> Monitor:
    return Monitor(movie=film, targets=[AMB_IV_3D, AMB_IV_2D], interval_minutes=10,
                   monitor_until=at + timedelta(days=2), notify_email="watcher@example.com",
                   date_codes=[SHOW_DATE], owner_uid="uid-test-1")


def tick(provider_factory, monkeypatch, at, sent, **events):
    provider = provider_factory(responses(**events))
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    checker.run_once(at=at, force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    return provider


def test_the_amb_monitor_goes_live_on_the_ms_infinity_vision_show_and_emails_once(
        provider_factory, monkeypatch, at, film, capsys):
    monitor = production_monitor(at, film)
    upsert_monitor(monitor, mirror=False)
    sent: list = []

    # Before: AMB is listed (plain 3D, Barco screen) with no Infinity Vision show.
    tick(provider_factory, monkeypatch, at, sent, plain_3d=AMB_3D)
    targets = load_state()[monitor.id].targets
    assert targets[AMB_IV_2D.key].availability is Availability.SHOW_NOT_AVAILABLE
    assert targets[AMB_IV_3D.key].availability is Availability.SHOW_NOT_AVAILABLE
    assert sent == []

    # BookMyShow lists the "MS - Infinity Vision" shows, 09:05 AM among them.
    capsys.readouterr()
    provider = tick(provider_factory, monkeypatch, at + timedelta(minutes=5), sent,
                    plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS)
    assert [c["params"]["eventCode"] for c in provider.session.calls] == list(SWEEP)

    targets = load_state()[monitor.id].targets
    assert targets[AMB_IV_2D.key].availability is Availability.AVAILABLE
    # The 3D target is not told a 2D show is 3D.
    assert targets[AMB_IV_3D.key].availability is Availability.SHOW_NOT_AVAILABLE

    [change] = sent
    assert (change.kind.value, change.venue_name, change.fmt) == ("TICKETS_LIVE", "AMB Cinemas", "Infinity Vision 2D")
    assert change.previous is Availability.SHOW_NOT_AVAILABLE and change.current is Availability.AVAILABLE
    assert change.time_labels == ["09:05 AM", "03:45 PM", "10:40 PM"]
    assert change.date_codes == [SHOW_DATE]
    out = capsys.readouterr().out
    assert "none in 'Infinity Vision 2D'" not in out
    assert "formats listed: ['3D', 'Barco Flagship Laser Dolby Atmos', 'Infinity Vision 2D']" in out

    # The same listing again: nothing more.
    tick(provider_factory, monkeypatch, at + timedelta(minutes=10), sent, plain_3d=AMB_3D, iv_2d=AMB_IV_SHOWS)
    assert len(sent) == 1


def test_a_sold_out_ms_infinity_vision_listing_stays_silent_then_fires_when_seats_open(
        provider_factory, monkeypatch, at, film):
    monitor = production_monitor(at, film)
    upsert_monitor(monitor, mirror=False)
    sent: list = []

    sold_out = [amb("09:05 AM", "0905", "119140", "0")]
    tick(provider_factory, monkeypatch, at, sent, plain_3d=AMB_3D, iv_2d=sold_out)
    assert load_state()[monitor.id].targets[AMB_IV_2D.key].availability is Availability.SOLD_OUT
    assert sent == []

    tick(provider_factory, monkeypatch, at + timedelta(minutes=5), sent, plain_3d=AMB_3D, iv_2d=[AMB_IV_0905])
    assert [(c.kind.value, c.fmt, c.previous) for c in sent] == [
        ("TICKETS_LIVE", "Infinity Vision 2D", Availability.SOLD_OUT)]


def test_the_allu_infinity_vision_3d_monitor_is_found_through_the_same_path(
        provider_factory, monkeypatch, at, film):
    monitor = Monitor(movie=film, targets=[ALLU_IV_3D, ALLU_IV_2D], interval_minutes=10,
                      monitor_until=at + timedelta(days=2), notify_email="watcher@example.com",
                      date_codes=[SHOW_DATE], owner_uid="uid-test-1")
    upsert_monitor(monitor, mirror=False)
    sent: list = []
    allu_base = show("ALUC", "ALLU Cinemas", "Kokapet", "BARCO LASER 4K ATMOS", "11:00 AM", "1100", "77000", "3")

    tick(provider_factory, monkeypatch, at, sent, base=[allu_base], iv_3d=[ALLU_IV3D])
    targets = load_state()[monitor.id].targets
    assert targets[ALLU_IV_3D.key].availability is Availability.AVAILABLE
    assert targets[ALLU_IV_2D.key].availability is Availability.SHOW_NOT_AVAILABLE
    assert [(c.kind.value, c.fmt, c.time_labels) for c in sent] == [
        ("TICKETS_LIVE", "Infinity Vision 3D", ["06:00 PM"])]
