"""Movie Ticket Radar — Streamlit front end.

Layout follows TicketRadar UI Design System / DESIGN-SPEC.md §6:

    sidebar (nav)  |  main (hero + 5 setup steps + CTA)  |  right rail (status)

The app only ever *configures* monitoring and *reports* what the worker
observed. It never claims a check happened — every timestamp on screen comes
from ``data/state.json``, which only the GitHub Actions worker writes.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import streamlit as st

st.set_page_config(
    page_title="TicketRadar — Movie Ticket Monitor",
    page_icon="🎟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from config.store import (  # noqa: E402
    dispatch_workflow,
    github_status,
    github_token,
    load_history,
    load_settings,
    save_settings,
)
from config.timezone import IST, fmt_datetime, fmt_time, now_ist  # noqa: E402
from monitor import catalogue  # noqa: E402
from monitor.models import (  # noqa: E402
    ANY_FORMAT,
    Availability,
    Monitor,
    MonitorStatus,
    TheatreTarget,
    dedupe,
)
from monitor.state import (  # noqa: E402
    MonitorState,
    delete_monitor,
    expire_due_monitors,
    extend_monitor,
    load_monitors,
    load_state,
    record_history,
    stop_monitor,
    upsert_monitor,
)
from notifications.email import NotificationError, is_configured, send_test_email  # noqa: E402
from platforms import PLATFORMS  # noqa: E402
from platforms.base import PlatformBlocked, PlatformError  # noqa: E402
from ui import components as C  # noqa: E402
from ui.theme import inject  # noqa: E402

INTERVALS = [10, 15, 30]
APP_VERSION = "1.0.0"

inject()


# ──────────────────────────────────────────────────────────────────────────
# Session bootstrap
# ──────────────────────────────────────────────────────────────────────────
def boot() -> None:
    st.session_state.setdefault("movie_id", "")
    st.session_state.setdefault("theatres", [])
    st.session_state.setdefault("formats", {})
    st.session_state.setdefault("interval", load_settings().get("default_interval", 10))
    st.session_state.setdefault("movie_query", "")
    st.session_state.setdefault("flash", None)
    st.session_state.setdefault("page", "Home")


def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def drain_flash() -> None:
    payload = st.session_state.pop("flash", None)
    if not payload:
        return
    kind, message = payload
    {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(message)


@st.cache_data(ttl=20, show_spinner=False)
def _cached_view(_stamp: float) -> None:  # pragma: no cover - cache key only
    return None


def load_view() -> tuple[list[Monitor], dict[str, MonitorState], list[dict]]:
    """Read persisted config + state, expiring anything past its end time.

    Expiry runs here as well as in the worker so a monitor cannot look alive
    in the UI just because no scheduled run has happened yet.
    """
    monitors, _ = expire_due_monitors(mirror=bool(github_token()))
    return monitors, load_state(), load_history()


# ──────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────
def sidebar(active_count: int) -> str:
    with st.sidebar:
        C.html(
            """<div class="tr-logo">
              <div class="tr-logo-mark">R</div>
              <div><div class="tr-logo-name">Ticket<em>Radar</em></div>
              <div class="tr-logo-sub">Be first in line.</div></div>
            </div>"""
        )
        st.write("")
        pages = ["Home", "My Monitors", "History", "Settings"]
        labels = {"My Monitors": f"My Monitors  ·  {active_count}" if active_count else "My Monitors"}
        choice = st.radio(
            "Navigation",
            pages,
            format_func=lambda p: labels.get(p, p),
            key="page",
            label_visibility="collapsed",
        )
        st.write("")
        C.html(
            '<div class="tr-quote">“Good movies find their audience. '
            "You just have to be faster.”</div>"
            f'<div class="tr-version"><span>V{APP_VERSION}</span><span>HYDERABAD</span></div>'
        )
        return choice


# ──────────────────────────────────────────────────────────────────────────
# Step 1 — movie
# ──────────────────────────────────────────────────────────────────────────
def add_movie_panel() -> None:
    """Resolve a BookMyShow listing into the catalogue.

    A pasted 'Book tickets' URL is the one input BookMyShow gives us that is
    unambiguous and stable. Everything after this — the theatre list, the
    formats each theatre runs — is read from the platform, never typed in.
    """
    with st.expander("Add a movie from BookMyShow", expanded=not catalogue.list_entries()):
        st.caption(
            "Open the movie on BookMyShow, hit **Book tickets**, and paste the URL. "
            "TicketRadar reads the theatres and formats from the listing itself."
        )
        url = st.text_input(
            "BookMyShow link",
            placeholder="https://in.bookmyshow.com/movies/hyderabad/…/ET00123456",
            label_visibility="collapsed",
            key="add_movie_url",
        )
        left, right = st.columns([1, 1])
        if left.button("Resolve movie", use_container_width=True, key="resolve_btn"):
            _resolve(url)
        if right.button("Resolve on GitHub", use_container_width=True, key="dispatch_btn",
                        help="Runs the resolver as a GitHub Action — use this if BookMyShow "
                             "blocks requests from here."):
            _dispatch_resolve(url)


def _resolve(url: str) -> None:
    if not url.strip():
        flash("warning", "Paste a BookMyShow link first.")
        st.rerun()
    try:
        with st.spinner("Reading the BookMyShow listing…"):
            entry = catalogue.resolve_url(url, mirror=bool(github_token()))
    except PlatformBlocked as exc:
        flash(
            "error",
            f"{exc}\n\nBookMyShow blocks some networks (Streamlit Cloud included). "
            "Try **Resolve on GitHub** — the Actions runner usually gets through.",
        )
    except PlatformError as exc:
        flash("error", str(exc))
    else:
        movie = catalogue.movie_from_entry(entry)
        st.session_state["movie_id"] = movie.id
        st.session_state["theatres"] = []
        st.session_state["formats"] = {}
        flash("success", f"Added **{movie.title}** — {len(entry.get('venues', []))} theatre(s) found.")
    st.rerun()


def _dispatch_resolve(url: str) -> None:
    if not url.strip():
        flash("warning", "Paste a BookMyShow link first.")
        st.rerun()
    ok, message = dispatch_workflow("resolve-movie.yml", {"url": url.strip()})
    flash(
        "success" if ok else "error",
        f"{message} Give it a minute, then reload — the movie appears once the "
        "workflow commits the catalogue." if ok else message,
    )
    st.rerun()


def movie_step() -> dict | None:
    C.step_header(1, "Select movie", "Search the release you want on your radar.")
    st.write("")
    add_movie_panel()

    entries = catalogue.list_entries()
    if not entries:
        st.caption("No movies yet. Add one above and its theatres appear in step 2.")
        return None

    query = st.text_input(
        "Search",
        placeholder="Search for a movie…",
        label_visibility="collapsed",
        key="movie_query",
    )
    matches = catalogue.search_entries(query)
    if not matches:
        st.caption(f"Nothing in your catalogue matches “{query}”.")
        return None

    selected_id = st.session_state.get("movie_id", "")
    columns = st.columns(min(6, max(2, len(matches))))
    for idx, entry in enumerate(matches[:12]):
        movie = catalogue.movie_from_entry(entry)
        with columns[idx % len(columns)]:
            meta = " · ".join(x for x in (movie.language, f"{len(entry.get('venues', []))} theatres") if x)
            C.poster_tile(movie.title, meta, movie.id == selected_id, movie.poster_url)
            if st.button(
                "Selected" if movie.id == selected_id else "Select",
                key=f"pick_{movie.id}",
                use_container_width=True,
            ):
                st.session_state["movie_id"] = movie.id
                st.session_state["theatres"] = []
                st.session_state["formats"] = {}
                st.rerun()

    return catalogue.find_entry(st.session_state.get("movie_id", ""))


# ──────────────────────────────────────────────────────────────────────────
# Steps 2 + 3 — theatres and their formats
# ──────────────────────────────────────────────────────────────────────────
def theatre_step(entry: dict) -> list:
    venues = catalogue.venues_from_entry(entry)
    head, action = st.columns([3, 1])
    with head:
        C.step_header(2, "Where do you want to watch?", "One or more theatres in Hyderabad.")
    with action:
        st.write("")
        if st.button("Select all", key="select_all", use_container_width=True):
            st.session_state["theatres"] = [v.code for v in venues]
            st.rerun()
    st.write("")

    if not venues:
        st.caption(
            "No theatres are listed for this movie yet — that usually means BookMyShow "
            "hasn't opened it. Refresh the movie in Settings once it does."
        )
        return []

    chosen = set(st.session_state.get("theatres", []))
    for venue in venues:
        picked = st.checkbox(
            f"**{venue.name}**  ·  {venue.area or 'Hyderabad'}",
            value=venue.code in chosen,
            key=f"th_{venue.code}",
        )
        if picked:
            chosen.add(venue.code)
        else:
            chosen.discard(venue.code)

    st.session_state["theatres"] = [v.code for v in venues if v.code in chosen]
    return [v for v in venues if v.code in chosen]


def format_step(selected_venues: list) -> dict[str, str]:
    C.step_header(3, "Formats, per theatre", "Only formats that theatre actually runs.")
    st.write("")

    if not selected_venues:
        st.caption("Pick a theatre in step 2 and its formats appear here.")
        return {}

    formats = dict(st.session_state.get("formats", {}))
    for venue in selected_venues:
        # "Any format" first, then whatever this movie is actually screening
        # at this theatre. Nothing is hard-coded.
        options = [ANY_FORMAT] + dedupe(venue.formats)
        current = formats.get(venue.code, ANY_FORMAT)
        index = options.index(current) if current in options else 0

        C.html(
            f'<div style="font-size:13px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.02em;margin-top:6px;">{C.e(venue.name)}</div>'
            f'<div style="font-size:11.5px;color:#8E8E98;margin-top:3px;margin-bottom:6px;">'
            f"{C.e(venue.area or 'Hyderabad')}</div>"
        )
        if len(options) == 1:
            st.caption("No format published for this theatre yet — watching every show.")
        formats[venue.code] = st.radio(
            f"Format for {venue.name}",
            options,
            index=index,
            key=f"fmt_{venue.code}",
            label_visibility="collapsed",
        )

    st.session_state["formats"] = formats
    return formats


# ──────────────────────────────────────────────────────────────────────────
# Steps 4 + 5 — cadence and end time
# ──────────────────────────────────────────────────────────────────────────
def frequency_step() -> int:
    C.step_header(4, "How often should I check?", "Checks run automatically in the background.")
    st.write("")
    current = st.session_state.get("interval", 10)
    try:
        chosen = st.segmented_control(
            "Interval",
            INTERVALS,
            default=current if current in INTERVALS else 10,
            format_func=lambda m: f"{m}\nMINUTES",
            label_visibility="collapsed",
            key="interval_pick",
        )
    except AttributeError:  # older Streamlit without segmented_control
        chosen = st.radio(
            "Interval",
            INTERVALS,
            index=INTERVALS.index(current) if current in INTERVALS else 0,
            format_func=lambda m: f"{m} minutes",
            horizontal=True,
            label_visibility="collapsed",
            key="interval_pick",
        )
    interval = chosen if chosen in INTERVALS else current
    st.session_state["interval"] = interval
    st.caption(
        "Background checks are scheduled, not instant — GitHub runs them a few "
        "minutes late under load, so treat this as a floor, not a promise."
    )
    return interval


def until_step() -> datetime:
    C.step_header(5, "Monitor until", "The monitor stops itself after this time.")
    st.write("")
    default = now_ist() + timedelta(days=1)
    left, right = st.columns([1.5, 1])
    end_date = left.date_input(
        "End date",
        value=default.date(),
        min_value=now_ist().date(),
        format="DD/MM/YYYY",
        key="until_date",
        label_visibility="collapsed",
    )
    end_time = right.time_input(
        "End time",
        value=dtime(23, 59),
        step=timedelta(minutes=15),
        key="until_time",
        label_visibility="collapsed",
    )
    st.toggle("Start checking immediately", value=True, key="start_now")
    return datetime.combine(end_date, end_time, tzinfo=IST)


# ──────────────────────────────────────────────────────────────────────────
# Start
# ──────────────────────────────────────────────────────────────────────────
def start_monitor(entry: dict, venues: list, formats: dict[str, str],
                  interval: int, until: datetime, email: str) -> None:
    problems = []
    if not venues:
        problems.append("pick at least one theatre")
    if until <= now_ist():
        problems.append("choose an end time in the future")
    if not email.strip():
        problems.append("set your notification email in Settings")
    if problems:
        flash("warning", "Almost — " + ", ".join(problems) + ".")
        st.rerun()

    movie = catalogue.movie_from_entry(entry)
    monitor = Monitor(
        movie=movie,
        targets=[
            TheatreTarget(
                venue_code=v.code,
                venue_name=v.name,
                area=v.area,
                fmt=formats.get(v.code, ANY_FORMAT),
            )
            for v in venues
        ],
        interval_minutes=interval,
        monitor_until=until,
        start_immediately=bool(st.session_state.get("start_now", True)),
        notify_email=email.strip(),
    )
    upsert_monitor(monitor, mirror=bool(github_token()))
    record_history(monitor, "CREATED", "Monitor created.", mirror=bool(github_token()))
    flash(
        "success",
        f"Watching **{movie.title}** at {len(monitor.targets)} theatre(s), every "
        f"{interval} minutes, until {fmt_datetime(until)}.",
    )
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# Right rail
# ──────────────────────────────────────────────────────────────────────────
@st.fragment(run_every=30)
def status_card(monitor_id: str) -> None:
    """The rail's status card, re-rendered on its own every 30 seconds.

    A fragment rather than a whole-page refresh (DESIGN-SPEC §10): it keeps
    the countdown and "last checked" honest while the tab is open, without
    resetting the form being filled in above it. It re-reads the files each
    time, so it also picks up a check the worker committed while you were
    looking at the page.
    """
    monitor = next((m for m in load_monitors() if m.id == monitor_id), None)
    if monitor is None:
        return
    C.active_monitor_card(monitor, load_state().get(monitor_id, MonitorState()))


def rail(monitors: list[Monitor], states: dict[str, MonitorState], history: list[dict]) -> None:
    active = [m for m in monitors if m.is_running()]
    if not active:
        C.html('<div class="tr-eyebrow" style="margin-bottom:9px;">Active monitor</div>')
        C.empty_card()
        st.write("")
        if history:
            C.html('<div class="tr-eyebrow" style="margin:16px 0 9px;">Recent history</div>')
            C.history_rows(history)
        return

    monitor = active[0]
    state = states.get(monitor.id, MonitorState())

    C.html(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
        '<span class="tr-dot ok live"></span>'
        '<span style="font-size:14px;font-weight:700;">Active monitor</span></div>'
    )
    status_card(monitor.id)
    st.write("")

    with st.container():
        C.html('<div class="tr-danger">')
        if st.button("■  Stop monitoring", key=f"stop_{monitor.id}", use_container_width=True):
            stop_monitor(monitor.id, mirror=bool(github_token()))
            flash("success", "Monitoring stopped. The background worker will skip it from now on.")
            st.rerun()
        C.html("</div>")

    st.write("")
    C.target_rows(monitor, state)

    if len(active) > 1:
        st.caption(f"+{len(active) - 1} more active monitor(s) — see **My Monitors**.")

    if history:
        C.html('<div class="tr-eyebrow" style="margin:18px 0 9px;">Recent history</div>')
        C.history_rows(history)


# ──────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────
def page_home(monitors: list[Monitor], states: dict[str, MonitorState], history: list[dict],
              settings: dict) -> None:
    main, side = st.columns([3.3, 1.25], gap="large")

    with main:
        C.hero(
            "Movie Ticket Monitor",
            "Know the moment your tickets go live.",
            "We watch the booking page for you, so you don't have to.",
        )
        drain_flash()

        C.rule("Platform")
        entries = catalogue.list_entries()
        theatre_count = len({v.get("code") for e_ in entries for v in e_.get("venues", [])})
        C.platform_selector(PLATFORMS, "bookmyshow", theatre_count, "Hyderabad")
        st.write("")

        live_monitor = next(
            (
                m for m in monitors
                if m.is_running()
                and any(
                    ts.availability is Availability.AVAILABLE
                    for ts in states.get(m.id, MonitorState()).targets.values()
                )
            ),
            None,
        )
        if live_monitor is not None:
            state = states[live_monitor.id]
            for key, ts in state.targets.items():
                if ts.availability is Availability.AVAILABLE:
                    C.live_card(live_monitor, state, key)
                    break
            st.caption(
                "One theatre going live doesn't stop the others — the rest keep being "
                f"checked until {fmt_datetime(live_monitor.monitor_until)}."
            )
            st.write("")

        errored = next(
            (m for m in monitors if m.is_running() and states.get(m.id, MonitorState()).consecutive_errors),
            None,
        )
        if errored is not None:
            C.error_card(states[errored.id], errored.interval_minutes)
            st.write("")

        with st.container(border=True, key="trcard_movie"):
            entry = movie_step()

        st.write("")
        col_a, col_b = st.columns([1, 1.08], gap="medium")
        selected_venues: list = []
        formats: dict[str, str] = {}
        with col_a, st.container(border=True, key="trcard_theatres"):
            selected_venues = theatre_step(entry) if entry else []
            if not entry:
                C.step_header(2, "Where do you want to watch?", "One or more theatres in Hyderabad.")
                st.caption("Select a movie first.")
        with col_b, st.container(border=True, key="trcard_formats"):
            formats = format_step(selected_venues)

        st.write("")
        col_c, col_d = st.columns(2, gap="medium")
        with col_c, st.container(border=True, key="trcard_frequency"):
            interval = frequency_step()
        with col_d, st.container(border=True, key="trcard_until"):
            until = until_step()

        st.write("")
        cta, helper = st.columns([2.2, 1], gap="medium")
        with cta:
            disabled = entry is None or not selected_venues
            if st.button("▶  Start monitoring", type="primary", use_container_width=True,
                         disabled=disabled, key="start"):
                start_monitor(entry, selected_venues, formats, interval, until,
                              settings.get("notify_email", ""))
        with helper:
            C.html(
                '<div style="font-size:12.5px;line-height:1.5;color:#8E8E98;padding-top:8px;">'
                '<span style="color:#E8B25C;">⚡</span> You\'ll get an email the second tickets '
                "appear — and the monitor keeps running for the other theatres.</div>"
            )

    with side:
        rail(monitors, states, history)


def page_monitors(monitors: list[Monitor], states: dict[str, MonitorState]) -> None:
    C.hero("My Monitors", "Everything you've asked us to watch.",
           "Active alerts first, then the ones that finished.")
    drain_flash()

    if not monitors:
        C.empty_card()
        return

    order = {MonitorStatus.ACTIVE: 0, MonitorStatus.EXPIRED: 1, MonitorStatus.STOPPED: 2}
    for monitor in sorted(monitors, key=lambda m: (order.get(m.status, 3), m.created_at), reverse=False):
        state = states.get(monitor.id, MonitorState())
        kind = C.status_for(monitor, state)

        left, right = st.columns([2.4, 1], gap="large")
        with left:
            if kind in ("active", "live"):
                C.active_monitor_card(monitor, state)
            elif kind == "error":
                C.error_card(state, monitor.interval_minutes)
            elif kind == "expired":
                C.expired_card(monitor, state)
            else:
                C.stopped_card(monitor, state)
            st.write("")
            C.target_rows(monitor, state)

        with right:
            st.write("")
            if monitor.is_running():
                C.html('<div class="tr-danger">')
                if st.button("■  Stop monitoring", key=f"m_stop_{monitor.id}", use_container_width=True):
                    stop_monitor(monitor.id, mirror=bool(github_token()))
                    flash("success", "Monitoring stopped.")
                    st.rerun()
                C.html("</div>")
            else:
                C.html('<div class="tr-amber">')
                if st.button("Extend by 24 hours", key=f"m_ext_{monitor.id}", use_container_width=True):
                    extend_monitor(monitor.id, 24, mirror=bool(github_token()))
                    flash("success", "Extended by 24 hours — monitoring is active again.")
                    st.rerun()
                C.html("</div>")
            if st.button("Delete", key=f"m_del_{monitor.id}", use_container_width=True):
                delete_monitor(monitor.id, mirror=bool(github_token()))
                flash("success", "Monitor deleted.")
                st.rerun()

            st.caption(f"Created {fmt_datetime(monitor.created_at)}")
            st.caption(f"Checks run: {state.check_count} · succeeded: {state.success_count}")
            if state.last_error:
                st.caption(f"Last error: {state.last_error}")
        st.divider()


def page_history(history: list[dict]) -> None:
    C.hero("History", "What the radar has picked up.",
           "Every release, stop and expiry, newest first.")
    drain_flash()
    if not history:
        C.empty_card("Nothing yet — history fills up once a monitor runs.")
        return
    C.history_rows(history, limit=50)


def page_settings(settings: dict) -> None:
    C.hero("Settings", "Where alerts go, and what's wired up.",
           "Credentials live in secrets — never in this repository.")
    drain_flash()

    left, right = st.columns(2, gap="large")

    with left, st.container(border=True, key="trcard_notify"):
        C.html('<div class="tr-step-title">Notifications</div>')
        st.caption("The address every alert is sent to.")
        email = st.text_input("Notification email", value=settings.get("notify_email", ""),
                              placeholder="you@gmail.com", key="settings_email")
        default_interval = st.selectbox(
            "Default check interval",
            INTERVALS,
            index=INTERVALS.index(settings.get("default_interval", 10))
            if settings.get("default_interval", 10) in INTERVALS else 0,
            format_func=lambda m: f"{m} minutes",
        )
        a, b = st.columns(2)
        if a.button("Save", use_container_width=True, key="save_settings"):
            save_settings(
                {**settings, "notify_email": email.strip(), "default_interval": default_interval},
                mirror=bool(github_token()),
            )
            flash("success", "Settings saved.")
            st.rerun()
        if b.button("Send test email", use_container_width=True, key="test_email"):
            try:
                send_test_email(email.strip() or settings.get("notify_email", ""))
            except NotificationError as exc:
                flash("error", str(exc))
            else:
                flash("success", f"Test email sent to {email.strip()}.")
            st.rerun()

        st.write("")
        if is_configured():
            st.caption("✓ Gmail credentials are present in this environment.")
        else:
            st.caption(
                "◷ No Gmail credentials here. The UI only needs them for the test "
                "button; the worker reads them from GitHub Actions secrets."
            )

    with right, st.container(border=True, key="trcard_storage"):
        C.html('<div class="tr-step-title">Storage & background checks</div>')
        connected, message = github_status()
        st.caption(("✓ " if connected else "! ") + message)
        st.caption(
            "Monitors and observed state live in `data/` in the repository. The "
            "scheduled workflow reads them, checks BookMyShow, and commits what it saw."
        )
        st.write("")
        if st.button("Run a check now (GitHub Actions)", use_container_width=True, key="run_now"):
            ok, msg = dispatch_workflow("bookmyshow-monitor.yml", {"force": "true"})
            flash("success" if ok else "error", msg)
            st.rerun()
        if st.button("Refresh every movie's theatres", use_container_width=True, key="refresh_all"):
            ok, msg = dispatch_workflow("resolve-movie.yml", {"refresh_all": "true"})
            flash("success" if ok else "error", msg)
            st.rerun()

    st.write("")
    with st.container(border=True, key="trcard_catalogue"):
        C.html('<div class="tr-step-title">Movie catalogue</div>')
        entries = catalogue.list_entries()
        if not entries:
            st.caption("Empty — add a movie from the Home page.")
        for entry in entries:
            movie = catalogue.movie_from_entry(entry)
            cols = st.columns([3, 1, 1])
            cols[0].caption(
                f"**{movie.title}** · {movie.event_code} · "
                f"{len(entry.get('venues', []))} theatres · resolved {entry.get('resolved_at', '')[:16]}"
            )
            if cols[1].button("Refresh", key=f"ref_{movie.id}", use_container_width=True):
                try:
                    catalogue.refresh_entry(movie.id, mirror=bool(github_token()))
                except PlatformError as exc:
                    flash("error", str(exc))
                else:
                    flash("success", f"{movie.title} refreshed.")
                st.rerun()
            if cols[2].button("Remove", key=f"rm_{movie.id}", use_container_width=True):
                catalogue.remove_entry(movie.id, mirror=bool(github_token()))
                flash("success", f"{movie.title} removed from the catalogue.")
                st.rerun()


# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    boot()
    monitors, states, history = load_view()
    settings = load_settings()
    page = sidebar(sum(1 for m in monitors if m.is_running()))

    if page == "Home":
        page_home(monitors, states, history, settings)
    elif page == "My Monitors":
        page_monitors(monitors, states)
    elif page == "History":
        page_history(history)
    else:
        page_settings(settings)

    st.write("")
    C.html(
        '<div class="tr-version" style="max-width:1600px;margin-top:30px;">'
        f"<span>MOVIE TICKET RADAR V{APP_VERSION}</span>"
        f"<span>{C.e(fmt_time(now_ist()))} IST</span></div>"
    )


main()
