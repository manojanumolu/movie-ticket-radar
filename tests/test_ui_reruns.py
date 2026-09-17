"""One script run per click, one wizard per run, one page slot per session.

Measured on the real app in a real browser before this change: every
wizard tile — city, poster, theatre, select-all, view-all, the step rail,
Back, every Continue, interval, date mode — cost **two** full script runs.
The tile's button returned True on the run after the click, that run set
the state and called ``st.rerun()``, and only the run after *that* drew the
result. Two runs, twice the page over the websocket (93 KB of CSS each
time), for every click. A plain widget edit cost one.

Now a tile's effect is its button's ``on_click``, which Streamlit runs
*before* the script, so the single run that follows already draws it.
Pinned here by counting ``st.rerun`` calls while clicking through the whole
wizard, and by counting the wizard itself — exactly one — after every kind
of interaction.

Also pinned: the gate's page slot is per session. It was a module global,
which every session in the process shared; two sessions running at once
(a second tab, or the Google popup and the tab that opened it) could render
into each other's container.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
import streamlit as st

from config.timezone import now_ist
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget
from monitor.state import ACTIVE_MONITOR_LIMIT, Scope
from tests.test_app import run, seeded, text  # noqa: F401


def _monitor(uid: str, title: str = "Film") -> Monitor:
    return Monitor(movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                                  region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
                   targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=10, monitor_until=now_ist() + timedelta(days=2),
                   notify_email="w@example.com", owner_uid=uid)


def _running(uid: str, n: int) -> None:
    state_mod.set_scope_provider(lambda: Scope("", uid, lambda: "t"))
    try:
        for i in range(n):
            state_mod.upsert_monitor(_monitor(uid, f"Film {i}"), mirror=False)
    finally:
        state_mod.set_scope_provider(None)
    state_mod.invalidate_cache()


@pytest.fixture
def reruns(monkeypatch):
    """Count every st.rerun() the app calls, without changing what it does."""
    calls = []
    real = st.rerun

    def counting(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    monkeypatch.setattr(st, "rerun", counting)
    return calls


# ──────────────────────────────────────────────────────────────────────────
# One run per click, through the whole wizard
# ──────────────────────────────────────────────────────────────────────────
def test_every_wizard_tile_is_one_run_no_rerun(seeded, reruns):
    app = run()                                                   # step 1
    assert reruns == []

    app.button(key="loc_hyderabad").click().run()                 # city tile -> step 2
    assert app.session_state["step"] == 2 and app.session_state["location"] == "hyderabad"
    assert reruns == []

    poster = next(b for b in app.button if b.key.startswith("movie_") and b.key != "movie_continue")
    app.button(key=poster.key).click().run()                      # poster tile -> step 3
    assert app.session_state["step"] == 3 and app.session_state["movie_id"]
    assert reruns == []

    theatre = next(b for b in app.button if b.key.startswith(("th_", "feat_")) and b.key != "th_continue")
    app.button(key=theatre.key).click().run()                     # theatre tile (toggle)
    assert app.session_state["theatres"], "the theatre was not selected"
    app.button(key="select_all").click().run()                    # select all
    assert len(app.session_state["theatres"]) > 1
    app.button(key="select_all").click().run()                    # …and back off
    assert reruns == []

    app.button(key=theatre.key).click().run() if not app.session_state["theatres"] else None
    app.button(key="th_continue").click().run()                   # Continue -> step 4
    assert app.session_state["step"] == 4
    assert reruns == []

    app.button(key="step_2").click().run()                        # step rail back to 2
    assert app.session_state["step"] == 2 and app.session_state["movie_id"]   # nothing reset
    app.button(key="step_4").click().run()                        # …and forward again
    assert app.session_state["step"] == 4
    app.button(key="back").click().run()                          # Back button
    assert app.session_state["step"] == 3
    assert reruns == []


def test_step_five_tiles_are_one_run_no_rerun(seeded, reruns):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    app.button(key="interval_15").click().run()
    assert app.session_state["interval"] == 15
    app.button(key="interval_30").click().run()
    assert app.session_state["interval"] == 30
    app.button(key="datemode_single").click().run()
    assert app.session_state["date_mode"] == "single"
    app.button(key="datemode_range").click().run()
    assert app.session_state["date_mode"] == "range"
    app.button(key="datemode_any").click().run()
    assert app.session_state["date_mode"] == "any"
    assert reruns == []
    # The selection is what the page draws on that same run.
    assert "every 30 minutes" in text(app)


def test_the_search_box_still_picks_a_theatre_in_one_run(seeded, reruns):
    app = run(step=3, location="hyderabad", movie_id=seeded, theatres=[])
    box = next(s for s in app.selectbox if s.key.startswith("theatre_query_"))
    box.select_index(0).run()
    assert len(app.session_state["theatres"]) == 1
    assert app.session_state["theatre_nonce"] == 1                # the box is cleared on the same run
    assert not any(s.key == box.key for s in app.selectbox)       # …so it has a new key
    assert reruns == []


def test_starting_a_monitor_still_reruns_into_step_one(seeded, reruns):
    """The one rerun that stays: Start monitoring writes, then wants the
    page redrawn at step 1 with its flash — that is not a tile."""
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    app.text_input(key="notify_email").set_value("me@example.com").run()
    app.button(key="start").click().run()
    assert not app.exception
    assert app.session_state["step"] == 1
    assert len(reruns) == 1
    assert len(state_mod.load_monitors()) == 1


# ──────────────────────────────────────────────────────────────────────────
# Exactly one wizard, whatever happened last
# ──────────────────────────────────────────────────────────────────────────
WIZARD = ("How often should I check?", "Monitor until", "Which show dates?", "Where should we email you?")


def _wizards(app) -> dict[str, int]:
    body = " ".join(m.value for m in app.markdown)
    counts = {w: body.count(w) for w in WIZARD}
    counts["start buttons"] = sum(1 for b in app.button if b.key == "start")
    counts["email inputs"] = sum(1 for t in app.text_input if t.key == "notify_email")
    return counts


def test_the_wizard_renders_exactly_once_after_every_interaction(seeded, signed_in):
    wiz = dict(step=5, furthest=5, location="hyderabad", movie_id=seeded,
               theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    once = {w: 1 for w in WIZARD} | {"start buttons": 1, "email inputs": 1}

    app = run(**wiz)
    assert _wizards(app) == once
    app.button(key="interval_15").click().run();          assert _wizards(app) == once
    app.button(key="datemode_single").click().run();      assert _wizards(app) == once
    app.text_input(key="notify_email").set_value("me@example.com").run(); assert _wizards(app) == once
    app.run();                                             assert _wizards(app) == once
    app.button(key="step_3").click().run()
    assert _wizards(app)["start buttons"] == 0             # step 3 is not the wizard's last step
    app.button(key="step_5").click().run();               assert _wizards(app) == once

    # …with the live rail (its fragment) on the page as well.
    _running(signed_in.uid, 1)
    app = run(**wiz)
    assert _wizards(app) == once
    app.button(key="interval_30").click().run();          assert _wizards(app) == once
    app.run(); app.run();                                  assert _wizards(app) == once


# ──────────────────────────────────────────────────────────────────────────
# The limit on the page: the fifth is allowed, the sixth is not
# ──────────────────────────────────────────────────────────────────────────
def test_four_active_monitors_leave_start_enabled_and_silent(seeded, signed_in):
    _running(signed_in.uid, ACTIVE_MONITOR_LIMIT - 1)
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    assert next(b for b in app.button if b.key == "start").disabled is False
    assert not any("Active monitor limit" in w.value for w in app.warning)


def test_five_active_monitors_disable_start_with_the_message(seeded, signed_in):
    _running(signed_in.uid, ACTIVE_MONITOR_LIMIT)
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    assert next(b for b in app.button if b.key == "start").disabled is True
    assert any("up to 5 active monitors" in w.value for w in app.warning)


# ──────────────────────────────────────────────────────────────────────────
# The page slot belongs to the session that reserved it
# ──────────────────────────────────────────────────────────────────────────
def test_the_page_slot_is_per_thread_not_per_process():
    from auth import gate

    seen = {}

    def session(name, ready, go):
        gate._page.container = f"container-of-{name}"
        ready.set()
        go.wait()                                  # the other session sets its own meanwhile
        seen[name] = gate.app_container()

    a_ready, b_ready, go = threading.Event(), threading.Event(), threading.Event()
    ta = threading.Thread(target=session, args=("A", a_ready, go))
    tb = threading.Thread(target=session, args=("B", b_ready, go))
    ta.start(); tb.start()
    a_ready.wait(); b_ready.wait()
    go.set(); ta.join(); tb.join()

    assert seen == {"A": "container-of-A", "B": "container-of-B"}
