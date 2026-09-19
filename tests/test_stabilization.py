"""The pre-redesign stabilisation pass: state that must not leak, and
navigation that must not move on its own.

Four bugs from production, each pinned at its root:

* **Stop → start a new monitor showed the last monitor's details.** Saving a
  monitor reset only the wizard's step; every pick and every widget value
  (formats, dates, end time, email) stayed in the session and came back
  behind a step-1 screen. ``flow.reset_wizard`` now drops all of it.
* **Stopping monitors one after another landed on Home.** The sidebar's
  ``st.radio`` option label carried the active count ("My Monitors `3`").
  Streamlit 1.64 stores a radio's selection in the browser as the formatted
  label; when the count changed the stored label matched nothing and the
  next click reset the radio to its default — Home. Reproduced in a real
  browser; the options are constant now and the count is drawn by CSS.
* **The notification email was blank for a new account.** It starts as the
  account's own address, and what the person types is kept in a draft the
  reruns never overwrite.
* **Nothing of one account may survive into the next.** Sign-out wipes the
  wizard, the draft and the read cache with everything else.
"""

from __future__ import annotations

import pytest

from monitor import state as state_mod
from monitor.models import ANY_FORMAT
from monitor.state import upsert_monitor
from tests.test_app import run, section, seeded, text  # noqa: F401 - fixture


def load_monitors():
    """The store as it is — not the five-second read cache, which a call
    from the test thread would otherwise populate and then trust."""
    return state_mod._backend().load_monitors()


def get_monitor(monitor_id: str):
    return next((m for m in load_monitors() if m.id == monitor_id), None)


def _code_only(chunk: str) -> str:
    """Source without comment lines or docstrings — what actually runs."""
    import re

    chunk = re.sub(r'"""[\s\S]*?"""', "", chunk)
    return chr(10).join(l for l in chunk.splitlines() if not l.strip().startswith("#"))


# ──────────────────────────────────────────────────────────────────────────
# F. Stop A → start new monitor: nothing of A comes back
# ──────────────────────────────────────────────────────────────────────────
def test_starting_a_monitor_leaves_a_clean_wizard_for_the_next_one(seeded):
    from ui import flow

    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU", "AMB"],
              formats={"ALLU": ["Dolby Cinema"], "AMB": [ANY_FORMAT]}, interval=30,
              date_mode="single", furthest=5)
    app.text_input(key="notify_email").set_value("a@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    monitor_a = load_monitors()[0]
    assert monitor_a.interval_minutes == 30 and monitor_a.notify_email == "a@example.com"

    # Every wizard key is back at its default; the widget-backed ones are gone.
    for key, default in flow.DEFAULTS.items():
        if key != "location":
            assert app.session_state[key] == default, key
    assert app.session_state["location"] == "hyderabad"          # the city is where they are, not the monitor
    for key in flow.WIDGET_KEYS:
        assert key not in app.session_state, key
    assert not [k for k in app.session_state if str(k).startswith(("fmt_", "theatre_query_"))]
    # Step 1, no summary strip, nothing selected: the rail still shows the
    # monitor that was just made — that is the monitor, not the wizard.
    keys = [b.key for b in app.button]
    assert "loc_continue" in keys and "movie_continue" not in keys and "start" not in keys
    assert "a@example.com" not in text(app)

    # Stop A, come back to Home, and walk to the monitoring step: still nothing of A.
    app.session_state["page"] = "My Monitors"
    app.run()
    app.button(key=f"m_stop_{monitor_a.id}").click().run()
    assert get_monitor(monitor_a.id).status.value == "STOPPED"
    app.session_state["page"] = "Home"
    app.run()
    assert app.session_state["step"] == 1 and app.session_state["furthest"] == 1
    assert app.session_state["movie_id"] == "" and app.session_state["theatres"] == []
    assert app.session_state["formats"] == {} and app.session_state["interval"] == 10
    assert app.session_state["date_mode"] == "any" and app.session_state["show_dates"] == []
    assert "notify_email" not in app.session_state and "notify_email_draft" not in app.session_state
    assert "start" not in [b.key for b in app.button] and "a@example.com" not in text(app)


def test_the_reset_keeps_the_city_and_nothing_else():
    from ui import flow

    app = run(step=4, location="hyderabad", movie_id="m1", theatres=["ALLU"],
              formats={"ALLU": ["IMAX"]}, interval=15, notify_email_draft="x@example.com",
              fmt_ALLU_IMAX=True, theatre_query_3="AMB", show_dates=["20260925"], furthest=4)
    app.session_state[flow.RESET_FLAG] = True       # what flow.request_reset() sets
    app.run()
    assert app.session_state["location"] == "hyderabad"
    assert app.session_state["step"] == 1 and app.session_state["movie_id"] == ""
    assert app.session_state["theatres"] == [] and app.session_state["formats"] == {}
    assert app.session_state["interval"] == 10 and app.session_state["show_dates"] == []
    for gone in ("notify_email_draft", "fmt_ALLU_IMAX", "theatre_query_3"):
        assert gone not in app.session_state


# ──────────────────────────────────────────────────────────────────────────
# G. Stopping monitors one after another stays on My Monitors
# ──────────────────────────────────────────────────────────────────────────
def test_stopping_five_monitors_in_a_row_stays_on_my_monitors(make_monitor):
    monitors = [make_monitor() for _ in range(5)]
    for m in monitors:
        upsert_monitor(m, mirror=False)
    app = run("My Monitors")
    for index, monitor in enumerate(monitors):
        app.button(key=f"m_stop_{monitor.id}").click().run()
        assert not app.exception, [str(e) for e in app.exception]
        assert app.session_state["page"] == "My Monitors"
        assert app.radio(key="page").value == "My Monitors"
        assert "asked us to watch" in text(app)             # the My Monitors hero
        assert section(text(app), "Active") == str(4 - index)
        assert section(text(app), "Finished") == str(index + 1)
        assert get_monitor(monitor.id).status.value == "STOPPED"
        assert app.session_state["page"] == "My Monitors"
    # Each monitor was stopped exactly once: one STOPPED line per monitor.
    from monitor.state import load_history

    stopped = [h for h in load_history() if h.get("kind") == "STOPPED"]
    assert len(stopped) == 5


def test_the_navigation_options_never_change_so_the_browser_selection_stays_valid(make_monitor):
    """The root cause, pinned: the radio's options are the same four strings
    whatever the active count — the count lives in a CSS variable."""
    pages = ["Home", "My Monitors", "History", "Settings"]
    app = run("My Monitors")
    assert list(app.radio(key="page").options) == pages
    for _ in range(3):
        upsert_monitor(make_monitor(), mirror=False)
    app = run("My Monitors")
    assert list(app.radio(key="page").options) == pages
    assert any('--tr-nav-count:"3"' in m.value for m in app.sidebar.markdown)
    from pathlib import Path

    sidebar_src = Path("app.py").read_text(encoding="utf-8").split("def sidebar(")[1].split("\ndef ")[0]
    code = _code_only(sidebar_src)
    assert "format_func" not in code and 'st.radio("Navigation", PAGES' in code


def test_card_actions_run_as_callbacks_not_mid_script_reruns():
    """One clean run per click. ``st.rerun()`` from the middle of the page
    interrupts the script part-way through, which is where widget state
    got lost; a callback runs before the script and never interrupts."""
    import re
    from pathlib import Path

    src = Path("app.py").read_text(encoding="utf-8")

    def body_of(name: str) -> str:
        chunk = re.split(r"\n(?:def |@st\.|[A-Z_]+ = )", src.split(f"def {name}(")[1])[0]
        return _code_only(chunk)

    actions = body_of("monitor_actions")
    assert "st.rerun()" not in actions and actions.count("on_click=") == 4
    rail = body_of("live_monitor_panel")
    assert "st.rerun()" not in rail and "on_click=do_stop_monitor_from_rail" in rail
    assert 'st.rerun(scope="app")' in body_of("do_stop_monitor_from_rail")


def test_the_rail_stop_button_still_stops_and_redraws_the_whole_page(make_monitor):
    monitor = make_monitor()
    upsert_monitor(monitor, mirror=False)
    app = run("Home")
    assert "Active monitor" in text(app)
    app.button(key=f"stop_{monitor.id}").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert get_monitor(monitor.id).status.value == "STOPPED"
    assert "Monitoring stopped" in text(app)
    assert app.session_state["page"] == "Home"


# ──────────────────────────────────────────────────────────────────────────
# H / I. The notification email: the account's own, then whatever was typed
# ──────────────────────────────────────────────────────────────────────────
def test_a_new_account_sees_its_own_email_prefilled(seeded, signed_in):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    assert app.text_input(key="notify_email").value == signed_in.email == "tester@example.com"


def test_a_saved_notification_address_wins_over_the_account_email(seeded):
    from monitor.state import Scope, save_settings, set_scope_provider

    # Settings are per account; save under the signed-in fixture's UID.
    set_scope_provider(lambda: Scope("", "uid-test-1", lambda: ""))
    try:
        save_settings({"notify_email": "alerts@example.com"}, mirror=False)
    finally:
        set_scope_provider(None)
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    assert app.text_input(key="notify_email").value == "alerts@example.com"


def test_a_typed_address_survives_reruns_and_a_trip_to_another_page(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    app.text_input(key="notify_email").set_value("custom@example.com").run()
    assert app.session_state["notify_email_draft"] == "custom@example.com"
    app.run()                                                      # a rerun changes nothing
    assert app.text_input(key="notify_email").value == "custom@example.com"
    app.session_state["page"] = "History"                          # widget not drawn: Streamlit drops its state
    app.run()
    app.session_state["page"] = "Home"
    app.run()
    assert app.text_input(key="notify_email").value == "custom@example.com"
    app.button(key="start").click().run()
    assert load_monitors()[0].notify_email == "custom@example.com"


def test_the_prefill_is_the_signed_in_persons_email_only(seeded, monkeypatch):
    from auth import session

    other = session.AuthUser(uid="uid-other", email="other@example.com", display_name="Other",
                             id_token="t", refresh_token="r", expires_at=4102444800.0)

    def restore():
        import streamlit as st

        if st.session_state.get("auth_restore_tried"):
            return None
        st.session_state["auth_restore_tried"] = True
        st.session_state[session.USER_KEY] = other
        return other

    monkeypatch.setattr(session, "restore", restore)
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    assert app.text_input(key="notify_email").value == "other@example.com"


# ──────────────────────────────────────────────────────────────────────────
# J. Switching accounts: nothing of A reaches B, and A gets its own back
# ──────────────────────────────────────────────────────────────────────────
def test_a_sign_out_wipes_the_wizard_the_draft_and_the_page():
    from auth import session

    app = run("My Monitors", step=5, movie_id="m1", theatres=["ALLU"], formats={"ALLU": ["IMAX"]},
              notify_email_draft="a@example.com", fmt_ALLU_IMAX=True, _tr_read_cache={"uid-test-1:monitors": (0, [])})
    assert app.session_state["auth_user"].uid == "uid-test-1"
    app.session_state[session.RESET_FLAG] = True   # what sign_out() and sign_in_user() set
    app.session_state["auth_user"] = session.AuthUser(uid="uid-b", email="b@example.com",
                                                      id_token="t", refresh_token="r", expires_at=4102444800.0)
    app.run()
    assert app.session_state["page"] == "Home"
    assert app.session_state["step"] == 1 and app.session_state["movie_id"] == ""
    assert app.session_state["theatres"] == [] and app.session_state["formats"] == {}
    for gone in ("notify_email_draft", "fmt_ALLU_IMAX"):
        assert gone not in app.session_state, gone
    # The read cache was wiped with the rest and rebuilt by this run for B:
    # nothing keyed on A's UID is in it.
    cache = app.session_state.get("_tr_read_cache") or {}
    assert not [k for k in cache if str(k).startswith("uid-test-1:")]
    assert app.session_state["auth_user"].uid == "uid-b"


# ──────────────────────────────────────────────────────────────────────────
# Formats: venue-aware at creation, never rewritten afterwards
# ──────────────────────────────────────────────────────────────────────────
def test_a_format_the_theatre_lists_is_accepted(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": ["Dolby Cinema"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert [t.key for t in load_monitors()[0].targets] == ["ALLU::Dolby Cinema"]


def test_a_format_the_theatre_is_not_known_to_run_is_refused(seeded):
    """Stale or forged state naming a format the catalogue has never seen
    the theatre run is refused at save time — the page's checkboxes are
    only the courtesy."""
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": ["IMAX 3D"]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert load_monitors() == []
    assert any("isn't known to run IMAX 3D" in w.value for w in app.warning)


def test_any_format_and_a_known_but_unlisted_format_pass(seeded, monkeypatch):
    """A theatre's premium screen the movie has not opened there yet is a
    legitimate target — the whole point of a release monitor."""
    from ui import catalogue_view as cv
    from monitor.models import Venue

    real = cv.selected_venues

    def wider(movie_id, slug, codes):
        # The directory knows ALLU runs IMAX for other films; this movie lists only Dolby.
        return [Venue(code=v.code, name=v.name, area=v.area, formats=(*v.formats, "IMAX"))
                if v.code == "ALLU" else v for v in real(movie_id, slug, codes)]

    monkeypatch.setattr(cv, "selected_venues", wider)
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU", "AMB"],
              formats={"ALLU": ["IMAX"], "AMB": [ANY_FORMAT]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception
    assert sorted(t.key for t in load_monitors()[0].targets) == ["ALLU::IMAX", "AMB::Any format"]


def test_a_theatre_with_no_known_formats_keeps_the_existing_behaviour():
    from monitor.models import Venue
    from ui import flow

    assert flow.unknown_formats(Venue(code="X", name="X", area="", formats=()), ["Whatever"]) == []
    assert flow.unknown_formats(Venue(code="X", name="X", area="", formats=("Dolby Cinema",)),
                                ["dolby cinema", ANY_FORMAT]) == []
    assert flow.unknown_formats(Venue(code="X", name="X", area="", formats=("Dolby Cinema",)),
                                ["IMAX"]) == ["IMAX"]


def test_the_format_step_says_which_options_are_not_listed_yet(seeded):
    app = run(step=4, location="hyderabad", movie_id=seeded, theatres=["ALLU"])
    boxes = {c.key: c for c in app.checkbox}
    assert "fmt_ALLU_Dolby Cinema" in boxes
    assert boxes["fmt_ALLU_Dolby Cinema"].proto.help == "Listed for this movie at this theatre now."
    assert boxes["fmt_ALLU_any"].proto.help.startswith("Watch every format")


def test_an_existing_monitor_is_never_rewritten_by_the_catalogue(make_monitor, provider_factory, monkeypatch):
    """The production monitor watches ALLU for a format BookMyShow lists
    elsewhere today. Nothing — not a page load, not a worker tick — changes
    what it watches."""
    from monitor import catalogue, checker
    from monitor.models import TheatreTarget
    from tests.conftest import ALLU_LIVE, build_payload

    monitor = make_monitor(targets=[TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", "Dolby Cinema"),
                                    TheatreTarget("AMB", "AMB Cinemas", "Gachibowli, Hyderabad", "HDR By Barco")])
    upsert_monitor(monitor, mirror=False)
    before = [(t.venue_code, t.fmt) for t in monitor.targets]

    provider = provider_factory([build_payload(ALLU_LIVE)] * 3)
    monkeypatch.setattr(checker, "get_provider", lambda slug: provider, raising=False)
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    app = run("My Monitors")
    assert not app.exception
    checker.run_once(force=True, mirror=False, notifier=lambda *a, **k: None)
    after = get_monitor(monitor.id)
    assert [(t.venue_code, t.fmt) for t in after.targets] == before
    assert after.interval_minutes == monitor.interval_minutes
    assert after.monitor_until == monitor.monitor_until
    assert after.notify_email == monitor.notify_email
