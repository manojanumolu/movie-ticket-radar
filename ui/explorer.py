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

MODES = {
    "movie": ("By movie", "Find where and when a movie is playing.", "movie"),
    "theatre": ("By theatre", "See every movie and showtime at a theatre.", "theatre"),
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
    C.html('<div class="tr-field-label">Date</div>')
    for column, code in flow.grid(date_codes(), DAYS, "sxdate"):
        with column:
            name, sub = date_label(code)
            sel = code == selected
            flow.pick(f"sxdate_{code}", "Selected" if sel else f"Show {name} {sub}",
                      lambda n=name, s=sub, x=sel: C.choice_tile(n, s, x),
                      on_click=_set_date, args=(code,))


def _window() -> tuple[int, int] | None:
    C.html('<div class="tr-field-label">Time window · IST</div>')
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
                       on_click=_set_window, args=(lo, hi))
    try:
        return Q.validate_window(st.session_state.get("sx_from"), st.session_state.get("sx_to"))
    except Q.WindowError as exc:
        C.status_line("bad", str(exc))
        return None


def _multiselect(column, label: str, key: str, opts: list[str], placeholder: str) -> None:
    _clean(key, opts)
    with column:
        C.html(f'<div class="tr-field-label">{C.e(label)}</div>')
        st.multiselect(label, opts, key=key, placeholder=placeholder, label_visibility="collapsed",
                       on_change=_ui_only, disabled=not opts)


def _filters(mode: str, shows: list[Q.ExplorerShow], languages: list[str]) -> None:
    opts = Q.options(shows)
    with st.container(key="sxfilters"):
        if mode == "movie":
            cols = st.columns([1.15, 1.15, 1, 1, 1.15], gap="small")
            _multiselect(cols[0], "Area", "sx_area", opts["localities"], "All Hyderabad")
            _multiselect(cols[1], "Operator", "sx_ops", opts["operators"], "All operators")
            with cols[2]:
                C.html('<div class="tr-field-label">Theatre type</div>')
                st.selectbox("Theatre type", TYPE_CHOICES, key="sx_type", label_visibility="collapsed",
                             on_change=_ui_only)
            lang_col, fmt_col = cols[3], cols[4]
        else:
            lang_col, fmt_col = st.columns(2, gap="small")
        _multiselect(lang_col, "Language", "sx_lang", languages, "All languages")
        _multiselect(fmt_col, "Format", "sx_fmt", opts["formats"], "All formats")


def _searchbox(mode: str, titles: list[catalog.Title], theatres: list[catalog.Theatre]):
    if mode == "movie":
        C.html('<div class="tr-field-label">Movie</div>')
        labels = [t.label for t in titles]
        _clean_single("sx_movie", labels)
        choice = st.selectbox("Movie", labels, index=None, key="sx_movie", label_visibility="collapsed",
                              placeholder="Search movies playing in Hyderabad…", on_change=_ui_only)
        return next((t for t in titles if t.label == choice), None)
    C.html('<div class="tr-field-label">Theatre</div>')
    labels = [t.label for t in theatres]
    _clean_single("sx_theatre", labels)
    choice = st.selectbox("Theatre", labels, index=None, key="sx_theatre", label_visibility="collapsed",
                          placeholder="Search theatres in Hyderabad — name or area…", on_change=_ui_only)
    return next((t for t in theatres if t.label == choice), None)


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
            f'title="Open on BookMyShow">{C.e(text)}{C.icon("external", 12)}</a>')


def _fmt_badges(show: Q.ExplorerShow) -> str:
    out = "".join(f'<span class="tr-badge muted">{C.e(f)}</span>' for f in show.formats[:2])
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
                primary, secondary = s.venue_name, " · ".join(x for x in (_where(s), s.language) if x)
            else:
                primary, secondary = s.movie_title, s.language
            parts.append(
                f'<div class="tr-sx-row"><div class="t"><b>{C.e(clock)}</b><span>{C.e(half)}</span></div>'
                f'<div class="m"><div class="n">{C.e(primary)}</div><div class="s">{C.e(secondary)}</div></div>'
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
            + (f'<span>{C.e(s.formats[0])}</span>' if s.formats else "")
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


def _empty(title: str, text: str) -> None:
    C.html(f'<div class="tr-state empty tr-sx-empty"><div class="ring">{C.icon("clock", 18)}</div>'
           f'<div class="h">{C.e(title)}</div><div class="p">{C.e(text)}</div></div>')


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
        hide_started_before=now_ist() if today else None,
    )
    out = Q.apply(shows, filt)
    venues = {s.venue_code for s in out.shows}

    with st.container(key="trpair_sxhead"):
        head, toggle = st.columns([3, 2], gap="small", vertical_alignment="center")
        with head:
            count = len(out.shows)
            extra = f" · {len(venues)} theatre{'s' if len(venues) != 1 else ''}" if mode == "movie" else ""
            C.html(f'<div class="tr-sx-count"><b>{count}</b> showtime{"s" if count != 1 else ""}{C.e(extra)}</div>')
        with toggle:
            view = st.session_state.get("sx_view", "time")
            group_name = "By theatre" if mode == "movie" else "By movie"
            with st.container(key="trpair_sxview"):
                a, b = st.columns(2, gap="small")
                for col, key, label in ((a, "time", "By time"), (b, "group", group_name)):
                    with col:
                        flow.pick(f"sxview_{key}", "Selected" if view == key else f"Show {label.lower()}",
                                  lambda lb=label, x=view == key: C.html(
                                      f'<div class="tr-sx-seg{" selected" if x else ""}">{C.e(lb)}</div>'),
                                  on_click=_set_view, args=(key,))
    _fresh_strip(days, date_code, window)

    if all(d.origin == S.FAILED for d in days):
        _empty("No showtimes to show", "BookMyShow couldn't be read and there is no earlier read for this "
               "selection. Nothing is shown rather than a guess — try Refresh in a minute.")
        return
    if not out.shows:
        name, sub = date_label(date_code)
        if not shows or out.wrong_date == len(shows):
            _empty("Nothing listed for this date", f"BookMyShow lists no shows for this selection on {name} {sub}"
                   " — it may not have opened bookings yet.")
        elif out.already_started and not (out.outside_window or out.filtered_out):
            _empty("Today's shows have started", "Every show listed for today in this selection has already "
                   "begun. Pick tomorrow or a later date to see what's next.")
        else:
            _empty("No shows match", "Nothing in this time window matches your filters. Widen the window or "
                   "clear a filter.")
        _notes(out, days)
        return

    limit = st.session_state.get("sx_limit", PAGE_SIZE)
    if st.session_state.get("sx_view") == "group":
        groups = Q.group_by_theatre(out.shows) if mode == "movie" else Q.group_by_movie(out.shows)
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
    skipped = [d for d in days if d.skipped]
    if skipped:
        notes.append(f"{len(skipped)} more film{'s' if len(skipped) != 1 else ''} at this theatre weren't read "
                     "live (to keep requests to BookMyShow bounded) — pick a language to narrow the list.")
    notes.append("A listed show isn't a promise of seats — BookMyShow confirms availability when you book.")
    C.html('<div class="tr-sx-notes">' + "".join(f"<div>{C.e(n)}</div>" for n in notes) + "</div>")


def page() -> None:
    _boot()
    C.hero("Showtime Explorer", "Find your movie. Find your theatre. Find your time.",
           "Showtimes across Hyderabad, read from BookMyShow.")
    slug = "hyderabad"
    view = cv.view(slug, "bookmyshow")
    titles = catalog.titles(view.entries)
    theatres = catalog.theatres(view.entries)
    mode = st.session_state.get("sx_mode", "movie")

    _modes(mode)
    with st.container(border=True, key="trcard_sx_query"):
        target = _searchbox(mode, titles, theatres)
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
                films = catalog.films_at(view.entries, target.code, date_code)
                languages = sorted({w.movie.language for w in films if w.movie.language}, key=str.casefold)
                chosen = set(st.session_state.get("sx_lang") or ()) & set(languages)
                wanted = [w for w in films if not chosen or w.movie.language in chosen]
            result = _load(mode, wanted, date_code) if wanted else None
            if result is not None and mode == "theatre":
                result = {**result, "venue": target.code}
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
        _empty("Nothing listed here", f"The catalogue has no film listed at {target.name} for {name} {sub}"
               + (" in the chosen languages." if st.session_state.get("sx_lang") else "."))
        return
    _results(mode, result, window, date_code)


__all__ = ["DEFAULTS", "MODES", "date_codes", "date_label", "freshness", "group_markup", "page",
           "time_rows_markup"]
