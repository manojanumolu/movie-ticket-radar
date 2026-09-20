"""Choosing a movie from the search box shows the result in the same run.

The bug (21 Sep 2026, deployed site): search for a movie, pick the
suggestion, and nothing changes — no "Selected" state, no way to continue,
until the next click on anything. Session state was right (``movie_id`` set,
``step`` = 3) but the *page* was not: ``step_movie`` called ``_select_movie``
from the script body, and ``_select_movie`` had been changed to
``goto(3, rerun=False)`` for the poster tiles' ``on_click`` (one run per
click). From a callback that is correct — the run that follows draws the new
step. From the script body it set ``step`` *after* ``app.wizard`` had
already read it and chosen to draw step 2, so the run finished drawing the
movie step with the pre-selection ``selected`` — no banner, no Continue —
and the theatre step waited for a rerun nobody asked for.

The search box's choice now lands in an ``on_change`` callback, the same
path the tiles use. These tests pin what the person sees after each
interaction — the rendered tree, not just session state.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

from tests.test_app import run, seeded, text  # noqa: F401  (fixture re-export)
from ui import flow, theme

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

HANUMAN = "Hanuman Ansh · Hindi · 2 theatres"
HANUMAN_ID = "bookmyshow:ET00507738"
MANDAADI_TE = "Mandaadi · Telugu · 2 theatres"
THEATRE_STEP = "Where do you want to watch?"
UI_DIR = Path(flow.__file__).resolve().parent


def keys(app) -> list[str]:
    return [b.key for b in app.button]


@pytest.fixture
def free_text(monkeypatch):
    """Let the harness type free text into the search box.

    ``AppTest``'s Selectbox does not model ``accept_new_options``: it sends
    ``options[index]`` and raises for a value that is not an option. The real
    frontend sends the typed string as is; this shim does the same, so a test
    can type "hanu" and then click what the filter left on screen.
    """
    from streamlit.proto.WidgetStates_pb2 import WidgetState
    from streamlit.testing.v1 import element_tree

    def widget_state(self):
        ws = WidgetState()
        ws.id = self.id
        if self.value is not None:
            ws.string_value = str(self.value)
        return ws

    monkeypatch.setattr(element_tree.Selectbox, "_widget_state", property(widget_state))


def on_theatre_step(app) -> bool:
    return THEATRE_STEP in text(app) and "select_all" in keys(app)


# ──────────────────────────────────────────────────────────────────────────
# 1 · The search result appears
# ──────────────────────────────────────────────────────────────────────────
def test_search_offers_the_movie(seeded):
    app = run(step=2, location="hyderabad")
    box = app.selectbox(key="movie_query")
    assert HANUMAN in box.options
    assert box.value is None


# ──────────────────────────────────────────────────────────────────────────
# 2/3/4/5 · Picking the suggestion selects it, and the same run shows it
# ──────────────────────────────────────────────────────────────────────────
def test_choosing_a_suggestion_draws_the_next_step_in_the_same_run(seeded):
    """The regression: one run after the choice, the theatre step is on
    screen — not the movie step with nothing selected."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["movie_id"] == HANUMAN_ID
    assert app.session_state["step"] == 3
    # What was actually drawn — this is what the old code got wrong.
    assert on_theatre_step(app)
    assert "Select movie" not in text(app)


def test_choosing_a_suggestion_needs_no_second_interaction(seeded):
    """A no-op rerun changes nothing: the first run was already complete."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    first = (app.session_state["step"], app.session_state["movie_id"], on_theatre_step(app))
    app.run()
    assert (app.session_state["step"], app.session_state["movie_id"], on_theatre_step(app)) == first
    assert first == (3, HANUMAN_ID, True)


def test_selection_is_visible_and_continue_is_offered_on_the_movie_step(seeded):
    """Back on the movie step the choice is visibly selected, the Continue
    control is there, and it advances on one click."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    app.button(key="back").click().run()
    assert app.session_state["step"] == 2
    assert app.session_state["movie_id"] == HANUMAN_ID
    body = text(app)
    assert "Selected:" in body and "Hanuman Ansh" in body
    assert "movie_continue" in keys(app)
    # The chosen film's tiles read "Selected"; the others still offer "Select …".
    tiles = {b.key: b.label for b in app.button if b.key.endswith(HANUMAN_ID)}
    assert tiles and all(label == "Selected" for label in tiles.values())
    assert any(b.label.startswith("Select ") for b in app.button if b.key.startswith("movie_bookmyshow:"))
    app.button(key="movie_continue").click().run()
    assert app.session_state["step"] == 3
    assert on_theatre_step(app)


def test_poster_tile_still_selects_in_one_run(seeded):
    app = run(step=2, location="hyderabad")
    app.button(key=f"movie_{seeded}").click().run()
    assert app.session_state["movie_id"] == seeded
    assert app.session_state["step"] == 3
    assert on_theatre_step(app)


# ──────────────────────────────────────────────────────────────────────────
# 6 · Search + selection with a filtered result
# ──────────────────────────────────────────────────────────────────────────
def test_free_text_filters_and_the_filtered_poster_selects(seeded, free_text):
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").set_value("hanu").run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["step"] == 2
    assert app.session_state["movie_id"] == ""
    body = text(app)
    assert "1 movie(s) matching" in body and "Mandaadi" not in body
    assert "movie_continue" not in keys(app)
    app.button(key=f"movie_{HANUMAN_ID}").click().run()
    assert app.session_state["movie_id"] == HANUMAN_ID
    assert app.session_state["step"] == 3
    assert on_theatre_step(app)


def test_free_text_with_a_selection_keeps_the_continue_control(seeded):
    app = run(step=2, location="hyderabad", movie_id=HANUMAN_ID, movie_query="mand")
    body = text(app)
    assert "2 movie(s) matching" in body
    assert "Selected:" in body and "movie_continue" in keys(app)


# ──────────────────────────────────────────────────────────────────────────
# 7 · Replacing or clearing the search leaves no stale selection behind
# ──────────────────────────────────────────────────────────────────────────
def test_replacing_the_search_choice_replaces_the_selection(seeded):
    """A different film from the box drops the previous film's theatres and
    formats — nothing of the old pick may ride along into the new monitor."""
    app = run(step=2, location="hyderabad", movie_id=HANUMAN_ID,
              theatres=["AMB"], formats={"AMB": ["HDR By Barco"]})
    app.selectbox(key="movie_query").select(MANDAADI_TE).run()
    assert app.session_state["movie_id"] == seeded
    assert app.session_state["theatres"] == []
    assert app.session_state["formats"] == {}
    assert app.session_state["step"] == 3
    assert on_theatre_step(app)


def test_clearing_the_search_keeps_the_pick_without_navigating(seeded):
    """An empty box is not a choice: the person's poster pick stays, the
    Continue control stays, and the wizard does not jump anywhere."""
    app = run(step=2, location="hyderabad", movie_id=HANUMAN_ID, movie_query=HANUMAN)
    app.selectbox(key="movie_query").set_value(None).run()
    assert app.session_state["step"] == 2
    assert app.session_state["movie_id"] == HANUMAN_ID
    assert "movie_continue" in keys(app)


def test_choosing_the_same_film_again_still_advances(seeded):
    """The old code remembered the last choice to avoid re-selecting on every
    rerun; with a callback there is nothing to remember, and re-choosing a
    film after coming back must work like the first time."""
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    app.button(key="back").click().run()
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert app.session_state["step"] == 3
    assert on_theatre_step(app)


def test_the_choice_is_a_callback_not_a_script_body_check():
    """Guard the shape of the fix: the search box has an ``on_change``, and
    ``step_movie`` never calls ``_select_movie`` inline."""
    tree = ast.parse(Path(flow.__file__).read_text(encoding="utf-8"))
    step_movie = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "step_movie")
    inline_calls = [n for n in ast.walk(step_movie)
                    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_select_movie"]
    assert not inline_calls, "step_movie must not select from the script body"
    selectbox = next(n for n in ast.walk(step_movie)
                     if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "selectbox")
    assert any(kw.arg == "on_change" for kw in selectbox.keywords)
    assert "movie_query_seen" not in flow.DEFAULTS


# ──────────────────────────────────────────────────────────────────────────
# 8 · Phone / tablet: the same selection through the folded grid
# ──────────────────────────────────────────────────────────────────────────
def test_movie_grid_rows_are_keyed_for_the_phone_layout(seeded):
    """The theme's phone rules size ``trgrid_`` rows; every poster row must
    carry that key or the tiles do not fold, and a tile's button is still
    the whole hit target at 390px (``pick_`` container + button)."""
    mobile = theme.CSS[theme.CSS.index("@media (max-width: 768px) {"):]
    assert "trgrid_" in mobile and "--tr-cols" in mobile
    assert "pick_" in theme.CSS
    src = Path(flow.__file__).read_text(encoding="utf-8")
    assert 'st.container(key=f"trgrid_{key}_{row // per_row}")' in src
    # Selecting through the folded grid is the same button as on a desktop.
    app = run(step=2, location="hyderabad")
    app.button(key=f"movie_{seeded}").click().run()
    assert app.session_state["step"] == 3 and on_theatre_step(app)


# ──────────────────────────────────────────────────────────────────────────
# 9 · The sidebar ambience cannot touch the wizard
# ──────────────────────────────────────────────────────────────────────────
def test_sidebar_ambience_is_inert_to_movie_selection(seeded):
    """CSS-only, pointer-transparent, scoped to the sidebar: it renders no
    element and handles no event, so a selection behaves identically with
    it on the page."""
    ambience = theme.CSS[theme.CSS.index("Sidebar ambience"):]
    ambience = ambience[:ambience.index("the rail's own content stays above the room")]
    assert ambience.count("pointer-events: none") == 2      # both layers
    assert "<script" not in theme.CSS.lower()
    # Every rule in the block is a pseudo-element rooted at the sidebar, and
    # the reduced-motion override still holds the layer still.
    stripped = re.sub(r"/\*.*?\*/", "", ambience, flags=re.S)
    rules = [m.group(1).strip() for m in re.finditer(r"([^{}@]+)\{[^{}]*\}", stripped)]
    rules = [r for r in rules if r and not r.startswith(("0%", "50%", "100%"))]
    assert rules == ['[data-testid="stSidebar"]::before', '[data-testid="stSidebar"]::after'], rules
    reduced = theme.CSS[theme.CSS.index("@media (prefers-reduced-motion: reduce)"):]
    reduced = reduced[:reduced.index("}\n}") + 3]
    assert '[data-testid="stSidebar"]::before' in reduced and "animation: none" in reduced
    app = run(step=2, location="hyderabad")
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert app.session_state["step"] == 3 and on_theatre_step(app)


# ──────────────────────────────────────────────────────────────────────────
# Performance and isolation of the interaction
# ──────────────────────────────────────────────────────────────────────────
def test_selection_touches_no_store_and_parses_no_catalogue(seeded, monkeypatch):
    """The click itself is session state only: no catalogue parse, at most
    the page's one batched read, and no worker-side sharing logic."""
    from monitor import catalogue, state as state_mod

    app = run(step=2, location="hyderabad")
    reads: list[str] = []
    builds: list[str] = []
    real_load_many = state_mod.load_many
    real_list_entries = catalogue.list_entries

    def counting_load_many(*kinds):
        reads.extend(kinds)
        return real_load_many(*kinds)

    def counting_list_entries(slug):
        builds.append(slug)
        return real_list_entries(slug)

    monkeypatch.setattr(state_mod, "load_many", counting_load_many)
    # ``cv._build`` is ``st.cache_resource``d on the file's digest; a parse
    # is the one thing that reads the catalogue entries.
    monkeypatch.setattr(catalogue, "list_entries", counting_list_entries)

    t0 = time.perf_counter()
    app.selectbox(key="movie_query").select(HANUMAN).run()
    elapsed = time.perf_counter() - t0
    assert on_theatre_step(app)
    assert builds == [], "catalogue was re-parsed during movie selection"
    # One page run → at most one batched read of the four kinds; the old
    # code needed a second run to show anything, i.e. a second read.
    assert len(reads) <= 4
    assert elapsed < 5.0


def test_ui_does_not_import_worker_sharing():
    for name in ("flow", "home", "catalogue_view", "theme"):
        src = (UI_DIR / f"{name}.py").read_text(encoding="utf-8")
        assert "monitor.sharing" not in src and "from monitor import sharing" not in src, name
    app_src = (UI_DIR.parent / "app.py").read_text(encoding="utf-8")
    assert "monitor.sharing" not in app_src and "import sharing" not in app_src


# ──────────────────────────────────────────────────────────────────────────
# The lag (21 Sep 2026, second pass): "Browse all" is drawn only when asked
# ──────────────────────────────────────────────────────────────────────────
# Measured in the browser with the real 52-film Hyderabad catalogue: the
# movie step was 322 deltas / 89 KB per run and 230–1086 ms of main-thread
# work, three quarters of it the 52 poster tiles inside a *collapsed*
# expander, which Streamlit renders in full. Behind a toggle it is 98 deltas
# / 40 KB, and opening the step or coming back to it takes ~0.5 s instead
# of ~1.2 s. The tests below use the small fixture catalogue with the
# threshold lowered, so the collapsed state is exercised in-process.

@pytest.fixture
def big_catalogue(monkeypatch):
    """Make the three-film fixture count as a large city."""
    monkeypatch.setattr(flow, "BROWSE_ALL_THRESHOLD", 2)


def test_a_large_catalogue_draws_the_full_grid_only_when_asked(seeded, big_catalogue):
    app = run(step=2, location="hyderabad")
    ks = keys(app)
    assert "browse_all_movies" in ks and "hide_all_movies" not in ks
    assert not any(k.startswith("movie_bookmyshow:") for k in ks), "no hidden poster grid"
    assert any(k.startswith("pop_") for k in ks), "the shelf is still the first screen"
    app.button(key="browse_all_movies").click().run()
    ks = keys(app)
    assert "hide_all_movies" in ks and "browse_all_movies" not in ks
    assert {k for k in ks if k.startswith("movie_bookmyshow:")} == {
        f"movie_{seeded}", "movie_bookmyshow:ET00442702", "movie_bookmyshow:ET00507738"}
    app.button(key="hide_all_movies").click().run()
    assert not any(k.startswith("movie_bookmyshow:") for k in keys(app))


def test_a_poster_from_the_opened_grid_selects_in_one_run(seeded, big_catalogue):
    app = run(step=2, location="hyderabad")
    app.button(key="browse_all_movies").click().run()
    app.button(key=f"movie_{HANUMAN_ID}").click().run()
    assert app.session_state["movie_id"] == HANUMAN_ID
    assert app.session_state["step"] == 3 and on_theatre_step(app)
    # coming back, the grid stays open — the person opened it
    app.button(key="back").click().run()
    assert "hide_all_movies" in keys(app)
    assert app.session_state["show_all_movies"] is True


def test_search_and_the_shelf_never_need_the_grid(seeded, big_catalogue):
    """The primary paths — the search box and the shelf — work with the grid
    collapsed, and the free-text filter shows its matches regardless."""
    app = run(step=2, location="hyderabad")
    assert "browse_all_movies" in keys(app)
    app.selectbox(key="movie_query").select(HANUMAN).run()
    assert app.session_state["step"] == 3 and on_theatre_step(app)
    app = run(step=2, location="hyderabad", movie_query="mand")
    assert "2 movie(s) matching" in text(app)
    assert "browse_all_movies" not in keys(app), "the filter replaces the grid, as before"


def test_changing_the_city_collapses_the_grid_again(seeded, big_catalogue):
    import streamlit as st

    app = run(step=2, location="hyderabad", show_all_movies=True)
    assert "hide_all_movies" in keys(app)
    # ``reset_from(1)`` is what a city change calls; in bare mode it acts on
    # the process-wide session state, which is what is asserted here.
    st.session_state["show_all_movies"] = True
    flow.reset_from(1)
    assert st.session_state["show_all_movies"] is False
    assert "show_all_movies" in flow.DEFAULTS and flow.DEFAULTS["show_all_movies"] is False


def test_a_small_catalogue_still_shows_every_poster_at_once(seeded):
    """Twelve films or fewer: nothing to hide, nothing to click."""
    app = run(step=2, location="hyderabad")
    ks = keys(app)
    assert "browse_all_movies" not in ks and "hide_all_movies" not in ks
    assert any(k.startswith("movie_bookmyshow:") for k in ks)


def test_the_browse_row_is_styled_like_the_expander_it_replaces():
    css = theme.CSS
    assert "st-key-trbrowseall" in css
    row = css[css.index('[class*="st-key-trbrowseall"] .stButton button {'):]
    row = row[:row.index("/* Alerts")]
    assert "content:'BROWSE ALL'" in row and "content:'COLLAPSE'" in row
    assert "min-height:54px" in row and "border-radius:14px" in row
