"""Movie Ticket Radar — Streamlit front end.

Flow (ticketradar-ui-design-system-2, led by location):

    1 Location → 2 Movie → 3 Theatres → 4 Formats → 5 Monitoring → START

The user never sees a BookMyShow URL, an event code, or anything about how
the data got here. They pick a city, and the movies showing in it are already
there — synced into ``data/catalogue.json`` by a scheduled GitHub Action,
because BookMyShow's bot check cannot be relied on to answer a browser-facing
host. ``ui/flow.py`` owns the wizard; this module owns the page and the rail.

The app only ever *configures* monitoring and *reports* what the worker saw.
It never claims a check happened: every timestamp on screen comes from
``data/state.json``, which only the worker writes. What it *does* do, the
moment a monitor is saved, is ask the worker to run right now — so the first
check is a minute away, not the next time GitHub's scheduler gets round to it.

Speed
-----
A Streamlit interaction is a rerun of this whole script, so a rerun has to be
cheap. Three things keep it so: the catalogue is parsed once per file
(``ui.catalogue_view``), the GitHub read-through that keeps "Last checked"
honest runs off the request thread after the first paint, and the Settings
page's GitHub probe is cached. Nothing here makes a BookMyShow request.
"""

from __future__ import annotations

import os
import threading

import streamlit as st

st.set_page_config(
    page_title="TicketRadar — Movie Ticket Monitor",
    page_icon="🎟️",
    layout="wide",
    # "auto": open on a desktop, an overlay a phone opens from the top-left —
    # "expanded" would pin a 244px sidebar over a 390px page.
    initial_sidebar_state="auto",
)

from auth import firebase  # noqa: E402
from auth import session as auth_session  # noqa: E402
from auth.gate import require_user  # noqa: E402
from config.locations import get_location  # noqa: E402
from config.store import (  # noqa: E402
    dispatch_workflow,
    github_status,
    github_token,
    last_mirror,
    request_check_now,
    sync_from_github,
)
from config.timezone import fmt_datetime, fmt_time, now_ist  # noqa: E402
from monitor import catalogue  # noqa: E402
from monitor.models import ANY_FORMAT, Availability, Monitor, MonitorStatus, TheatreTarget  # noqa: E402
from monitor import state as state_store  # noqa: E402
from monitor.state import (  # noqa: E402
    MonitorState,
    Scope,
    delete_monitor,
    expire_due_monitors,
    extend_monitor,
    load_history,
    load_monitors,
    load_settings,
    load_state,
    record_history,
    save_settings,
    stop_monitor,
    upsert_monitor,
)
from notifications.email import NotificationError, is_configured, send_test_email  # noqa: E402
from platforms import PLATFORMS  # noqa: E402
from platforms.http import HAS_CURL_CFFI  # noqa: E402
from ui import catalogue_view as cv  # noqa: E402
from ui import components as C  # noqa: E402
from ui import flow  # noqa: E402
from ui import login  # noqa: E402
from ui.theme import inject  # noqa: E402

APP_VERSION = "2.6.0"

inject()


def _scope() -> Scope | None:
    """Whose data a store call is for: the signed-in Firebase account, read
    from Streamlit's own session (so it is right for the thread asking),
    in the configured Firebase project. None → nobody signed in, or no
    project configured (tests, a laptop without secrets) → the JSON files."""
    # Firebase Authentication and Firestore are separate products. A project
    # id alone must not switch a working login deployment to Firestore before
    # its database and rules have been provisioned.
    enabled = os.environ.get("TICKETRADAR_FIRESTORE_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        try:
            enabled = str(st.secrets.get("firestore", {}).get("enabled", "")).strip().lower()
        except Exception:  # noqa: BLE001 - no secrets file in tests/local runs
            enabled = ""
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    user = auth_session.current_user()
    project = firebase.config().project_id
    if user is None or not project:
        return None
    return Scope(project, user.uid, auth_session.id_token)


state_store.set_scope_provider(_scope)


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
    # The design's own voice for feedback; st.warning/st.error stay for the
    # tests' sake and are restyled by the theme.
    if kind == "warning":
        st.warning(message)
    elif kind == "error":
        st.error(message)
    else:
        C.flash(kind, message)


def mirrored() -> bool:
    return bool(github_token())


_refresh_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None


def refresh_from_github() -> None:
    """Pull the worker's latest observations without holding up the rerun.

    The read-through is rate-limited inside ``sync_from_github`` and is what
    keeps "Last checked" honest on a host whose checkout may be behind. It
    costs one to three GitHub round trips, which is exactly the delay that
    made switching pages feel slow — so after the first paint of a session
    it runs on a background thread and the *next* rerun (every click is one)
    sees the fresh files. The data on screen is still only ever the worker's
    own; it is at most one interaction staler than before.
    """
    global _refresh_thread
    if state_store.backend_name() == "firestore":
        return   # the store is read directly; there is nothing to pull from the repo
    if not st.session_state.get("_synced_once"):
        # The first paint waits, so a fresh tab never shows a stale monitor.
        sync_from_github()
        st.session_state["_synced_once"] = True
        return
    with _refresh_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_thread = threading.Thread(target=sync_from_github, name="tr-read-through",
                                           daemon=True)
        _refresh_thread.start()


def load_view():
    """Persisted config + state, expiring anything past its end time.

    Expiry runs here as well as in the worker, so a monitor can never look
    alive in the UI merely because no run has happened yet.
    """
    refresh_from_github()
    monitors, _ = expire_due_monitors(mirror=mirrored())
    return monitors, load_state(), load_history()


@st.cache_data(ttl=300, show_spinner=False)
def cached_github_status() -> tuple[bool, str]:
    """Settings-page probe. One GitHub round trip per five minutes, not per click."""
    return github_status()


# ──────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────
def sidebar(active_count: int) -> str:
    with st.sidebar:
        C.logo()
        pages = ["Home", "My Monitors", "History", "Settings"]
        labels = {"My Monitors": f"My Monitors `{active_count}`"} if active_count else {}
        choice = st.radio("Navigation", pages, format_func=lambda p: labels.get(p, p),
                          key="page", label_visibility="collapsed")
        C.html(
            '<div class="tr-side-foot">'
            '<div class="tr-quote">“Good movies find their audience. '
            "You just have to be faster.”</div>"
            f'<div class="tr-version"><span>V{APP_VERSION}</span>'
            f"<span>{C.e(get_location(st.session_state.get('location') or 'hyderabad').name.upper())}</span></div>"
            "</div>"
        )
        return choice


def open_account_settings() -> None:
    """The account menu's "Account settings": the existing Settings page,
    until a dedicated account page exists. Runs as an ``on_click``, before
    the nav radio is drawn, so the radio can be moved."""
    st.session_state["page"] = "Settings"


def account_bar() -> None:
    """The account control, top-right of the main content: who Firebase
    says you are — never a name the browser supplied — as a compact chip
    that opens a small menu with Account settings and Sign out. It is part
    of the TicketRadar page, not the sidebar and not Streamlit's toolbar."""
    user = auth_session.current_user()
    if user is None:
        return
    with st.container(key="tracct_top"):
        C.html(
            '<div class="tr-acct-chip">'
            f'<div class="av">{C.e(user.first_name[:1].upper() or "·")}</div>'
            f'<div class="n">{C.e(user.first_name)}</div>'
            f'<div class="chev">{C.icon("chevron", 14, "currentColor", "2")}</div></div>'
        )
        with st.popover("Account", key="acct_menu"):
            C.html(
                '<div class="tr-acct-menu">'
                f'<div class="n">{C.e(user.label)}</div>'
                f'<div class="m">{C.e(user.email)}</div>'
                '<div class="k">SIGNED IN WITH FIREBASE</div></div>'
            )
            st.button("Account settings", key="acct_settings", use_container_width=True,
                      icon=":material/manage_accounts:", on_click=open_account_settings)
            if st.button("Sign out", key="auth_signout", use_container_width=True, icon=":material/logout:"):
                auth_session.sign_out()
                st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# Starting a monitor
# ──────────────────────────────────────────────────────────────────────────
def start_monitor(interval: int, until, email: str, start_now: bool,
                  date_codes: list[str] | None = None) -> None:
    slug = st.session_state.get("location", "")
    movie_id = st.session_state.get("movie_id", "")
    entry = cv.entry(movie_id, slug)
    # Listed theatres and theatres being watched for release alike: the
    # worker matches on the venue code either way, and a theatre that isn't
    # listed yet simply reads THEATRE_NOT_AVAILABLE until the day it is.
    venues = {v.code: v for v in cv.selected_venues(movie_id, slug, st.session_state.get("theatres", []))}
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
        start_immediately=start_now,
        date_codes=list(date_codes or []),
        # Ownership: the verified Firebase UID, nothing typed on this page.
        owner_uid=auth_session.current_uid(),
    )
    # 1. Persist. In Firestore the monitor is the signed-in person's and the
    #    worker reads it from there; with the JSON store it is mirrored to
    #    the repo, and that commit is itself what starts the worker (the
    #    workflow listens for pushes to data/monitors.json).
    if start_now:
        monitor.first_check_requested_at = now_ist()
    upsert_monitor(monitor, mirror=mirrored())
    firestore = state_store.backend_name() == "firestore"
    mirror = {"committed": False, "error": ""} if firestore else last_mirror()
    record_history(monitor, "CREATED", "Monitor created.", mirror=mirrored())

    settings = load_settings()
    if settings.get("notify_email") != email:
        save_settings({**settings, "notify_email": email}, mirror=mirrored())

    # 2. Make sure a check is actually on its way. The commit above triggers
    #    the workflow by itself; a workflow_dispatch is tried as well when the
    #    commit didn't happen (or as belt-and-braces when the token allows).
    #    If *neither* worked, that is a problem and is shown as one — never as
    #    "waiting for first check".
    started_by = ""
    reasons: list[str] = []
    if start_now:
        if mirror.get("committed"):
            started_by = "push"
        elif mirror.get("error"):
            reasons.append(f"Saving the monitor to GitHub failed — {mirror['error']}")
        ok, why = request_check_now(monitor.id)
        if ok:
            started_by = started_by or "dispatch"
        elif not started_by:
            reasons.append(f"Starting the worker on demand failed — {why}")
        if not started_by:
            monitor.first_check_requested_at = None
            monitor.set_problem("FIRST_CHECK_NOT_STARTED", " ".join(reasons) or "Unknown reason.")
            upsert_monitor(monitor, mirror=mirrored())

    where = (f"Watching **{monitor.movie.title}** across {len(targets)} theatre/format "
             f"combination(s), every {interval} minutes, until {fmt_datetime(until)}"
             + (f", shows on {monitor.date_range_label}" if monitor.date_range_label else "") + ".")
    if started_by:
        flash("success", where + " First check is starting now.")
    elif start_now:
        flash("error", where + " PROBLEM: the first check could not be started — see the "
              "monitor card for the reason.")
    else:
        flash("success", where + " The scheduled worker will pick it up.")

    st.session_state["step"] = 1
    st.session_state["furthest"] = 1
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# Problems
# ──────────────────────────────────────────────────────────────────────────
def retry_check(monitor: Monitor) -> None:
    """Ask for a check again and clear a stale problem if that worked."""
    ok, msg = request_check_now(monitor.id)
    if ok:
        monitor.first_check_requested_at = now_ist()
        monitor.clear_problem()
        upsert_monitor(monitor, mirror=mirrored())
        flash("success", "Retry requested — the worker is checking again.")
    else:
        monitor.set_problem("RETRY_FAILED", f"Starting the worker on demand failed — {msg}")
        upsert_monitor(monitor, mirror=mirrored())
        flash("error", f"PROBLEM: {msg}")
    st.rerun()


def problem_panel(monitor: Monitor, state: MonitorState) -> None:
    """A red PROBLEM OCCURRED button; pressing it opens the actual causes."""
    problems = C.problems_for(monitor, state)
    if not problems:
        return
    key = f"show_problem_{monitor.id}"
    label = f"!  PROBLEM OCCURRED ({len(problems)})" if len(problems) > 1 else "!  PROBLEM OCCURRED"
    if st.button(label, key=f"prob_{monitor.id}", use_container_width=True):
        st.session_state[key] = not st.session_state.get(key, False)
    if st.session_state.get(key):
        C.problem_card(problems)
        if st.button("Retry now", key=f"retry_{monitor.id}", use_container_width=True, icon=":material/refresh:"):
            retry_check(monitor)


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
    if state_store.backend_name() != "firestore":
        sync_from_github()
    monitor = next((m for m in load_monitors() if m.id == monitor_id), None)
    if monitor is None:
        return
    C.active_monitor_card(monitor, load_state().get(monitor_id, MonitorState()))


def languages_of(monitors: list[Monitor]) -> dict[str, str]:
    """monitor id -> the language of the row it watches, for history lines
    written before history recorded it. Real stored data, never a guess."""
    return {m.id: m.movie.language for m in monitors if m.movie.language}


def rail(monitors: list[Monitor], states: dict[str, MonitorState], history: list[dict]) -> None:
    active = [m for m in monitors if m.is_running()]
    if not active:
        C.html('<div class="tr-rail-title"><span class="tr-dot grey"></span><span>Active monitor</span></div>')
        C.empty_card()
        if history:
            C.html('<div class="tr-rail-head" style="margin-top:18px;"><span class="tr-eyebrow">Recent history</span></div>')
            C.history_rows(history, languages=languages_of(monitors))
        return

    monitor = active[0]
    state = states.get(monitor.id, MonitorState())
    C.html(
        '<div class="tr-rail-title">'
        '<span class="tr-dot ok live big"></span>'
        '<span>Active monitor</span>'
        f'<span class="tr-count">{len(active)}</span></div>'
    )
    status_card(monitor.id)
    problem_panel(monitor, state)

    if st.button("Stop monitoring", key=f"stop_{monitor.id}", use_container_width=True, icon=":material/stop:"):
        stop_monitor(monitor.id, mirror=mirrored())
        flash("success", "Monitoring stopped. The background worker will skip it from now on.")
        st.rerun()

    C.target_rows(monitor, state)

    if len(active) > 1:
        st.caption(f"+{len(active) - 1} more active — see **My Monitors**.")
    if history:
        C.html('<div class="tr-rail-head" style="margin-top:14px;"><span class="tr-eyebrow">Recent history</span></div>')
        C.history_rows(history, languages=languages_of(monitors))


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
        city = get_location(st.session_state.get("location") or "hyderabad")
        C.platform_selector(PLATFORMS, "bookmyshow", cv.view(city.slug).theatre_count, city.name)

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

        step = st.session_state.get("step", 1)
        flow.step_rail(step, st.session_state.get("furthest", 1))
        flow.summary(step)

        with st.container(border=True, key="trcard_step"):
            flow.back_button(step)
            if step == 1:
                flow.step_location()
            elif step == 2:
                flow.step_movie()
            elif step == 3:
                flow.step_theatres()
            elif step == 4:
                slug = st.session_state.get("location", "")
                movie_id = st.session_state.get("movie_id", "")
                codes = st.session_state.get("theatres", [])
                flow.step_formats(cv.selected_venues(movie_id, slug, codes),
                                  coming=cv.coming_soon_codes(movie_id, slug, codes),
                                  listed=cv.listed_formats(movie_id, slug, codes))
            else:
                interval, until, email, start_now, dates = flow.step_monitoring(settings.get("notify_email", ""))
                cta, helper = st.columns([2.2, 1], gap="medium")
                with cta:
                    if st.button("Start monitoring", type="primary",
                                 use_container_width=True, key="start", icon=":material/play_arrow:"):
                        start_monitor(interval, until, email, start_now, dates)
                with helper:
                    C.html(
                        f'<div class="tr-cta-help">{C.icon("bolt", 15, "#E8B25C")}<span>You\'ll get an email the second '
                        "tickets appear — and the monitor keeps running for the other theatres.</span></div>"
                    )

    with side:
        rail(monitors, states, history)


def monitor_actions(monitor: Monitor, state: MonitorState) -> None:
    """Stop / Extend / Delete for one card. Every action rewrites the file the
    worker reads, then reruns — so the card is gone (or changed) on the very
    next paint, never left on screen as a stale copy."""
    with st.container(key=f"tractions_{monitor.id}"):
        if monitor.is_running():
            problem_panel(monitor, state)
            a, b, _ = st.columns([1, 1, 2.2], gap="small")
            if a.button("Stop", key=f"m_stop_{monitor.id}", use_container_width=True, icon=":material/stop:"):
                stop_monitor(monitor.id, mirror=mirrored())
                flash("success", "Monitoring stopped.")
                st.rerun()
            if b.button("Delete", key=f"m_del_{monitor.id}", use_container_width=True, icon=":material/delete:"):
                delete_monitor(monitor.id, mirror=mirrored())
                flash("success", "Monitor deleted.")
                st.rerun()
        else:
            a, b, _ = st.columns([1.3, 1, 1.9], gap="small")
            if a.button("Extend by 24 hours", key=f"m_ext_{monitor.id}", use_container_width=True, icon=":material/more_time:"):
                extend_monitor(monitor.id, 24, mirror=mirrored())
                ok, _ = request_check_now(monitor.id)
                flash("success", "Extended by 24 hours — monitoring is active again"
                      + (" and a check is running now." if ok else "."))
                st.rerun()
            if b.button("Delete", key=f"m_del_{monitor.id}", use_container_width=True, icon=":material/delete:"):
                delete_monitor(monitor.id, mirror=mirrored())
                flash("success", "Monitor deleted.")
                st.rerun()


def page_monitors(monitors, states) -> None:
    C.hero("My Monitors", "Everything you've asked us to watch.",
           "Active alerts first, then the ones that finished.")
    drain_flash()
    if not monitors:
        C.empty_card()
        return

    active = sorted((m for m in monitors if m.is_running()), key=lambda m: m.created_at, reverse=True)
    finished = sorted((m for m in monitors if not m.is_running()),
                      key=lambda m: (m.stopped_at or m.created_at), reverse=True)

    C.rule(f"Active · {len(active)}")
    if not active:
        C.empty_card("Nothing is being watched right now. Set one up from Home.")
    for monitor in active:
        state = states.get(monitor.id, MonitorState())
        with st.container(key=f"trcard_mon_{monitor.id}"):
            C.monitor_card(monitor, state)
            monitor_actions(monitor, state)

    if finished:
        head, clear = st.columns([3, 1], gap="small", vertical_alignment="center")
        with head:
            C.rule(f"Finished · {len(finished)}")
        with clear:
            if st.button(f"Delete all {len(finished)} finished", key="m_clear_finished",
                         use_container_width=True, icon=":material/delete_sweep:"):
                for monitor in finished:
                    delete_monitor(monitor.id, mirror=mirrored())
                flash("success", f"Deleted {len(finished)} finished monitor(s). History keeps their record.")
                st.rerun()
        for monitor in finished:
            state = states.get(monitor.id, MonitorState())
            with st.container(key=f"trcard_mon_{monitor.id}"):
                C.monitor_card(monitor, state)
                monitor_actions(monitor, state)


def page_history(history, monitors) -> None:
    C.hero("History", "What the radar has picked up.",
           "Every release, stop and expiry, newest first.")
    drain_flash()
    if not history:
        C.empty_card("Nothing yet — history fills up once a monitor runs.")
        return
    C.history_rows(history, limit=50, languages=languages_of(monitors))


def page_settings(settings) -> None:
    C.hero("Settings", "Where alerts go, and what's wired up.",
           "Credentials live in secrets — never in this repository.")
    drain_flash()

    left, right = st.columns(2, gap="large")

    with left, st.container(border=True, key="trcard_notify"):
        C.step_header("mail", "Notifications", "The address every alert is sent to.")
        C.html('<div class="tr-field-label">Notification email</div>')
        email = st.text_input("Notification email", value=settings.get("notify_email", ""),
                              placeholder="you@gmail.com", key="settings_email",
                              label_visibility="collapsed")
        with st.container(key="trpair_settings"):
            a, b = st.columns(2, gap="small")
        if a.button("Save", use_container_width=True, key="save_settings", type="primary", icon=":material/save:"):
            save_settings({**settings, "notify_email": email.strip()}, mirror=mirrored())
            flash("success", "Settings saved.")
            st.rerun()
        if b.button("Send test email", use_container_width=True, key="test_email", icon=":material/send:"):
            try:
                send_test_email(email.strip() or settings.get("notify_email", ""))
            except NotificationError as exc:
                flash("error", str(exc))
            else:
                flash("success", f"Test email sent to {email.strip()}.")
            st.rerun()
        C.status_line("ok" if is_configured() else "wait",
                      "Gmail credentials are present here." if is_configured() else
                      "No Gmail credentials here. The UI only needs them for the test button; "
                      "the worker reads them from GitHub Actions secrets.")

    with right, st.container(border=True, key="trcard_storage"):
        C.step_header("catalogue", "Movie catalogue", "What the movie and theatre pickers read.")
        slug = st.session_state.get("location") or "hyderabad"
        view = cv.view(slug)
        state = view.sync
        C.catalogue_banner(state["status"].value, state["message"],
                           flow.ago(state["at"]), len(view.movies))
        st.caption(
            "The movie list, the theatres each movie plays at and the formats they run "
            "are synced by a scheduled background job and stored in the repository. "
            "The app reads that file, so it works even when BookMyShow won't answer it "
            "directly."
        )
        if not HAS_CURL_CFFI:
            C.status_line("warn", "curl_cffi isn't installed here, so this app can't refresh the "
                          "catalogue itself — BookMyShow bot-checks plain requests. The "
                          "background job still can.")
        a, b = st.columns(2, gap="small")
        if a.button("Refresh catalogue now", use_container_width=True, key="sync_now", icon=":material/sync:"):
            ok, msg = dispatch_workflow("catalogue-sync.yml", {"city": slug})
            flash("success" if ok else "error", msg)
            st.rerun()
        if b.button("Run a ticket check now", use_container_width=True, key="run_now", icon=":material/bolt:"):
            ok, msg = request_check_now()
            flash("success" if ok else "error",
                  "Check started — results land in the rail within a minute or two." if ok else msg)
            st.rerun()
        connected, message = cached_github_status()
        C.status_line("ok" if connected else "bad", message)
        if connected:
            C.status_line("info", "Starting a monitor triggers the worker through the commit it "
                          "makes. Retry now and Run a ticket check now additionally need the "
                          "token to have the Actions: write scope.")


# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    # Nobody signed in → the login page, and nothing below this line runs.
    # Firebase is the source of truth: ``user.uid`` is the account's UID, and
    # ``auth.session.id_token()`` is a live token for the next step's Firestore.
    user = require_user()  # noqa: F841 - kept here so the boundary is visible
    login.entrance()
    auth_session.flush_cookie()
    flow.boot()
    st.session_state.setdefault("page", "Home")
    st.session_state.setdefault("flash", None)

    monitors, states, history = load_view()
    settings = load_settings()
    page = sidebar(sum(1 for m in monitors if m.is_running()))
    account_bar()

    # Navigation lands at the top of the new page — the hero, on Home.
    if st.session_state.get("_page_seen") != page:
        st.session_state["_page_seen"] = page
        st.session_state["_nav_count"] = st.session_state.get("_nav_count", 0) + 1
    C.scroll_to_top(f"{page}#{st.session_state.get('_nav_count', 0)}")

    if page == "Home":
        page_home(monitors, states, history, settings)
    elif page == "My Monitors":
        page_monitors(monitors, states)
    elif page == "History":
        page_history(history, monitors)
    else:
        page_settings(settings)

    C.html(
        '<div class="tr-version tr-page-foot">'
        f"<span>MOVIE TICKET RADAR V{APP_VERSION}</span>"
        f"<span>{C.e(fmt_time(now_ist()))} IST</span></div>"
    )


main()
