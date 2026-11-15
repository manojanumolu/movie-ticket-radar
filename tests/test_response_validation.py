"""An HTTP 200 is only an answer once it looks like one (audit REL-02).

BookMyShow's showtimes endpoint is undocumented and can change; a bot check
can also be served as JSON with a 200. Read leniently, either one parsed to
"no theatres, no shows", and every monitor on the film reported *this
theatre isn't listed for the movie yet* for as long as it lasted — silently,
because nothing had failed.

Pinned here: which responses are refused (as ``PlatformError`` — "we don't
know"), which empty ones are still honoured as genuinely empty, that a
refused response never becomes a *not listed* verdict or disturbs stored
state, that the diagnostic names shapes and keys but never values, and the
one canary for the case validation cannot see.
"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from monitor.checker import evaluate_target, fetch_listing, outcome_for, run_once
from monitor.models import ANY_FORMAT, Availability, Monitor, MovieRef, TheatreTarget
from monitor.state import get_monitor_state, upsert_monitor
from platforms.base import PlatformBlocked, PlatformError
from platforms.bookmyshow import validate_showtimes_payload
from tests.conftest import ALLU_LIVE, NOT_ON_SALE, FakeResponse, build_payload

EVENT = "ET00478890"
SIBLING = "ET00999001"


def theatre_list(payload: dict) -> dict:
    """The ``groupList`` widget of a built payload."""
    return next(w for w in payload["data"]["showtimeWidgets"] if w.get("type") == "groupList")


def reshaped(change) -> dict:
    payload = copy.deepcopy(build_payload(ALLU_LIVE))
    change(payload)
    return payload


def _rename_group(p):
    theatre_list(p)["data"][0]["type"] = "cinemaGroup"


def _rename_card(p):
    for card in theatre_list(p)["data"][0]["data"]:
        card["type"] = "cinema-card"


def _groups_not_a_list(p):
    theatre_list(p)["data"] = {"venueGroup": []}


def _cards_not_a_list(p):
    theatre_list(p)["data"][0]["data"] = "…"


def _showtimes_not_a_list(p):
    theatre_list(p)["data"][0]["data"][0]["showtimes"] = {"0": {}}


#: Every one of these arrived with HTTP 200. None of them is a listing.
UNRECOGNISED = {
    "empty object": {},
    "missing data": {"status": "ok"},
    "data is null": {"data": None},
    "data is a list": {"data": []},
    "data is a string": {"data": "Access denied"},
    "missing showtimeWidgets": {"data": {"topStickyWidgets": []}},
    "showtimeWidgets is null": {"data": {"showtimeWidgets": None}},
    "showtimeWidgets is a string": {"data": {"showtimeWidgets": "not-a-list"}},
    "showtimeWidgets is an object": {"data": {"showtimeWidgets": {"groupList": []}}},
    "error JSON served with 200": {"error": {"code": 1020, "message": "Access denied"}, "success": False},
    "error inside data": {"data": {"error": "blocked", "message": "Please verify you are human"}},
    "date strip is not a list": {"data": {"showtimeWidgets": [], "topStickyWidgets": "x"}},
    "widgets without any type": {"data": {"showtimeWidgets": [{"foo": 1}, "bar"]}},
    "theatre group renamed": reshaped(_rename_group),
    "theatre card renamed": reshaped(_rename_card),
    "theatre groups not a list": reshaped(_groups_not_a_list),
    "theatre cards not a list": reshaped(_cards_not_a_list),
    "a theatre's showtimes not a list": reshaped(_showtimes_not_a_list),
}

#: …and these are listings with nothing in them, which is a real answer.
EMPTY_BUT_GENUINE = {
    "no theatres in the theatre group": build_payload([]),
    "no widgets at all": {"data": {"showtimeWidgets": []}},
    "widgets but no theatre list": {"data": {"showtimeWidgets": [{"type": "adtech", "data": []},
                                                                  {"type": "info", "data": []}],
                                             "topStickyWidgets": []}},
    "an empty theatre list": {"data": {"showtimeWidgets": [{"type": "groupList", "data": []}]}},
}


def movie(variants=()) -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=EVENT, title="A Film", region_code="HYD",
                    region_slug="hyderabad", variants=tuple(variants),
                    source_url=f"https://in.bookmyshow.com/movies/hyderabad/a-film/buytickets/{EVENT}")


def watch(at, **kw) -> Monitor:
    return Monitor(movie=kw.pop("movie", movie()),
                   targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur", ANY_FORMAT)],
                   notify_email="w@example.com", owner_uid="uid-test-1",
                   monitor_until=at + timedelta(days=1), **kw)


# ──────────────────────────────────────────────────────────────────────────
# 1 · What is refused
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", UNRECOGNISED.values(), ids=UNRECOGNISED.keys())
def test_an_unrecognised_200_is_an_error_not_an_empty_listing(provider_factory, listing_url, body):
    provider = provider_factory([body])
    with pytest.raises(PlatformError) as exc:
        provider.fetch(movie())
    # "We don't know" — and not "we were refused", which is worded differently.
    assert not isinstance(exc.value, PlatformBlocked)
    assert "not a 'no shows' answer" in str(exc.value)
    # It is the response that is wrong, not the connection: asked once.
    assert len(provider.session.calls) == 1


@pytest.mark.parametrize("body", UNRECOGNISED.values(), ids=UNRECOGNISED.keys())
def test_resolve_refuses_it_too(provider_factory, listing_url, body):
    with pytest.raises(PlatformError):
        provider_factory([body]).resolve(listing_url)


@pytest.mark.parametrize("body", [[], "ok", 7, None], ids=["list", "string", "number", "null"])
def test_a_body_that_is_not_an_object_is_refused(provider_factory, body):
    with pytest.raises(PlatformError):
        provider_factory([FakeResponse(200, body) if body is not None else FakeResponse(200, None, "x")]
                         ).fetch(movie())


@pytest.mark.parametrize("body", UNRECOGNISED.values(), ids=UNRECOGNISED.keys())
def test_it_never_becomes_a_not_listed_verdict(provider_factory, monkeypatch, at, body):
    """The audit's reproduction C: each of these used to be recorded as
    THEATRE_NOT_AVAILABLE with ``is_answer`` True."""
    provider = provider_factory([body])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    monitor = watch(at)
    listing = fetch_listing(monitor.movie, monitor.date_codes)
    assert not listing.ok and not listing.blocked
    outcome = outcome_for(monitor, listing, at=at)
    assert outcome.ok is False and outcome.results == []


# ──────────────────────────────────────────────────────────────────────────
# 2 · What is still honoured
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", EMPTY_BUT_GENUINE.values(), ids=EMPTY_BUT_GENUINE.keys())
def test_a_genuinely_empty_listing_is_still_an_answer(provider_factory, at, body):
    snapshot = provider_factory([body]).fetch(movie())
    assert snapshot.complete and snapshot.venues == [] and snapshot.showtimes == []
    monitor = watch(at)
    result = evaluate_target(monitor, monitor.targets[0], snapshot)
    assert result.availability is Availability.THEATRE_NOT_AVAILABLE
    assert result.availability.is_answer


def test_a_populated_listing_reads_exactly_as_before(provider_factory, at):
    snapshot = provider_factory([build_payload(ALLU_LIVE)]).fetch(movie())
    assert snapshot.complete
    assert [(s.venue_code, s.time_label, s.availability) for s in snapshot.showtimes] == [
        ("ALLU", "07:30 PM", Availability.AVAILABLE), ("AMB", "08:00 PM", Availability.NOT_BOOKABLE)]
    assert snapshot.bookable_dates == ["20260925"] and snapshot.closed_dates == ["20260926"]
    monitor = watch(at)
    assert evaluate_target(monitor, monitor.targets[0], snapshot).availability is Availability.AVAILABLE


def test_validation_returns_the_data_it_checked():
    payload = build_payload(NOT_ON_SALE)
    assert validate_showtimes_payload(payload, EVENT) is payload["data"]


def test_unknown_widgets_beside_the_theatre_list_are_ignored_not_refused(provider_factory):
    """BookMyShow adds presentational widgets freely (live payloads lead
    with an ad slot); only the path the parser walks is held to a shape."""
    payload = build_payload(ALLU_LIVE)
    payload["data"]["showtimeWidgets"].append({"type": "brand-new-carousel", "data": {"anything": 1}})
    payload["data"]["somethingNew"] = {"x": 1}
    assert len(provider_factory([payload]).fetch(movie()).showtimes) == 2


# ──────────────────────────────────────────────────────────────────────────
# 3 · It reaches the monitor as "couldn't check", and changes nothing stored
# ──────────────────────────────────────────────────────────────────────────
def test_an_unrecognised_response_leaves_the_last_observation_alone(provider_factory, monkeypatch, at):
    monitor = watch(at)
    upsert_monitor(monitor, mirror=False)
    sent: list = []

    def tick(minutes, body):
        provider = provider_factory([body])
        monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
        return run_once(at=at + timedelta(minutes=minutes), force=True, mirror=False,
                        notifier=lambda m, c: sent.append(c))

    tick(0, build_payload(ALLU_LIVE))
    assert len(sent) == 1
    key = monitor.targets[0].key

    report = tick(10, {"data": {"error": "blocked"}})
    assert report.failed == [monitor.id] and report.checked == []
    state = get_monitor_state(monitor.id)
    assert state.targets[key].availability is Availability.AVAILABLE
    assert state.targets[key].notified_availability is Availability.AVAILABLE
    assert (state.consecutive_errors, state.last_error_kind) == (1, "ERROR")
    assert "not a 'no shows' answer" in state.last_error

    tick(20, build_payload(ALLU_LIVE))
    assert len(sent) == 1, "a format hiccup must not re-announce what was already announced"


def test_an_unrecognised_sibling_response_makes_the_read_partial_not_empty(provider_factory, at):
    """The film's own event answers properly; a premium sibling answers with
    something else. That is a partial read (REL-01), not a crash and not a
    silently shorter listing."""
    film = movie(variants=((SIBLING, "Dolby Cinema 2D"),))
    snapshot = provider_factory([build_payload(ALLU_LIVE), {"data": {}}]).fetch(film)
    [failed] = snapshot.failures
    assert failed.event_code == SIBLING and not failed.blocked
    assert len(snapshot.showtimes) == 2


# ──────────────────────────────────────────────────────────────────────────
# 4 · The diagnostic
# ──────────────────────────────────────────────────────────────────────────
def test_the_log_names_the_shape_and_never_a_value(provider_factory, capsys):
    body = {"error": {"message": "SECRET-VALUE-IN-BODY"}, "token": "SECRET-VALUE-IN-BODY",
            "data": {"note": "SECRET-VALUE-IN-BODY"}}
    with pytest.raises(PlatformError) as exc:
        provider_factory([body]).fetch(movie())
    out = capsys.readouterr().out
    assert f"unrecognised showtimes response for {EVENT}: 'showtimeWidgets' is missing" in out
    assert "response=object{data,error,token}" in out and "data=object{note}" in out
    assert "SECRET-VALUE-IN-BODY" not in out and "SECRET-VALUE-IN-BODY" not in str(exc.value)


# ──────────────────────────────────────────────────────────────────────────
# 5 · The canary, for what validation cannot see
# ──────────────────────────────────────────────────────────────────────────
def test_a_listing_that_empties_after_theatres_were_seen_warns_once(provider_factory, monkeypatch, at, capsys):
    """A complete, well-formed, empty read after theatres had been listed is
    either a film that left cinemas or a format change validation missed.
    It is recorded as it stands — and flagged once, on the transition."""
    monitor = watch(at)
    upsert_monitor(monitor, mirror=False)

    def tick(minutes, body) -> str:
        provider = provider_factory([body])
        monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
        capsys.readouterr()
        run_once(at=at + timedelta(minutes=minutes), force=True, mirror=False, notifier=lambda m, c: None)
        return capsys.readouterr().out

    assert "::warning::" not in tick(0, build_payload(NOT_ON_SALE))
    out = tick(10, build_payload([]))
    assert out.count("::warning::") == 1 and "came back with no theatres at all" in out
    assert "1 monitor(s)" in out
    # Recorded as the answer it is…
    assert get_monitor_state(monitor.id).targets[monitor.targets[0].key].availability \
        is Availability.THEATRE_NOT_AVAILABLE
    # …and not repeated while it stays empty.
    assert "::warning::" not in tick(20, build_payload([]))


def test_a_listing_that_was_always_empty_never_warns(provider_factory, monkeypatch, at, capsys):
    """A film announced but not yet listed anywhere is the ordinary case for
    a new monitor; it must not page anybody."""
    monitor = watch(at)
    upsert_monitor(monitor, mirror=False)
    for minutes in (0, 10):
        provider = provider_factory([build_payload([])])
        monkeypatch.setattr("monitor.checker.get_provider", lambda slug, p=provider: p)
        run_once(at=at + timedelta(minutes=minutes), force=True, mirror=False, notifier=lambda m, c: None)
    assert "::warning::" not in capsys.readouterr().out
