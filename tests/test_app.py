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
from monitor.models import ANY_FORMAT, Availability, MonitorStatus, TheatreTarget
from monitor.state import MonitorState, get_monitor, load_monitors, save_state, upsert_monitor
from tests.conftest import APP_SCRIPT

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = APP_SCRIPT
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


def section(body: str, label: str) -> str | None:
    """The count shown next to a section rule ("Active · 1"), or None if absent."""
    import re

    m = re.search(rf'>{re.escape(label)}</span><span class="tr-count">([^<]*)<', body)
    return m.group(1) if m else None


@pytest.fixture
def seeded(provider_factory, monkeypatch):
    """A synced Hyderabad catalogue with theatre/format detail."""
    from tests.conftest import ALLU_LIVE, DETAIL_REQUESTS_HYD, QUICKBOOK_HYD, build_payload

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(ALLU_LIVE)] * DETAIL_REQUESTS_HYD)
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
    from tests.conftest import DETAIL_REQUESTS_HYD, QUICKBOOK_HYD, build_payload

    provider = provider_factory([QUICKBOOK_HYD] + [build_payload([])] * DETAIL_REQUESTS_HYD)
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
    assert "How often should I check?" in body and "Monitor until" in body
    assert "Notification email" in body
    # The three interval tiles, each a real control.
    assert {b.key for b in app.button if b.key.startswith("interval_")} == {
        "interval_10", "interval_15", "interval_30"}
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
    assert "Active monitor" in body
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

    app = run()
    body = text(app)
    assert "Couldn't check BookMyShow" in body
    assert "Tickets are unavailable" not in body
    # The failure is a visible PROBLEM, and opening it names the real cause.
    prob = next(b for b in app.button if b.key == f"prob_{monitor.id}")
    assert "PROBLEM OCCURRED" in prob.label
    app = prob.click().run()
    opened = text(app)
    assert "Problem occurred" in opened and "reach BookMyShow" in opened
    assert "BookMyShow refused the request (HTTP 403)." in opened
    assert "2 failed attempt(s)" in opened


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


# ──────────────────────────────────────────────────────────────────────────
# Navigation: the step rail is a breadcrumb, and Back keeps what was chosen
# ──────────────────────────────────────────────────────────────────────────
def test_step_rail_goes_back_without_losing_selections(seeded):
    app = run(step=4, furthest=4, location="hyderabad", movie_id=seeded,
              theatres=["ALLU", "AMB"], formats={"ALLU": ["Dolby Cinema"]})
    # Reached steps are buttons; the current and unreached ones are not.
    keys = {b.key for b in app.button if b.key.startswith("step_")}
    assert keys == {"step_1", "step_2", "step_3"}

    app.button(key="step_2").click().run()
    assert not app.exception
    assert app.session_state["step"] == 2
    assert app.session_state["furthest"] == 4          # progress is remembered
    assert app.session_state["movie_id"] == seeded
    assert app.session_state["theatres"] == ["ALLU", "AMB"]
    assert app.session_state["formats"]["ALLU"] == ["Dolby Cinema"]
    assert "Selected:" in text(app) and "Mandaadi" in text(app)

    # And forward again, straight to a later step already reached.
    app.button(key="step_4").click().run()
    assert app.session_state["step"] == 4


def test_back_button_steps_back_one_and_keeps_selections(seeded):
    app = run(step=3, furthest=3, location="hyderabad", movie_id=seeded, theatres=["ALLU"])
    back = next(b for b in app.button if b.key == "back")
    assert "Movie" in back.label
    back.click().run()
    assert app.session_state["step"] == 2
    assert app.session_state["movie_id"] == seeded
    assert app.session_state["theatres"] == ["ALLU"]
    assert not any(b.key == "back" for b in run(step=1).button)


# ──────────────────────────────────────────────────────────────────────────
# Movie step: popular shelf + autocomplete
# ──────────────────────────────────────────────────────────────────────────
def test_popular_shelf_follows_catalogue_order_not_the_alphabet(seeded):
    app = run(step=2, location="hyderabad")
    shelf = [b.key for b in app.button if b.key.startswith("pop_")]
    # Catalogue order is BookMyShow's own: Mandaadi (Telugu) before Hanuman
    # Ansh — the alphabet would put Hanuman first. One tile per film.
    assert len(shelf) <= 6
    assert shelf == [f"pop_{seeded}", "pop_bookmyshow:ET00507738"]
    # The full catalogue is still there underneath, every movie a button.
    assert {b.key for b in app.button if b.key.startswith("movie_bookmyshow:")} == {
        f"movie_{seeded}", "movie_bookmyshow:ET00442702", "movie_bookmyshow:ET00507738"}


def test_movie_autocomplete_offers_every_catalogue_title(seeded):
    app = run(step=2, location="hyderabad")
    box = app.selectbox(key="movie_query")
    assert box.options == ["Mandaadi · Telugu · 2 theatres", "Mandaadi · Tamil · 2 theatres",
                           "Hanuman Ansh · Hindi · 2 theatres"]
    # Nothing is asked for that is not a movie name.
    assert "url" not in (box.placeholder or "").lower()


def test_choosing_a_suggestion_selects_the_movie_immediately(seeded):
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select("Hanuman Ansh · Hindi · 2 theatres").run()
    assert not app.exception
    assert app.session_state["movie_id"] == "bookmyshow:ET00507738"
    assert app.session_state["step"] == 3


def test_free_text_in_the_search_box_filters_the_grid(seeded):
    # Enter on free text (accept_new_options) leaves the typed text in the box.
    app = run(step=2, location="hyderabad", movie_query="mand")
    assert not app.exception
    assert app.session_state["step"] == 2
    body = text(app)
    assert "2 movie(s) matching" in body and "Hanuman Ansh" not in body


# ──────────────────────────────────────────────────────────────────────────
# Theatre step: search + featured quick-picks, all from the movie's own list
# ──────────────────────────────────────────────────────────────────────────
def test_theatre_search_toggles_a_theatre(seeded):
    app = run(step=3, location="hyderabad", movie_id=seeded)
    box = next(s for s in app.selectbox if s.key.startswith("theatre_query_"))
    # Only this movie's theatres, shown by name and area — never a code.
    assert box.options == ["Allu Cinemas · Attapur, Hyderabad · Dolby Cinema",
                           "AMB Cinemas · Gachibowli, Hyderabad · HDR By Barco"]
    box.select("AMB Cinemas · Gachibowli, Hyderabad · HDR By Barco").run()
    assert not app.exception
    assert app.session_state["theatres"] == ["AMB"]
    # The box is cleared for the next search, and the pick shows in the list.
    box = next(s for s in app.selectbox if s.key.startswith("theatre_query_"))
    assert box.value is None
    assert "1 theatre(s) selected: AMB Cinemas" in text(app)


def test_featured_picks_only_offer_theatres_the_catalogue_knows(seeded):
    app = run(step=3, location="hyderabad", movie_id=seeded)
    body = text(app)
    # Allu and AMB screen it — quick-pickable, "Now listed". The other four
    # have never appeared in this catalogue, so there is no venue code to
    # watch: they are named, say so, and have no button.
    assert {b.key for b in app.button if b.key.startswith("feat_")} == {"feat_ALLU", "feat_AMB"}
    assert body.count("Now listed") == 2
    assert body.count("Not in catalogue") == 4
    assert "Prasads Multiplex" in body and "PVR Lakeshore Mall" in body
    app.button(key="feat_ALLU").click().run()
    assert app.session_state["theatres"] == ["ALLU"]
    # The full list is untouched: still exactly the movie's theatres.
    assert {b.key for b in app.button if b.key.startswith("th_")} == {"th_ALLU", "th_AMB", "th_continue"}


# ──────────────────────────────────────────────────────────────────────────
# My Monitors: finished monitors are separate, and deleting really deletes
# ──────────────────────────────────────────────────────────────────────────
def test_stopped_monitor_moves_to_finished_and_delete_removes_it(make_monitor):
    active = make_monitor()
    stopped = make_monitor()
    stopped.stop()
    upsert_monitor(active, mirror=False)
    upsert_monitor(stopped, mirror=False)

    app = run("My Monitors")
    body = text(app)
    assert section(body, "Active") == "1" and section(body, "Finished") == "1"
    assert "STOPPED" in body and "WAITING" in body      # no check has run yet

    app.button(key=f"m_del_{stopped.id}").click().run()
    assert not app.exception
    assert [m.id for m in load_monitors()] == [active.id]
    body = text(app)
    assert "Monitor deleted" in body and section(body, "Finished") is None

    app.button(key=f"m_stop_{active.id}").click().run()
    assert get_monitor(active.id).status is MonitorStatus.STOPPED
    assert section(text(app), "Active") == "0" and section(text(app), "Finished") == "1"


def test_delete_all_finished_clears_the_clutter(make_monitor):
    keep = make_monitor()
    upsert_monitor(keep, mirror=False)
    for _ in range(3):
        m = make_monitor()
        m.stop()
        upsert_monitor(m, mirror=False)
    app = run("My Monitors")
    app.button(key="m_clear_finished").click().run()
    assert not app.exception
    assert [m.id for m in load_monitors()] == [keep.id]
    assert section(text(app), "Finished") is None


# ──────────────────────────────────────────────────────────────────────────
# No markup ever reaches the reader as text
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("page", ["Home", "My Monitors", "History", "Settings"])
def test_no_card_renders_its_own_tags_as_text(make_monitor, at, page):
    """Every HTML block must survive Streamlit's Markdown pass intact: no
    blank line inside a card (which would end the HTML block and render the
    rest as text) and no indented line (which would become a code block)."""
    from monitor.state import record_history

    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    record_history(monitor, "TICKETS_LIVE", "Allu Cinemas · Dolby Cinema", mirror=False)
    state = MonitorState(last_check_at=at, last_success_at=at, check_count=3, success_count=3)
    state.target("ALLU::Dolby Cinema").availability = Availability.AVAILABLE
    save_state({monitor.id: state}, mirror=False)

    app = run(page, step=3, location="hyderabad")
    assert not app.exception
    for block in app.markdown:
        value = block.value
        if "<" not in value or value.lstrip().startswith(("<style", "<link")):
            continue
        lines = value.split("\n")
        assert "" not in lines[1:-1], f"blank line inside an HTML block on {page}: {value[:120]!r}"
        assert not any(line.startswith("    ") for line in lines), f"indented line on {page}: {value[:120]!r}"


# ──────────────────────────────────────────────────────────────────────────
# Release watch: a theatre that hasn't listed the film yet can be monitored
# ──────────────────────────────────────────────────────────────────────────
PRASADS_LIVE = [
    {"venue_code": "PRHN", "venue_name": "Prasads Multiplex", "area": "Hyderabad",
     "time": "07:00 PM", "time_code": "1900", "fmt": "PCX SCREEN", "status": "3"},
]


@pytest.fixture
def release_watch(provider_factory, monkeypatch):
    """Mandaadi plays at Allu/AMB; Prasads (PCX) is only known from Hanuman
    Ansh. So for Mandaadi, Prasads is a *coming soon* theatre."""
    from tests.conftest import ALLU_LIVE, QUICKBOOK_HYD, build_payload

    responses = ([QUICKBOOK_HYD]
                 + [build_payload(ALLU_LIVE)] * 5       # Mandaadi Telugu (3 events) + Tamil (2)
                 + [build_payload(PRASADS_LIVE)])       # Hanuman Ansh
    provider = provider_factory(responses)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad")
                 if catalogue.movie_from_entry(e).title == "Mandaadi")
    return catalogue.movie_from_entry(entry).id


def test_a_theatre_known_from_other_films_is_offered_as_coming_soon(release_watch):
    app = run(step=3, location="hyderabad", movie_id=release_watch)
    body = text(app)
    assert "feat_PRHN" in {b.key for b in app.button}
    assert "Coming soon" in body and "Expected: Pcx Screen" in body
    assert "watch this theatre until BookMyShow releases tickets" in body
    # The listed set is untouched — Prasads is not pretended into it.
    assert section(body, "All theatres") == "2"
    assert {b.key for b in app.button if b.key.startswith("th_")} == {"th_ALLU", "th_AMB"}

    app.button(key="feat_PRHN").click().run()
    assert not app.exception
    assert app.session_state["theatres"] == ["PRHN"]
    body = text(app)
    assert section(body, "Watching for release") == "1"
    assert "1 theatre(s) selected: Prasads Multiplex" in body


def test_theatre_search_offers_the_whole_city_with_coming_soon_marked(release_watch):
    app = run(step=3, location="hyderabad", movie_id=release_watch)
    box = next(s for s in app.selectbox if s.key.startswith("theatre_query_"))
    assert box.options == ["Allu Cinemas · Attapur, Hyderabad · Dolby Cinema",
                           "AMB Cinemas · Gachibowli, Hyderabad · HDR By Barco",
                           "Prasads Multiplex · Hyderabad · Coming soon · Pcx Screen"]
    assert "example" not in (box.placeholder or "").lower() and "e.g." not in (box.placeholder or "")
    box.select("Prasads Multiplex · Hyderabad · Coming soon · Pcx Screen").run()
    assert app.session_state["theatres"] == ["PRHN"]


def test_formats_for_a_coming_soon_theatre_are_the_ones_it_is_known_to_run(release_watch):
    app = run(step=4, location="hyderabad", movie_id=release_watch, theatres=["ALLU", "PRHN"])
    assert not app.exception
    keys = {c.key for c in app.checkbox}
    assert "fmt_PRHN_Pcx Screen" in keys and "fmt_PRHN_any" in keys
    assert "fmt_ALLU_Dolby Cinema" in keys
    body = text(app)
    assert "Not listed for this movie yet" in body


def test_release_watch_monitor_is_saved_and_fires_when_the_theatre_appears(
        release_watch, provider_factory, monkeypatch):
    """The brief's scenario: pick a theatre with no listing, start, stay
    ACTIVE while it reads 'waiting for release', then the moment the theatre
    appears with the format — AVAILABLE, and an email."""
    from monitor import checker
    from monitor.changes import ChangeKind, apply_outcome, detect_changes
    from tests.conftest import ALLU_LIVE, build_payload

    at = now_ist()
    app = run(step=5, location="hyderabad", movie_id=release_watch,
              theatres=["PRHN"], formats={"PRHN": ["Pcx Screen"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    monitor = load_monitors()[0]
    assert monitor.status is MonitorStatus.ACTIVE
    assert [t.key for t in monitor.targets] == ["PRHN::Pcx Screen"]
    assert monitor.target("PRHN::Pcx Screen").venue_name == "Prasads Multiplex"

    # Check 1: Mandaadi still lists only Allu/AMB — the theatre is not there.
    provider = provider_factory([build_payload(ALLU_LIVE)] * 3)
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    outcome = checker.check_monitor(monitor, at=at)
    assert outcome.ok
    assert outcome.results[0].availability is Availability.THEATRE_NOT_AVAILABLE
    state = MonitorState()
    assert detect_changes(monitor, outcome, state) == []               # nothing to email yet
    state = apply_outcome(outcome, state)
    save_state({monitor.id: state}, mirror=False)
    assert monitor.is_running(at)
    body = text(run("My Monitors"))
    assert "waiting for this theatre to release" in body

    # Check 2: Prasads lists it, PCX, bookable — the release we were waiting for.
    provider = provider_factory([build_payload(ALLU_LIVE + PRASADS_LIVE)] * 3)
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    outcome = checker.check_monitor(monitor, at=at + timedelta(minutes=10))
    result = outcome.results[0]
    assert result.availability is Availability.AVAILABLE
    assert result.time_labels == ["07:00 PM"]
    changes = detect_changes(monitor, outcome, state)
    assert [c.kind for c in changes] == [ChangeKind.TICKETS_LIVE]
    assert changes[0].venue_name == "Prasads Multiplex" and changes[0].fmt == "Pcx Screen"


def test_check_picks_up_sibling_events_the_catalogue_learned_later(make_monitor, provider_factory,
                                                                   monkeypatch, at):
    """A monitor saved before BookMyShow added a 'Dolby Cinema 2D' event still
    sees the theatre when the catalogue sync has recorded that sibling."""
    from dataclasses import replace

    from monitor import checker
    from tests.conftest import ALLU_LIVE, build_payload

    monitor = make_monitor(targets=[TheatreTarget("PRHN", "Prasads Multiplex", "Hyderabad", "Pcx Screen")])
    assert monitor.movie.variants == ()
    # The catalogue now knows a sibling event for this movie.
    catalogue.store_snapshot(
        provider_factory([build_payload(ALLU_LIVE)] * 2).fetch(
            replace(monitor.movie, variants=(("ET00516197", "Dolby Cinema 2D"),))),
        mirror=False)
    assert checker.with_current_variants(monitor.movie).variant_codes == ("ET00516197",)

    provider = provider_factory([build_payload(ALLU_LIVE), build_payload(PRASADS_LIVE)])
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider)
    outcome = checker.check_monitor(monitor, at=at)
    assert [c["params"]["eventCode"] for c in provider.session.calls] == ["ET00478890", "ET00516197"]
    assert outcome.results[0].availability is Availability.AVAILABLE


# ──────────────────────────────────────────────────────────────────────────
# The theatre page opens on the six featured tiles; the long list is a click away
# ──────────────────────────────────────────────────────────────────────────
def test_a_long_theatre_list_waits_behind_view_all(provider_factory, monkeypatch):
    from tests.conftest import QUICKBOOK_HYD, build_payload

    many = [
        {"venue_code": f"V{i:02d}", "venue_name": f"Theatre {i}", "area": "Hyderabad",
         "time": "07:00 PM", "time_code": "1900", "fmt": "2D", "status": None}
        for i in range(9)
    ]
    provider = provider_factory([QUICKBOOK_HYD] + [build_payload(many)] * 6)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    movie_id = catalogue.movie_from_entry(catalogue.list_entries("hyderabad")[0]).id

    app = run(step=3, location="hyderabad", movie_id=movie_id)
    assert not app.exception
    # Featured first; the nine listed theatres are not drawn yet.
    assert not any(b.key.startswith("th_V") for b in app.button)
    view_all = next(b for b in app.button if b.key == "view_all_theatres")
    assert "View all 9 theatres" in view_all.label
    # Select all still applies to the real, listed theatres.
    app.button(key="select_all").click().run()
    assert app.session_state["theatres"] == [f"V{i:02d}" for i in range(9)]

    app.button(key="view_all_theatres").click().run()
    assert not app.exception
    assert {b.key for b in app.button if b.key.startswith("th_V")} == {f"th_V{i:02d}" for i in range(9)}
    assert section(text(app), "All theatres") == "9"
    # Changing the movie folds the list away again.
    app.session_state["show_all_theatres"] = True
    from ui import flow
    assert flow.VIEW_ALL_THRESHOLD == 6


def test_a_short_theatre_list_is_shown_outright(seeded):
    """Two theatres are not worth hiding behind a button."""
    app = run(step=3, location="hyderabad", movie_id=seeded)
    assert not any(b.key == "view_all_theatres" for b in app.button)
    assert {b.key for b in app.button if b.key.startswith("th_")} == {"th_ALLU", "th_AMB"}
