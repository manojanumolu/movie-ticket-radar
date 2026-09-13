"""End-to-end UI tests through Streamlit's own harness.

``AppTest`` executes ``app.py`` exactly as the server does, so anything that
would throw in the browser throws here. These drive the whole location-first
wizard: location → movie → theatres → formats → monitoring → start.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from config.timezone import now_ist
from monitor import catalogue
from monitor.models import ANY_FORMAT, Availability, MonitorStatus
from monitor.state import MonitorState, get_monitor, load_monitors, save_state, upsert_monitor

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = "app.py"
TIMEOUT = 60


def run(page: str = "Home", **session):
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.session_state["page"] = page
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def text(app) -> str:
    return " ".join(
        [m.value for m in app.markdown]
        + [c.value for c in app.caption]
        + [w.value for w in app.warning]
    )


@pytest.fixture
def seeded(provider_factory, monkeypatch):
    """A synced Hyderabad catalogue with theatre/format detail."""
    from tests.conftest import ALLU_LIVE, QUICKBOOK_HYD, build_payload

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * 3)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad")
                 if catalogue.movie_from_entry(e).title == "Mandaadi")
    return catalogue.movie_from_entry(entry).id


# ──────────────────────────────────────────────────────────────────────────
# It starts, and every page renders
# ──────────────────────────────────────────────────────────────────────────
def test_app_starts_without_exceptions():
    app = run()
    assert not app.exception, [str(e) for e in app.exception]


@pytest.mark.parametrize("page", ["Home", "My Monitors", "History", "Settings"])
def test_every_page_renders(page):
    app = run(page)
    assert not app.exception, [str(e) for e in app.exception]


def test_coming_soon_platforms_are_not_claimed_as_working():
    body = text(run())
    assert body.count("Coming soon") >= 3
    assert "District" in body and "PVR Cinemas" in body


# ──────────────────────────────────────────────────────────────────────────
# The hard requirement: no URL anywhere in the user's flow
# ──────────────────────────────────────────────────────────────────────────
def test_no_url_input_anywhere_in_the_flow(seeded):
    """The user must never be asked for a BookMyShow link or an ET code."""
    for page in ("Home", "My Monitors", "History", "Settings"):
        app = run(page)
        assert not app.exception
        body = text(app)
        for banned in ("in.bookmyshow.com", "Paste", "paste", "Book tickets",
                       "Resolve movie", "ET00", "event code"):
            assert banned not in body, f"{banned!r} leaked onto the {page} page"
        for widget in app.text_input:
            placeholder = (widget.placeholder or "") + (widget.label or "")
            assert "bookmyshow" not in placeholder.lower()
            assert "url" not in placeholder.lower()
        assert not any("resolve" in (b.key or "").lower() for b in app.button)


# ──────────────────────────────────────────────────────────────────────────
# Test 1 — location is the first step
# ──────────────────────────────────────────────────────────────────────────
def test_location_is_the_first_step():
    app = run()
    body = text(app)
    assert "Where are you watching?" in body
    assert "Hyderabad" in body
    assert "Telangana" in body
    # The step strip names the flow in order.
    for label in ("Location", "Movie", "Theatres", "Formats", "Monitoring"):
        assert label in body
    assert any(b.key == "loc_hyderabad" for b in app.button)


def test_choosing_hyderabad_advances_to_the_movie_step(seeded):
    app = run()
    app.button(key="loc_hyderabad").click().run()
    assert not app.exception
    assert app.session_state["location"] == "hyderabad"
    assert app.session_state["step"] == 2
    assert "Select movie" in text(app)


def test_other_cities_are_shown_as_coming_soon():
    body = text(run())
    assert "Bengaluru" in body or "Chennai" in body


# ──────────────────────────────────────────────────────────────────────────
# Test 2/3 — real catalogue, movie selection
# ──────────────────────────────────────────────────────────────────────────
def test_movie_step_lists_the_synced_catalogue(seeded):
    app = run(step=2, location="hyderabad")
    assert not app.exception
    body = text(app)
    assert "Mandaadi" in body
    assert "Hanuman Ansh" in body
    assert "Catalogue loaded" in body
    assert any(b.key.startswith("movie_bookmyshow:") for b in app.button)


def test_movie_search_filters(seeded):
    app = run(step=2, location="hyderabad", movie_query="hanuman")
    body = text(app)
    assert "Hanuman Ansh" in body
    assert "1 movie(s)" in body


def test_selecting_a_movie_advances_to_theatres(seeded):
    app = run(step=2, location="hyderabad")
    app.button(key=f"movie_{seeded}").click().run()
    assert not app.exception
    assert app.session_state["movie_id"] == seeded
    assert app.session_state["step"] == 3


def test_a_blocked_catalogue_never_reads_as_no_movies(provider_factory, monkeypatch):
    from tests.conftest import FakeResponse

    provider = provider_factory([FakeResponse(403)] * 3)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=False)

    body = text(run(step=2, location="hyderabad"))
    assert "reach BookMyShow" in body
    assert "bot check refused the request" in body
    assert "No movies listed right now" not in body


def test_an_empty_city_says_so_plainly(provider_factory, monkeypatch):
    from tests.conftest import build_quickbook

    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider_factory([build_quickbook([])]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)

    body = text(run(step=2, location="hyderabad"))
    assert "No movies listed right now" in body
    assert "bot check refused the request" not in body


# ──────────────────────────────────────────────────────────────────────────
# Test 4/5 — theatres, multiple selection
# ──────────────────────────────────────────────────────────────────────────
def test_theatre_step_shows_real_theatres(seeded):
    app = run(step=3, location="hyderabad", movie_id=seeded)
    assert not app.exception
    body = text(app)
    assert "Allu Cinemas" in body and "AMB Cinemas" in body
    assert "Attapur, Hyderabad" in body      # area split out of the venue name
    assert {b.key for b in app.button if b.key.startswith("th_")} == {"th_ALLU", "th_AMB"}


def test_multiple_theatres_can_be_selected(seeded):
    app = run(step=3, location="hyderabad", movie_id=seeded)
    app.button(key="th_ALLU").click().run()
    app.button(key="th_AMB").click().run()
    assert not app.exception
    assert set(app.session_state["theatres"]) == {"ALLU", "AMB"}
    assert "2 theatre(s) selected" in text(app)


def test_select_all_picks_every_theatre(seeded):
    app = run(step=3, location="hyderabad", movie_id=seeded)
    app.button(key="select_all").click().run()
    assert set(app.session_state["theatres"]) == {"ALLU", "AMB"}


def test_a_movie_with_no_theatres_explains_itself(provider_factory, monkeypatch):
    from tests.conftest import QUICKBOOK_HYD, build_payload

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload([])] * 3)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    movie_id = catalogue.movie_from_entry(catalogue.list_entries("hyderabad")[0]).id

    body = text(run(step=3, location="hyderabad", movie_id=movie_id))
    assert "hasn't published any theatres" in body


# ──────────────────────────────────────────────────────────────────────────
# Test 6 — per-theatre formats, and they must not bleed across theatres
# ──────────────────────────────────────────────────────────────────────────
def test_formats_are_listed_per_theatre(seeded):
    app = run(step=4, location="hyderabad", movie_id=seeded, theatres=["ALLU", "AMB"])
    assert not app.exception
    keys = {c.key for c in app.checkbox}
    # Each theatre's formats are namespaced by its own venue code.
    assert "fmt_ALLU_Dolby Cinema" in keys
    assert "fmt_AMB_HDR By Barco" in keys
    # ...and a theatre is never offered a format it does not run.
    assert "fmt_ALLU_HDR By Barco" not in keys
    assert "fmt_AMB_Dolby Cinema" not in keys


def test_selecting_one_theatres_format_does_not_select_anothers(seeded):
    app = run(step=4, location="hyderabad", movie_id=seeded, theatres=["ALLU", "AMB"])
    app.checkbox(key="fmt_AMB_HDR By Barco").check().run()
    assert not app.exception
    formats = app.session_state["formats"]
    assert formats["AMB"] == ["HDR By Barco"]
    assert formats.get("ALLU", []) == []
    assert "Pick at least one format for: Allu Cinemas" in text(app)


def test_any_format_overrides_the_specific_picks(seeded):
    app = run(step=4, location="hyderabad", movie_id=seeded, theatres=["ALLU"])
    app.checkbox(key="fmt_ALLU_Dolby Cinema").check().run()
    app.checkbox(key="fmt_ALLU_any").check().run()
    assert app.session_state["formats"]["ALLU"] == [ANY_FORMAT]


# ──────────────────────────────────────────────────────────────────────────
# Test 7/8 — monitoring settings and creating a monitor
# ──────────────────────────────────────────────────────────────────────────
def test_monitoring_step_offers_the_three_intervals(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded,
              theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]})
    assert not app.exception
    body = text(app)
    assert "Check frequency" in body and "Monitor until" in body
    assert "Notification email" in body
    assert any(b.key == "start" for b in app.button)


def test_start_monitoring_creates_one_target_per_theatre_format(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded,
              theatres=["ALLU", "AMB"],
              formats={"ALLU": ["Dolby Cinema"], "AMB": ["HDR By Barco", ANY_FORMAT]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]

    monitors = load_monitors()
    assert len(monitors) == 1
    monitor = monitors[0]
    assert monitor.movie.title == "Mandaadi"
    assert monitor.status is MonitorStatus.ACTIVE
    assert monitor.notify_email == "me@example.com"
    assert sorted(t.key for t in monitor.targets) == [
        "ALLU::Dolby Cinema", "AMB::Any format", "AMB::HDR By Barco",
    ]
    # The area survives into the monitor, split out of the venue name.
    assert monitor.target("ALLU::Dolby Cinema").area == "Attapur, Hyderabad"


def test_start_is_blocked_without_an_email(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded,
              theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    app.text_input(key="notify_email").set_value("").run()
    app.button(key="start").click().run()
    assert load_monitors() == []
    assert any("notification email" in w.value for w in app.warning)


def test_changing_the_movie_clears_theatres_and_formats(seeded):
    entries = catalogue.list_entries("hyderabad")
    other = catalogue.movie_from_entry(entries[1]).id
    app = run(step=2, location="hyderabad", movie_id=seeded,
              theatres=["ALLU"], formats={"ALLU": ["Dolby Cinema"]})
    app.button(key=f"movie_{other}").click().run()
    assert app.session_state["theatres"] == []
    assert app.session_state["formats"] == {}


# ──────────────────────────────────────────────────────────────────────────
# Active monitoring, stopping, expiry
# ──────────────────────────────────────────────────────────────────────────
def test_active_monitoring_state_is_displayed(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=14)
    state.target("ALLU::Dolby Cinema").availability = Availability.NOT_BOOKABLE
    state.target("AMB::Any format").availability = Availability.NOT_BOOKABLE
    save_state({monitor.id: state}, mirror=False)

    body = text(run())
    assert "Active monitoring" in body
    assert "MONITORING ACTIVE" in body
    assert "Avengers: Endgame Encore" in body
    assert "10 minutes" in body
    assert "26 Sep 2026, 11:59 PM" in body
    assert "Allu Cinemas" in body and "AMB Cinemas" in body


def test_stop_button_writes_the_stopped_state(make_monitor):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    app = run()
    app.button(key=f"stop_{monitor.id}").click().run()
    assert not app.exception
    stored = get_monitor(monitor.id)
    assert stored.status is MonitorStatus.STOPPED
    assert stored.is_running() is False
    assert "Monitoring stopped" in text(run("My Monitors"))


def test_a_past_end_time_expires_on_render(make_monitor):
    monitor = make_monitor(until=now_ist() - timedelta(minutes=5))
    upsert_monitor(monitor, mirror=False)
    app = run()
    assert not app.exception
    assert get_monitor(monitor.id).status is MonitorStatus.EXPIRED
    body = text(run("My Monitors"))
    assert "Monitoring expired" in body and "Nothing went wrong" in body


def test_expired_monitor_can_be_extended(make_monitor):
    monitor = make_monitor(until=now_ist() - timedelta(minutes=5))
    upsert_monitor(monitor, mirror=False)
    run()
    app = run("My Monitors")
    app.button(key=f"m_ext_{monitor.id}").click().run()
    assert get_monitor(monitor.id).status is MonitorStatus.ACTIVE


# ──────────────────────────────────────────────────────────────────────────
# The states that must never be confused
# ──────────────────────────────────────────────────────────────────────────
def test_a_failed_check_never_renders_as_no_tickets(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: MonitorState(
        last_check_at=at, last_success_at=at - timedelta(minutes=10),
        check_count=5, success_count=3, consecutive_errors=2,
        last_error="BookMyShow refused the request (HTTP 403).",
    )}, mirror=False)

    body = text(run())
    assert "Couldn't check BookMyShow" in body
    assert "Last successful check" in body
    assert "Tickets are unavailable" not in body


def test_a_live_target_shows_the_booking_link(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=14)
    live = state.target("ALLU::Dolby Cinema")
    live.availability = Availability.AVAILABLE
    live.since = at
    live.time_labels = ["07:30 PM", "09:45 PM"]
    live.date_code = "20260925"
    live.booking_url = "https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"
    live.notified_at = at
    state.target("AMB::Any format").availability = Availability.NOT_BOOKABLE
    save_state({monitor.id: state}, mirror=False)

    app = run()
    assert not app.exception
    body = text(app)
    assert "TICKETS ARE LIVE" in body
    assert "BOOK ON BOOKMYSHOW" in body
    assert live.booking_url in body
    assert "25 September 2026" in body
    assert "07:30 PM" in body and "09:45 PM" in body
    assert "AMB Cinemas" in body           # the other theatre is still watched
    assert any("doesn't stop the others" in c.value for c in app.caption)


def test_settings_page_never_prints_a_credential(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "supersecretpw123")
    app = run("Settings")
    assert not app.exception
    assert "supersecretpw123" not in text(app)


def test_history_page_lists_events(make_monitor):
    from monitor.state import record_history

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    record_history(monitor, "TICKETS_LIVE", "Allu Cinemas · Dolby Cinema", mirror=False)
    body = text(run("History"))
    assert "Avengers: Endgame Encore" in body
    assert "Tickets found" in body
