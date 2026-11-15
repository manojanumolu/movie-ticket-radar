"""SOLD OUT is a claim about everything watched, so everything has to say so
(audit REL-04).

The category watch already held itself to this ("all-blocks verdicts",
``4803f0e``). The ordinary monitor — the one every non-admin account uses —
did not: a show was SOLD_OUT as soon as its *readable* categories were, and a
target was SOLD_OUT as soon as *one* matching show was. So "one show sold
out, two not yet on sale" was shown as "Every seat category is sold out."

The rule, for both kinds of monitor:

* AVAILABLE   — at least one bookable category in scope;
* SOLD_OUT    — every show in scope was read, every relevant category of
                every one of them explicitly says sold out;
* otherwise   — NOT_BOOKABLE (or UNKNOWN when the read itself was partial):
                an unknown status, a missing category list, an unreadable
                show and a show not yet on sale each prevent "sold out".
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from monitor.changes import apply_outcome, detect_changes, mark_notified
from monitor.checker import Listing, evaluate_target, outcome_for
from monitor.models import (
    ANY_FORMAT,
    Availability,
    Monitor,
    MovieRef,
    ReadFailure,
    Snapshot,
    TheatreTarget,
)
from monitor.state import MonitorState
from tests.conftest import build_payload

EVENT = "ET00478890"
URL = f"https://in.bookmyshow.com/movies/hyderabad/a-film/buytickets/{EVENT}"
SOLD, OPEN, FAST, UNREADABLE, NEW_CODE = "0", "3", "2", "", "7"
ALLU = TheatreTarget("ALUC", "ALLU Cinemas", "Kokapet", ANY_FORMAT)
GOLD, PLATINUM, LOUNGER = "GOLD", "PLATINUM", "LOUNGER"


def movie() -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=EVENT, title="A Film", region_code="HYD",
                    region_slug="hyderabad", source_url=URL)


def watch(at, *, categories=()) -> Monitor:
    return Monitor(movie=movie(), targets=[ALLU], notify_email="w@example.com", owner_uid="uid-test-1",
                   monitor_until=at + timedelta(days=1), categories=list(categories))


def listing(*shows) -> dict:
    """A payload of ALLU shows. Each show is ``{category name: availStatus}``,
    or ``None`` for a show that publishes no categories — not yet on sale."""
    rows = [{"venue_code": "ALUC", "venue_name": "ALLU Cinemas", "area": "Kokapet",
             "time": f"{6 + i:02d}:00 PM", "time_code": f"{18 + i}00", "fmt": "DOLBY CINEMA", "status": None}
            for i, _ in enumerate(shows)]
    payload = build_payload(rows)
    cards = next(w for w in payload["data"]["showtimeWidgets"] if w["type"] == "groupList")["data"][0]["data"]
    for built, categories in zip(cards[0]["showtimes"], shows):
        built["additionalData"]["categories"] = json.loads(json.dumps([
            {"priceDesc": name, "priceCode": f"{n:04d}", "areaCatCode": f"{n:010d}", "availStatus": status,
             "curPrice": "295.00"}
            for n, (name, status) in enumerate((categories or {}).items(), 1)]))
    return payload


def verdict(provider_factory, monitor, *shows):
    snapshot = provider_factory([listing(*shows)]).fetch(monitor.movie)
    return evaluate_target(monitor, ALLU, snapshot), snapshot


# ──────────────────────────────────────────────────────────────────────────
# 1 · One show
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("categories,expected", [
    ({GOLD: OPEN, PLATINUM: SOLD, LOUNGER: SOLD}, Availability.AVAILABLE),
    ({GOLD: FAST, PLATINUM: SOLD}, Availability.AVAILABLE),
    ({GOLD: OPEN, PLATINUM: UNREADABLE}, Availability.AVAILABLE),
    ({GOLD: SOLD, PLATINUM: SOLD, LOUNGER: SOLD}, Availability.SOLD_OUT),
    ({GOLD: SOLD}, Availability.SOLD_OUT),
    ({GOLD: SOLD, PLATINUM: UNREADABLE, LOUNGER: NEW_CODE}, Availability.NOT_BOOKABLE),
    ({GOLD: SOLD, PLATINUM: SOLD, LOUNGER: UNREADABLE}, Availability.NOT_BOOKABLE),
    ({GOLD: SOLD, PLATINUM: NEW_CODE}, Availability.NOT_BOOKABLE),
    ({GOLD: UNREADABLE, PLATINUM: NEW_CODE}, Availability.NOT_BOOKABLE),
    (None, Availability.NOT_BOOKABLE),
], ids=["one open, others sold", "filling fast counts", "open beside unreadable", "every one sold",
        "single category sold", "one sold, two unreadable (audit D)", "one unreadable among sold",
        "a status code we do not know", "nothing readable", "no categories published"])
def test_a_show_is_sold_out_only_when_every_category_says_so(provider_factory, at, categories, expected):
    result, snapshot = verdict(provider_factory, watch(at), categories)
    assert snapshot.showtimes[0].availability is expected
    assert result.availability is expected


# ──────────────────────────────────────────────────────────────────────────
# 2 · Several shows
# ──────────────────────────────────────────────────────────────────────────
ALL_SOLD = {GOLD: SOLD, PLATINUM: SOLD}


def test_every_show_sold_out_is_sold_out(provider_factory, at):
    result, _ = verdict(provider_factory, watch(at), ALL_SOLD, ALL_SOLD, ALL_SOLD)
    assert result.availability is Availability.SOLD_OUT
    assert result.detail == "Every seat category is sold out." and len(result.showtimes) == 3


def test_one_show_sold_out_and_others_not_yet_on_sale_is_not_sold_out(provider_factory, at):
    """The audit's second reproduction D, which read "Every seat category is sold out."."""
    result, _ = verdict(provider_factory, watch(at), ALL_SOLD, None, None)
    assert result.availability is Availability.NOT_BOOKABLE
    assert "1 of 3 showtime(s) are sold out" in result.detail
    assert "sold out." not in result.detail.replace("are sold out;", "")


def test_one_show_with_an_unreadable_category_keeps_the_target_from_sold_out(provider_factory, at):
    result, _ = verdict(provider_factory, watch(at), ALL_SOLD, {GOLD: SOLD, PLATINUM: UNREADABLE})
    assert result.availability is Availability.NOT_BOOKABLE


def test_one_bookable_show_among_sold_out_ones_is_available(provider_factory, at):
    result, _ = verdict(provider_factory, watch(at), ALL_SOLD, {GOLD: SOLD, PLATINUM: OPEN}, ALL_SOLD)
    assert result.availability is Availability.AVAILABLE
    assert [s.time_label for s in result.showtimes] == ["07:00 PM"]


def test_nothing_on_sale_yet_reads_as_before(provider_factory, at):
    result, _ = verdict(provider_factory, watch(at), None, None)
    assert result.availability is Availability.NOT_BOOKABLE
    assert result.detail == "Showtimes are listed but booking hasn't opened."


@pytest.mark.parametrize("categories", [(), (GOLD,)], ids=["ordinary", "category watch"])
def test_a_matching_show_that_could_not_be_read_is_never_sold_out(provider_factory, at, categories):
    """Every show that *was* read is sold out, but one request of the listing
    failed: the unread part may hold an open show, so this is "we don't
    know" — for both kinds of monitor."""
    monitor = watch(at, categories=categories)
    snapshot = provider_factory([listing(ALL_SOLD, ALL_SOLD)]).fetch(monitor.movie)
    assert evaluate_target(monitor, ALLU, snapshot).availability is Availability.SOLD_OUT
    partial = Snapshot(movie=snapshot.movie, venues=snapshot.venues, showtimes=snapshot.showtimes,
                       failures=[ReadFailure("ET00999001", "", "HTTP 403", blocked=True)])
    result = evaluate_target(monitor, ALLU, partial)
    assert result.availability is Availability.UNKNOWN and not result.availability.is_answer


# ──────────────────────────────────────────────────────────────────────────
# 3 · The ordinary monitor and the category watch agree
# ──────────────────────────────────────────────────────────────────────────
#: (shows, ordinary verdict, verdict of a watch on GOLD). They differ only
#: where their *scope* does: the watch ignores PLATINUM, the monitor does not.
CASES = {
    "everything sold": ([ALL_SOLD, ALL_SOLD], Availability.SOLD_OUT, Availability.SOLD_OUT),
    "gold open": ([{GOLD: OPEN, PLATINUM: SOLD}], Availability.AVAILABLE, Availability.AVAILABLE),
    "gold unreadable": ([{GOLD: UNREADABLE, PLATINUM: SOLD}], Availability.NOT_BOOKABLE, Availability.NOT_BOOKABLE),
    "one show not on sale": ([ALL_SOLD, None], Availability.NOT_BOOKABLE, Availability.NOT_BOOKABLE),
    "no show on sale": ([None, None], Availability.NOT_BOOKABLE, Availability.NOT_BOOKABLE),
    # scope: only PLATINUM is open — news for the monitor, not for a GOLD watch
    "only platinum open": ([{GOLD: SOLD, PLATINUM: OPEN}], Availability.AVAILABLE, Availability.SOLD_OUT),
    # scope: PLATINUM unreadable — blocks the monitor's claim, not the GOLD watch's
    "platinum unreadable": ([{GOLD: SOLD, PLATINUM: UNREADABLE}], Availability.NOT_BOOKABLE, Availability.SOLD_OUT),
}


@pytest.mark.parametrize("shows,ordinary,gold_watch", CASES.values(), ids=CASES.keys())
def test_both_kinds_of_monitor_need_the_same_evidence(provider_factory, at, shows, ordinary, gold_watch):
    assert verdict(provider_factory, watch(at), *shows)[0].availability is ordinary
    assert verdict(provider_factory, watch(at, categories=[GOLD]), *shows)[0].availability is gold_watch


def test_a_category_watch_is_not_sold_out_while_a_show_has_yet_to_open(provider_factory, at):
    """It used to look only at the shows that list the category, so one sold
    show beside one unopened show read "no seats left"."""
    result, _ = verdict(provider_factory, watch(at, categories=[GOLD]), {GOLD: SOLD}, None)
    assert result.availability is Availability.NOT_BOOKABLE
    assert "1 more haven't opened for booking" in result.detail


def test_a_show_that_simply_lacks_the_watched_category_does_not_block_it(provider_factory, at):
    """A show that has published its categories and has no GOLD is not
    waiting on anything: the watch's scope is the shows that have it."""
    result, _ = verdict(provider_factory, watch(at, categories=[GOLD]), {GOLD: SOLD}, {PLATINUM: OPEN})
    assert result.availability is Availability.SOLD_OUT


# ──────────────────────────────────────────────────────────────────────────
# 4 · The alert that matters still fires
# ──────────────────────────────────────────────────────────────────────────
def _life(provider_factory, monitor, at, *ticks):
    state, inbox = MonitorState(), []
    seen = []
    for i, shows in enumerate(ticks):
        when = at + timedelta(minutes=10 * i)
        snapshot = provider_factory([listing(*shows)]).fetch(monitor.movie)
        outcome = outcome_for(monitor, Listing(snapshot=snapshot), at=when)
        changes = detect_changes(monitor, outcome, state)
        apply_outcome(outcome, state)
        for change in changes:
            inbox.append(change)
            mark_notified(change, state, when)
        seen.append(state.target(ALLU.key).availability)
    return seen, inbox


@pytest.mark.parametrize("categories", [(), (GOLD,)], ids=["ordinary", "category watch"])
def test_sold_out_to_available_is_announced_once(provider_factory, at, categories):
    seen, inbox = _life(provider_factory, watch(at, categories=categories), at,
                        [ALL_SOLD], [{GOLD: OPEN, PLATINUM: SOLD}], [{GOLD: OPEN, PLATINUM: SOLD}])
    assert seen == [Availability.SOLD_OUT, Availability.AVAILABLE, Availability.AVAILABLE]
    assert [(c.previous, c.current) for c in inbox] == [(Availability.SOLD_OUT, Availability.AVAILABLE)]


def test_the_state_that_replaces_a_false_sold_out_still_announces_the_opening(provider_factory, at):
    """What used to be recorded as SOLD_OUT is now NOT_BOOKABLE; the opening
    that follows is announced exactly as it was — once."""
    seen, inbox = _life(provider_factory, watch(at), at,
                        [ALL_SOLD, None], [ALL_SOLD, {GOLD: OPEN}], [ALL_SOLD, {GOLD: OPEN}])
    assert seen == [Availability.NOT_BOOKABLE, Availability.AVAILABLE, Availability.AVAILABLE]
    assert [(c.previous, c.current) for c in inbox] == [(Availability.NOT_BOOKABLE, Availability.AVAILABLE)]


def test_available_to_sold_out_to_available_announces_the_return(provider_factory, at):
    seen, inbox = _life(provider_factory, watch(at), at,
                        [{GOLD: OPEN}], [{GOLD: SOLD}], [{GOLD: OPEN}])
    assert seen == [Availability.AVAILABLE, Availability.SOLD_OUT, Availability.AVAILABLE]
    assert len(inbox) == 2
