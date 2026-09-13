"""End-to-end UI tests through Streamlit's own harness.

``AppTest`` executes ``app.py`` exactly as the server does, so anything that
would throw in the browser throws here. These are the tests that answer
"does it actually start, and can I actually drive it?" rather than
"do the functions return the right thing".
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from config.timezone import now_ist
from monitor.models import ANY_FORMAT, Availability, MonitorStatus, TheatreTarget
from monitor.state import (
    MonitorState,
    get_monitor,
    load_monitors,
    save_state,
    upsert_monitor,
)

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = "app.py"
TIMEOUT = 60


def run(page: str = "Home", **session):
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.session_state["page"] = page
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def seed_catalogue(provider_factory, listing_url, monkeypatch, shows=None):
    from monitor import catalogue
    from tests.conftest import ALLU_LIVE, build_payload

    provider = provider_factory([build_payload(shows or ALLU_LIVE)])
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    return catalogue.resolve_url(listing_url, mirror=False)


# ──────────────────────────────────────────────────────────────────────────
# Test 1 — it starts
# ──────────────────────────────────────────────────────────────────────────
def test_app_starts_without_exceptions():
    app = run()
    assert not app.exception, [str(e) for e in app.exception]


@pytest.mark.parametrize("page", ["Home", "My Monitors", "History", "Settings"])
def test_every_page_renders(page):
    app = run(page)
    assert not app.exception, [str(e) for e in app.exception]


def test_empty_state_is_shown_when_nothing_is_configured():
    app = run()
    body = " ".join(m.value for m in app.markdown)
    assert "No active monitors" in body
    assert "Movie Ticket Monitor" in body


def test_coming_soon_platforms_are_not_claimed_as_working():
    body = " ".join(m.value for m in run().markdown)
    assert body.count("Coming soon") == 3          # District, PVR, Cinépolis
    assert "District" in body and "PVR Cinemas" in body


# ──────────────────────────────────────────────────────────────────────────
# Tests 2, 3, 4 — selecting a movie, theatres and per-theatre formats
# ──────────────────────────────────────────────────────────────────────────
def test_movie_selection_renders_the_catalogue(provider_factory, listing_url, monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run()
    assert not app.exception
    body = " ".join(m.value for m in app.markdown)
    assert "Avengers: Endgame Encore" in body
    assert any(b.key == "pick_bookmyshow:ET00478890" for b in app.button)


def test_selecting_a_movie_reveals_its_theatres(provider_factory, listing_url, monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run(movie_id="bookmyshow:ET00478890")
    assert not app.exception
    keys = {c.key for c in app.checkbox}
    assert keys == {"th_ALLU", "th_AMB"}


def test_multiple_theatres_can_be_selected(provider_factory, listing_url, monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run(movie_id="bookmyshow:ET00478890")
    app.checkbox(key="th_ALLU").check().run()
    app.checkbox(key="th_AMB").check().run()
    assert not app.exception
    assert set(app.session_state["theatres"]) == {"ALLU", "AMB"}


def test_each_selected_theatre_gets_its_own_format_control(provider_factory, listing_url,
                                                           monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run(movie_id="bookmyshow:ET00478890", theatres=["ALLU", "AMB"])
    assert not app.exception

    radios = {r.key: r for r in app.radio}
    assert "fmt_ALLU" in radios and "fmt_AMB" in radios
    # "Any format" first, then only what that theatre actually runs.
    assert radios["fmt_ALLU"].options == [ANY_FORMAT, "Dolby Cinema"]
    assert radios["fmt_AMB"].options == [ANY_FORMAT, "HDR By Barco"]

    radios["fmt_ALLU"].set_value("Dolby Cinema").run()
    assert app.session_state["formats"]["ALLU"] == "Dolby Cinema"


def test_select_all_picks_every_theatre(provider_factory, listing_url, monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run(movie_id="bookmyshow:ET00478890")
    app.button(key="select_all").click().run()
    assert set(app.session_state["theatres"]) == {"ALLU", "AMB"}


# ──────────────────────────────────────────────────────────────────────────
# Tests 5, 6 — saving a monitor and seeing it
# ──────────────────────────────────────────────────────────────────────────
def test_start_monitoring_persists_a_monitor(provider_factory, listing_url, monkeypatch):
    from config.store import save_settings

    seed_catalogue(provider_factory, listing_url, monkeypatch)
    save_settings({"notify_email": "me@example.com", "default_interval": 10}, mirror=False)

    app = run(
        movie_id="bookmyshow:ET00478890",
        theatres=["ALLU", "AMB"],
        formats={"ALLU": "Dolby Cinema", "AMB": ANY_FORMAT},
    )
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]

    monitors = load_monitors()
    assert len(monitors) == 1
    monitor = monitors[0]
    assert monitor.movie.title == "Avengers: Endgame Encore"
    assert {t.venue_code: t.fmt for t in monitor.targets} == {
        "ALLU": "Dolby Cinema", "AMB": ANY_FORMAT,
    }
    assert monitor.notify_email == "me@example.com"
    assert monitor.status is MonitorStatus.ACTIVE


def test_start_is_blocked_without_an_email(provider_factory, listing_url, monkeypatch):
    seed_catalogue(provider_factory, listing_url, monkeypatch)
    app = run(movie_id="bookmyshow:ET00478890", theatres=["ALLU"], formats={"ALLU": ANY_FORMAT})
    app.button(key="start").click().run()
    assert load_monitors() == []
    assert any("Settings" in w.value for w in app.warning)


def test_active_monitoring_state_is_displayed(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=14)
    state.target("ALLU::Dolby Cinema").availability = Availability.NOT_BOOKABLE
    state.target("AMB::Any format").availability = Availability.NOT_BOOKABLE
    save_state({monitor.id: state}, mirror=False)

    body = " ".join(m.value for m in run().markdown)
    assert "MONITORING ACTIVE" in body
    assert "Avengers: Endgame Encore" in body
    assert "BookMyShow · Hyderabad" in body
    assert "10:20 PM" in body               # last checked
    assert "10 minutes" in body             # configured interval
    assert "26 Sep 2026, 11:59 PM" in body  # monitoring until
    assert "checked 14 times" in body
    assert "Allu Cinemas" in body and "AMB Cinemas" in body


# ──────────────────────────────────────────────────────────────────────────
# Test 7 — stopping from the UI changes persisted state
# ──────────────────────────────────────────────────────────────────────────
def test_stop_button_writes_the_stopped_state(make_monitor):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)

    app = run()
    app.button(key=f"stop_{monitor.id}").click().run()
    assert not app.exception

    stored = get_monitor(monitor.id)
    assert stored.status is MonitorStatus.STOPPED
    assert stored.is_running() is False

    body = " ".join(m.value for m in run("My Monitors").markdown)
    assert "Monitoring stopped" in body


# ──────────────────────────────────────────────────────────────────────────
# Test 8 — expiry, in the UI
# ──────────────────────────────────────────────────────────────────────────
def test_a_past_end_time_expires_on_render(make_monitor):
    monitor = make_monitor(until=now_ist() - timedelta(minutes=5))
    upsert_monitor(monitor, mirror=False)

    app = run()
    assert not app.exception
    assert get_monitor(monitor.id).status is MonitorStatus.EXPIRED

    body = " ".join(m.value for m in run("My Monitors").markdown)
    assert "Monitoring expired" in body
    assert "Nothing went wrong" in body


def test_expired_monitor_can_be_extended(make_monitor):
    monitor = make_monitor(until=now_ist() - timedelta(minutes=5))
    upsert_monitor(monitor, mirror=False)
    run()  # triggers expiry

    app = run("My Monitors")
    app.button(key=f"m_ext_{monitor.id}").click().run()
    assert get_monitor(monitor.id).status is MonitorStatus.ACTIVE


# ──────────────────────────────────────────────────────────────────────────
# The states that must never be confused
# ──────────────────────────────────────────────────────────────────────────
def test_a_failed_check_never_renders_as_no_tickets(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    state = MonitorState(
        last_check_at=at, last_success_at=at - timedelta(minutes=10),
        check_count=5, success_count=3, consecutive_errors=2,
        last_error="BookMyShow refused the request (HTTP 403).",
    )
    save_state({monitor.id: state}, mirror=False)

    body = " ".join(m.value for m in run().markdown)
    assert "Couldn't check BookMyShow" in body
    assert "not a &quot;no tickets&quot; answer" in body or 'not a "no tickets" answer' in body
    assert "Last successful check" in body
    assert "Tickets are unavailable" not in body
    assert "Sold out" not in body


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
    body = " ".join(m.value for m in app.markdown)
    assert "TICKETS ARE LIVE" in body
    assert "BOOK ON BOOKMYSHOW" in body
    assert live.booking_url in body
    assert "25 September 2026" in body
    assert "07:30 PM" in body and "09:45 PM" in body
    assert "✓ Email sent" in body
    # The other theatre is still being watched, not silenced.
    assert "AMB Cinemas" in body
    assert any("doesn't stop the others" in c.value for c in app.caption)


def test_settings_page_never_prints_a_credential(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "supersecretpw123")
    app = run("Settings")
    assert not app.exception
    rendered = " ".join(
        [m.value for m in app.markdown] + [c.value for c in app.caption]
    )
    assert "supersecretpw123" not in rendered
    assert "me@gmail.com" not in rendered


def test_history_page_lists_events(make_monitor):
    from monitor.state import record_history

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    record_history(monitor, "TICKETS_LIVE", "Allu Cinemas · Dolby Cinema", mirror=False)

    body = " ".join(m.value for m in run("History").markdown)
    assert "Avengers: Endgame Encore" in body
    assert "Tickets found" in body
