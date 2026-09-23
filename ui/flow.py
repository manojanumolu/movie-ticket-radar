"""The five-step setup flow.

    1 Location → 2 Movie → 3 Theatres → 4 Formats → 5 Monitoring

Each step is a function that renders itself and reports whether it is
satisfied. ``app.py`` owns the page; this module owns the wizard, so the two
can change independently.

Two rules run through all of it:

* **No URL, ever.** The user picks a city and then a movie from the catalogue
  the backend synced. Nothing in this flow asks anyone for a BookMyShow link,
  an event code, or any other internal identifier.
* **Nothing is invented.** Movies, theatres and formats are only ever rendered
  from what a provider returned. When the catalogue could not be built, the
  UI says so — it never falls back to an empty grid that reads as "nothing's
  on". The featured-theatre shelf is a shortcut into that same list: a
  featured theatre that isn't screening the chosen film says so and cannot
  be picked.

Selectable tiles use the *pick* pattern from ``ui/theme.py``: the design's
exact HTML inside ``st.container(key="pick_…")`` plus one ``st.button`` that
the CSS stretches over the tile, so the whole tile is the hit target. The
numbered step rail uses the same pattern, which is what makes it a working
breadcrumb and not just a progress indicator.

Catalogue reads go through ``ui.catalogue_view`` — parsed once per file, so
every rerun (each click is one) costs a dictionary lookup, not a JSON parse.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import streamlit as st

from config.locations import LOCATIONS, enabled_locations, get_location
from config.theatre_capabilities import format_aliases, premium_formats
from config.timezone import IST, now_ist
from monitor.models import (ANY_FORMAT, Venue, date_codes_between, dedupe, describe_date_codes, is_infinity_vision,
                            is_marvel_title, normalise_format, short_date)
from ui import catalogue_view as cv
from ui import components as C

STEPS = ["Location", "Movie", "Theatres", "Formats", "Monitoring"]
INTERVALS = [10, 15, 30]
ADMIN_INTERVALS = [5, 10, 15, 30]

#: How many posters the "Now showing" shelf holds.
POPULAR_LIMIT = 6
#: Posters per row in the movie grids.
GRID_COLUMNS = 6
#: A movie's full theatre list stays behind "View all" once it is longer than
#: this — the six featured tiles are the first screen.
VIEW_ALL_THRESHOLD = 6
#: The city's full poster grid stays behind "Browse all" once it is longer
#: than this (two rows). Every tile is a container, an HTML block, a button
#: and a column — a 52-film city is ~220 deltas and most of the movie step's
#: render time in the browser, for posters nobody has asked to see yet. The
#: shelf and the search box are the first screen; the grid is drawn when
#: opened, exactly as the theatre step's "View all" is.
BROWSE_ALL_THRESHOLD = 12

#: Session keys the wizard owns, and what they reset to.
DEFAULTS = {
    "step": 1,
    "furthest": 1,
    "location": "",
    "movie_id": "",
    "theatres": [],
    "formats": {},
    "interval": 10,
    "movie_query": None,    # the search box: a catalogue label, free text, or nothing
    "theatre_nonce": 0,     # bumps to clear the theatre search box after a pick
    "show_all_theatres": False,   # the full movie-specific list, behind "View all"
    "show_all_movies": False,     # the city's full poster grid, behind "Browse all"
    "start_now": True,
    "date_mode": "any",     # any · single · range — which show dates count
    "show_dates": [],       # YYYYMMDD codes; [] = every date BookMyShow offers
}


#: Widget-backed keys the monitoring step owns. They are not in DEFAULTS
#: because Streamlit initialises them from the widget's ``value`` argument
#: when the widget is drawn — but they persist in the session exactly like
#: the keys above, so a reset has to drop them too.
WIDGET_KEYS = ("notify_email", "notify_email_draft", "until_date", "until_time",
               "start_now_toggle", "show_date_single", "show_date_range",
               "catwatch_categories", "catwatch_show_time")
#: Prefixes of the per-venue / per-nonce widget keys (format checkboxes,
#: theatre search boxes) — the exact keys depend on what was picked.
WIDGET_PREFIXES = ("fmt_", "theatre_query_")
RESET_FLAG = "wizard_reset_pending"
#: Set by a wizard callback whose entire effect is a change to *this
#: session's own* picks — the city, the film, a theatre, a format, an
#: interval, the step. Nothing stored moved, so the run that follows needs
#: no fresh read of the account's monitors, state, history or settings: the
#: page reuses the snapshot it drew a moment ago (``app.load_view``). On
#: Firestore that read is a network round trip, and the read cache lapses
#: after five seconds — long enough that anybody deliberating over a poster
#: paid for four of them on every click. Consumed once, by the run it was
#: set for; anything that writes (starting, stopping, extending or deleting
#: a monitor, saving settings or an avatar) never sets it and is read fresh
#: exactly as before.
UI_ONLY_KEY = "wizard_ui_only_run"


def boot() -> None:
    """Prepare the wizard's session keys for this run.

    Runs before any widget is drawn, so this is also where a reset asked for
    on the previous run (``request_reset``) is carried out: widget-backed keys
    cannot be touched while their widget is on screen.
    """
    if st.session_state.pop(RESET_FLAG, False):
        reset_wizard()
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value.copy() if isinstance(value, (list, dict)) else value)


def wizard_keys() -> list[str]:
    """Every session key that describes the monitor being set up."""
    dynamic = [k for k in st.session_state.keys()
               if isinstance(k, str) and k.startswith(WIDGET_PREFIXES)]
    return [k for k in DEFAULTS if k != "location"] + list(WIDGET_KEYS) + dynamic


def reset_wizard() -> None:
    """Forget the monitor that was being set up — every pick, every widget.

    Called once a monitor has been saved (and by anything else that wants a
    clean wizard). Only the city survives: it is where the person is, not
    something about the monitor they just made. Everything else — movie,
    theatres, formats, dates, interval, end time, email, step — goes, so the
    next monitor starts from nothing rather than inheriting the last one's
    details behind a step-1 screen.
    """
    for key in wizard_keys():
        st.session_state.pop(key, None)
    for key, value in DEFAULTS.items():
        if key != "location":
            st.session_state[key] = value.copy() if isinstance(value, (list, dict)) else value


def request_reset() -> None:
    """Ask for :func:`reset_wizard` at the start of the next run."""
    st.session_state[RESET_FLAG] = True


def mark_ui_only() -> None:
    """"The next run changes nothing stored." Set by the wizard's own
    callbacks; see :data:`UI_ONLY_KEY`."""
    st.session_state[UI_ONLY_KEY] = True


def take_ui_only() -> bool:
    """Whether this run is one of those, consuming the mark."""
    return bool(st.session_state.pop(UI_ONLY_KEY, False))


def goto(step: int, *, rerun: bool = True) -> None:
    """Move the wizard to ``step``.

    From an ``on_click`` callback pass ``rerun=False``: a callback runs
    *before* the script, so the run that follows already draws the new
    step — asking for another would be a second full run for nothing.
    """
    st.session_state["step"] = step
    st.session_state["furthest"] = max(st.session_state.get("furthest", 1), step)
    mark_ui_only()
    if rerun:
        st.rerun()


def _go(step: int) -> None:
    """``goto`` as a button callback."""
    goto(step, rerun=False)


def reset_from(step: int) -> None:
    """Clear everything downstream of a step the user just changed.

    Changing the city has to drop the movie, and changing the movie has to
    drop theatres and formats — carrying them forward would silently attach a
    monitor to theatres that do not screen the newly chosen film. Merely
    *revisiting* a step (the Back button, the step rail) resets nothing.
    """
    if step <= 1:
        st.session_state["movie_id"] = ""
        st.session_state["show_all_movies"] = False
    if step <= 2:
        st.session_state["theatres"] = []
        st.session_state["formats"] = {}
        st.session_state["show_all_theatres"] = False
    if step <= 3:
        st.session_state["formats"] = {
            k: v for k, v in st.session_state.get("formats", {}).items()
            if k in st.session_state.get("theatres", [])
        }


def grid(items, per_row: int, key: str, *, gap: str = "small"):
    """Yield ``(column, item)`` row by row: one ``st.columns`` per row of
    ``per_row``, each row inside ``st.container(key=f"trgrid_{key}_{row}")``.

    Row-major on purpose. One tall ``st.columns`` with items dealt into it
    by ``index % n`` reads the same on a desktop — but a phone folds every
    column set to two per row, and then column-major order puts item 6 next
    to item 0. Separate rows fold in reading order, and the ``trgrid_`` key
    is what the theme's mobile rules size (``--tr-cols``).
    """
    items = list(items)
    for row in range(0, len(items), per_row):
        with st.container(key=f"trgrid_{key}_{row // per_row}"):
            columns = st.columns(per_row, gap=gap)
            for column, item in zip(columns, items[row:row + per_row]):
                yield column, item


def pick(key: str, label: str, render, *, disabled: bool = False,
         on_click=None, args: tuple = ()) -> bool:
    """Render a tile and return True when it was clicked.

    ``render`` draws the design HTML; the button underneath is what Streamlit
    sees. ``key`` is the button's key (what tests click); the container gets
    ``pick_<key>`` so the theme can find the pair.

    ``on_click`` is where a tile's effect belongs. A callback runs before
    the script, so the one run that follows the click already draws the
    result. The old shape — ``if pick(...): change state; st.rerun()`` —
    drew the page with the stale selection first and then ran it all again
    (two full runs, twice the page's CSS over the wire) for every click.
    """
    with st.container(key=f"pick_{key}"):
        render()
        if disabled:
            return False
        return st.button(label, key=key, use_container_width=True, on_click=on_click, args=args)


# ──────────────────────────────────────────────────────────────────────────
# Navigation: the step rail and the Back button
# ──────────────────────────────────────────────────────────────────────────
def step_rail(step: int, furthest: int) -> None:
    """The 1–5 rail, and a second way back.

    Every step already reached is a button: clicking *2 Movie* from *3
    Theatres* returns to the movie step with the movie, theatres and formats
    still selected. Steps not yet reached are inert — there is nothing there
    to go to.
    """
    with st.container(key="trsteps"):
        columns = st.columns(len(STEPS), gap="small")
        for index, (column, label) in enumerate(zip(columns, STEPS), start=1):
            state = "now" if index == step else ("done" if index <= furthest else "todo")
            with column:
                reachable = state == "done"
                pick(f"step_{index}", f"Go to step {index}: {label}",
                     lambda i=index, lb=label, s=state: C.step_pip(i, lb, s),
                     disabled=not reachable, on_click=_go, args=(index,))
        C.html(f'<div class="tr-step-mobile">Step {step} of {len(STEPS)} · {C.e(STEPS[step - 1])}</div>')


def back_button(step: int) -> None:
    """The explicit way back, at the top of every step after the first."""
    if step <= 1:
        return
    with st.container(key="trback"):
        st.button(f"Back to {STEPS[step - 2]}", key="back", icon=":material/arrow_back:",
                  on_click=_go, args=(step - 1,))


# ──────────────────────────────────────────────────────────────────────────
# 1 · Location
# ──────────────────────────────────────────────────────────────────────────
def _choose_location(slug: str) -> None:
    mark_ui_only()
    if slug != st.session_state.get("location", ""):
        st.session_state["location"] = slug
        reset_from(1)
    goto(2, rerun=False)


def step_location() -> None:
    C.step_header(1, "Where are you watching?", "Pick your city and we'll load what's on.")

    locations = enabled_locations()
    coming = [loc for loc in LOCATIONS if not loc.enabled][:2]
    current = st.session_state.get("location", "")

    tiles = [(loc, True) for loc in locations] + [(loc, False) for loc in coming]
    for column, (loc, enabled) in grid(tiles, max(3, len(tiles)), "loc"):
        with column:
            if not enabled:
                C.location_tile(loc.name, loc.state, selected=False, enabled=False)
                continue
            selected = loc.slug == current
            pick(f"loc_{loc.slug}", "Selected" if selected else f"Choose {loc.name}",
                 lambda loc=loc, selected=selected: C.location_tile(loc.name, loc.state, selected),
                 on_click=_choose_location, args=(loc.slug,))

    if current:
        st.button("Continue to movies", type="primary", use_container_width=True,
                  key="loc_continue", icon=":material/arrow_forward:", on_click=_go, args=(2,))


# ──────────────────────────────────────────────────────────────────────────
# 2 · Movie
# ──────────────────────────────────────────────────────────────────────────
def _select_movie(movie_id: str) -> None:
    """A tile's ``on_click`` / the search box's ``on_change``: pick the movie
    and move on. Callback-only — ``goto(..., rerun=False)`` relies on the
    run that follows the callback drawing the theatre step; called from the
    script body it would set ``step`` after the page had already chosen
    which step to draw, and the click would look like it did nothing."""
    mark_ui_only()
    if movie_id != st.session_state.get("movie_id", ""):
        st.session_state["movie_id"] = movie_id
        reset_from(2)
    goto(3, rerun=False)


def _pick_from_movie_search() -> None:
    """The search box's ``on_change``.

    A catalogue label selects that movie; free text (``accept_new_options``)
    or a cleared box changes nothing — the grid below filters on it. This
    has to be a callback, not a check in the script: the choice lands before
    the page reads ``step``, so the same run draws the theatre step.
    """
    choice = st.session_state.get("movie_query") or ""
    chosen_id = cv.movie_for_label(choice, st.session_state.get("location", ""))
    if chosen_id:
        _select_movie(chosen_id)


def _set_show_all_movies(value: bool) -> None:
    mark_ui_only()
    st.session_state["show_all_movies"] = value


def _poster_grid(cards: list[cv.MovieCard], selected: str, *, compact: bool = False,
                 prefix: str = "movie_") -> None:
    """``prefix`` keeps the shelf's buttons distinct from the full grid's —
    the same movie can legitimately appear in both."""
    for column, card in grid(cards, GRID_COLUMNS, prefix.rstrip("_")):
        with column:
            is_sel = card.id == selected
            pick(f"{prefix}{card.id}", "Selected" if is_sel else f"Select {card.title}",
                 lambda c=card, is_sel=is_sel: C.poster_tile(
                     c.title, c.meta, is_sel, c.poster_url, compact=compact),
                 on_click=_select_movie, args=(card.id,))


def step_movie() -> bool:
    location = get_location(st.session_state["location"])
    C.step_header(2, "Select movie", f"What's on in {location.name} right now.")

    catalogue = cv.view(location.slug)
    state = catalogue.sync
    C.catalogue_banner(state["status"].value, state["message"], ago(state["at"]),
                       len(catalogue.movies))

    if not catalogue.movies:
        # Never let an unreadable catalogue masquerade as "no movies".
        if state["status"].is_failure:
            st.caption(
                "The background sync will retry automatically. You can also run it now "
                "from **Settings → Refresh catalogue**."
            )
        else:
            st.caption("Nothing cached for this city yet — run a sync from **Settings**.")
        return False

    selected = st.session_state.get("movie_id", "")

    # ── search: the whole catalogue, filtered in the browser as you type ──
    # A selectbox filters its options client-side on every keystroke, so
    # "av" shows "Avengers Endgame: Encore" immediately with no round trip.
    # Choosing a suggestion selects the movie (``on_change``, so the run
    # that follows already shows the theatre step); pressing Enter on free
    # text (accept_new_options) filters the grid below instead.
    C.html('<div class="tr-field-label">Search movies</div>')
    choice = st.selectbox(
        "Search movies", [m.label for m in catalogue.movies], index=None, key="movie_query",
        placeholder=f"Search movies playing in {location.name}…", label_visibility="collapsed",
        accept_new_options=True, filter_mode="contains", on_change=_pick_from_movie_search,
    )
    query = "" if cv.movie_for_label(choice or "", location.slug) else (choice or "")

    if selected:
        current = cv.card(selected, location.slug)
        if current is not None:
            C.selected_movie_card(current.title, current.meta, current.poster_url)
            st.button("Continue to theatres", type="primary", use_container_width=True,
                      key="movie_continue", icon=":material/arrow_forward:", on_click=_go, args=(3,))

    if query:
        matches = cv.search_movies(query, location.slug)
        if not matches:
            st.caption(f"No movie in the {location.name} listing matches “{query}”.")
            return bool(selected)
        st.caption(f"{len(matches)} movie(s) matching “{query}” · tap a poster to select it")
        _poster_grid(matches[:GRID_COLUMNS * 4], selected)
    else:
        shelf = cv.popular(location.slug, POPULAR_LIMIT)
        if shelf:
            C.rule("Or change selection" if selected else "Now showing")
            _poster_grid(shelf, selected, prefix="pop_")
        rest = catalogue.movies
        st.caption(f"{len(rest)} movie(s) · tap a poster to select it")
        # The full grid is drawn only once asked for. A collapsed expander
        # still renders every tile underneath — for a 52-film city that was
        # three quarters of the step's elements, hidden.
        show_all = (st.session_state.get("show_all_movies", False)
                    or not shelf or len(rest) <= BROWSE_ALL_THRESHOLD)
        if not show_all:
            with st.container(key="trbrowseall"):
                st.button(f"Browse all {len(rest)} movies in {location.name}", key="browse_all_movies",
                          use_container_width=True, icon=":material/expand_more:",
                          on_click=_set_show_all_movies, args=(True,))
        else:
            if shelf and len(rest) > BROWSE_ALL_THRESHOLD:
                with st.container(key="trbrowseall"):
                    st.button(f"All {len(rest)} movies in {location.name}", key="hide_all_movies",
                              use_container_width=True, icon=":material/expand_less:",
                              on_click=_set_show_all_movies, args=(False,))
            _poster_grid(rest, selected, compact=True)

    return bool(selected)


# ──────────────────────────────────────────────────────────────────────────
# 3 · Theatres
# ──────────────────────────────────────────────────────────────────────────
def _toggle_theatre(code: str, listed: list[Venue]) -> None:
    """Select or deselect one theatre.

    The chosen list keeps the movie's listed theatres first, in listing
    order, then any theatre being watched for release (one the catalogue
    knows from other films but that hasn't listed this movie yet), in the
    order they were picked.
    """
    mark_ui_only()
    chosen = list(st.session_state.get("theatres", []))
    if code in chosen:
        chosen.remove(code)
        st.session_state["formats"].pop(code, None)
    else:
        chosen.append(code)
    listed_codes = [v.code for v in listed]
    st.session_state["theatres"] = (
        [c for c in listed_codes if c in chosen] + [c for c in chosen if c not in listed_codes]
    )


def _select_all(venues: list[Venue]) -> None:
    """Every theatre listed for the film — or none, when they all already
    are; release-watch picks stay as they are."""
    mark_ui_only()
    chosen = list(st.session_state.get("theatres", []))
    every = [v.code for v in venues]
    extra = [c for c in chosen if c not in every]
    listed_now = [] if set(every) <= set(chosen) and every else every
    st.session_state["theatres"] = listed_now + extra


def _pick_from_search(nonce: int, venues: list[Venue]) -> None:
    """The search box's ``on_change``: toggle the picked theatre and bump
    the box's key so the run that follows draws it empty again."""
    code = st.session_state.get(f"theatre_query_{nonce}")
    if code:
        st.session_state["theatre_nonce"] = nonce + 1
        _toggle_theatre(code, venues)


def _set_show_all(value: bool) -> None:
    mark_ui_only()
    st.session_state["show_all_theatres"] = value


def _theatre_label(venue: Venue, city: str, listed: bool) -> str:
    """What the theatre search shows: name · area · status or premium format."""
    parts = [venue.name, venue.area or city]
    if not listed:
        parts.append("Coming soon")
    shown = venue.formats if listed else premium_formats(venue.code)
    if shown:
        parts.append(shown[0])
    return " · ".join(parts)


def step_theatres() -> list[Venue]:
    slug = st.session_state.get("location", "")
    movie = cv.movie(st.session_state.get("movie_id", ""), slug)
    if movie is None:
        st.caption("Pick a movie first.")
        return []

    # Every theatre the catalogue holds for *this* movie — the whole film,
    # every format, nothing added and nothing left out. When show dates were
    # chosen (a return from the last step), "listed" means listed on them.
    dates = list(st.session_state.get("show_dates", []))
    venues = cv.venues(movie.id, slug)
    not_on_dates = cv.coming_soon_codes(movie.id, slug, [v.code for v in venues], dates) if dates else set()
    venues = [v for v in venues if v.code not in not_on_dates]
    directory = cv.view(slug).directory

    head, action = st.columns([3.2, 1], vertical_alignment="center")
    with head:
        C.step_header(3, "Where do you want to watch?",
                      f"Theatres showing {movie.title} in {movie.city} — or ones you want watched "
                      "until they release it.")
    with action:
        st.button("Select all", key="select_all", use_container_width=True,
                  icon=":material/done_all:", on_click=_select_all, args=(venues,),
                  help="Select every theatre showing this movie (again to clear)")
    if not venues and not directory:
        problem = st.session_state.get("detail_problem", "")
        if problem:
            C.catalogue_banner("BLOCKED", problem, "", 0)
        else:
            C.catalogue_banner("EMPTY", "", ago(cv.view(movie.region_slug).sync["at"]), 0)
            st.caption(
                f"BookMyShow lists **{movie.title}** but hasn't published any theatres for "
                "it yet — that's normal before a release opens. The background sync will "
                "pick them up as soon as they appear."
            )
        return []

    chosen = list(st.session_state.get("theatres", []))

    # ── search: client-side, over every theatre the city catalogue knows ──
    # A theatre not yet listed for this movie is offered as "coming soon":
    # picking it watches it until BookMyShow releases tickets there.
    listed_codes = {v.code for v in venues}
    by_code = {**directory, **{v.code: v for v in venues}}
    search_codes = [v.code for v in venues] + sorted(
        (c for c in directory if c not in listed_codes), key=lambda c: directory[c].name.lower())
    nonce = st.session_state.get("theatre_nonce", 0)
    C.html('<div class="tr-field-label">Search theatres</div>')
    st.selectbox(
        "Search theatres", search_codes, index=None,
        format_func=lambda c: _theatre_label(by_code[c], movie.city, c in listed_codes),
        key=f"theatre_query_{nonce}", placeholder=f"Search theatres in {movie.city}…",
        label_visibility="collapsed", filter_mode="contains",
        on_change=_pick_from_search, args=(nonce, venues),
    )

    # ── featured quick-picks: released, coming soon, or unknown ──────────
    shelf = cv.featured(venues, directory)
    C.rule("Featured theatres")
    for column, match in grid(shelf, 3, "feat"):
        with column:
            if match.venue is None:
                C.featured_tile(match.pick.name, match.pick.area, None, False, released=False)
                continue
            is_sel = match.venue.code in chosen
            label = "Selected ✓" if is_sel else (
                f"Select {match.venue.name}" if match.released else f"Watch {match.venue.name} for release")
            pick(f"feat_{match.venue.code}", label,
                 lambda m=match, is_sel=is_sel: C.featured_tile(
                     m.pick.name, m.pick.area, m.venue, is_sel, released=m.released),
                 on_click=_toggle_theatre, args=(match.venue.code, venues))

    # ── the full list: behind "View all" when it is long ─────────────────
    # Six featured tiles are the first screen. A film playing at 70 theatres
    # is a wall of cards nobody asked to see yet; a film playing at three is
    # not worth hiding. The full, movie-specific list is unchanged either way.
    featured_codes = {m.venue.code for m in shelf if m.venue is not None}
    show_all = st.session_state.get("show_all_theatres", False) or len(venues) <= VIEW_ALL_THRESHOLD
    if not venues:
        C.rule("All theatres · 0")
        st.caption(f"No theatre has listed **{movie.title}** yet — pick the ones you want "
                   "watched, above, and we'll tell you the moment tickets open.")
    elif not show_all:
        with st.container(key="trviewall"):
            st.button(f"View all {len(venues)} theatres", key="view_all_theatres",
                      use_container_width=True, icon=":material/expand_more:",
                      on_click=_set_show_all, args=(True,))
    else:
        head, hide = st.columns([3, 1], vertical_alignment="center")
        with head:
            C.rule(f"All theatres · {len(venues)}")
        with hide:
            if len(venues) > VIEW_ALL_THRESHOLD:
                st.button("Show fewer", key="hide_all_theatres", use_container_width=True,
                          icon=":material/expand_less:", on_click=_set_show_all, args=(False,))
        for column, venue in grid(venues, 2, "th"):
            with column:
                is_sel = venue.code in chosen
                label = "Selected ✓" if is_sel else f"Select {venue.name}"
                pick(f"th_{venue.code}", label,
                     lambda v=venue, is_sel=is_sel: C.theatre_row(
                         v.name, v.area, list(v.formats), is_sel, v.abbr,
                         featured=v.code in featured_codes),
                     on_click=_toggle_theatre, args=(venue.code, venues))

    # ── theatres being watched for release (chosen, not listed yet) ──────
    watching = [by_code[c] for c in chosen if c not in listed_codes and c in by_code]
    if watching:
        C.rule(f"Watching for release · {len(watching)}")
        for column, venue in grid(watching, 2, "watch"):
            with column:
                pick(f"th_{venue.code}", "Selected ✓",
                     lambda v=venue: C.theatre_row(v.name, v.area, list(premium_formats(v.code)), True, v.abbr,
                                                   featured=v.code in featured_codes, coming=True),
                     on_click=_toggle_theatre, args=(venue.code, venues))

    picked = cv.selected_venues(movie.id, slug, chosen)
    if picked:
        st.caption(f"{len(picked)} theatre(s) selected: " + ", ".join(v.name for v in picked))
        st.button("Continue to formats", type="primary", use_container_width=True,
                  key="th_continue", icon=":material/arrow_forward:", on_click=_go, args=(4,))
    else:
        st.caption("Select at least one theatre to continue.")
    return picked


# ──────────────────────────────────────────────────────────────────────────
# 4 · Formats
# ──────────────────────────────────────────────────────────────────────────
def infinity_vision_note(venues: list[Venue]) -> None:
    """For a Marvel film only: which of the chosen theatres BookMyShow lists
    an Infinity Vision screen at, 2D and 3D, from the catalogue's own format
    strings — nothing is inferred from a theatre's name. Silent when the
    film is not Marvel's or no chosen theatre lists one."""
    movie = cv.movie(st.session_state.get("movie_id", ""), st.session_state.get("location", ""))
    if movie is None or not is_marvel_title(movie.title):
        return
    where = [(v.name, [f for f in v.formats if is_infinity_vision(f)]) for v in venues]
    where = [(name, fmts) for name, fmts in where if fmts]
    if not where:
        return
    listed = " · ".join(f"{name} ({', '.join(f.replace('Infinity Vision ', '') for f in fmts)})" for name, fmts in where)
    C.html(f'<div class="tr-cta-help">{C.icon("format", 15, "#E8B25C")}<span><b>Infinity Vision</b> — '
           f"BookMyShow lists it at {C.e(listed)}. Pick it below to watch that screen.</span></div>")


def unknown_formats(venue: Venue, chosen: list[str], capable: tuple[str, ...] = (),
                    watchable: tuple[str, ...] = ()) -> list[str]:
    """The chosen formats this theatre is not known to run at all.

    ``venue.formats`` is what this movie lists at the theatre, ``capable``
    the theatre's verified premium screens plus every format BookMyShow has
    been seen to use there, and ``watchable`` the formats BookMyShow sells
    *this film* in in this city through its own sibling events
    (``catalogue_view.release_watch_formats`` — Infinity Vision), which may
    be watched at a chosen theatre before that theatre lists them. A format
    outside all of that is not "unreleased" — it has no basis — and is the
    one thing the monitoring step refuses. Any format always passes, and a
    theatre with no known formats has nothing reliable to check against, so
    everything passes for it: the existing behaviour, unchanged.
    """
    known = {normalise_format(f) for f in (*venue.formats, *capable, *watchable)}
    if not known:
        return []
    return [f for f in chosen if f != ANY_FORMAT and normalise_format(f) not in known]


def step_formats(venues: list[Venue], coming: set[str] | None = None,
                 listed: dict[str, tuple[str, ...]] | None = None,
                 capable: dict[str, tuple[str, ...]] | None = None,
                 dates_by_venue: dict[str, dict[str, tuple[str, ...]]] | None = None,
                 dates: list[str] | None = None,
                 release: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """``venues`` carry exactly the formats BookMyShow lists *this movie* in
    at each theatre — on the chosen show dates when there are any, else on
    the dates the catalogue read (see ``catalogue_view.selected_venues``);
    those are the options. ``coming`` names the theatres the movie is not
    listed at (on those dates); ``capable`` is what each theatre runs for
    other films, shown as such and never offered; ``dates_by_venue`` is
    date -> formats per theatre, for the hints; ``release`` is the formats
    BookMyShow sells *this film* in in this city through its own sibling
    events (``catalogue_view.release_watch_formats``), offered at every
    chosen theatre that has not listed them yet — the format-release
    watch."""
    coming = coming or set()
    capable = capable or {}
    dates_by_venue = dates_by_venue or {}
    dates = [d for d in (dates or []) if d]
    C.step_header(4, "Formats, per theatre",
                  "The formats BookMyShow lists this movie in at that theatre"
                  + (f" on {describe_date_codes(dates)}" if dates else "") + ". A theatre that hasn't "
                  "listed the movie yet offers its premium screens to wait for, or every format.")

    if not venues:
        st.caption("Pick a theatre first.")
        return {}

    infinity_vision_note(venues)
    formats: dict[str, list[str]] = dict(st.session_state.get("formats", {}))
    for venue in venues:
        premium = list(capable.get(venue.code, ()))
        listed = dedupe(venue.formats)
        is_coming = venue.code in coming or not listed

        def covered(fmt: str) -> bool:
            """Is this premium screen what the movie is listed in here, under
            its own name or BookMyShow's other name for it ("Led Screen
            Dolby Atmos" is AAA's EPIQ)?"""
            names = [fmt, *format_aliases(venue.code, fmt)]
            return any(normalise_format(n) in normalise_format(label) for n in names for label in listed)

        def is_alias_label(label: str) -> bool:
            """A listed label that only names a premium screen by its other
            name folds into that screen's option."""
            return any(normalise_format(a) in normalise_format(label)
                       for f in premium for a in format_aliases(venue.code, f))

        # The options: every verified premium screen — a permanent watch
        # target, listed or not — then the formats BookMyShow sells this
        # film in in this city that no capability list can name (Infinity
        # Vision), also watchable before this theatre lists them, then
        # whatever else BookMyShow lists the movie in here (the ordinary
        # formats). Nothing else.
        watch = dedupe([*premium, *[f for f in release
                                    if not any(normalise_format(q) == normalise_format(f) for q in premium)]])
        options = dedupe([*watch, *[f for f in listed if not is_alias_label(f)
                                    and not any(normalise_format(q) == normalise_format(f) for q in watch)]])
        current = {f for f in options if (f in watch and covered(f)) or (f not in watch)}
        waiting = [f for f in watch if not covered(f)]
        listed_on = dates_by_venue.get(venue.code, {})
        when = ""
        if dates and is_coming:
            when = describe_date_codes(dates)
        elif listed_on and not is_coming:
            when = "on " + ", ".join(short_date(d) for d in sorted(listed_on))
        with st.container(key=f"trpanel_{venue.code}"):
            left, right = st.columns([1, 1.6], gap="medium")
            with left:
                C.format_panel_head(venue.name, venue.area, listed[0] if listed else "",
                                    coming=is_coming, expected=waiting if not is_coming else [], when=when,
                                    premium=premium if is_coming else [])
            with right:
                if not options:
                    st.caption("Not listed for this movie here yet, and no premium screen to wait for — "
                               "watching every format until it opens.")
                    formats[venue.code] = [ANY_FORMAT]
                    continue

                # Widget keys are namespaced by venue code, which is what keeps
                # "AMB → HDR by Barco" from ever leaking into "Allu → Dolby Cinema".
                chosen: list[str] = []
                any_key = f"fmt_{venue.code}_any"
                any_on = st.checkbox("Any format", key=any_key,
                                     value=ANY_FORMAT in formats.get(venue.code, []),
                                     help="Watch every format this theatre runs — alerts on the first to open.")
                for fmt in options:
                    # Said at the box itself: CURRENTLY LISTED — the dates
                    # BookMyShow lists this movie in this format here — or
                    # a premium screen the movie is not in yet, watched
                    # until it opens.
                    names = [fmt, *format_aliases(venue.code, fmt)]
                    on = sorted(d for d, fmts in listed_on.items()
                                if any(normalise_format(n) in normalise_format(f) for n in names for f in fmts))
                    if fmt in current:
                        # BookMyShow's own label for it, when that differs — the listing is the source
                        under = dedupe(f for fmts in listed_on.values() for f in fmts
                                       if any(normalise_format(n) in normalise_format(f) for n in names)
                                       and normalise_format(f) != normalise_format(fmt))
                        as_named = f" as “{under[0]}” on BookMyShow" if under else ""
                        hint = ("Currently listed for this movie here on " + ", ".join(short_date(d) for d in on)
                                + as_named + "." if on else f"Currently listed for this movie at this theatre{as_named}.")
                    elif fmt in premium:
                        hint = (f"{venue.name}'s {fmt} screen (HyderabadTheatres). Not yet listed for this movie — "
                                "watching until it opens here in this format.")
                    else:
                        # A format-release watch: BookMyShow sells this film
                        # in it in this city, this theatre has not listed it.
                        hint = (f"BookMyShow sells this movie in {fmt} in this city but hasn't listed it at "
                                f"{venue.name} — watching until it appears here in this format.")
                    if st.checkbox(fmt, key=f"fmt_{venue.code}_{fmt}",
                                   value=fmt in formats.get(venue.code, []), help=hint):
                        chosen.append(fmt)
                if any_on:
                    chosen = [ANY_FORMAT]
                formats[venue.code] = chosen

    st.session_state["formats"] = formats
    unset = [v.name for v in venues if not formats.get(v.code)]
    if unset:
        st.caption("Pick at least one format for: " + ", ".join(unset) + ".")
        return formats

    total = sum(len(formats[v.code]) for v in venues)
    st.caption(f"Watching {total} theatre/format combination(s) independently.")
    st.button("Continue to monitoring", type="primary", use_container_width=True,
              key="fmt_continue", icon=":material/arrow_forward:", on_click=_go, args=(5,),
              help="Next: how often to check, until when, and where to email you")
    return formats


# ──────────────────────────────────────────────────────────────────────────
# 5 · Monitoring
# ──────────────────────────────────────────────────────────────────────────
DATE_MODES = [("any", "Any date", "Every date on sale"),
              ("single", "Single date", "One show date"),
              ("range", "Date range", "First to last day")]


def _set(key: str, value) -> None:
    """A tile that sets one value — as a callback, so one run draws it."""
    mark_ui_only()
    st.session_state[key] = value


def step_monitoring(default_email: str, *, is_admin: bool = False) -> tuple[int, datetime, str, bool, list[str]]:
    C.rule("Schedule")
    left, right = st.columns(2, gap="large")

    with left:
        C.step_header(5, "How often should I check?", "Checks run automatically in the background.")
        intervals = ADMIN_INTERVALS if is_admin else INTERVALS
        current = st.session_state.get("interval", 10)
        if current not in intervals:
            current = 10
        for column, minutes in grid(intervals, 4 if is_admin else 3, "interval"):
            with column:
                pick(f"interval_{minutes}", f"Every {minutes} minutes",
                     lambda m=minutes, sel=(minutes == current): C.interval_tile(m, sel),
                     on_click=_set, args=("interval", minutes))
        interval = current
        st.caption("The first check runs as soon as you start; after that, about every "
                   f"{interval} minutes.")

    with right:
        C.step_header("clock", "Monitor until", "The monitor stops itself after this time.")
        with st.container(key="trpair_until"):
            date_col, time_col = st.columns([1.5, 1], gap="small")
        end_date = date_col.date_input("End date", value=(now_ist() + timedelta(days=1)).date(),
                                       min_value=now_ist().date(), format="DD/MM/YYYY",
                                       key="until_date", label_visibility="collapsed",
                                       help="The last day this monitor keeps checking")
        end_time = time_col.time_input("End time", value=dtime(23, 59), step=timedelta(minutes=15),
                                       key="until_time", label_visibility="collapsed",
                                       help="The time on that day it stops")
        until = datetime.combine(end_date, end_time, tzinfo=IST)
        start_now = st.toggle("Start checking immediately", key="start_now_toggle",
                              value=bool(st.session_state.get("start_now", True)),
                              help="Runs the first check the moment you press Start, "
                                   "instead of waiting for the next scheduled run.")
        st.session_state["start_now"] = bool(start_now)

    # Which *show* dates to watch — a different thing from how long to monitor.
    C.rule("Show dates")
    C.step_header("calendar", "Which show dates?",
                  "Only shows on these dates count. Leave on Any date to watch every date on sale.")
    mode = st.session_state.get("date_mode", "any")
    if mode not in {m for m, _, _ in DATE_MODES}:
        mode = "any"
    for column, (value, label, sub) in grid(DATE_MODES, 3, "datemode"):
        with column:
            pick(f"datemode_{value}", label,
                 lambda label=label, sub=sub, sel=(value == mode): C.choice_tile(label, sub, sel),
                 on_click=_set, args=("date_mode", value))
    tomorrow = (now_ist() + timedelta(days=1)).date()
    show_dates: list[str] = []
    if mode == "single":
        day = st.date_input("Show date", value=tomorrow, min_value=now_ist().date(),
                            format="DD/MM/YYYY", key="show_date_single", label_visibility="collapsed")
        show_dates = date_codes_between(day, day)
    elif mode == "range":
        picked = st.date_input("Show dates", value=(tomorrow, tomorrow + timedelta(days=3)),
                               min_value=now_ist().date(), format="DD/MM/YYYY",
                               key="show_date_range", label_visibility="collapsed")
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            show_dates = date_codes_between(picked[0], picked[1])
        elif isinstance(picked, (tuple, list)) and len(picked) == 1:
            show_dates = date_codes_between(picked[0], picked[0])
            st.caption("Pick the last day of the range too.")
    st.session_state["show_dates"] = show_dates
    if show_dates:
        st.caption(f"Watching shows on {describe_date_codes(show_dates)} "
                   f"({len(show_dates)} day{'s' if len(show_dates) != 1 else ''}).")
        date_listing_note(show_dates)
    else:
        st.caption("Watching every date BookMyShow has on sale.")

    C.rule("Alerts")
    C.step_header("mail", "Where should we email you?", "One message the moment tickets open — nothing else.")
    C.html('<div class="tr-field-label">Notification email</div>')
    # ``default_email`` fills the box only while nothing has been typed: the
    # draft is what the person last entered, kept outside the widget so it
    # survives a visit to another page (Streamlit drops a widget's own state
    # when the widget is not drawn) and is never overwritten by a rerun.
    draft = st.session_state.get("notify_email_draft")
    email = st.text_input("Notification email", value=default_email if draft is None else draft,
                          placeholder="you@gmail.com", label_visibility="collapsed",
                          key="notify_email", on_change=_remember_email,
                          help="Where the alert is sent. Prefilled with your account's email — change it if you like.")
    return interval, until, email.strip(), bool(start_now), show_dates


def date_listing_note(show_dates: list[str]) -> None:
    """For the chosen show dates, which chosen theatre/format pairs BookMyShow
    does not list this movie in yet — from the catalogue's per-date
    listings; nothing is fetched. The monitor watches for them; the person
    is told, not blocked."""
    slug = st.session_state.get("location", "")
    movie_id = st.session_state.get("movie_id", "")
    codes = list(st.session_state.get("theatres", []))
    formats: dict[str, list[str]] = st.session_state.get("formats", {})
    if not (movie_id and codes):
        return
    known = cv.listings(movie_id, slug)
    if not any(d in known for d in show_dates):
        return                                   # the catalogue has not read these dates: nothing to say
    listed = cv.listed_formats(movie_id, slug, codes, show_dates)
    names = {v.code: v.name for v in cv.selected_venues(movie_id, slug, codes)}
    missing = []
    for code in codes:
        here = {normalise_format(f) for f in listed.get(code, ())}
        for fmt in formats.get(code) or [ANY_FORMAT]:
            if fmt == ANY_FORMAT:
                if not here:
                    missing.append(names.get(code, code))
            elif normalise_format(fmt) not in here:
                missing.append(f"{names.get(code, code)} · {fmt}")
    if missing:
        st.caption(f"Not listed on {describe_date_codes(show_dates)} yet: " + "; ".join(dedupe(missing))
                   + " — you'll be emailed when it is.")


def _remember_email() -> None:
    st.session_state["notify_email_draft"] = st.session_state.get("notify_email", "")


# ──────────────────────────────────────────────────────────────────────────
# Category watch — admin only
# ──────────────────────────────────────────────────────────────────────────
#: The admin's picks on the Monitoring step: the seat categories a show must
#: have bookable, and optionally one show time. Both are session widgets and
#: are dropped with the rest of the wizard on reset.
CATEGORY_WATCH_KEY = "catwatch_categories"
SHOW_TIME_KEY = "catwatch_show_time"
CATEGORY_WATCH_TITLE = "Category watch"


def step_category_watch(venues: list[Venue], *, is_admin: bool = False) -> tuple[list[str], str]:
    """Admin only: turn this monitor into a *category watch* — an alert when
    one of the chosen seat categories (GOLD, PLATINUM…) becomes bookable at
    the theatres, format and dates already picked, optionally at one show
    time. Draws nothing for anyone else. The choices are the categories the
    catalogue has seen those theatres list; the worker reads the live ones
    from the same response it already fetches. Returns (categories, show
    time); empty means an ordinary monitor.
    """
    if not is_admin:
        return [], ""
    C.rule(f"{CATEGORY_WATCH_TITLE} · admin")
    C.step_header("ticket", "Watch specific seat categories?",
                  "Leave empty for a normal monitor. With categories chosen, you're emailed only when one "
                  "of them opens up — not on the first check, and not again while it stays open.")
    seen = {c.label: c.key for v in venues for c in v.categories}
    if not seen:
        st.caption("The catalogue hasn't seen seat categories at these theatres yet — the next sync "
                   "will list them. A normal monitor is still available.")
        st.session_state[CATEGORY_WATCH_KEY] = []
        return [], st.session_state.get(SHOW_TIME_KEY, "").strip()
    with st.container(key="trpair_catwatch"):
        left, right = st.columns([1.6, 1], gap="small")
    chosen = left.multiselect("Seat categories", list(seen), key=CATEGORY_WATCH_KEY,
                              placeholder="Choose categories…", label_visibility="collapsed",
                              help="Only the categories BookMyShow lists at the chosen theatres")
    show_time = right.text_input("Show time", key=SHOW_TIME_KEY, placeholder="Show time, e.g. 07:15 PM",
                                 label_visibility="collapsed",
                                 help="Optional. One show only; leave empty to watch every show at the theatre.")
    if chosen:
        st.caption(f"Category watch: {', '.join(chosen)}"
                   + (f" at {show_time.strip()}" if show_time.strip() else " at every show")
                   + " — for the theatres, format and dates above.")
    return [seen[label] for label in chosen if label in seen], show_time.strip()


# ──────────────────────────────────────────────────────────────────────────
def summary(step: int) -> None:
    """Everything already answered, as one compact strip above the step card."""
    slug = st.session_state.get("location", "")
    items: list[tuple[int, str, str]] = []
    if step > 1 and slug:
        items.append((1, "Location", get_location(slug).label))
    movie = cv.movie(st.session_state.get("movie_id", ""), slug) if step > 2 else None
    if movie is not None:
        items.append((2, "Movie", f"{movie.title}{f' · {movie.language}' if movie.language else ''}"))
    codes = list(st.session_state.get("theatres", []))
    names = {v.code: v.name for v in cv.selected_venues(st.session_state.get("movie_id", ""), slug, codes)}
    if step > 3 and codes:
        items.append((3, "Theatres", ", ".join(names.get(c, c) for c in codes)))
    if step > 4 and st.session_state.get("formats"):
        parts = [
            f"{names.get(code, code)} → {', '.join(fmts)}"
            for code, fmts in st.session_state["formats"].items()
            if fmts
        ]
        items.append((4, "Formats", " · ".join(parts)))
    C.summary_strip(items)


def ago(when) -> str:
    if when is None:
        return "never"
    delta = (now_ist() - when).total_seconds()
    if delta < 90:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


__all__ = [
    "INTERVALS",
    "CATEGORY_WATCH_KEY",
    "CATEGORY_WATCH_TITLE",
    "SHOW_TIME_KEY",
    "STEPS",
    "VIEW_ALL_THRESHOLD",
    "ago",
    "back_button",
    "boot",
    "date_listing_note",
    "goto",
    "grid",
    "infinity_vision_note",
    "pick",
    "request_reset",
    "reset_from",
    "reset_wizard",
    "step_formats",
    "step_location",
    "step_monitoring",
    "step_movie",
    "step_category_watch",
    "step_rail",
    "step_theatres",
    "summary",
    "unknown_formats",
    "wizard_keys",
]
