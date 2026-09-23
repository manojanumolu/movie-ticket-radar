"""What a wizard click costs: the store reads it makes, and the bytes it sends.

Moving through the flow — a city, a film, a theatre, a format, an interval —
changes nothing but this session's own picks. Every one of those clicks was
nonetheless re-reading the account's monitors, observed state, history and
settings, because the store's read cache lapses after five seconds and
looking at a poster takes longer than that. On Firestore each of those is a
network round trip, and that is what the movie step felt like.

The run after such a click now draws the snapshot the page already had.
These tests pin both halves: the read is skipped when nothing stored moved,
and it is *not* skipped for anything that writes or for a fresh page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from monitor import state as state_mod
from tests.test_account import code_of
from tests.test_app import run, seeded, text  # noqa: F401  (fixture re-export)
from tests.test_movie_selection import HANUMAN, on_theatre_step
from ui import flow

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
ROOT = Path(flow.__file__).resolve().parent.parent


@pytest.fixture
def counted(monkeypatch):
    """Count every batched store read the page makes."""
    calls: list[tuple[str, ...]] = []
    real = state_mod.load_many

    def counting(*kinds):
        calls.append(kinds)
        return real(*kinds)

    monkeypatch.setattr(state_mod, "load_many", counting)
    return calls


# ──────────────────────────────────────────────────────────────────────────
# The click that changes nothing stored
# ──────────────────────────────────────────────────────────────────────────
def test_choosing_a_movie_makes_no_store_read_at_all(seeded, counted):
    app = run(step=2, location="hyderabad")
    counted.clear()
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert on_theatre_step(app), "the click must still do its job"
    assert counted == [], f"a movie click cost {len(counted)} store read(s)"


def test_a_poster_tile_costs_no_read_either(seeded, counted):
    app = run(step=2, location="hyderabad")
    counted.clear()
    app.button(key=f"movie_{seeded}").click().run()
    assert app.session_state["step"] == 3
    assert counted == []


def test_moving_between_steps_costs_no_read(seeded, counted):
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    counted.clear()
    app.button(key="back").click().run()                 # back to the movie step
    assert app.session_state["step"] == 2
    assert counted == []


def test_the_picks_survive_the_reused_snapshot(seeded, counted):
    """Skipping the read must change nothing about what the click did."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    app.button(key="select_all").click().run()
    assert app.session_state["theatres"], "theatres did not select"
    assert app.session_state["movie_id"]
    assert not app.exception, [str(e) for e in app.exception]


# ──────────────────────────────────────────────────────────────────────────
# …and the runs that must still read
# ──────────────────────────────────────────────────────────────────────────
def test_a_fresh_page_still_reads(seeded, counted):
    counted.clear()
    run(step=2, location="hyderabad")
    assert counted, "the first paint must read the account's records"


def test_changing_page_still_reads(seeded, counted):
    app = run(step=2, location="hyderabad")
    counted.clear()
    app.session_state["page"] = "My Monitors"
    app.run()
    assert counted, "a page change must read"


def test_the_mark_is_consumed_by_the_run_it_was_set_for(seeded):
    """One skipped read per click, never two: a second run reads again."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert flow.UI_ONLY_KEY not in app.session_state
    assert app.session_state.get("_tr_view_snapshot") is not None


def test_only_the_wizards_own_callbacks_set_the_mark():
    """Anything that writes — starting, stopping, extending or deleting a
    monitor, saving settings, choosing an avatar — must never mark a run as
    costing nothing, or the page after the write would show the state before
    it."""
    for name in ("app.py", "ui/avatar.py", "ui/account.py", "ui/detail.py", "ui/home.py"):
        assert "mark_ui_only" not in code_of(ROOT / name), f"{name} must not mark a run UI-only"
    src = code_of(ROOT / "ui" / "flow.py")
    # …and the wizard's own callbacks do, including the one the search box uses.
    assert src.count("mark_ui_only()") >= 8


def test_the_snapshot_is_keyed_on_the_account_it_was_read_for():
    """One person's records can never answer another's page — the same rule
    the store's own read cache keeps."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "snapshot[0] == auth_session.current_uid()" in src


# ──────────────────────────────────────────────────────────────────────────
# The bytes a rerun carries
# ──────────────────────────────────────────────────────────────────────────
def test_the_platform_logos_are_served_not_carried_in_every_rerun(seeded):
    """Three PNGs as base64 were 50 KB inside the markup of every single
    rerun. They are files the browser fetches once now, like the avatars."""
    app = run()
    page = " ".join(m.value for m in app.markdown)
    assert "app/static/branding/bookmyshow.png" in page
    assert "data:image/png;base64" not in page, "an image is riding inside the page again"


def test_the_brand_marks_are_where_the_registry_says(seeded):
    from ui import assets_registry as art

    for key in ("bookmyshow", "district", "pvr"):
        asset = art.brand(key)
        assert asset is not None, f"static/branding/{key}.png is missing"
        assert asset.mime == "image/png"
        assert art.static_url(asset).startswith("app/static/branding/")
