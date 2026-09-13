"""Movie Ticket Radar — Streamlit front end.

Flow (TicketRadar UI Design System, extended to lead with location):

    1 Location → 2 Movie → 3 Theatres → 4 Formats → 5 Monitoring → START

The user never sees a BookMyShow URL, an event code, or anything about how
the data got here. They pick a city, and the movies showing in it are already
there — synced into ``data/catalogue.json`` by a scheduled GitHub Action,
because BookMyShow's bot check cannot be relied on to answer a browser-facing
host. ``ui/flow.py`` owns the wizard; this module owns the page and the rail.

The app only ever *configures* monitoring and *reports* what the worker saw.
It never claims a check happened: every timestamp on screen comes from
``data/state.json``, which only the worker writes.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="TicketRadar — Movie Ticket Monitor",
    page_icon="🎟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from config.locations import get_location  # noqa: E402
from config.store import (  # noqa: E402
    dispatch_workflow,
    github_status,
    github_token,
    load_history,
    load_settings,
    save_settings,
)
from config.timezone import fmt_datetime, fmt_time, now_ist  # noqa: E402
from monitor import catalogue  # noqa: E402
from monitor.models import ANY_FORMAT, Availability, Monitor, MonitorStatus, TheatreTarget  # noqa: E402
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
from platforms.http import HAS_CURL_CFFI  # noqa: E402
from ui import components as C  # noqa: E402
from ui import flow  # noqa: E402
from ui.theme import inject  # noqa: E402

APP_VERSION = "2.0.0"

inject()


# ──────────────────────────────────────────────────────────────────────────
# Session
# ──────────────────────────────────────────────────────────────────────────
def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def drain_flash() -> None:
    payload = st.session_state.pop("flash", None)
    if not payload:
        return
    kind, message = payload
    {"success": st.success, "error": st.error, "warning": st.warning}.get(kind, st.info)(message)


def load_view():
    """Persisted config + state, expiring anything past its end time.

    Expiry runs here as well as in the worker, so a monitor can never look
    alive in the UI merely because no scheduled run has happened yet.
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
        labels = {"My Monitors": f"My Monitors  ·  {active_count}"} if active_count else {}
        choice = st.radio("Navigation", pages, format_func=lambda p: labels.get(p, p),
                          key="page", label_visibility="collapsed")
        st.write("")
        C.html(
            '<div class="tr-quote">“Good movies find their audience. '
            "You just have to be faster.”</div>"
            f'<div class="tr-version"><span>V{APP_VERSION}</span>'
            f"<span>{C.e(get_location(st.session_state.get('location') or 'hyderabad').name.upper())}</span></div>"
        )
        return choice


# ──────────────────────────────────────────────────────────────────────────
# Starting a monitor
# ──────────────────────────────────────────────────────────────────────────
def start_monitor(interval: int, until, email: str) -> None:
    entry = catalogue.find_entry(st.session_state.get("movie_id", ""))
    venues = {v.code: v for v in catalogue.venues_from_entry(entry or {})}
    formats: dict[str, list[str]] = st.session_state.get("formats", {})

    problems = []
    if entry is None:
        problems.append("pick a movie")
    if not st.session_state.get("theatres"):
        problems.append("pick at least one theatre")
    if until <= now_ist():
        problems.append("choose an end time in the future")
    if not email:
        problems.append("enter a notification email")
    if problems:
        flash("warning", "Almost — " + ", ".join(problems) + ".")
        st.rerun()

    # One target per theatre × format. They are watched independently, so a
    # theatre going live in IMAX never mutes the same theatre's 2D screen,
    # let alone a different theatre.
    targets = []
    for code in st.session_state["theatres"]:
        venue = venues.get(code)
        if venue is None:
            continue
        for fmt in formats.get(code) or [ANY_FORMAT]:
            targets.append(
                TheatreTarget(venue_code=venue.code, venue_name=venue.name,
                              area=venue.area, fmt=fmt)
            )
    if not targets:
        flash("warning", "Pick at least one format.")
        st.rerun()

    monitor = Monitor(
        movie=catalogue.movie_from_entry(entry),
        targets=targets,
        interval_minutes=interval,
        monitor_until=until,
        notify_email=email,
    )
    upsert_monitor(monitor, mirror=bool(github_token()))
    record_history(monitor, "CREATED", "Monitor created.", mirror=bool(github_token()))

    settings = load_settings()
    if settings.get("notify_email") != email:
        save_settings({**settings, "notify_email": email}, mirror=bool(github_token()))

    flash(
        "success",
        f"Watching **{monitor.movie.title}** across {len(targets)} theatre/format "
        f"combination(s), every {interval} minutes, until {fmt_datetime(until)}.",
    )
    st.session_state["step"] = 1
    st.session_state["furthest"] = 1
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# Right rail
# ──────────────────────────────────────────────────────────────────────────
@st.fragment(run_every=30)
def status_card(monitor_id: str) -> None:
    """Re-rendered on its own every 30s (DESIGN-SPEC §10).

    Keeps the countdown and "last checked" honest while the tab is open
    without resetting the wizard above it, and picks up a check the worker
    committed while you were looking at the page.
    """
    monitor = next((m for m in load_monitors() if m.id == monitor_id), None)
    if monitor is None:
        return
    C.active_monitor_card(monitor, load_state().get(monitor_id, MonitorState()))


def rail(monitors: list[Monitor], states: dict[str, MonitorState], history: list[dict]) -> None:
    active = [m for m in monitors if m.is_running()]
    if not active:
        C.html('<div class="tr-eyebrow" style="margin-bottom:9px;">Active monitoring</div>')
        C.empty_card()
        if history:
            C.html('<div class="tr-eyebrow" style="margin:18px 0 9px;">Recent history</div>')
            C.history_rows(history)
        return

    monitor = active[0]
    state = states.get(monitor.id, MonitorState())
    C.html(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
        '<span class="tr-dot ok live"></span>'
        '<span style="font-size:14px;font-weight:700;">Active monitoring</span>'
        f'<span class="tr-count">{len(active)}</span></div>'
    )
    status_card(monitor.id)
    st.write("")

    C.html('<div class="tr-danger">')
    if st.button("■  Stop monitoring", key=f"stop_{monitor.id}", use_container_width=True):
        stop_monitor(monitor.id, mirror=bool(github_token()))
        flash("success", "Monitoring stopped. The background worker will skip it from now on.")
        st.rerun()
    C.html("</div>")

    st.write("")
    C.target_rows(monitor, state)

    if len(active) > 1:
        st.caption(f"+{len(active) - 1} more active — see **My Monitors**.")
    if history:
        C.html('<div class="tr-eyebrow" style="margin:18px 0 9px;">Recent history</div>')
        C.history_rows(history)


# ──────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────
def page_home(monitors, states, history, settings) -> None:
    main, side = st.columns([3.3, 1.25], gap="large")

    with main:
        C.hero("Movie Ticket Monitor", "Know the moment your tickets go live.",
               "We watch the booking page for you, so you don't have to.")
        drain_flash()

        C.rule("Platform")
        C.platform_selector(PLATFORMS, "bookmyshow",
                            len(catalogue.list_entries()), "BookMyShow")
        st.write("")

        # A live target is the loudest thing on screen when it happens.
        live_monitor = next(
            (m for m in monitors if m.is_running() and any(
                ts.availability is Availability.AVAILABLE
                for ts in states.get(m.id, MonitorState()).targets.values())),
            None,
        )
        if live_monitor is not None:
            state = states[live_monitor.id]
            for key, ts in state.targets.items():
                if ts.availability is Availability.AVAILABLE:
                    C.live_card(live_monitor, state, key)
                    break
            st.caption("One theatre going live doesn't stop the others — the rest keep "
                       f"being checked until {fmt_datetime(live_monitor.monitor_until)}.")
            st.write("")

        errored = next(
            (m for m in monitors if m.is_running()
             and states.get(m.id, MonitorState()).consecutive_errors), None)
        if errored is not None:
            C.error_card(states[errored.id], errored.interval_minutes)
            st.write("")

        step = st.session_state.get("step", 1)
        C.step_strip(flow.STEPS, step, st.session_state.get("furthest", 1))
        st.write("")

        flow.summary(step)
        if step > 1:
            back, _ = st.columns([1, 4])
            if back.button("←  Back", key="back", use_container_width=True):
                flow.goto(step - 1)
            st.write("")

        with st.container(border=True, key="trcard_step"):
            if step == 1:
                flow.step_location()
            elif step == 2:
                flow.step_movie()
            elif step == 3:
                flow.step_theatres()
            elif step == 4:
                entry = catalogue.find_entry(st.session_state.get("movie_id", ""))
                venues = {v.code: v for v in catalogue.venues_from_entry(entry or {})}
                flow.step_formats([venues[c] for c in st.session_state.get("theatres", [])
                                   if c in venues])
            else:
                interval, until, email = flow.step_monitoring(settings.get("notify_email", ""))
                st.write("")
                cta, helper = st.columns([2.2, 1], gap="medium")
                with cta:
                    if st.button("▶  Start monitoring", type="primary",
                                 use_container_width=True, key="start"):
                        start_monitor(interval, until, email)
                with helper:
                    C.html(
                        '<div style="font-size:12.5px;line-height:1.5;color:#8E8E98;padding-top:8px;">'
                        '<span style="color:#E8B25C;">⚡</span> You\'ll get an email the second '
                        "tickets appear — and the monitor keeps running for the other theatres.</div>"
                    )

    with side:
        rail(monitors, states, history)


def page_monitors(monitors, states) -> None:
    C.hero("My Monitors", "Everything you've asked us to watch.",
           "Active alerts first, then the ones that finished.")
    drain_flash()
    if not monitors:
        C.empty_card()
        return

    order = {MonitorStatus.ACTIVE: 0, MonitorStatus.EXPIRED: 1, MonitorStatus.STOPPED: 2}
    for monitor in sorted(monitors, key=lambda m: (order.get(m.status, 3), m.created_at)):
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
                if st.button("■  Stop monitoring", key=f"m_stop_{monitor.id}",
                             use_container_width=True):
                    stop_monitor(monitor.id, mirror=bool(github_token()))
                    flash("success", "Monitoring stopped.")
                    st.rerun()
                C.html("</div>")
            else:
                C.html('<div class="tr-amber">')
                if st.button("Extend by 24 hours", key=f"m_ext_{monitor.id}",
                             use_container_width=True):
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


def page_history(history) -> None:
    C.hero("History", "What the radar has picked up.",
           "Every release, stop and expiry, newest first.")
    drain_flash()
    if not history:
        C.empty_card("Nothing yet — history fills up once a monitor runs.")
        return
    C.history_rows(history, limit=50)


def page_settings(settings) -> None:
    C.hero("Settings", "Where alerts go, and what's wired up.",
           "Credentials live in secrets — never in this repository.")
    drain_flash()

    left, right = st.columns(2, gap="large")

    with left, st.container(border=True, key="trcard_notify"):
        C.html('<div class="tr-step-title">Notifications</div>')
        st.caption("The address every alert is sent to.")
        email = st.text_input("Notification email", value=settings.get("notify_email", ""),
                              placeholder="you@gmail.com", key="settings_email")
        a, b = st.columns(2)
        if a.button("Save", use_container_width=True, key="save_settings"):
            save_settings({**settings, "notify_email": email.strip()},
                          mirror=bool(github_token()))
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
        st.caption("✓ Gmail credentials are present here." if is_configured() else
                   "◷ No Gmail credentials here. The UI only needs them for the test "
                   "button; the worker reads them from GitHub Actions secrets.")

    with right, st.container(border=True, key="trcard_storage"):
        C.html('<div class="tr-step-title">Movie catalogue</div>')
        slug = st.session_state.get("location") or "hyderabad"
        state = catalogue.sync_state(slug)
        C.catalogue_banner(state["status"].value, state["message"],
                           flow._ago(state["at"]), len(catalogue.list_entries(slug)))
        st.caption(
            "The movie list, the theatres each movie plays at and the formats they run "
            "are synced by a scheduled background job and stored in the repository. "
            "The app reads that file, so it works even when BookMyShow won't answer it "
            "directly."
        )
        if not HAS_CURL_CFFI:
            st.caption("⚠ `curl_cffi` isn't installed here, so this app can't refresh the "
                       "catalogue itself — BookMyShow bot-checks plain requests. The "
                       "background job still can.")
        st.write("")
        if st.button("Refresh catalogue now", use_container_width=True, key="sync_now"):
            ok, msg = dispatch_workflow("catalogue-sync.yml", {"city": slug})
            flash("success" if ok else "error", msg)
            st.rerun()
        if st.button("Run a ticket check now", use_container_width=True, key="run_now"):
            ok, msg = dispatch_workflow("bookmyshow-monitor.yml", {"force": "true"})
            flash("success" if ok else "error", msg)
            st.rerun()
        connected, message = github_status()
        st.caption(("✓ " if connected else "! ") + message)


# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    flow.boot()
    st.session_state.setdefault("page", "Home")
    st.session_state.setdefault("flash", None)

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
