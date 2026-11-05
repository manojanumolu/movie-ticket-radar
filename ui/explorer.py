"""Showtime Explorer — "Find your movie. Find your theatre. Find your time."

A browsing page inside the TicketRadar shell. Two ways in:

* **By movie** — where and when a film is playing across Hyderabad;
* **By theatre** — every film and showtime at one theatre.

Both end in the same results: a time-first list across every theatre, or the
same shows grouped (by theatre for a film, by film for a theatre).

Rules this page keeps:

* It never writes anything a monitor owns. No import of the monitor store's
  write paths, no wizard key touched; its session keys all start ``sx_``.
* Filters never read BookMyShow. Only a new film/theatre/date — or the
  Refresh action — does, through ``explorer.source`` (cached, single-flight,
  budgeted). Every widget marks its rerun UI-only, so the page also skips
  the account's store reads (``app.load_view``).
* Every result says where it came from and when: live, cached, or the
  catalogue sync's snapshot — never "live" for data that is not.
* A listed show is not a claim that seats are free. The only seat fact shown
  is "Sold out", and only when every seat category BookMyShow returned said so.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import streamlit as st

from config.timezone import fmt_time, now_ist
from explorer import catalog, query as Q, source as S
from explorer.classify import MULTIPLEX, SINGLE_SCREEN, UNKNOWN
from ui import catalogue_view as cv
from ui import components as C
from ui import flow
from content import explorer_dialogues, format_directory as fmtdir
from ui import crafts, radar
from ui import home as home_ui

MODES = {
    "movie": ("By movie", "Find where and when a movie is playing.", "movie"),
    "theatre": ("By theatre", "See every movie and showtime at a theatre.", "cinema"),
}
DAYS = 7
PAGE_SIZE = 120
TYPE_CHOICES = ["All theatres", MULTIPLEX, SINGLE_SCREEN]

#: Every key this page owns. Nothing here touches any other key.
DEFAULTS = {
    "sx_mode": "movie",
    "sx_date": "",
    "sx_from": dtime(0, 0),
    "sx_to": dtime(23, 59),
    "sx_area": [],
    "sx_ops": [],
    "sx_type": TYPE_CHOICES[0],
    "sx_lang": [],
    "sx_fmt": [],
    "sx_view": "time",
    "sx_limit": PAGE_SIZE,
}
#: Filters that only mean something across many theatres (By movie).
CITY_FILTERS = ("sx_area", "sx_ops", "sx_type")
LOADED = "sx_loaded"
FORCE = "sx_force"
REFRESH_NOTE = "sx_refresh_note"


def _ui_only() -> None:
    flow.mark_ui_only()


def _boot() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, list(value) if isinstance(value, list) else value)
    dates = date_codes()
    if st.session_state.get("sx_date") not in dates:
        st.session_state["sx_date"] = dates[0]


def date_codes(at: datetime | None = None) -> list[str]:
    today = (at or now_ist()).date()
    return [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(DAYS)]


def date_label(code: str, at: datetime | None = None) -> tuple[str, str]:
    day = datetime.strptime(code, "%Y%m%d").date()
    today = (at or now_ist()).date()
    name = "Today" if day == today else "Tomorrow" if day == today + timedelta(days=1) else day.strftime("%a")
    return name, f"{day.day} {day.strftime('%b')}"


# ── callbacks: each changes only this page's own keys ───────────────────
def _set_mode(mode: str) -> None:
    _ui_only()
    if st.session_state.get("sx_mode") == mode:
        return
    st.session_state["sx_mode"] = mode
    # Area, operator and theatre type mean nothing for one theatre; the
    # date, the time window, language and format carry over.
    if mode == "theatre":
        for key in CITY_FILTERS:
            st.session_state[key] = DEFAULTS[key] if not isinstance(DEFAULTS[key], list) else []
    st.session_state["sx_limit"] = PAGE_SIZE


def _set_date(code: str) -> None:
    _ui_only()
    st.session_state["sx_date"] = code
    st.session_state["sx_limit"] = PAGE_SIZE


def _set_window(start: dtime, end: dtime) -> None:
    _ui_only()
    st.session_state["sx_from"], st.session_state["sx_to"] = start, end


def _set_view(view: str) -> None:
    _ui_only()
    st.session_state["sx_view"] = view


def _refresh() -> None:
    _ui_only()
    st.session_state[FORCE] = True


def _more() -> None:
    _ui_only()
    st.session_state["sx_limit"] = st.session_state.get("sx_limit", PAGE_SIZE) + PAGE_SIZE


def _clean(key: str, allowed: list[str]) -> None:
    """Drop a stored pick the current options no longer contain — before the
    widget is drawn, which is the only time Streamlit lets a page set it."""
    current = st.session_state.get(key) or []
    kept = [v for v in current if v in allowed]
    if kept != current:
        st.session_state[key] = kept


# ── drawing ─────────────────────────────────────────────────────────────
def _label(icon: str, text: str) -> None:
    """A field label in the panel: the product's icon, then the mono caption."""
    C.html(f'<div class="tr-field-label tr-sx-label">{C.icon(icon, 13)}<span>{C.e(text)}</span></div>')


def day_tile(code: str, selected: bool) -> None:
    """A date as a ticket stub: the day's name, its number, the month."""
    day = datetime.strptime(code, "%Y%m%d").date()
    name, _ = date_label(code)
    C.html(f'<div class="tr-sx-day{" selected" if selected else ""}"><span class="k">{C.e(name)}</span>'
           f'<b>{day.day}</b><span class="m tr-mono">{C.e(day.strftime("%b"))}</span></div>')


def mode_tile(mode: str, selected: bool) -> None:
    title, line, glyph = MODES[mode]
    C.html(
        f'<div class="tr-sx-mode{" selected" if selected else ""}">'
        f'<div class="ic">{C.icon(glyph, 20)}</div>'
        f'<div class="tx"><div class="t">{C.e(title)}</div><div class="s">{C.e(line)}</div></div>'
        f'<div class="tick">{C.icon("check", 13, "#fff", "2.4")}</div></div>'
    )


def _modes(mode: str) -> None:
    for column, key in flow.grid(list(MODES), 2, "sxmode"):
        with column:
            sel = key == mode
            flow.pick(f"sxmode_{key}", "Selected" if sel else f"Search {MODES[key][0].lower()}",
                      lambda k=key, s=sel: mode_tile(k, s), on_click=_set_mode, args=(key,))


def _dates(selected: str) -> None:
    _label("calendar", "Date")
    for column, code in flow.grid(date_codes(), DAYS, "sxdate"):
        with column:
            name, sub = date_label(code)
            sel = code == selected
            flow.pick(f"sxdate_{code}", "Selected" if sel else f"Show {name} {sub}",
                      lambda c=code, x=sel: day_tile(c, x),
                      on_click=_set_date, args=(code,))


def _window() -> tuple[int, int] | None:
    _label("clock", "Time window · IST")
    with st.container(key="trpair_sxwin"):
        a, b = st.columns(2, gap="small")
        a.time_input("From", key="sx_from", step=timedelta(minutes=15), on_change=_ui_only)
        b.time_input("To", key="sx_to", step=timedelta(minutes=15), on_change=_ui_only)
    with st.container(key="sxpresets"):
        cols = st.columns(len(Q.PRESETS), gap="small")
        current = (st.session_state.get("sx_from"), st.session_state.get("sx_to"))
        for col, (label, lo, hi) in zip(cols, Q.PRESETS):
            col.button(label, key=f"sxpre_{label.lower().replace(' ', '_')}", use_container_width=True,
                       type="primary" if current == (lo, hi) else "secondary",
                       help=f"{Q.fmt_clock(lo)} – {Q.fmt_clock(hi)}",
                       on_click=_set_window, args=(lo, hi))
    try:
        return Q.validate_window(st.session_state.get("sx_from"), st.session_state.get("sx_to"))
    except Q.WindowError as exc:
        C.status_line("bad", str(exc))
        return None


FILTER_ICONS = {"Area": "location", "Operator": "theatre", "Theatre type": "cinema",
                "Language": "language", "Format": "format"}


def _multiselect(column, label: str, key: str, opts: list[str], placeholder: str) -> None:
    _clean(key, opts)
    with column:
        _label(FILTER_ICONS.get(label, "filter"), label)
        st.multiselect(label, opts, key=key, placeholder=placeholder, label_visibility="collapsed",
                       on_change=_ui_only, disabled=not opts)


def _filters(mode: str, shows: list[Q.ExplorerShow], languages: list[str]) -> None:
    opts = Q.options(shows)
    with st.container(key="sxfilters"):
        C.html(f'<div class="tr-sx-refine">{C.icon("filter", 13)}<span>Refine</span></div>')
        if mode == "movie":
            cols = st.columns([1.15, 1.15, 1, 1, 1.15], gap="small")
            _multiselect(cols[0], "Area", "sx_area", opts["localities"], "All Hyderabad")
            _multiselect(cols[1], "Operator", "sx_ops", opts["operators"], "All operators")
            with cols[2]:
                _label("cinema", "Theatre type")
                st.selectbox("Theatre type", TYPE_CHOICES, key="sx_type", label_visibility="collapsed",
                             on_change=_ui_only)
            lang_col, fmt_col = cols[3], cols[4]
        else:
            lang_col, fmt_col = st.columns(2, gap="small")
        _multiselect(lang_col, "Language", "sx_lang", languages, "All languages")
        _multiselect(fmt_col, "Format", "sx_fmt", opts["formats"], "All formats")


def _keep(key: str) -> None:
    """A search box's ``on_change``: remember the choice in a key this page owns.
    Streamlit forgets a widget's value on any run that does not draw it, and
    each mode's box is not drawn in the other mode — so without this, going
    to By theatre and back lost the movie (and the other way round)."""
    _ui_only()
    st.session_state[f"{key}_keep"] = st.session_state.get(key)


def _kept_index(key: str, labels: list[str]) -> int | None:
    kept = st.session_state.get(f"{key}_keep")
    if key not in st.session_state and kept in labels:
        return labels.index(kept)
    return None


def _searchbox(mode: str, titles: list[catalog.Title], theatres: list[catalog.Theatre]):
    if mode == "movie":
        _label("movie", "Movie")
        labels = [t.label for t in titles]
        _clean_single("sx_movie", labels)
        choice = st.selectbox("Movie", labels, index=_kept_index("sx_movie", labels), key="sx_movie",
                              label_visibility="collapsed", placeholder="Search movies playing in Hyderabad…",
                              on_change=_keep, args=("sx_movie",))
        return next((t for t in titles if t.label == choice), None)
    _label("cinema", "Theatre")
    labels = [t.label for t in theatres]
    _clean_single("sx_theatre", labels)
    choice = st.selectbox("Theatre", labels, index=_kept_index("sx_theatre", labels), key="sx_theatre",
                          label_visibility="collapsed", placeholder="Search theatres in Hyderabad — name or area…",
                          on_change=_keep, args=("sx_theatre",))
    return next((t for t in theatres if t.label == choice), None)


def _pick_film() -> None:
    """The "Find a movie" box's ``on_change``: remember the film, and open its
    shows grouped by language — the view that answers "when is it playing in
    my language?" at a glance."""
    _keep("sx_film")
    st.session_state["sx_limit"] = PAGE_SIZE
    if st.session_state.get("sx_film"):
        st.session_state["sx_view"] = "group"


def _filmbox(films: list[catalog.FilmAt]) -> catalog.FilmAt | None:
    """By theatre: narrow the theatre to one film. Only films the catalogue
    knows at this theatre are offered; none chosen keeps every film."""
    _label("movie", "Find a movie")
    labels = [f.label for f in films]
    _clean_single("sx_film", labels)
    choice = st.selectbox("Find a movie", labels, index=_kept_index("sx_film", labels), key="sx_film",
                          label_visibility="collapsed", placeholder="All movies at this theatre — or search one…",
                          on_change=_pick_film, disabled=not labels)
    return next((f for f in films if f.label == choice), None)


def _clean_single(key: str, labels: list[str]) -> None:
    if st.session_state.get(key) is not None and st.session_state.get(key) not in labels:
        st.session_state[key] = None


def _load(mode: str, wanted: list[S.Wanted], date_code: str) -> dict:
    """The read for this film/theatre and date: from this session when it is
    young enough, else through ``explorer.source`` — which serves its own
    cache first. Filters never come through here."""
    force = bool(st.session_state.pop(FORCE, False))
    key = (mode, tuple(w.movie_id for w in wanted), date_code)
    loaded: dict = st.session_state.setdefault(LOADED, {})
    held = loaded.get(key)
    if held and not force and (now_ist() - held["at"]).total_seconds() < S.CACHE_TTL:
        return held
    films = len(wanted)
    label = (f"Reading BookMyShow for {films} film{'s' if films != 1 else ''}…" if mode == "theatre"
             else "Reading BookMyShow showtimes…")
    with st.spinner(label):
        days = S.read_many(wanted, date_code, force=force)
    result = {"days": days, "at": now_ist(), "forced": force}
    if force:
        failed = [d for d in days if d.error]
        st.session_state[REFRESH_NOTE] = (
            ("bad", f"Refresh failed at {fmt_time(now_ist())} — {failed[0].error}") if failed and
            len(failed) == len(days) else
            ("warn", f"Refresh at {fmt_time(now_ist())} could not read {len(failed)} of {len(days)} films.")
            if failed else ("ok", f"Refreshed at {fmt_time(now_ist())}."))
    loaded[key] = result
    while len(loaded) > 6:
        loaded.pop(next(iter(loaded)))
    return result


def freshness(days: list[S.MovieDay], at: datetime | None = None) -> tuple[str, str, str]:
    """(kind, headline, detail) for the strip above the results."""
    at = at or now_ist()
    if not days:
        return "wait", "Nothing read yet", ""
    failed = [d for d in days if d.origin == S.FAILED]
    snap = [d for d in days if d.origin == S.SNAPSHOT]
    live = [d for d in days if d.origin in (S.LIVE, S.CACHED)]
    if len(failed) == len(days):
        return "bad", "Couldn't read BookMyShow", failed[0].error or "No answer, and no earlier read to fall back on."
    times = [d.read_at for d in days if d.read_at is not None and d.origin != S.FAILED]
    oldest = min(times) if times else None
    when = f"read {fmt_time(oldest)} IST" if oldest else ""
    if snap and not live:
        reason = next((d.error for d in snap if d.error), "")
        detail = "Not live — the scheduled catalogue sync's read. Refresh to try BookMyShow now."
        if reason:
            detail = f"Not live — BookMyShow couldn't be read just now ({reason}). Showing the scheduled sync's read."
        return "snap", f"From the catalogue sync · {when}", detail
    age = (at - oldest).total_seconds() if oldest else 0
    parts = []
    if snap:
        parts.append(f"{len(snap)} film{'s' if len(snap) != 1 else ''} from the catalogue sync (not live)")
    if failed:
        parts.append(f"{len(failed)} film{'s' if len(failed) != 1 else ''} couldn't be read")
    partial = [d for d in days if d.missing]
    if partial:
        parts.append("some premium-format listings couldn't be read, so their shows are missing")
    detail = "; ".join(parts).capitalize() + "." if parts else ""
    if age < S.FRESH_FOR:
        return ("warn" if parts else "ok"), f"Live from BookMyShow · {when}", detail
    return ("warn" if parts else "cached"), f"Cached · {when} ({flow.ago(oldest)})", detail


def _fresh_strip(days: list[S.MovieDay], date_code: str, window: tuple[int, int]) -> None:
    kind, head, detail = freshness(days)
    name, sub = date_label(date_code)
    lo, hi = window
    span = f"{Q.fmt_clock(dtime(lo // 60, lo % 60))} – {Q.fmt_clock(dtime(hi // 60, hi % 60))}"
    with st.container(key="trpair_sxfresh"):
        left, right = st.columns([5, 1.2], gap="small", vertical_alignment="center")
        with left:
            C.html(
                f'<div class="tr-sx-fresh {C.e(kind)}"><span class="dot"></span>'
                f'<div><div class="h">{C.e(head)}</div>'
                f'<div class="w">{C.e(name)} {C.e(sub)} · {C.e(span)}</div>'
                + (f'<div class="d">{C.e(detail)}</div>' if detail else "") + "</div></div>"
            )
        with right:
            st.button("Refresh", key="sx_refresh", icon=":material/refresh:", use_container_width=True,
                      on_click=_refresh, help="Read BookMyShow again for this selection")
    note = st.session_state.pop(REFRESH_NOTE, None)
    if note and note[0] != "ok":
        C.status_line(note[0], note[1])


def _book(url: str, text: str = "Book") -> str:
    if not url:
        return '<span class="tr-sx-book off" title="No verified BookMyShow link for this show">No link</span>'
    return (f'<a class="tr-sx-book" href="{C.e(url)}" target="_blank" rel="noopener noreferrer" '
            f'title="Book on BookMyShow" aria-label="{C.e(text)} on BookMyShow">{C.icon("ticket", 13)}'
            f'<span>{C.e(text)}</span></a>')


def _fmt_badges(show: Q.ExplorerShow, *, language: bool = True) -> str:
    """Dimension first (accent), then the language, then the screen BookMyShow
    named — only what the listing says; nothing is filled in."""
    out = ""
    screens = list(show.formats)
    if show.dimension and screens and screens[0] == show.dimension:
        out += f'<span class="tr-badge dim" title="Dimension">{C.e(show.dimension)}</span>'
        screens = screens[1:]
    if language and show.language:
        out += f'<span class="tr-badge muted lang" title="Language">{C.e(show.language)}</span>'
    out += "".join(f'<span class="tr-badge muted" title="Screen">{C.e(f)}</span>' for f in screens[:1])
    if show.sold_out:
        out += '<span class="tr-badge soon" title="Every seat category BookMyShow returned was sold out">Sold out</span>'
    return out


def _where(show: Q.ExplorerShow) -> str:
    return show.locality if show.locality != UNKNOWN else (show.area or "Hyderabad")


def time_rows_markup(shows: list[Q.ExplorerShow], mode: str) -> str:
    parts = ['<div class="tr-sx-list">']
    for hour, items in Q.by_hour(shows):
        parts.append(f'<div class="tr-sx-hour"><span class="tr-eyebrow">{C.e(hour)}</span><span class="line"></span></div>')
        for s in items:
            clock, half = s.display_time
            if mode == "movie":
                primary, secondary = s.venue_name, _where(s)
            else:
                primary, secondary = s.movie_title, ""
            parts.append(
                f'<div class="tr-sx-row"><div class="t"><b>{C.e(clock)}</b><span>{C.e(half)}</span></div>'
                f'<div class="m"><div class="n">{C.e(primary)}</div>'
                + (f'<div class="s">{C.e(secondary)}</div>' if secondary else "") + '</div>'
                f'<div class="f">{_fmt_badges(s)}</div>{_book(s.booking_url)}</div>'
            )
    parts.append("</div>")
    return "".join(parts)


def group_markup(groups: list[Q.Group], mode: str) -> str:
    parts = ['<div class="tr-sx-groups">']
    for g in groups:
        n = len(g.shows)
        if mode == "movie":
            first = g.shows[0]
            meta = " · ".join(x for x in (_where(first), first.operator if not first.operator.startswith("Independent") else "") if x)
        else:
            meta = g.subtitle
        thumb = C.thumb(g.poster_url, small=True) if mode == "theatre" else ""
        times = "".join(
            (f'<a class="tr-sx-time{" sold" if s.sold_out else ""}" href="{C.e(s.booking_url)}" target="_blank" '
             f'rel="noopener noreferrer">' if s.booking_url else f'<span class="tr-sx-time{" sold" if s.sold_out else ""}">')
            + f'<b>{C.e(" ".join(s.display_time).strip())}</b>'
            + (f'<span>{C.e(s.dimension or s.formats[0])}</span>' if (s.dimension or s.formats) else "")
            + ("</a>" if s.booking_url else "</span>")
            for s in g.shows
        )
        parts.append(
            f'<div class="tr-sx-group"><div class="h">{thumb}<div class="b"><div class="n">{C.e(g.title)}</div>'
            f'<div class="s">{C.e(meta)}{" · " if meta else ""}{n} show{"s" if n != 1 else ""}</div></div></div>'
            f'<div class="times">{times}</div></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def _empty(title: str, text: str, kind: str = "") -> None:
    """The page's empty states. ``kind`` "warn" (incomplete) or "bad" (couldn't
    read) changes the glyph and its colour, never only the colour."""
    glyph = {"warn": "warning", "bad": "error"}.get(kind, "clock")
    C.html(f'<div class="tr-state empty tr-sx-empty {C.e(kind)}"><div class="ring">{C.icon(glyph, 18)}</div>'
           f'<div class="h">{C.e(title)}</div><div class="p">{C.e(text)}</div></div>')


def shows_title(result: dict) -> str:
    return result.get("venue_name") or "This theatre"


def _results(mode: str, result: dict, window: tuple[int, int], date_code: str) -> None:
    days: list[S.MovieDay] = result["days"]
    shows = [s for d in days for s in d.shows]
    lo, hi = window
    today = date_code == now_ist().strftime("%Y%m%d")
    filt = Q.Filters(
        date_code=date_code, start=dtime(lo // 60, lo % 60), end=dtime(hi // 60, hi % 60),
        localities=tuple(st.session_state.get("sx_area") or ()) if mode == "movie" else (),
        operators=tuple(st.session_state.get("sx_ops") or ()) if mode == "movie" else (),
        theatre_type=(st.session_state.get("sx_type") if mode == "movie"
                      and st.session_state.get("sx_type") in (MULTIPLEX, SINGLE_SCREEN) else ""),
        languages=tuple(st.session_state.get("sx_lang") or ()),
        formats=tuple(st.session_state.get("sx_fmt") or ()),
        venue_code=result.get("venue", ""),
        title_key=(result.get("film_at").key if result.get("film_at") is not None else ""),
        hide_started_before=now_ist() if today else None,
    )
    out = Q.apply(shows, filt)
    venues = {s.venue_code for s in out.shows}

    film = result.get("film")
    film_at: catalog.FilmAt | None = result.get("film_at")
    if film_at is not None:
        poster = next((S.safe_poster(w.poster) for w in film_at.rows if S.safe_poster(w.poster)), "")
        C.html(f'<div class="tr-sx-film">{C.thumb(poster)}<div class="b"><span class="tr-eyebrow">'
               f'At {C.e(shows_title(result))}</span><div class="n">{C.e(film_at.title)}</div>'
               f'<div class="s">{C.e(" · ".join(film_at.languages) or Q.NO_LANGUAGE)}</div></div></div>')
    if film is not None:
        poster = next((S.safe_poster(w.poster) for w in film.rows if S.safe_poster(w.poster)), "")
        C.html(f'<div class="tr-sx-film">{C.thumb(poster)}<div class="b"><span class="tr-eyebrow">Now showing</span>'
               f'<div class="n">{C.e(film.title)}</div>'
               f'<div class="s">{C.e(" · ".join(film.languages))}</div></div></div>')
    with st.container(key="trpair_sxhead"):
        head, toggle = st.columns([3, 2], gap="small", vertical_alignment="center")
        with head:
            count = len(out.shows)
            langs = {s.language or Q.NO_LANGUAGE for s in out.shows}
            extra = (f" · {len(venues)} theatre{'s' if len(venues) != 1 else ''}" if mode == "movie" else
                     f" · {len(langs)} language{'s' if len(langs) != 1 else ''}" if film_at is not None else "")
            C.html(f'<div class="tr-sx-count"><b>{count}</b> showtime{"s" if count != 1 else ""}{C.e(extra)}</div>')
        with toggle:
            view = st.session_state.get("sx_view", "time")
            group_name = "By theatre" if mode == "movie" else "By language" if film_at is not None else "By movie"
            with st.container(key="trpair_sxview"):
                a, b = st.columns(2, gap="small")
                for col, key, label in ((a, "time", "By time"), (b, "group", group_name)):
                    with col:
                        flow.pick(f"sxview_{key}", "Selected" if view == key else f"Show {label.lower()}",
                                  lambda lb=label, x=view == key: C.html(
                                      f'<div class="tr-sx-seg{" selected" if x else ""}">{C.e(lb)}</div>'),
                                  on_click=_set_view, args=(key,))
    _fresh_strip(days, date_code, window)

    failed = [d for d in days if d.origin == S.FAILED]
    if days and len(failed) == len(days):
        _empty("BookMyShow couldn't be read", f"{failed[0].error or 'No answer'}, and there is no earlier read "
               "for this selection. Nothing is shown rather than a guess — try Refresh in a minute.", kind="bad")
        return
    if not out.shows:
        name, sub = date_label(date_code)
        # What this selection actually has, before the time window and filters:
        # for one theatre, only that theatre's shows (a theatre read is city-wide).
        venue = result.get("venue", "")
        on_date = [s for s in shows if s.date_code == date_code and (not venue or s.venue_code == venue)]
        if failed:
            _empty("Results may be incomplete",
                   f"BookMyShow couldn't be read for {len(failed)} of {len(days)} film"
                   f"{'s' if len(days) != 1 else ''} ({failed[0].error}). None of the ones it did read has a "
                   "show matching this selection — try Refresh before relying on that.", kind="warn")
        elif not on_date and film_at is not None:
            _empty(f"{film_at.title} isn't playing here on this date",
                   f"BookMyShow lists no {film_at.title} shows at {shows_title(result)} on {name} {sub} — "
                   "bookings may not have opened yet, or try another date.")
        elif not on_date:
            where = f"{shows_title(result)} lists" if venue else "BookMyShow lists"
            _empty("Nothing listed for this date", f"{where} no shows for this selection on {name} {sub}"
                   " — bookings may not have opened yet.")
        elif out.already_started and not (out.outside_window or out.filtered_out):
            _empty("Today's shows have started", "Every show listed for today in this selection has already "
                   "begun. Pick tomorrow or a later date to see what's next.")
        else:
            _empty("No shows match", f"{len(on_date)} show{'s' if len(on_date) != 1 else ''} listed for {name} "
                   f"{sub}, none in this time window with these filters. Widen the window or clear a filter.")
        _notes(out, days)
        return

    limit = st.session_state.get("sx_limit", PAGE_SIZE)
    if st.session_state.get("sx_view") == "group":
        groups = (Q.group_by_theatre(out.shows) if mode == "movie" else
                  Q.group_by_language(out.shows) if film_at is not None else Q.group_by_movie(out.shows))
        C.html(group_markup(groups, mode))
    else:
        C.html(time_rows_markup(out.shows[:limit], mode))
        if len(out.shows) > limit:
            st.button(f"Show {min(PAGE_SIZE, len(out.shows) - limit)} more", key="sx_more",
                      on_click=_more, icon=":material/expand_more:")
    _notes(out, days)


def _notes(out: Q.FilterResult, days: list[S.MovieDay]) -> None:
    notes = []
    if out.already_started:
        notes.append(f"{out.already_started} earlier show{'s' if out.already_started != 1 else ''} today "
                     "already started and aren't shown.")
    if out.unreadable_time:
        notes.append(f"{out.unreadable_time} show{'s' if out.unreadable_time != 1 else ''} had no readable "
                     "time and aren't placed in the window.")
    failed = [d for d in days if d.origin == S.FAILED]
    if failed and len(failed) < len(days):
        notes.append(f"BookMyShow couldn't be read for {len(failed)} film{'s' if len(failed) != 1 else ''} "
                     f"({failed[0].error}), so shows may be missing — try Refresh.")
    if any(d.missing for d in days):
        notes.append("Some premium-format listings (3D, IMAX, 4DX…) couldn't be read, so their shows may be missing.")
    skipped = [d for d in days if d.skipped]
    if skipped:
        notes.append(f"{len(skipped)} more film{'s' if len(skipped) != 1 else ''} weren't read live, to keep "
                     "requests to BookMyShow bounded — pick a language to narrow the list.")
    notes.append("A listed show isn't a promise of seats — BookMyShow confirms availability when you book.")
    C.html('<div class="tr-sx-notes">' + "".join(f"<div>{C.e(n)}</div>" for n in notes) + "</div>")


#: The tribute line under the hero's copy: recent Indian cinema, one line at
#: a time — the original words in Roman letters, then film, year, speaker
#: and language. No translations. The collection lives in
#: ``content.explorer_dialogues``, apart from Home's.
LINES = explorer_dialogues.DIALOGUES
#: How long each line holds the slot, in seconds.
LINE_SECONDS = 6


def _line_keyframes() -> str:
    """Each line holds the slot for its slice of the loop; opacity and a 3px
    rise only, never layout."""
    shown = 100 / len(LINES)
    fade = 1.2 / (len(LINES) * LINE_SECONDS) * 100
    return (f"@keyframes tr-sx-line {{ 0% {{ opacity:0; transform:translateY(3px); }} "
            f"{fade:.2f}% {{ opacity:1; transform:none; }} {shown - fade:.2f}% {{ opacity:1; transform:none; }} "
            f"{shown:.2f}%, 100% {{ opacity:0; transform:translateY(-3px); }} }}")

#: The Explorer's atmosphere: Home's own radar (``ui.radar``) — the same mark,
#: rings, sweep and glow, nothing drawn anew — once at hero size in the hero's
#: top-right, as Home has it, and once large and faint behind the page so the
#: rings carry on behind the results; plus Home's room light. No reel, no film
#: strip. Transform and opacity only (``radar.CSS``'s own keyframes); nothing
#: blurred; pointer-transparent; ``prefers-reduced-motion`` stills it all.
AMBIENCE_CSS = "<style>" + radar.CSS + crafts.CSS + """
@keyframes tr-sx-breathe { 0%,100% { opacity:.65; transform:scale(1); } 50% { opacity:1; transform:scale(1.05); } }
[class*="st-key-trsx_root"] { position:relative; isolation:isolate; }
[class*="st-key-trsx_root"] > [data-testid="stElementContainer"],
[class*="st-key-trsx_root"] > [data-testid="stLayoutWrapper"] { position:relative; z-index:1; }
[class*="st-key-trsx_root"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-sx-ambience) {
  position:absolute; inset:0; width:100%; height:100%; margin:0; z-index:0; pointer-events:none; }
.tr-sx-ambience { position:absolute; left:-60px; right:-60px; top:-140px; bottom:-120px; pointer-events:none; overflow:hidden; }
.tr-sx-ambience::before { content:""; position:absolute; right:-12%; top:-6%; width:80%; height:62%; opacity:.85;
  background: conic-gradient(from 200deg at 92% 4%, transparent 0deg, rgba(255,107,133,.10) 14deg, rgba(255,51,85,.04) 30deg,
  rgba(255,51,85,.01) 44deg, transparent 56deg);
  /* Home softens this layer's box edges with a blur; a mask does it without one */
  -webkit-mask-image: radial-gradient(ellipse 70% 80% at 92% 4%, #000 40%, transparent 100%);
  mask-image: radial-gradient(ellipse 70% 80% at 92% 4%, #000 40%, transparent 100%); }
.tr-sx-ambience::after { content:""; position:absolute; left:-8%; top:46%; width:52%; height:40%;
  background: radial-gradient(closest-side, rgba(255,51,85,.11), rgba(255,51,85,.03) 55%, transparent 75%);
  animation: tr-sx-breathe 9s ease-in-out infinite; }
.tr-sx-ambience .tr-radar.sxbg { position:absolute; right:-18%; top:640px; opacity:.2; }
.tr-sx-ambience .tr-radar.sxbg .r1, .tr-sx-ambience .tr-radar.sxbg .core { opacity:.5; }
[class*="st-key-trsx_hero"] { position:relative; }
[class*="st-key-trsx_hero"] > [data-testid="stElementContainer"]:has(> .stMarkdown .tr-sx-hero-radar) {
  position:absolute; inset:0 auto auto 0; width:100%; height:0; margin:0; z-index:2; }
.tr-sx-hero-radar { position:absolute; left:0; right:0; top:0; height:200px; border-radius:22px; overflow:hidden; pointer-events:none; }
.tr-sx-hero-radar .scan { position:absolute; right:-520px; top:-540px; width:1200px; height:1200px; border-radius:50%; opacity:.6;
  background: conic-gradient(from 0deg, transparent 0deg 292deg, rgba(255,51,85,.022) 334deg, rgba(255,51,85,.06) 359deg, transparent 360deg);
  animation: tr-radar-sweep 11s linear infinite; }
.tr-sx-hero-radar .tr-radar.sxhero { position:absolute; right:46px; top:22px; filter: drop-shadow(0 0 14px rgba(255,51,85,.32)); }
.tr-radar.sxhero .ring { stroke-width:1.6; } .tr-radar.sxhero .r4 { opacity:.34; } .tr-radar.sxhero .r3 { opacity:.42; }
[class*="st-key-trsx_hero"] .tr-hero-inner { min-height:150px; }
[class*="st-key-trsx_hero"] .tr-hero-inner > div:first-child { align-self:flex-start; }
.tr-sx-empty.warn { border-color:rgba(232,178,92,.34); }
.tr-sx-empty.warn .ring { color:var(--tr-warning); border-color:rgba(232,178,92,.4); }
.tr-sx-empty.bad { border-color:rgba(255,92,92,.34); }
.tr-sx-empty.bad .ring { color:var(--tr-danger); border-color:rgba(255,92,92,.4); }
/* Home's craft marks, at Home's positions: the band above the hero, a ledge
   below the results. Each explains one cinema format (``content.format_directory``)
   in Home's own card, on hover, keyboard focus or a tap. */
.tr-sx-crafts { position:relative; pointer-events:none; }
.tr-sx-crafts .tr-craft { opacity:.42; pointer-events:auto; cursor:default; outline:none;
  transition: opacity .18s var(--tr-ease), color .18s var(--tr-ease), transform .18s var(--tr-ease); }
.tr-sx-crafts .tr-craft::before, .tr-sx-crafts .tr-craft::after { display:none; }
.tr-sx-crafts .tr-craft .tr-ic, .tr-sx-fxstrip .tr-craft .tr-ic { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round; }
.tr-sx-crafts.band { position:absolute; left:0; right:250px; top:-108px; height:96px; }
.tr-sx-crafts.foot { height:150px; margin-top:18px; }
.tr-sx-crafts .tr-craft:hover, .tr-sx-crafts .tr-craft:focus, .tr-sx-crafts .tr-craft:focus-visible,
.tr-sx-fxstrip .tr-craft:hover, .tr-sx-fxstrip .tr-craft:focus, .tr-sx-fxstrip .tr-craft:focus-visible { opacity:1; color:#FF8CA0; z-index:6; }
.tr-sx-crafts .tr-craft:focus-visible, .tr-sx-fxstrip .tr-craft:focus-visible { box-shadow:0 0 0 2px rgba(255,51,85,.6); }
.tr-sx-crafts .tr-sub, .tr-sx-fxstrip .tr-sub { position:absolute; bottom:calc(100% + 12px); left:50%; transform:translate(-50%, 4px); z-index:6;
  display:flex; flex-direction:column; gap:4px; padding:10px 14px 9px; border-radius:11px; white-space:normal; width:max-content;
  max-width:min(320px, calc(100vw - 32px)); text-align:left; pointer-events:none; opacity:0; visibility:hidden;
  background: linear-gradient(168deg, #17131A, #0F0D12); border:1px solid rgba(255,255,255,.12);
  box-shadow: 0 18px 40px -16px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.10);
  transition: opacity .18s var(--tr-ease), transform .18s var(--tr-ease), visibility .18s; }
.tr-sx-crafts .tr-craft:hover .tr-sub, .tr-sx-crafts .tr-craft:focus .tr-sub, .tr-sx-crafts .tr-craft:focus-visible .tr-sub {
  opacity:1; visibility:visible; transform:translate(-50%, 0); }
.tr-sx-crafts .tr-craft.tip-r .tr-sub { left:-4px; transform:translate(0, 4px); }
.tr-sx-crafts .tr-craft.tip-l .tr-sub { left:auto; right:-4px; transform:translate(0, 4px); }
.tr-sx-crafts .tr-craft.tip-r:hover .tr-sub, .tr-sx-crafts .tr-craft.tip-r:focus .tr-sub,
.tr-sx-crafts .tr-craft.tip-l:hover .tr-sub, .tr-sx-crafts .tr-craft.tip-l:focus .tr-sub { transform:translate(0, 0); }
.tr-sx-crafts .tr-craft.tip-b .tr-sub { bottom:auto; top:calc(100% + 12px); }
.tr-sx-crafts .tr-sub .t, .tr-sx-fxstrip .tr-sub .t { font-size:13.5px; font-weight:700; line-height:1.3; color:#F2F2F4; }
.tr-sx-crafts .tr-sub .g, .tr-sx-fxstrip .tr-sub .g { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.18em; text-transform:uppercase; color:#FF8CA0; }
.tr-sx-crafts .tr-sub .d, .tr-sx-fxstrip .tr-sub .d { font-size:12.5px; line-height:1.45; color:#C8C8D0; }
.tr-sx-crafts .tr-sub .h, .tr-sx-fxstrip .tr-sub .h { font-size:11px; line-height:1.4; color:var(--tr-text-3); border-top:1px solid rgba(255,255,255,.08); padding-top:5px; }
@keyframes tr-sx-nod { 0% { transform:scale(1); } 45% { transform:scale(1.16) rotate(-6deg); } 100% { transform:scale(1); } }
.tr-sx-crafts .tr-craft:hover .tr-ic, .tr-sx-crafts .tr-craft:focus .tr-ic,
.tr-sx-fxstrip .tr-craft:hover .tr-ic, .tr-sx-fxstrip .tr-craft:focus .tr-ic { animation: tr-sx-nod .5s var(--tr-ease) 1; }
/* the phone's strip of the same 24 marks: the strip scrolls, so each card is
   anchored to the box around it, full width under the strip, never clipped */
.tr-sx-fxstrip { display:none; }
/* the tribute line: one fixed slot under the hero's copy, one line at a time */
.tr-sx-lines { position:relative; height:50px; margin-top:12px; overflow:hidden; pointer-events:none; }
.tr-sx-lines .ln { position:absolute; left:0; right:0; top:0; display:flex; flex-direction:column; gap:3px; opacity:0;
  animation: tr-sx-line """ + str(len(LINES) * LINE_SECONDS) + """s linear infinite; }
.tr-sx-lines .q { font-size:12.5px; line-height:17px; font-style:italic; font-weight:500; color:var(--tr-text-3); }
.tr-sx-lines .by { font-size:9.5px; line-height:13px; letter-spacing:.18em; text-transform:uppercase; color:var(--tr-accent-soft); opacity:.8; }
""" + _line_keyframes() + """
@media (max-width: 1200px) { .tr-sx-hero-radar .tr-radar.sxhero { width:88px !important; right:34px; } }
@media (max-width: 768px) {
  .tr-sx-hero-radar, .tr-sx-crafts { display:none; }
  .tr-sx-lines { height:90px; margin-top:12px; }
  .tr-sx-fxstrip { display:block; position:relative; margin-top:18px; }
  /* the strip's block sits above the results' blocks, so an open card is never under them */
  [class*="st-key-trsx_root"] > [data-testid="stElementContainer"]:has(.tr-sx-fxstrip) { z-index:3; }
  .tr-sx-fxstrip .row { display:flex; gap:8px; padding:6px 4px 10px; overflow-x:auto; scrollbar-width:none;
    border-bottom:1px solid rgba(255,255,255,.06); }
  .tr-sx-fxstrip .row::-webkit-scrollbar { display:none; }
  .tr-sx-fxstrip .tr-craft { position:static; flex-shrink:0; margin:0; width:34px; height:34px; opacity:.55; outline:none; }
  .tr-sx-fxstrip .tr-craft::before, .tr-sx-fxstrip .tr-craft::after { display:none; }
  /* upward: the version line that follows the page would paint over a card opening below */
  .tr-sx-fxstrip .tr-craft .tr-sub { left:0; right:0; top:auto; bottom:calc(100% + 6px); width:auto; max-width:none; transform:none !important; z-index:10; }
  .tr-sx-fxstrip .tr-craft:hover .tr-sub, .tr-sx-fxstrip .tr-craft:focus .tr-sub, .tr-sx-fxstrip .tr-craft:focus-visible .tr-sub { opacity:1; visibility:visible; }
  [class*="st-key-trsx_hero"] .tr-hero-inner { min-height:0; }
  .tr-sx-ambience .tr-radar.sxbg { right:-60%; top:1100px; opacity:.14; }
}
@media (prefers-reduced-motion: reduce) {
  .tr-sx-ambience::after, .tr-sx-hero-radar .scan, .tr-sx-lines .ln { animation:none !important; }
  .tr-sx-lines .ln:first-child { opacity:1; }
  .tr-sx-crafts .tr-craft .tr-ic, .tr-sx-fxstrip .tr-craft .tr-ic { animation:none !important; }
  .tr-sx-crafts .tr-craft, .tr-sx-crafts .tr-sub, .tr-sx-fxstrip .tr-sub { transition:none; }
}
</style>"""


#: Home's twenty-four crafts, as Home places them: eight in the band above the
#: hero (Home's ``band`` coordinates), sixteen in a two-row ledge under the
#: results (Home's ``rail`` and ``foot`` coordinates, one row each).
CRAFT_ROWS = {
    "band": [(k, x, y) for k, (z, x, y) in home_ui.HOME_LAYOUT.items() if z == "band"],
    "foot": ([(k, x, y * 0.5) for k, (z, x, y) in home_ui.HOME_LAYOUT.items() if z == "rail"]
             + [(k, x, 50 + y * 0.5) for k, (z, x, y) in home_ui.HOME_LAYOUT.items() if z == "foot"]),
}


def _side(x: float, zone: str) -> str:
    """Which way a card opens so it stays inside the window: a mark in the
    outer third anchors its card to its own edge (a format card is wider
    than Home's dialogue card), and the band, above the hero at the very top
    of the page, always opens downward."""
    side = "tip-l" if x > 66 else "tip-r" if x < 34 else ""
    return f"{side} tip-b".strip() if zone == "band" else side


def _format_mark(key: str, x: float | None = None, y: float | None = None, zone: str = "") -> str:
    """One craft mark carrying its format's card: name, category and group,
    what it is, and whether TicketRadar's Hyderabad data names it. The card
    is a real child element (hover, focus and a tap reveal it, in CSS); its
    text is spoken once, through the mark's ``aria-label``."""
    f = fmtdir.BY_CRAFT[key]
    where = fmtdir.availability(f)
    label = f"{f.name} — {f.category}, {f.group}. {f.description} {where}."
    positioned = x is not None
    side = _side(x, zone) if positioned else ""
    cls = f"tr-craft {side}".strip()
    style = f' style="left:{x}%;top:{y:.0f}%"' if positioned else ""
    return (f'<span class="{cls}"{style} tabindex="0" role="img" aria-label="{C.e(label)}">'
            f'{home_ui._icon(crafts.BY_KEY[key])}'
            f'<span class="tr-sub" aria-hidden="true"><span class="t">{C.e(f.name)}</span>'
            f'<span class="g">{C.e(f.category)} · {C.e(f.group)}</span>'
            f'<span class="d">{C.e(f.description)}</span><span class="h">{C.e(where)}</span></span></span>')


def craft_zone(zone: str) -> str:
    """Home's craft marks at their places (``ui.crafts`` paths, ``ui.home``'s
    markup for the icon), each explaining one cinema format."""
    marks = "".join(_format_mark(k, x, y, zone) for k, x, y in CRAFT_ROWS[zone])
    return (f'<div class="tr-sx-crafts {zone}" role="group" aria-label="Cinema formats, explained">'
            f'{marks}</div>')


def format_strip() -> str:
    """The phone's strip of the same 24 marks, in the same order."""
    keys = [k for zone in ("band", "foot") for k, _x, _y in CRAFT_ROWS[zone]]
    return ('<div class="tr-sx-fxstrip" role="group" aria-label="Cinema formats, explained"><div class="row">'
            + "".join(_format_mark(k) for k in keys) + '</div></div>')


def lines_markup() -> str:
    """The rotating tribute: every line in one fixed slot, each shown in turn by
    its own CSS delay — no script, no rerun, no layout shift."""
    spans = "".join(
        f'<span class="ln" style="animation-delay:{i * LINE_SECONDS}s"><span class="q">“{C.e(d.line)}”</span>'
        f'<span class="by tr-mono">{C.e(d.credit)}</span></span>'
        for i, d in enumerate(LINES))
    return f'<div class="tr-sx-lines" aria-hidden="true">{spans}</div>'


def ambience() -> None:
    """The room behind the page, drawn first so everything sits on top of it."""
    C.html(AMBIENCE_CSS + '<div class="tr-sx-ambience" aria-hidden="true">'
           + radar.svg(size="min(78vw, 980px)", state="idle", cls="sxbg", label="") + "</div>")


def hero() -> None:
    with st.container(key="trsx_hero"):
        C.html('<div class="tr-sx-hero-radar" aria-hidden="true"><div class="scan"></div>'
               + radar.svg(size=104, state="idle", cls="sxhero", label="") + "</div>" + craft_zone("band"))
        # ``C.hero``'s own markup, with the tribute line in the copy's empty space
        C.html('<div class="tr-hero"><div class="tr-hero-inner"><div>'
               '<h1>Showtime Explorer</h1>'
               '<div class="tr-hero-lede">Find your movie. Find your theatre. Find your time.</div>'
               '<div class="tr-hero-sub">Showtimes across Hyderabad, read from BookMyShow.</div>'
               + lines_markup() + '</div>'
               '<div class="tr-hero-mark"><span>Some stories</span><span>are worth</span><b>the wait</b></div>'
               '</div></div>')


def page() -> None:
    _boot()
    with st.container(key="trsx_root"):
        ambience()
        hero()
        _page_body()
        C.html(craft_zone("foot") + format_strip())


@st.cache_resource(show_spinner=False, max_entries=4)
def _lookups(signature: tuple[str, str], slug: str) -> tuple[list, list]:
    """The movie and theatre pickers' contents, built once per catalogue file
    (``signature`` is its content digest) instead of on every rerun. Public
    catalogue data only — nothing about whoever is looking."""
    entries = cv.view(slug, "bookmyshow").entries
    return catalog.titles(entries), catalog.theatres(entries)


def _page_body() -> None:
    slug = "hyderabad"
    view = cv.view(slug, "bookmyshow")
    titles, theatres = _lookups(cv._signature("bookmyshow"), slug)
    mode = st.session_state.get("sx_mode", "movie")

    _modes(mode)
    film: catalog.FilmAt | None = None
    with st.container(border=True, key="trcard_sx_query"):
        target = _searchbox(mode, titles, theatres)
        if mode == "theatre" and target is not None:
            here = catalog.films_at(view.entries, target.code, st.session_state["sx_date"])
            film = _filmbox(catalog.films_by_title(here))
        _dates(st.session_state["sx_date"])
        window = _window()
        date_code = st.session_state["sx_date"]

        if target is None:
            languages = sorted({w.movie.language for t in titles for w in t.rows if w.movie.language})
            _filters(mode, [], languages)
            wanted, result = [], None
        else:
            if mode == "movie":
                languages = list(target.languages)
                chosen = set(st.session_state.get("sx_lang") or ()) & set(languages)
                wanted = [w for w in target.rows if not chosen or w.movie.language in chosen]
            else:
                films = list(film.rows) if film is not None else catalog.films_at(view.entries, target.code, date_code)
                languages = sorted({w.movie.language for w in films if w.movie.language}, key=str.casefold)
                chosen = set(st.session_state.get("sx_lang") or ()) & set(languages)
                wanted = [w for w in films if not chosen or w.movie.language in chosen]
            result = _load(mode, wanted, date_code) if wanted else None
            if result is not None and mode == "theatre":
                result = {**result, "venue": target.code, "venue_name": target.name, "film_at": film}
            elif result is not None:
                result = {**result, "film": target}
            shows = [s for d in (result or {}).get("days", []) for s in d.shows]
            _filters(mode, shows, languages)

    if target is None:
        if not view.movies:
            _empty("The catalogue isn't ready", "The movie list for Hyderabad hasn't synced yet, so there is "
                   "nothing to search. It refreshes on its own several times a day.")
            return
        what = "a movie" if mode == "movie" else "a theatre"
        _empty(f"Choose {what} to begin", "Pick a date and a time window, and every matching show across "
               "Hyderabad appears here — in time order, or grouped." if mode == "movie" else
               "Pick a date and a time window to see every film and showtime it lists.")
        return
    if window is None:
        return
    if not wanted:
        name, sub = date_label(date_code)
        what = film.title if film is not None else "film"
        _empty("Nothing listed here", f"The catalogue has no {what} listed at {target.name} for {name} {sub}"
               + (" in the chosen languages." if st.session_state.get("sx_lang") else "."))
        return
    _results(mode, result, window, date_code)


__all__ = ["DEFAULTS", "MODES", "date_codes", "date_label", "freshness", "group_markup", "page",
           "time_rows_markup"]
