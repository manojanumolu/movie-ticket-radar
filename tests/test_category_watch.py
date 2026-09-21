"""Category watch — an admin-only monitor that waits for specific seat
categories (GOLD, PLATINUM…) to open up.

A category watch is an ordinary :class:`Monitor` with ``categories`` (and
optionally one ``show_time``): the same worker, the same shared read, the
same state, history, ownership and email. The categories come from the
showtimes response the provider already reads — no second request — parsed
onto each ``Showtime``. These tests pin the parsing, the verdict, the
transitions (first read is a baseline; one email per opening; a second
after it closes and reopens; a failed email retried), the matching, the
gating (admin only, owner only, invisible to everyone else) and that
ordinary monitors and their code paths are untouched.
"""

from __future__ import annotations

import json

import pytest

from monitor.changes import detect_changes
from monitor.checker import evaluate_target, run_once
from monitor.models import (ANY_FORMAT, Availability, Monitor, MovieRef, SeatCategory, Showtime,
                            TheatreTarget, Venue, category_display, category_matches, normalise_category)
from monitor.state import get_monitor_state as get_state, load_monitors, upsert_monitor
from platforms.bookmyshow import parse_seat_categories
from tests.conftest import ALLU_LIVE, NOT_ON_SALE, SOLD_OUT, FakeResponse, build_payload
from tests.test_app import seeded  # noqa: F401 - the synced catalogue the page tests run against

#: ``additionalData.categories`` of one live show, as a runner printed it (Sept 2026).
REAL_CATEGORIES = [
    {"priceCode": "0001", "additionalData": "0", "curPrice": "350.00", "areaCatCode": "0000000001",
     "availStatus": "0", "seatLayout": True, "priceDesc": "PLATINUM"},
    {"priceCode": "0002", "additionalData": "0", "curPrice": "295.00", "areaCatCode": "0000000002",
     "availStatus": "3", "seatLayout": True, "priceDesc": "GOLD", "categoryRange": "1|2|3|4|5|6"},
    {"priceCode": "0003", "additionalData": "0", "curPrice": "295.00", "areaCatCode": "0000000003",
     "availStatus": "3", "seatLayout": True, "priceDesc": "LOUNGER"},
]


#: ALLU Cinemas, Dolby Cinema, Avengers 3D (11:35 AM, session 5315) — exactly as
#: the runner printed it on 21 Sep 2026: two GOLDs, told apart by the platform
#: only by a trailing dot in the name and by their price/area codes.
TWO_GOLDS = [
    {"additionalData": "0", "areaCatCode": "0000000002", "availStatus": "0", "curPrice": "450.00", "priceCode": "0005", "priceDesc": "3D PLATINUM", "seatLayout": True},
    {"additionalData": "0", "areaCatCode": "0000000003", "availStatus": "2", "categoryRange": "1|2|3|4|5|6", "curPrice": "395.00", "priceCode": "0006", "priceDesc": "3D GOLD", "seatLayout": True},
    {"additionalData": "0", "areaCatCode": "0000000004", "availStatus": "0", "curPrice": "450.00", "priceCode": "0007", "priceDesc": "3D DIRECTOR CHOICE", "seatLayout": True},
    {"additionalData": "0", "areaCatCode": "0000000005", "availStatus": "3", "categoryRange": "1|2|3|4|5|6", "curPrice": "395.00", "priceCode": "0008", "priceDesc": "3D GOLD.", "seatLayout": True},
]


def payload(shows, categories):
    """A showtimes payload whose every show carries ``categories``."""
    p = build_payload(shows)
    for widget in p["data"]["showtimeWidgets"]:
        for group in widget.get("data", []):
            for card in (group.get("data", []) if isinstance(group, dict) else []):
                for show in card.get("showtimes", []):
                    show["additionalData"]["categories"] = json.loads(json.dumps(categories))
    return p


def cat(name, status=Availability.AVAILABLE, code="") -> SeatCategory:
    return SeatCategory(code=code or name[:2], name=name, availability=status)


def cats(**status):
    """``cats(GOLD="3", PLATINUM="0")`` → a categories list."""
    return [{"priceDesc": name, "priceCode": f"{i:04d}", "availStatus": s, "curPrice": "295.00"}
            for i, (name, s) in enumerate(status.items(), 1)]


def _snapshot(provider_factory, listing_url, shows, categories):
    provider = provider_factory([payload(shows, categories)])
    return provider.resolve(listing_url)


def _run(provider, monkeypatch, at, sent):
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider)
    return run_once(at=at, force=True, mirror=False, notifier=lambda m, c: sent.append((at, c)))


# ──────────────────────────────────────────────────────────────────────────
# 1 · Parsing and normalisation
# ──────────────────────────────────────────────────────────────────────────
def test_parses_the_live_category_shape_onto_the_showtime(provider_factory, listing_url):
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, REAL_CATEGORIES)
    show = snap.shows_for("ALLU")[0]
    assert [c.name for c in show.categories] == ["PLATINUM", "GOLD", "LOUNGER"]
    assert [c.availability for c in show.categories] == [Availability.SOLD_OUT, Availability.AVAILABLE, Availability.AVAILABLE]
    assert show.category("gold").price == "295.00" and show.category("Lounger ").code == "0003"
    assert show.category("SILVER") is None


@pytest.mark.parametrize("status,expected", [
    ("0", Availability.SOLD_OUT), ("1", Availability.AVAILABLE), ("2", Availability.AVAILABLE),
    ("3", Availability.AVAILABLE), ("", Availability.NOT_BOOKABLE), ("9", Availability.NOT_BOOKABLE),
])
def test_category_status_maps_like_the_show_status(status, expected):
    assert parse_seat_categories({"categories": cats(GOLD=status)})[0].availability is expected


@pytest.mark.parametrize("show_data", [
    None, "", 7, [], {}, {"categories": None}, {"categories": "GOLD"}, {"categories": {}},
    {"categories": [None, "x", 3]}, {"categories": [{"availStatus": "3"}]},
])
def test_missing_or_malformed_categories_yield_nothing(show_data):
    assert parse_seat_categories(show_data) == ()


def test_normalisation_ignores_case_and_spacing_but_keeps_punctuation():
    assert normalise_category(" Gold  Class ") == "gold class" == normalise_category("GOLD CLASS")
    assert normalise_category("3D GOLD") != normalise_category("3D GOLD.")       # two sections at ALLU
    m = Monitor(movie=MovieRef("bookmyshow", "ET1", "x", "HYD", "hyderabad"), targets=[], categories=["gold", "Platinum"])
    assert m.watches_category(cat("GOLD")) and m.watches_category(cat("PLATINUM ")) and not m.watches_category(cat("LOUNGER"))
    show = Showtime("ALLU", "Allu", "S1", "20260925", "07:15 PM", "1915", "Dolby Cinema", Availability.AVAILABLE)
    for wanted in ("07:15 PM", "7:15 PM", "1915", "7:15pm", ""):
        assert Monitor(movie=m.movie, targets=[], show_time=wanted).matches_show_time(show), wanted
    assert not Monitor(movie=m.movie, targets=[], show_time="09:45 PM").matches_show_time(show)


def test_monitor_round_trips_categories_and_ordinary_monitors_are_unchanged(make_monitor):
    plain = make_monitor()
    assert plain.categories == [] and plain.show_time == "" and not plain.is_category_watch
    assert plain.to_dict()["categories"] == [] and Monitor.from_dict(plain.to_dict()).to_dict() == plain.to_dict()
    legacy = {k: v for k, v in plain.to_dict().items() if k not in ("categories", "show_time")}
    assert Monitor.from_dict(legacy).categories == []                 # a document saved before this feature
    watch = make_monitor()
    watch.categories, watch.show_time = ["GOLD", "PLATINUM"], "07:15 PM"
    again = Monitor.from_dict(json.loads(json.dumps(watch.to_dict())))
    assert again.categories == ["GOLD", "PLATINUM"] and again.show_time == "07:15 PM" and again.is_category_watch
    assert again.category_label == "GOLD, PLATINUM"


# ──────────────────────────────────────────────────────────────────────────
# 2 · The verdict
# ──────────────────────────────────────────────────────────────────────────
def test_verdict_is_available_only_when_a_watched_category_is(make_monitor, provider_factory, listing_url):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    watch.categories = ["PLATINUM"]
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, REAL_CATEGORIES)   # GOLD/LOUNGER open, PLATINUM sold out
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.SOLD_OUT and "PLATINUM" in result.detail
    # the ordinary verdict on the very same snapshot is still AVAILABLE (any category)
    plain = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    assert evaluate_target(plain, plain.targets[0], snap).availability is Availability.AVAILABLE

    watch.categories = ["gold"]
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.AVAILABLE and result.detail.startswith("GOLD · ₹295 · area 2 bookable")
    assert result.showtimes and result.booking_url.startswith("https://in.bookmyshow.com/")


def test_verdict_when_the_category_is_not_listed_or_nothing_is_published(make_monitor, provider_factory, listing_url):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", ANY_FORMAT)])
    watch.categories = ["RECLINER"]
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, REAL_CATEGORIES)
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.NOT_BOOKABLE and "not listed" in result.detail
    snap = _snapshot(provider_factory, listing_url, NOT_ON_SALE, [])
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.NOT_BOOKABLE and "no seat categories" in result.detail


def test_show_time_narrows_the_watch_to_one_show(make_monitor, provider_factory, listing_url):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", ANY_FORMAT)])
    watch.categories, watch.show_time = ["GOLD"], "09:45 PM"
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, REAL_CATEGORIES)           # only a 07:30 PM show
    assert evaluate_target(watch, watch.targets[0], snap).availability is Availability.SHOW_NOT_AVAILABLE
    watch.show_time = "7:30 PM"
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.AVAILABLE and [s.time_label for s in result.showtimes] == ["07:30 PM"]


# ──────────────────────────────────────────────────────────────────────────
# 3 · Transitions through the real worker tick
# ──────────────────────────────────────────────────────────────────────────
def _watch(make_monitor, categories=("PLATINUM",)):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    watch.categories = list(categories)
    upsert_monitor(watch, mirror=False)
    return watch


def test_first_check_unavailable_is_a_baseline_with_no_email(make_monitor, provider_factory, monkeypatch, later):
    watch = _watch(make_monitor, ["PLATINUM"])                  # PLATINUM is sold out on the first read
    sent = []
    _run(provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]), monkeypatch, later(0), sent)
    _run(provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]), monkeypatch, later(10), sent)
    assert sent == []
    assert get_state(watch.id).targets[watch.targets[0].key].availability is Availability.SOLD_OUT


def test_first_check_already_available_is_one_email_saying_so(make_monitor, provider_factory, monkeypatch, later):
    watch = _watch(make_monitor, ["GOLD"])                      # GOLD is already open on the first read
    sent = []
    _run(provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]), monkeypatch, later(0), sent)
    assert len(sent) == 1
    change = sent[0][1]
    assert change.already_open and change.previous is Availability.UNKNOWN
    assert change.headline == "GOLD · ₹295 · area 2 ALREADY AVAILABLE — Avengers: Endgame Encore"
    ts = get_state(watch.id).targets[watch.targets[0].key]
    assert ts.notified_availability is Availability.AVAILABLE and ts.notified_at is not None
    # …and stays quiet while it stays open
    for step in (10, 20, 30):
        _run(provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]), monkeypatch, later(step), sent)
    assert len(sent) == 1
    # an ordinary monitor never carries the flag
    plain = make_monitor(targets=[TheatreTarget("AMB", "AMB Cinemas", "", ANY_FORMAT)])
    upsert_monitor(plain, mirror=False)
    _run(provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]), monkeypatch, later(40), sent)
    assert [c.already_open for _, c in sent] == [True, False] and sent[1][1].monitor_id == plain.id


def test_already_available_email_says_it_was_already_open(make_monitor):
    from monitor.changes import Change, ChangeKind
    from notifications.email import render_change

    watch = make_monitor(); watch.categories = ["0000000002|GOLD"]
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=watch.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title=watch.movie.title,
                    previous=Availability.UNKNOWN, current=Availability.AVAILABLE, date_code="20260925",
                    time_labels=["07:30 PM"], date_codes=["20260925"], categories=["GOLD · ₹295 · area 2"], already_open=True)
    subject, html, text = render_change(watch, change)
    assert subject == "ALREADY AVAILABLE — GOLD · ₹295 · area 2 — Avengers: Endgame Encore at Allu Cinemas"
    for body in (html, text):
        assert "ALREADY AVAILABLE WHEN THIS WATCH STARTED" in body and "already available when this watch started" in body
        assert "TICKETS ARE LIVE" not in body and "just became bookable" not in body
    assert json.loads(json.dumps(change.to_dict()))["already_open"] is True


def test_a_failed_already_available_email_is_retried_with_the_same_words(make_monitor, provider_factory, monkeypatch, later):
    watch = _watch(make_monitor, ["GOLD"])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]))

    def broken(monitor, change):
        raise RuntimeError("SMTP down")

    report = run_once(at=later(0), force=True, mirror=False, notifier=broken)
    assert report.emails_sent == 0 and report.email_errors
    sent = []
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)]))
    run_once(at=later(10), force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1 and sent[0].already_open                # still "already open", not "just opened"
    run_once(at=later(20), force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1


def test_unavailable_to_available_is_one_email_then_silence(make_monitor, provider_factory, monkeypatch, later):
    watch = _watch(make_monitor, ["PLATINUM"])
    sent = []
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0", GOLD="3"))]), monkeypatch, later(0), sent)
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0", GOLD="3"))]), monkeypatch, later(10), sent)
    assert sent == []                                            # GOLD opening is not our category
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3", GOLD="3"))]), monkeypatch, later(20), sent)
    assert len(sent) == 1
    change = sent[0][1]
    assert change.categories == ["PLATINUM · ₹295"] and change.previous is Availability.SOLD_OUT
    assert change.headline == "PLATINUM · ₹295 AVAILABLE — Avengers: Endgame Encore"
    for step in (30, 40, 50):
        _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3", GOLD="3"))]), monkeypatch, later(step), sent)
    assert len(sent) == 1


def test_available_then_gone_then_back_is_a_second_email(make_monitor, provider_factory, monkeypatch, later):
    _watch(make_monitor, ["PLATINUM"])
    sent = []
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0"))]), monkeypatch, later(0), sent)
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3"))]), monkeypatch, later(10), sent)
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0"))]), monkeypatch, later(20), sent)
    _run(provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3"))]), monkeypatch, later(30), sent)
    assert [c.previous for _, c in sent] == [Availability.SOLD_OUT, Availability.SOLD_OUT] and len(sent) == 2


def test_a_failed_email_is_retried_not_lost(make_monitor, provider_factory, monkeypatch, later):
    watch = _watch(make_monitor, ["PLATINUM"])
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0"))]))
    run_once(at=later(0), force=True, mirror=False, notifier=lambda m, c: None)
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3"))]))

    def broken(monitor, change):
        raise RuntimeError("SMTP down")

    report = run_once(at=later(10), force=True, mirror=False, notifier=broken)
    assert report.emails_sent == 0 and report.email_errors
    assert get_state(watch.id).last_email_error.startswith("RuntimeError")
    sent = []
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, cats(PLATINUM="3"))]))
    run_once(at=later(20), force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1 and sent[0].categories == ["PLATINUM · ₹295"]     # the same transition, delivered
    run_once(at=later(30), force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1


def test_no_extra_request_and_the_ordinary_monitor_beside_it_is_unchanged(make_monitor, provider_factory, monkeypatch, later):
    """One read serves both: the ordinary monitor emails on the first read
    that finds tickets (as it always has), the category watch does not; the
    provider is asked exactly once per tick."""
    plain = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    upsert_monitor(plain, mirror=False)
    _watch(make_monitor, ["PLATINUM"])
    sent = []
    provider = provider_factory([payload(ALLU_LIVE, cats(PLATINUM="0", GOLD="3"))])
    report = _run(provider, monkeypatch, later(0), sent)
    assert len(provider.session.calls) == 1                       # one BookMyShow read for two monitors
    assert [c.monitor_id for _, c in sent] == [plain.id] and sent[0][1].categories == []
    assert sent[0][1].headline.startswith("TICKETS ARE LIVE")
    assert report.emails_sent == 1 and not report.failed


def test_email_names_the_category_and_reuses_the_stored_link(make_monitor):
    from monitor.changes import Change, ChangeKind
    from notifications.email import render_change

    watch = make_monitor()
    watch.categories, watch.show_time = ["GOLD"], "07:30 PM"
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=watch.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title=watch.movie.title,
                    previous=Availability.SOLD_OUT, current=Availability.AVAILABLE, date_code="20260925",
                    booking_url="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925",
                    time_labels=["07:30 PM"], time_links=[["07:30 PM", "https://in.bookmyshow.com/x"]],
                    date_codes=["20260925"], categories=["GOLD"])
    subject, html, text = render_change(watch, change)
    assert subject == "GOLD AVAILABLE — Avengers: Endgame Encore at Allu Cinemas"
    for body in (html, text):
        assert "GOLD" in body and "Allu Cinemas" in body and "Dolby Cinema" in body and "07:30 PM" in body
        assert "25 Sep" in body and "https://in.bookmyshow.com/x" in body
    assert "GOLD AVAILABLE" in text and json.loads(json.dumps(change.to_dict()))["categories"] == ["GOLD"]


# ──────────────────────────────────────────────────────────────────────────
# 4 · The page: admin only, owner only, invisible to everyone else
# ──────────────────────────────────────────────────────────────────────────
def _wizard(movie_id):
    return dict(step=5, location="hyderabad", movie_id=movie_id, theatres=["ALLU"],
                formats={"ALLU": ["Dolby Cinema"]})


@pytest.fixture
def seeded_with_categories(provider_factory, monkeypatch):
    """test_app's synced catalogue, with the live category shape in every show."""
    from monitor import catalogue
    from tests.conftest import DETAIL_REQUESTS_HYD, QUICKBOOK_HYD

    provider = provider_factory([QUICKBOOK_HYD] + [payload(ALLU_LIVE, REAL_CATEGORIES)] * DETAIL_REQUESTS_HYD)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad") if catalogue.movie_from_entry(e).title == "Mandaadi")
    return catalogue.movie_from_entry(entry).id


def test_the_catalogue_remembers_which_categories_a_theatre_lists(seeded_with_categories):
    from ui import catalogue_view as cv

    venue = next(v for v in cv.selected_venues(seeded_with_categories, "hyderabad", ["ALLU"]))
    assert [c.name for c in venue.categories] == ["PLATINUM", "GOLD", "LOUNGER"]
    assert [c.key for c in venue.categories] == ["0000000001|PLATINUM", "0000000002|GOLD", "0000000003|LOUNGER"]
    assert isinstance(venue, Venue) and venue.formats                # the rest of the venue is as before


def test_an_ordinary_account_sees_nothing_and_cannot_create_one(seeded_with_categories, signed_in):
    from ui.flow import CATEGORY_WATCH_KEY, CATEGORY_WATCH_TITLE
    from tests.test_app import run, text

    app = run(**_wizard(seeded_with_categories))
    assert not app.exception, [str(e) for e in app.exception]
    body = text(app)
    assert CATEGORY_WATCH_TITLE not in body and "seat categories" not in body.lower()
    assert not app.multiselect and not any((w.key or "").startswith("catwatch") for w in app.text_input)
    # even with the picks smuggled into the session, the saved monitor is ordinary
    app = run(**_wizard(seeded_with_categories), **{CATEGORY_WATCH_KEY: ["GOLD"], "catwatch_show_time": "07:30 PM"})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    monitors = load_monitors()
    assert len(monitors) == 1 and monitors[0].categories == [] and monitors[0].show_time == ""


def test_the_admin_sees_the_block_and_creates_a_category_watch(seeded_with_categories, signed_in):
    from ui.flow import CATEGORY_WATCH_KEY, CATEGORY_WATCH_TITLE
    from tests.test_app import run, text

    signed_in.admin = True
    app = run(**_wizard(seeded_with_categories))
    assert not app.exception, [str(e) for e in app.exception]
    assert CATEGORY_WATCH_TITLE in text(app)
    picker = next(m for m in app.multiselect if m.key == CATEGORY_WATCH_KEY)
    assert list(picker.options) == ["PLATINUM · ₹350 · area 1", "GOLD · ₹295 · area 2", "LOUNGER · ₹295 · area 3"]
    assert not any(w for w in app.checkbox if "row" in (w.label or "").lower() or "seat" in (w.label or "").lower())
    app = picker.select("GOLD · ₹295 · area 2").select("PLATINUM · ₹350 · area 1").run()
    app.text_input(key="catwatch_show_time").set_value("07:30 PM").run()
    app.text_input(key="notify_email").set_value("admin@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    monitors = load_monitors()
    assert len(monitors) == 1
    watch = monitors[0]
    assert watch.categories == ["0000000002|GOLD", "0000000001|PLATINUM"] and watch.show_time == "07:30 PM"
    assert watch.owner_uid == signed_in.uid and watch.interval_minutes == 10
    assert [t.key for t in watch.targets] == ["ALLU::Dolby Cinema"]
    # it is listed as any monitor is, with its categories on the card
    page = run("My Monitors")
    assert "GOLD · AREA 2, PLATINUM · AREA 1 · 07:30 PM" in text(page).upper() and "EVERY 10 MIN" in text(page)


def test_a_category_watch_is_its_owners_only(make_monitor, monkeypatch):
    """Ownership is the existing model: the JSON/Firestore stores key every
    read on owner_uid. Another account's list never contains it."""
    from monitor import state as state_mod

    def as_(uid):
        monkeypatch.setattr(state_mod, "_scope_provider",
                            lambda: state_mod.Scope("", uid, lambda: f"id.{uid}"))
        state_mod.invalidate_cache("monitors")

    as_("uid-admin")
    watch = make_monitor(owner_uid="uid-admin")
    watch.categories = ["GOLD"]
    upsert_monitor(watch, mirror=False)
    assert [m.id for m in load_monitors()] == [watch.id]
    as_("uid-someone-else")
    assert all(m.id != watch.id for m in load_monitors())


@pytest.fixture
def seeded_without_categories(provider_factory, monkeypatch):
    """A synced catalogue whose shows publish no categories yet."""
    from monitor import catalogue
    from tests.conftest import DETAIL_REQUESTS_HYD, QUICKBOOK_HYD

    provider = provider_factory([QUICKBOOK_HYD] + [payload(ALLU_LIVE, [])] * DETAIL_REQUESTS_HYD)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad") if catalogue.movie_from_entry(e).title == "Mandaadi")
    return catalogue.movie_from_entry(entry).id


def test_the_admin_block_is_empty_handed_without_categories_and_offers_no_seat_controls(seeded_without_categories, signed_in):
    from ui.flow import CATEGORY_WATCH_TITLE
    from tests.test_app import run, text

    signed_in.admin = True
    app = run(**_wizard(seeded_without_categories))
    assert not app.exception
    body = text(app)
    assert CATEGORY_WATCH_TITLE in body and "hasn't seen seat categories" in body
    assert not app.multiselect
    labels = " ".join((w.label or "") for w in [*app.checkbox, *app.multiselect, *app.text_input, *app.selectbox]).lower()
    for banned in ("row", "seat map", "layout", "encrypted"):
        assert banned not in labels and banned not in " ".join(b.label.lower() for b in app.button)


# ──────────────────────────────────────────────────────────────────────────
# 5 · Two GOLDs at one theatre: distinct identities, never merged
# ──────────────────────────────────────────────────────────────────────────
def test_two_golds_are_two_categories_with_telling_labels(provider_factory, listing_url):
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, TWO_GOLDS)
    show = snap.shows_for("ALLU")[0]
    golds = [c for c in show.categories if "GOLD" in c.name]
    assert [c.name for c in golds] == ["3D GOLD", "3D GOLD."]
    assert [c.key for c in golds] == ["0000000003|3D GOLD", "0000000005|3D GOLD."]
    assert [c.label for c in golds] == ["3D GOLD · ₹395 · area 3", "3D GOLD. · ₹395 · area 5"]
    assert [c.availability for c in golds] == [Availability.AVAILABLE, Availability.AVAILABLE]   # "2" filling fast, "3"
    # the theatre keeps both, and the picker offers both
    venue = snap.venue("ALLU")
    assert [c.label for c in venue.categories] == [
        "3D PLATINUM · ₹450 · area 2", "3D GOLD · ₹395 · area 3", "3D DIRECTOR CHOICE · ₹450 · area 4", "3D GOLD. · ₹395 · area 5"]
    # a key names one of them only; a plain legacy name matches by name only
    assert category_matches("0000000005|3D GOLD.", golds[1]) and not category_matches("0000000005|3D GOLD.", golds[0])
    assert category_matches("3d gold", golds[0]) and not category_matches("3d gold", golds[1])
    assert category_display("0000000005|3D GOLD.") == "3D GOLD. · area 5" and category_display("GOLD") == "GOLD"


def test_watching_one_gold_does_not_match_the_other(make_monitor, provider_factory, listing_url):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    watch.categories = ["0000000005|3D GOLD."]
    only_first_open = [dict(c, availStatus="3" if c["priceCode"] == "0006" else "0") for c in TWO_GOLDS]
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, only_first_open)
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.SOLD_OUT and "3D GOLD. · area 5" in result.detail
    only_second_open = [dict(c, availStatus="3" if c["priceCode"] == "0008" else "0") for c in TWO_GOLDS]
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, only_second_open)
    result = evaluate_target(watch, watch.targets[0], snap)
    assert result.availability is Availability.AVAILABLE and result.detail.startswith("3D GOLD. · ₹395 · area 5 bookable")


def test_the_email_names_the_gold_that_opened(make_monitor, provider_factory, monkeypatch, later):
    watch = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "", "Dolby Cinema")])
    watch.categories = ["0000000005|3D GOLD."]
    upsert_monitor(watch, mirror=False)
    sent = []
    closed = [dict(c, availStatus="0") for c in TWO_GOLDS]
    _run(provider_factory([payload(ALLU_LIVE, closed)]), monkeypatch, later(0), sent)
    first_gold_open = [dict(c, availStatus="3" if c["priceCode"] == "0006" else "0") for c in TWO_GOLDS]
    _run(provider_factory([payload(ALLU_LIVE, first_gold_open)]), monkeypatch, later(10), sent)
    assert sent == []                                                  # the other GOLD is not ours
    _run(provider_factory([payload(ALLU_LIVE, TWO_GOLDS)]), monkeypatch, later(20), sent)
    assert len(sent) == 1 and sent[0][1].categories == ["3D GOLD. · ₹395 · area 5"]
    from notifications.email import render_change
    subject, html, text = render_change(watch, sent[0][1])
    assert subject.startswith("3D GOLD. · ₹395 · area 5 AVAILABLE") and "area 5" in text and "area 3" not in text


def test_a_watch_saved_with_plain_names_still_works(make_monitor, provider_factory, listing_url):
    """Documents from before keys hold names; they match by name, and a
    name shared by two sections matches either — which is what they meant."""
    watch = Monitor.from_dict({**make_monitor().to_dict(), "categories": ["gold"]})
    watch.targets = [TheatreTarget("ALLU", "Allu Cinemas", "", ANY_FORMAT)]
    snap = _snapshot(provider_factory, listing_url, ALLU_LIVE, REAL_CATEGORIES)
    assert evaluate_target(watch, watch.targets[0], snap).availability is Availability.AVAILABLE
    assert watch.category_label == "gold"


def test_old_catalogue_rows_with_plain_category_names_still_load():
    from monitor.catalogue import categories_from_entry, venues_from_entry

    venues = venues_from_entry({"venues": [{"code": "ALLU", "name": "Allu", "formats": [], "categories": ["GOLD", "GOLD", "PLATINUM"]}]})
    assert [c.label for c in venues[0].categories] == ["GOLD", "PLATINUM"]
    assert categories_from_entry([{"code": "0006", "name": "3D GOLD", "area_code": "0000000003", "price": "395.00", "availability": "AVAILABLE"},
                                  {"code": "0008", "name": "3D GOLD.", "area_code": "0000000005", "price": "395.00"}, "junk", 3])[1].label == "3D GOLD. · ₹395 · area 5"
    assert categories_from_entry("GOLD") == () and categories_from_entry(None) == ()


# ──────────────────────────────────────────────────────────────────────────
# 6 · The page tells sent, failed, baseline and pending apart
# ──────────────────────────────────────────────────────────────────────────
def test_email_status_line_is_truthful(make_monitor):
    from datetime import datetime

    from config.timezone import IST
    from monitor.state import MonitorState, TargetState
    from ui.components import email_status

    when = datetime(2026, 9, 21, 21, 20, tzinfo=IST)
    plain, watch = make_monitor(), make_monitor()
    watch.categories = ["0000000002|GOLD"]
    live = TargetState(availability=Availability.AVAILABLE)
    # an ordinary monitor found live and not yet mailed: pending (the worker mails it this tick)
    assert email_status(plain, MonitorState(), live)[0] == "◷ Email pending"
    # sent
    sent = TargetState(availability=Availability.AVAILABLE, notified_availability=Availability.AVAILABLE, notified_at=when)
    assert email_status(watch, MonitorState(), sent)[0] == "✓ Email sent · 9:20 PM"
    # failed, being retried
    failing = MonitorState(); failing.last_email_error = "SMTPAuthenticationError: bad password"
    assert email_status(plain, failing, live)[0].startswith("✗ Email failed")
    assert email_status(watch, failing, live)[0].startswith("✗ Email failed")


def test_a_watch_found_open_on_its_first_read_shows_the_email_as_sent(seeded_with_categories, signed_in, provider_factory, monkeypatch, later):
    from tests.test_app import run, text

    signed_in.admin = True
    watch = Monitor.from_dict({**_make_watch_dict(seeded_with_categories)})
    upsert_monitor(watch, mirror=False)
    monkeypatch.setattr("monitor.checker.get_provider", lambda slug: provider_factory([payload(ALLU_LIVE, REAL_CATEGORIES)] * 8))
    sent = []
    run_once(at=later(0), force=True, mirror=False, notifier=lambda m, c: sent.append(c))
    assert len(sent) == 1 and sent[0].already_open
    from ui import detail

    page = run()                                                   # Home's live card
    body = text(page)
    assert "Email sent" in body and "Email pending" not in body
    assert "GOLD · AREA 2 AVAILABLE" in body and "TICKETS ARE LIVE" not in body
    page = run(**{detail.OPEN_KEY: watch.id})                      # the Details dialog
    assert "Email sent" in text(page) and "Email pending" not in text(page)


def _make_watch_dict(movie_id):
    from monitor import catalogue

    movie = next(catalogue.movie_from_entry(e) for e in catalogue.list_entries("hyderabad")
                 if catalogue.movie_from_entry(e).id == movie_id)
    from datetime import datetime

    from config.timezone import IST

    m = Monitor(movie=movie, targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema")],
                notify_email="admin@example.com", owner_uid="uid-test-1", categories=["0000000002|GOLD"],
                monitor_until=datetime(2026, 9, 30, 23, 59, tzinfo=IST))
    return m.to_dict()


# ──────────────────────────────────────────────────────────────────────────
# 7 · Infinity Vision: BookMyShow's three spellings, one label — Marvel only
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("MS - Infinity Vision", "Infinity Vision 2D"), ("Ms - Infinity Vsn", "Infinity Vision 2D"),
    ("MS-Infinity Vision", "Infinity Vision 2D"), ("Ms-Infinity Vsn 3D", "Infinity Vision 3D"),
    ("MS - Infinity Vision 3D", "Infinity Vision 3D"), ("ms-infinity vsn 3d", "Infinity Vision 3D"),
    ("Dolby Cinema 3D", "Dolby Cinema 3D"), ("DOLBY CINEMA", "Dolby Cinema"), ("imax 2d", "IMAX 2D"),
    ("4DX 3D", "4DX 3D"), ("Barco Flagship Laser Dolby Atmos", "Barco Flagship Laser Dolby Atmos"),
    ("Vision", "Vision"), ("", ""),
])
def test_clean_format_canonicalises_infinity_vision_and_nothing_else(raw, expected):
    from platforms.bookmyshow import clean_format

    assert clean_format(raw) == expected


def test_infinity_vision_variants_and_shows_carry_the_label_and_the_raw_string(provider_factory, listing_url):
    from monitor.models import is_infinity_vision

    shows = [{**ALLU_LIVE[0], "venue_code": "PRHN", "venue_name": "Prasads Multiplex", "fmt": "Ms-Infinity Vsn 3d"}]
    provider = provider_factory([build_payload(shows)])
    snap = provider.resolve(listing_url)
    show = snap.shows_for("PRHN")[0]
    assert show.format_label == "Infinity Vision 3D" and show.format_raw == "Ms-Infinity Vsn 3d"
    assert is_infinity_vision(show.format_label) and not is_infinity_vision("Dolby Cinema 3D")
    assert snap.venue("PRHN").formats == ("Infinity Vision 3D",)
    # a target picked as "Infinity Vision 2D" does not match the 3D screen, and vice versa
    assert TheatreTarget("PRHN", "Prasads", "", "Infinity Vision 3D").matches_format(show.format_label)
    assert not TheatreTarget("PRHN", "Prasads", "", "Infinity Vision 2D").matches_format(show.format_label)


def test_marvel_is_recognised_by_title_only():
    from monitor.models import is_marvel_title

    assert is_marvel_title("Avengers Endgame: Encore") and is_marvel_title("Spider-Man: Brand New Day")
    assert is_marvel_title("Thor: Love and Thunder") and is_marvel_title("The Marvels")
    for other in ("The Paradise", "Mandaadi", "Author", "Ironman Triathlon Story", ""):
        assert not is_marvel_title(other), other


def test_the_formats_step_notes_infinity_vision_for_marvel_only(monkeypatch):
    from ui import flow
    from ui import catalogue_view as cv

    notes = []
    monkeypatch.setattr(flow.C, "html", lambda markup: notes.append(markup))
    monkeypatch.setattr(flow.st, "session_state", {"movie_id": "bookmyshow:ET1", "location": "hyderabad"})
    prasads = Venue("PRHN", "Prasads Multiplex", "Hyderabad", ("Infinity Vision 2D", "Infinity Vision 3D", "EPIQ"))
    allu = Venue("ALUC", "ALLU Cinemas", "Kokapet", ("Dolby Cinema",))

    monkeypatch.setattr(cv, "movie", lambda mid, slug: MovieRef("bookmyshow", "ET1", "Avengers Endgame: Encore", "HYD", "hyderabad"))
    flow.infinity_vision_note([prasads, allu])
    assert len(notes) == 1 and "Prasads Multiplex (2D, 3D)" in notes[0] and "ALLU" not in notes[0]
    notes.clear()
    flow.infinity_vision_note([allu])                              # Marvel, but no chosen theatre lists it
    assert notes == []
    monkeypatch.setattr(cv, "movie", lambda mid, slug: MovieRef("bookmyshow", "ET2", "The Paradise", "HYD", "hyderabad"))
    flow.infinity_vision_note([prasads])                           # not Marvel: nothing, whatever the theatre lists
    assert notes == []
