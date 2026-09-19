"""The Home card's "View details" dialog (``ui/detail.py``).

A view over the monitor and state the page already holds: the film row,
the schedule, every theatre × format and — for a live target — the very
booking link the alert email carried. It never reads the store itself and
never builds a URL.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from monitor.models import Availability
from monitor.state import MonitorState, save_state, upsert_monitor
from tests.test_app import run, text
from ui import components as C
from ui import detail

pytestmark = pytest.mark.usefixtures("signed_in")


def _live_state(at, url="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"):
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=14, success_count=13)
    live = state.target("ALLU::Dolby Cinema")
    live.availability = Availability.AVAILABLE
    live.since = at
    live.time_labels = ["07:30 PM", "09:45 PM"]
    live.time_links = [["07:30 PM", "https://in.bookmyshow.com/buytickets/show-1"]]
    live.date_code = "20260925"
    live.booking_url = url
    live.notified_at = at
    state.target("AMB::Any format").availability = Availability.NOT_BOOKABLE
    return state


# ──────────────────────────────────────────────────────────────────────────
# What the dialog says
# ──────────────────────────────────────────────────────────────────────────
def test_the_markup_carries_every_secondary_fact(make_monitor, at):
    from dataclasses import replace

    monitor = make_monitor(interval=15)
    monitor.movie = replace(monitor.movie, language="English")
    monitor.date_codes = ["20260925", "20260926"]
    body = detail.markup(monitor, _live_state(at), at=at)

    for expected in ("Avengers: Endgame Encore", "English", "25–26 Sep 2026",          # movie
                     "Every 15 min", "13 of 14", "Monitoring until", "26 Sep 2026",    # monitoring
                     "Allu Cinemas", "Dolby Cinema", "AMB Cinemas", "Any format"):     # every target + format
        assert expected in body, expected
    assert "Tickets available" in body and "Not released yet" in body               # per-target answers
    assert len(re.findall(r'<div class="tr-det-tgt(?: ok)?">', body)) == 2


def test_a_live_target_gets_the_emails_booking_link_and_nothing_else_does(make_monitor, at):
    from monitor.changes import Change, ChangeKind
    from notifications import email as mail

    monitor = make_monitor()
    state = _live_state(at)
    live = state.target("ALLU::Dolby Cinema")
    body = detail.markup(monitor, state, at=at)

    # One BOOK button, and its href is the stored link — the same value the
    # notification renders, so the dialog and the email always agree.
    hrefs = re.findall(r'class="tr-book tr-det-book" href="([^"]+)"', body)
    assert hrefs == [live.booking_url]
    change = Change(kind=ChangeKind.TICKETS_LIVE, monitor_id=monitor.id, target_key="ALLU::Dolby Cinema",
                    venue_name="Allu Cinemas", fmt="Dolby Cinema", movie_title=monitor.movie.title,
                    previous=Availability.NOT_BOOKABLE, current=Availability.AVAILABLE, date_code="20260925",
                    booking_url=live.booking_url, time_labels=live.time_labels, time_links=live.time_links,
                    detected_at=at)
    _, html, _ = mail.render_change(monitor, change)
    assert live.booking_url in html
    # showtime chips link where the email's do: the show link when there is one
    assert 'href="https://in.bookmyshow.com/buytickets/show-1"' in body
    assert body.count('class="tr-chip"') == 2
    # the not-yet-released theatre shows a state, never a broken button
    assert "No booking link yet" in body
    assert body.count("BOOK ON BOOKMYSHOW") == 1


def test_a_link_that_is_not_bookmyshow_is_never_rendered(make_monitor, at):
    monitor = make_monitor()
    body = detail.markup(monitor, _live_state(at, url="http://evil.example/steal"), at=at)
    assert "evil.example" not in body
    assert "BOOK ON BOOKMYSHOW" not in body
    assert "Booking link not available yet" in body


def test_a_stopped_monitor_reads_as_finished(make_monitor, at):
    monitor = make_monitor()
    monitor.stop(at)
    body = detail.markup(monitor, MonitorState(last_check_at=at, check_count=3, success_count=3), at=at)
    assert "Stopped" in body and "Not being checked" in body
    assert "BOOK ON BOOKMYSHOW" not in body and "Next check" in body


# ──────────────────────────────────────────────────────────────────────────
# Where it lives on the page
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_offers_view_details_and_the_dialog_draws_from_page_data(make_monitor, at):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    save_state({monitor.id: _live_state(at)}, mirror=False)

    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    button = app.button(key=f"detail_{monitor.id}")
    assert button.label == "View details"
    assert "tr-det-head" not in text(app)                     # nothing drawn until asked

    button.click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state[detail.OPEN_KEY] == monitor.id
    body = text(app)
    assert "tr-det-head" in body and "BOOK ON BOOKMYSHOW" in body
    assert "AMB Cinemas" in body and "Monitoring until" in body

    app.button(key="close_detail").click().run()
    assert detail.OPEN_KEY not in app.session_state
    assert "tr-det-head" not in text(app)


def test_the_dialog_is_a_view_over_loaded_data_not_a_second_read():
    src = Path("ui/detail.py").read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]                              # past the module docstring
    assert "load_" not in code and "state_store" not in code and "firestore" not in code.lower()
    assert "is_bookmyshow_url" in src                          # the email's own test for a link
    assert "https://" not in code                             # no URL is ever built here

    import app

    rail = inspect.getsource(app.live_monitor_panel)
    assert "on_click=detail.open_detail" in rail
    assert "detail.dialog(monitors, states)" in inspect.getsource(app.page_home)
    assert 'st.rerun(scope="app")' in inspect.getsource(detail.open_detail)   # a fragment asks for the app
    assert "on_dismiss=close" in inspect.getsource(detail.dialog)              # the X clears the key


def test_the_dialog_stylesheet_is_small_and_folds_on_a_phone():
    assert detail.CSS.startswith("<style>") and len(detail.CSS) < 10000
    assert "@media (max-width: 768px)" in detail.CSS
    assert ".tr-det-grid { grid-template-columns:1fr; gap:12px; }" in detail.CSS
    assert "[class*=\"st-key-detail_\"] .stButton button" in __import__("ui.theme", fromlist=["CSS"]).CSS
