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
  on".

Selectable tiles use the *pick* pattern from ``ui/theme.py``: the design's
exact HTML inside ``st.container(key="pick_…")`` plus one ``st.button`` that
the CSS stretches over the tile, so the whole tile is the hit target.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import streamlit as st

from config.locations import LOCATIONS, enabled_locations, get_location
from config.timezone import IST, now_ist
from monitor import catalogue
from monitor.models import ANY_FORMAT, Venue, dedupe
from ui import components as C

STEPS = ["Location", "Movie", "Theatres", "Formats", "Monitoring"]
INTERVALS = [10, 15, 30]

#: Session keys the wizard owns, and what they reset to.
DEFAULTS = {
    "step": 1,
    "furthest": 1,
    "location": "",
    "movie_id": "",
    "theatres": [],
    "formats": {},
    "interval": 10,
    "movie_query": "",
    "start_now": True,
}


def boot() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value.copy() if isinstance(value, (list, dict)) else value)


def goto(step: int) -> None:
    st.session_state["step"] = step
    st.session_state["furthest"] = max(st.session_state.get("furthest", 1), step)
    st.rerun()


def reset_from(step: int) -> None:
    """Clear everything downstream of a step the user just changed.

    Changing the city has to drop the movie, and changing the movie has to
    drop theatres and formats — carrying them forward would silently attach a
    monitor to theatres that do not screen the newly chosen film.
    """
    if step <= 1:
        st.session_state["movie_id"] = ""
    if step <= 2:
        st.session_state["theatres"] = []
        st.session_state["formats"] = {}
    if step <= 3:
        st.session_state["formats"] = {
            k: v for k, v in st.session_state.get("formats", {}).items()
            if k in st.session_state.get("theatres", [])
        }


def pick(key: str, label: str, render, *, disabled: bool = False) -> bool:
    """Render a tile and return True when it was clicked.

    ``render`` draws the design HTML; the button underneath is what Streamlit
    sees. ``key`` is the button's key (what tests click); the container gets
    ``pick_<key>`` so the theme can find the pair.
    """
    with st.container(key=f"pick_{key}"):
        render()
        if disabled:
            return False
        return st.button(label, key=key, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────
# 1 · Location
# ──────────────────────────────────────────────────────────────────────────
def step_location() -> None:
    C.step_header(1, "Where are you watching?", "Pick your city and we'll load what's on.")
    st.write("")

    locations = enabled_locations()
    coming = [loc for loc in LOCATIONS if not loc.enabled][:2]
    current = st.session_state.get("location", "")

    columns = st.columns(max(3, len(locations) + len(coming)))
    for index, loc in enumerate(locations):
        with columns[index]:
            selected = loc.slug == current
            if pick(f"loc_{loc.slug}", "Selected" if selected else f"Choose {loc.name}",
                    lambda loc=loc, selected=selected: C.location_tile(loc.name, loc.state, selected)):
                if loc.slug != current:
                    st.session_state["location"] = loc.slug
                    reset_from(1)
                goto(2)
    for offset, loc in enumerate(coming):
        with columns[len(locations) + offset]:
            C.location_tile(loc.name, loc.state, selected=False, enabled=False)

    if current:
        st.write("")
        if st.button("Continue to movies  →", type="primary", use_container_width=True,
                     key="loc_continue"):
            goto(2)


# ──────────────────────────────────────────────────────────────────────────
# 2 · Movie
# ──────────────────────────────────────────────────────────────────────────
def step_movie() -> bool:
    location = get_location(st.session_state["location"])
    C.step_header(2, "Select movie", f"What's on in {location.name} right now.")
    st.write("")

    state = catalogue.sync_state(location.slug)
    entries = catalogue.list_entries(location.slug)
    C.catalogue_banner(state["status"].value, state["message"],
                       ago(state["at"]), len(entries))

    if not entries:
        # Never let an unreadable catalogue masquerade as "no movies".
        if state["status"].is_failure:
            st.caption(
                "The background sync will retry automatically. You can also run it now "
                "from **Settings → Refresh catalogue**."
            )
        else:
            st.caption("Nothing cached for this city yet — run a sync from **Settings**.")
        return False

    query = st.text_input("Search movies", placeholder="⌕  Search for a movie…",
                          label_visibility="collapsed", key="movie_query")
    matches = catalogue.search_entries(query, location.slug)
    if not matches:
        st.caption(f"No movie in the {location.name} listing matches “{query}”.")
        return False

    selected = st.session_state.get("movie_id", "")
    st.caption(f"{len(matches)} movie(s) · tap a poster to select it")
    columns = st.columns(6)
    for index, entry in enumerate(matches[:36]):
        movie = catalogue.movie_from_entry(entry)
        with columns[index % 6]:
            is_sel = movie.id == selected
            meta = movie.language or ""
            if pick(f"movie_{movie.id}", "Selected" if is_sel else f"Select {movie.title}",
                    lambda m=movie, meta=meta, is_sel=is_sel: C.poster_tile(m.title, meta, is_sel, m.poster_url)):
                if movie.id != selected:
                    st.session_state["movie_id"] = movie.id
                    reset_from(2)
                goto(3)

    if selected:
        st.write("")
        if st.button("Continue to theatres  →", type="primary", use_container_width=True,
                     key="movie_continue"):
            goto(3)
    return bool(selected)


# ──────────────────────────────────────────────────────────────────────────
# 3 · Theatres
# ──────────────────────────────────────────────────────────────────────────
def step_theatres() -> list[Venue]:
    entry = catalogue.find_entry(st.session_state.get("movie_id", ""))
    if entry is None:
        st.caption("Pick a movie first.")
        return []
    movie = catalogue.movie_from_entry(entry)

    head, action = st.columns([3.2, 1])
    with head:
        C.step_header(3, "Where do you want to watch?",
                      f"One or more theatres showing {movie.title} in {movie.city}.")
    with action:
        select_all = st.button("Select all", key="select_all", use_container_width=True)
    st.write("")

    venues = catalogue.venues_from_entry(entry)
    if not venues:
        problem = st.session_state.get("detail_problem", "")
        if problem:
            C.catalogue_banner("BLOCKED", problem, "", 0)
        else:
            C.catalogue_banner(
                "EMPTY", "",
                ago(catalogue.sync_state(movie.region_slug)["at"]), 0,
            )
            st.caption(
                f"BookMyShow lists **{movie.title}** but hasn't published any theatres for "
                "it yet — that's normal before a release opens. The background sync will "
                "pick them up as soon as they appear."
            )
        return []

    chosen = set(st.session_state.get("theatres", []))
    if select_all:
        chosen = set(v.code for v in venues) if chosen != set(v.code for v in venues) else set()
        st.session_state["theatres"] = [v.code for v in venues if v.code in chosen]
        st.rerun()

    columns = st.columns(2)
    for index, venue in enumerate(venues):
        with columns[index % 2]:
            is_sel = venue.code in chosen
            label = "Selected ✓" if is_sel else f"Select {venue.name}"
            if pick(f"th_{venue.code}", label,
                    lambda v=venue, is_sel=is_sel: C.theatre_row(v.name, v.area, list(v.formats), is_sel, v.abbr)):
                if venue.code in chosen:
                    chosen.discard(venue.code)
                    st.session_state["formats"].pop(venue.code, None)
                else:
                    chosen.add(venue.code)
                st.session_state["theatres"] = [v.code for v in venues if v.code in chosen]
                st.rerun()

    picked = [v for v in venues if v.code in chosen]
    st.write("")
    if picked:
        st.caption(f"{len(picked)} theatre(s) selected: " + ", ".join(v.name for v in picked))
        if st.button("Continue to formats  →", type="primary", use_container_width=True,
                     key="th_continue"):
            goto(4)
    else:
        st.caption("Select at least one theatre to continue.")
    return picked


# ──────────────────────────────────────────────────────────────────────────
# 4 · Formats
# ──────────────────────────────────────────────────────────────────────────
def step_formats(venues: list[Venue]) -> dict[str, list[str]]:
    C.step_header(4, "Formats, per theatre", "Only formats that theatre actually runs.")
    st.write("")

    if not venues:
        st.caption("Pick a theatre first.")
        return {}

    formats: dict[str, list[str]] = dict(st.session_state.get("formats", {}))
    for venue in venues:
        options = dedupe(venue.formats)
        with st.container(key=f"trpanel_{venue.code}"):
            left, right = st.columns([1, 1.6], gap="medium")
            with left:
                C.format_panel_head(venue.name, venue.area, options[0] if options else "")
            with right:
                if not options:
                    st.caption("No format published for this theatre yet — watching every show.")
                    formats[venue.code] = [ANY_FORMAT]
                    continue

                # Widget keys are namespaced by venue code, which is what keeps
                # "AMB → HDR by Barco" from ever leaking into "Allu → Dolby Cinema".
                chosen: list[str] = []
                any_key = f"fmt_{venue.code}_any"
                any_on = st.checkbox("Any format", key=any_key,
                                     value=ANY_FORMAT in formats.get(venue.code, []))
                for fmt in options:
                    if st.checkbox(fmt, key=f"fmt_{venue.code}_{fmt}",
                                   value=fmt in formats.get(venue.code, [])):
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
    if st.button("Continue to monitoring  →", type="primary", use_container_width=True,
                 key="fmt_continue"):
        goto(5)
    return formats


# ──────────────────────────────────────────────────────────────────────────
# 5 · Monitoring
# ──────────────────────────────────────────────────────────────────────────
def step_monitoring(default_email: str) -> tuple[int, datetime, str, bool]:
    left, right = st.columns(2, gap="large")

    with left:
        C.step_header(5, "How often should I check?", "Checks run automatically in the background.")
        st.write("")
        current = st.session_state.get("interval", 10)
        if current not in INTERVALS:
            current = 10
        cols = st.columns(3)
        for column, minutes in zip(cols, INTERVALS):
            with column:
                if pick(f"interval_{minutes}", f"Every {minutes} minutes",
                        lambda m=minutes, sel=(minutes == current): C.interval_tile(m, sel)):
                    st.session_state["interval"] = minutes
                    st.rerun()
        interval = current
        st.caption("The first check runs as soon as you start; after that, about every "
                   f"{interval} minutes.")

    with right:
        C.step_header("◷", "Monitor until", "The monitor stops itself after this time.")
        st.write("")
        date_col, time_col = st.columns([1.5, 1])
        end_date = date_col.date_input("End date", value=(now_ist() + timedelta(days=1)).date(),
                                       min_value=now_ist().date(), format="DD/MM/YYYY",
                                       key="until_date", label_visibility="collapsed")
        end_time = time_col.time_input("End time", value=dtime(23, 59), step=timedelta(minutes=15),
                                       key="until_time", label_visibility="collapsed")
        until = datetime.combine(end_date, end_time, tzinfo=IST)
        start_now = st.toggle("Start checking immediately", key="start_now_toggle",
                              value=bool(st.session_state.get("start_now", True)),
                              help="Runs the first check the moment you press Start, "
                                   "instead of waiting for the next scheduled run.")
        st.session_state["start_now"] = bool(start_now)

    st.write("")
    C.html('<div class="tr-field-label">Notification email</div>')
    email = st.text_input("Notification email", value=default_email,
                          placeholder="you@gmail.com", label_visibility="collapsed",
                          key="notify_email")
    return interval, until, email.strip(), bool(start_now)


# ──────────────────────────────────────────────────────────────────────────
def summary(step: int) -> None:
    """Collapsed rows for everything already answered."""
    if step > 1 and st.session_state.get("location"):
        C.summary_row(1, "Location", get_location(st.session_state["location"]).label)
    if step > 2 and st.session_state.get("movie_id"):
        entry = catalogue.find_entry(st.session_state["movie_id"])
        if entry:
            movie = catalogue.movie_from_entry(entry)
            C.summary_row(2, "Movie",
                          f"{movie.title}{f' · {movie.language}' if movie.language else ''}")
    if step > 3 and st.session_state.get("theatres"):
        entry = catalogue.find_entry(st.session_state.get("movie_id", ""))
        names = {v.code: v.name for v in catalogue.venues_from_entry(entry or {})}
        picked = [names.get(c, c) for c in st.session_state["theatres"]]
        C.summary_row(3, "Theatres", ", ".join(picked))
    if step > 4 and st.session_state.get("formats"):
        entry = catalogue.find_entry(st.session_state.get("movie_id", ""))
        names = {v.code: v.name for v in catalogue.venues_from_entry(entry or {})}
        parts = [
            f"{names.get(code, code)} → {', '.join(fmts)}"
            for code, fmts in st.session_state["formats"].items()
            if fmts
        ]
        C.summary_row(4, "Formats", " · ".join(parts))


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
    "STEPS",
    "ago",
    "boot",
    "goto",
    "pick",
    "reset_from",
    "step_formats",
    "step_location",
    "step_monitoring",
    "step_movie",
    "step_theatres",
    "summary",
]
