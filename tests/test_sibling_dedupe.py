"""One BookMyShow read per sibling event, however many labels it carries.

The audit (24 Sep 2026) found ``variant_codes == ('ET00516224', 'ET00516224')``.
A monitor saved before canonicalisation stores the Infinity Vision sibling as
``("ET00516224", "Ms - Infinity Vision")``; the catalogue now has it as
``("ET00516224", "Infinity Vision 2D")``. ``checker.with_current_variants``
unions the *pairs*, so both survive — and ``fetch`` read that one event twice
per date. Deduplication is by event code only: labels, formats and the
2D/3D split all stay as they were.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from monitor import catalogue, checker
from monitor.models import Availability, MovieRef
from monitor.state import load_state, upsert_monitor
from tests.conftest import build_payload
from tests.test_infinity_vision_live_matching import (
    AMB_3D,
    AMB_IV_2D,
    AMB_IV_3D,
    AMB_IV_SHOWS,
    BASE_2D,
    HDR,
    IV_2D,
    IV_3D,
    PLAIN_3D,
    SHOW_DATE,
    amb,
    film,  # noqa: F401 - fixture
    production_monitor,
)

#: How a monitor saved before canonicalisation spelled the Infinity Vision 2D event.
STALE_IV_2D = (IV_2D, "Ms - Infinity Vision")
#: The order ``fetch`` reads a stale-label movie in: the base event, then
#: each sibling once, first-seen — the stale pair comes first.
STALE_SWEEP = [BASE_2D, IV_2D, PLAIN_3D, IV_3D, HDR]


def with_stale_label(movie: MovieRef) -> MovieRef:
    """The stored label first, the canonical one after it — the order
    ``with_current_variants`` produces (monitor's pairs, then the catalogue's)."""
    return replace(movie, variants=(STALE_IV_2D, *movie.variants))


def answers(order: list[str], dates: int = 1, **shows_by_event) -> list[dict]:
    """One payload per expected read, in ``order`` (event codes), each date
    in turn. Exactly as many as a sweep with no duplicate reads needs: one
    more read would run the fake session dry and fail the fetch."""
    return [build_payload(list(shows_by_event.get(code, ())), title="Avengers Endgame: Encore")
            for code in order for _ in range(dates)]


def event_calls(provider) -> list[str]:
    return [c["params"]["eventCode"] for c in provider.session.calls]


# ── 1 · a duplicated code is one read ────────────────────────────────────
def test_a_code_stored_under_two_labels_is_one_sibling(film):
    movie = with_stale_label(film)
    assert [code for code, _ in movie.variants].count(IV_2D) == 2
    assert movie.variant_codes.count(IV_2D) == 1


def test_a_duplicated_sibling_is_fetched_once(provider_factory, film):
    provider = provider_factory(answers(STALE_SWEEP, **{PLAIN_3D: AMB_3D, IV_2D: AMB_IV_SHOWS}))
    snap = provider.fetch(with_stale_label(film), [SHOW_DATE])

    assert event_calls(provider) == STALE_SWEEP
    # No show is in the snapshot twice, and the Infinity Vision shows keep
    # the canonical event format.
    sessions = [s.session_id for s in snap.showtimes]
    assert len(sessions) == len(set(sessions))
    iv = [s for s in snap.showtimes if s.session_id in {"119140", "118437", "118435"}]
    assert len(iv) == 3 and {s.event_format for s in iv} == {"Infinity Vision 2D"}


def test_the_duplicated_sibling_is_fetched_once_per_date(provider_factory, film):
    dates = [SHOW_DATE, "20260926"]
    provider = provider_factory(answers(STALE_SWEEP, dates=len(dates)))
    provider.fetch(with_stale_label(film), dates)

    calls = [(c["params"]["eventCode"], c["params"]["dateCode"]) for c in provider.session.calls]
    assert calls == [(code, d) for code in STALE_SWEEP for d in dates]


def test_with_current_variants_output_reads_each_event_once(monkeypatch, provider_factory, film):
    """The real path: a monitor with the stale label, a catalogue with the
    canonical one — the merge keeps both pairs and ``fetch`` still reads
    each event once."""
    stored = replace(film, variants=(STALE_IV_2D,))
    entry = catalogue.entry_from_movie(film)
    monkeypatch.setattr(catalogue, "find_entry", lambda movie_id: entry if movie_id == film.id else None)

    merged = checker.with_current_variants(stored)
    assert STALE_IV_2D in merged.variants and (IV_2D, "Infinity Vision 2D") in merged.variants
    assert merged.variant_codes == (IV_2D, PLAIN_3D, IV_3D, HDR)

    provider = provider_factory(answers(STALE_SWEEP))
    provider.fetch(merged, [SHOW_DATE])
    assert event_calls(provider) == STALE_SWEEP


# ── 2 + 3 · distinct codes stay distinct, whatever their labels ──────────
def test_distinct_codes_stay_distinct_and_in_order(film):
    assert film.variant_codes == (PLAIN_3D, IV_3D, IV_2D, HDR)
    assert with_stale_label(film).variant_codes == (IV_2D, PLAIN_3D, IV_3D, HDR)


def test_two_events_with_the_same_label_are_not_merged():
    movie = MovieRef("bookmyshow", "ET00000001", "Film", "HYD", "hyderabad",
                     variants=(("ET00000002", "3D"), ("ET00000003", "3D"), ("ET00000004", "4DX 3D")))
    assert movie.variant_codes == ("ET00000002", "ET00000003", "ET00000004")


def test_the_primary_event_is_still_never_its_own_sibling():
    movie = MovieRef("bookmyshow", "ET00000001", "Film", "HYD", "hyderabad",
                     variants=(("ET00000001", ""), ("ET00000002", "3D"), ("ET00000001", "2D")))
    assert movie.variant_codes == ("ET00000002",)
    assert MovieRef("bookmyshow", "ET00000001", "Film", "HYD", "hyderabad").variant_codes == ()


# ── 4 · Infinity Vision 2D and 3D remain two events ──────────────────────
def test_infinity_vision_2d_and_3d_stay_separate_events(provider_factory, film):
    iv_3d_show = amb("06:00 PM", "1800", "119900")
    provider = provider_factory(answers(STALE_SWEEP, **{IV_3D: [iv_3d_show], IV_2D: AMB_IV_SHOWS}))
    snap = provider.fetch(with_stale_label(film), [SHOW_DATE])

    assert event_calls(provider) == STALE_SWEEP
    by_session = {s.session_id: s for s in snap.showtimes}
    assert by_session["119900"].event_format == "Infinity Vision 3D"
    assert by_session["119140"].event_format == "Infinity Vision 2D"
    assert AMB_IV_3D.matches_show(by_session["119900"]) and not AMB_IV_2D.matches_show(by_session["119900"])
    assert AMB_IV_2D.matches_show(by_session["119140"]) and not AMB_IV_3D.matches_show(by_session["119140"])


# ── 5 + 6 · the AMB production monitor behaves exactly as before ─────────
def test_a_monitor_with_the_stale_label_reads_once_and_emails_once(provider_factory, monkeypatch, at, film):
    monitor = production_monitor(at, with_stale_label(film))
    upsert_monitor(monitor, mirror=False)
    sent: list = []

    def tick(when):
        provider = provider_factory(answers(STALE_SWEEP, **{PLAIN_3D: AMB_3D, IV_2D: AMB_IV_SHOWS}))
        monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
        checker.run_once(at=when, force=True, mirror=False, notifier=lambda m, c: sent.append(c))
        return provider

    assert event_calls(tick(at)) == STALE_SWEEP
    targets = load_state()[monitor.id].targets
    assert targets[AMB_IV_2D.key].availability is Availability.AVAILABLE
    assert targets[AMB_IV_3D.key].availability is Availability.SHOW_NOT_AVAILABLE
    [change] = sent
    assert (change.kind.value, change.fmt) == ("TICKETS_LIVE", "Infinity Vision 2D")
    assert change.time_labels == ["09:05 AM", "03:45 PM", "10:40 PM"]

    # The same listing again: one read per event, and no second email.
    assert event_calls(tick(at + timedelta(minutes=10))) == STALE_SWEEP
    assert len(sent) == 1
