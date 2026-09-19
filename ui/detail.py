"""Monitor details — the Home card's "View details" dialog.

The rail card says what matters at a glance: the film, the phase, when
the last and next checks are, which theatres are watched. Everything
else a monitor knows sits behind one quiet action, so the card never
grows into an information panel. This dialog is that everything else:
the film row that is watched, the whole schedule, every theatre × format
with its current answer and — for a target that is live — the booking
link the alert email carried.

It is a view, not a source. It draws the :class:`Monitor` and the
:class:`MonitorState` the page already loaded (``load_view``), so opening
it costs no Firestore read; the fragment's fresher state belongs to the
rail, and the page's copy is what this shows. The booking link is
``TargetState.booking_url`` — the value the checker produced and the
email sent (``Change.booking_url`` is copied from the same result) —
shown only when it passes the same ``is_bookmyshow_url`` test the mail
applies, and only for a target that is AVAILABLE. Nothing here builds,
guesses or scrapes a URL.

Open state is one session key holding the monitor id. The rail's button
sets it from inside a fragment and asks for an app rerun, exactly as its
Stop button does; Close and the X clear it.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from config.timezone import fmt_datetime, fmt_time, now_ist
from monitor.models import Availability, Monitor
from monitor.state import MonitorState
from platforms.bookmyshow import is_bookmyshow_url
from ui import radar
from ui import components as C
from ui import home

OPEN_KEY = "monitor_detail"


# ── state ────────────────────────────────────────────────────────────────
def open_detail(monitor_id: str) -> None:
    """An ``on_click`` from the rail's fragment: remember which monitor,
    then ask for the whole app so the dialog (drawn by the page, not the
    fragment) appears on this run."""
    st.session_state[OPEN_KEY] = monitor_id
    st.rerun(scope="app")


def close() -> None:
    st.session_state.pop(OPEN_KEY, None)


def open_id() -> str:
    return str(st.session_state.get(OPEN_KEY) or "")


# ── markup ───────────────────────────────────────────────────────────────
def _fact(label: str, value: str, cls: str = "", icon: str = "") -> str:
    ic = f'{C.icon(icon, 11, "currentColor", "1.9")}' if icon else ""
    return (f'<div class="tr-det-fact"><div class="k">{ic}{C.e(label)}</div>'
            f'<div class="v{" " + cls if cls else ""}">{C.e(value)}</div></div>')


def _eyebrow(icon: str, text: str) -> str:
    return f'<div class="tr-eyebrow">{C.icon(icon, 12, "currentColor", "1.9")}{text}</div>'


def _target_block(monitor: Monitor, state: MonitorState, target, finished: bool) -> str:
    ts = state.targets.get(target.key)
    availability = ts.availability if ts else Availability.UNKNOWN
    label, cls, glyph = C.AVAILABILITY_UI.get(availability, ("Watching", "", "◌"))
    live = availability is Availability.AVAILABLE
    url = ts.booking_url if (ts and live and is_bookmyshow_url(ts.booking_url)) else ""

    # The state reads as a pill: a dot in the semantic colour, the word in
    # mono. Green only for a live target; amber for sold out; red only for
    # a real failure; everything still being watched is neutral.
    if live:
        state_line = f'<span class="tr-det-state ok"><span class="tr-dot ok live"></span>{C.e(label)}</span>'
    elif finished:
        state_line = '<span class="tr-det-state"><span class="d"></span>Not being checked</span>'
    elif availability is Availability.UNKNOWN and state.last_check_at is None:
        state_line = '<span class="tr-det-state"><span class="tr-spin grey"></span>Waiting for first check</span>'
    elif cls:
        state_line = f'<span class="tr-det-state {cls}"><span class="d"></span>{C.e(label)}</span>'
    else:
        state_line = f'<span class="tr-det-state"><span class="tr-spin grey"></span>{C.e(label)}</span>'

    where = " · ".join(x for x in (target.area, monitor.movie.city) if x)
    # Still being checked (not live, not finished): a small sweep beside the
    # state says so without a word — the same radar, at 22px.
    watching = "" if (live or finished) else radar.svg(size=22, state="scanning", label="", cls="watch")
    body = (f'<div class="tr-det-tgt-head"><div class="who"><div class="n">{C.e(target.venue_name)}</div>'
            f'<div class="s"><b>{C.e(target.fmt)}</b>{" · " + C.e(where) if where else ""}</div></div>'
            f'<div class="st">{watching}{state_line}</div></div>')

    if live and ts:
        links = {lbl: u for lbl, u in ts.time_links if is_bookmyshow_url(u)}
        chips = "".join(C._chip(t, links.get(t, url)) for t in ts.time_labels)
        times = (f'<div class="tr-det-times"><div class="tr-eyebrow">Showtimes</div>'
                 f'<div class="tr-chips">{chips}</div></div>') if chips else ""
        found = f"Found {fmt_time(ts.since)}" if ts.since else ""
        mailed = f"✓ Email sent {fmt_time(ts.notified_at)}" if ts.notified_at else "◷ Email pending"
        book = (f'<a class="tr-book tr-det-book" href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener">BOOK ON BOOKMYSHOW ↗</a>' if url else
                '<div class="tr-det-nobook">Booking link not available yet — open BookMyShow to book.</div>')
        foot = f'<div class="tr-det-tgt-foot"><span>{C.e(found)}</span><span>{C.e(mailed)}</span></div>' if found else ""
        return f'<div class="tr-det-tgt ok">{body}{times}{book}{foot}</div>'

    if finished:
        hint = "This monitor is no longer running."
    elif availability is Availability.SOLD_OUT:
        hint = "Shows are listed but every seat is taken — you'll be emailed if seats come back."
    elif availability in (Availability.ERROR, Availability.NOT_FOUND):
        hint = "The last check didn't get an answer for this theatre — it is retried automatically."
    else:
        hint = "No booking link yet — you'll be emailed the moment tickets open here."
    return f'<div class="tr-det-tgt">{body}<div class="tr-det-nobook">{C.e(hint)}</div></div>'


def markup(monitor: Monitor, state: MonitorState, *, at=None) -> str:
    """The whole dialog body as one HTML string, so tests can read it and
    the page paints it with a single markdown call."""
    at = at or now_ist()
    phase = C.phase_for(monitor, state, at=at)
    running = monitor.is_running(at)
    next_value, next_class = C.next_check_text(monitor, state, at) if running else ("—", "soft")
    checks = (f"{state.success_count} of {state.check_count}" if state.check_count != state.success_count
              else str(state.check_count))
    dates = monitor.date_range_label or "Any date on sale"
    if not running:
        ended = "Stopped" if phase.key == "stopped" else "Expired"
        until_label, until_value = ended, fmt_datetime(monitor.stopped_at or monitor.monitor_until)
    else:
        until_label, until_value = "Monitoring until", fmt_datetime(monitor.monitor_until)

    targets = "".join(_target_block(monitor, state, t, not running) for t in monitor.targets)
    live_count = sum(1 for ts in state.targets.values() if ts.availability is Availability.AVAILABLE)
    tgt_sub = (f"{live_count} live · {len(monitor.targets)} watched" if live_count
               else f"{len(monitor.targets)} theatre{'s' if len(monitor.targets) != 1 else ''} × format")

    # The room's own marks, only from numbers already on the card: what it
    # is doing, which check it is on, when the next sweep is due.
    if running:
        doing = {"available": "TICKETS FOUND", "sold_out": "WATCHING FOR A REOPEN", "waiting": "WAITING FOR FIRST CHECK",
                 "problem": "NEEDS ATTENTION", "blocked": "RETRYING", "error": "RETRYING"}.get(phase.key, "SCANNING")
        marks = [f"LIVE MONITOR · {doing}"]
        if state.success_count:
            marks.append(f"CHECK #{state.success_count}")
        if next_class == "mono":
            marks.append(f"NEXT SCAN {next_value}")
    else:
        marks = [phase.label.upper()]
    tech = "".join(f"<span>{C.e(m)}</span>" for m in marks)

    return f"""{CSS}<div class="tr-det{' live' if phase.key == 'available' else ''}" data-phase="{C.e(phase.key)}" data-running="{'1' if running else '0'}">
      <div class="tr-det-bg" aria-hidden="true"><div class="sweep"></div><div class="line"></div>{home.reel_svg("det")}</div>
      <div class="tr-det-eyebrow"><span>Monitor control room</span><em>Created {C.e(fmt_datetime(monitor.created_at))}</em></div>
      <div class="tr-det-tech">{tech}</div>
      <div class="tr-det-head">
        {C.thumb(monitor.movie.poster_url)}
        <div class="who">
          <div class="title">{C.e(monitor.movie.title)}</div>
          {C.language_line(monitor.movie.language)}
          <div class="where">BookMyShow · {C.e(monitor.movie.city)}</div>
        </div>
        <span class="tr-pill {phase.css} lg">{'<span class="tr-dot ok live"></span>' if phase.key == 'available' else ''}{C.e(phase.label)}</span>
      </div>
      <div class="tr-det-grid">
        <section class="tr-det-sec">
          {_eyebrow("movie", "Movie")}
          {_fact("Title", monitor.movie.title, icon="movie")}
          {_fact("Language", monitor.movie.language or "—", icon="ticket")}
          {_fact("Show dates", dates, icon="calendar")}
        </section>
        <section class="tr-det-sec">
          {_eyebrow("radar", "Monitoring")}
          <div class="tr-det-facts">
            {_fact("Status", phase.label.capitalize(), icon="monitor")}
            {_fact("Check interval", f"Every {monitor.interval_minutes} min", icon="clock")}
            {_fact("Checks completed", checks, icon="check")}
            {_fact("Last checked", fmt_time(state.last_check_at), icon="clock")}
            {_fact("Next check", next_value, next_class, icon="radar")}
            {_fact(until_label, until_value, icon="calendar")}
          </div>
        </section>
      </div>
      <section class="tr-det-sec wide">
        <div class="tr-det-sec-head">{_eyebrow("theatre", "Theatres &amp; formats")}<em>{C.e(tgt_sub)}</em></div>
        {targets}
      </section>
    </div>"""


# ── the dialog ───────────────────────────────────────────────────────────
def dialog(monitors: list[Monitor], states: dict[str, MonitorState]) -> None:
    """Draw the dialog if one is open. Nothing at all otherwise. A monitor
    that is gone (deleted from another tab) simply closes it."""
    wanted = open_id()
    if not wanted:
        return
    monitor = next((m for m in monitors if m.id == wanted), None)
    if monitor is None:
        close()
        return
    state = states.get(monitor.id, MonitorState())

    @st.dialog("Monitor details", width="large", on_dismiss=close)
    def _dialog() -> None:
        C.html(markup(monitor, state))
        if st.button("Close", key="close_detail", use_container_width=True):
            close()
            st.rerun()

    _dialog()


#: The dialog's own stylesheet — inside the dialog, so the page never
#: carries it. Facts, targets and the booking action reuse the theme's
#: pill, eyebrow, chip and ``tr-book`` mechanics.
CSS = "<style>" + radar.CSS + """
/* the box: the Login panel's charcoal, a hairline, a top light line, one pink corner light */
[data-testid="stDialog"] > div { max-width:920px !important; border-radius:24px !important; border:1px solid rgba(255,255,255,.10) !important;
  background: radial-gradient(640px 260px at 92% -6%, rgba(255,51,85,.14), transparent 62%), radial-gradient(420px 220px at -4% 104%, rgba(255,51,85,.06), transparent 60%),
    linear-gradient(168deg,#17131A 0%,#0F0D12 52%,#120E14 100%) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.09), 0 50px 100px -40px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.05), 0 30px 90px -50px rgba(255,51,85,.45) !important; }
[data-testid="stDialog"] > div::after { content:""; position:absolute; left:16%; right:16%; top:-1px; height:1px; pointer-events:none;
  background: linear-gradient(90deg, transparent, rgba(255,140,160,.7), transparent); }
.tr-det { min-width:0; position:relative; isolation:isolate; }
.tr-det > * { position:relative; z-index:1; }
/* the backdrop: a faint sweep, a scan line every eight seconds, the reel in the dark — all pointer-transparent */
.tr-det-bg { position:absolute; inset:0; z-index:0 !important; pointer-events:none; overflow:hidden; border-radius:18px; }
.tr-det-bg .sweep { position:absolute; right:-260px; top:-260px; width:900px; height:900px; border-radius:50%; opacity:.55;
  background: conic-gradient(from 0deg, transparent 0deg 300deg, rgba(255,51,85,.02) 340deg, rgba(255,51,85,.06) 359deg, transparent 360deg); animation: tr-radar-sweep 9s linear infinite; }
.tr-det-bg .line { position:absolute; left:0; right:0; top:0; height:2px; opacity:.75;
  background: linear-gradient(90deg, transparent 8%, rgba(255,140,160,.35) 50%, transparent 92%); box-shadow: 0 0 18px 4px rgba(255,51,85,.10); animation: tr-det-scan 8s ease-in-out infinite; }
.tr-det-bg .tr-reel { position:absolute; right:-140px; bottom:-160px; width:520px; color:#FF8CA0; transform-origin:50% 50%; animation: tr-det-reel 110s linear infinite; }
.tr-det-bg .tr-reel .rim { opacity:.07; } .tr-det-bg .tr-reel .sheen { stroke:#FFC9D4; opacity:.14; } .tr-det-bg .tr-reel .ring { opacity:.06; }
.tr-det-bg .tr-reel .holes { opacity:.07; } .tr-det-bg .tr-reel .hub { opacity:.08; } .tr-det-bg .tr-reel .pin { opacity:.2; }
@keyframes tr-det-scan { 0% { transform:translateY(-10px); opacity:0; } 6% { opacity:.75; } 60% { opacity:.75; } 70% { transform:translateY(760px); opacity:0; } 100% { transform:translateY(760px); opacity:0; } }
@keyframes tr-det-reel { to { transform:rotate(360deg); } }
@keyframes tr-det-breathe { 0%,100% { box-shadow: 0 0 0 0 rgba(62,213,152,.0), 0 0 0 1px rgba(62,213,152,.25); } 50% { box-shadow: 0 0 0 5px rgba(62,213,152,.10), 0 0 22px 2px rgba(62,213,152,.28); } }
@keyframes tr-det-wave { from { transform:translateY(-140%); } to { transform:translateY(1400%); } }
/* the living states: green only while the monitor is running and not in trouble; amber for a sold-out watch; success as it was */
.tr-det[data-running="1"][data-phase="not_released"] .tr-det-head .tr-pill,
.tr-det[data-running="1"][data-phase="available"] .tr-det-head .tr-pill { animation: tr-det-breathe 3.2s ease-in-out infinite; }
.tr-det[data-running="1"][data-phase="not_released"] .tr-det-bg .line,
.tr-det[data-running="1"][data-phase="available"] .tr-det-bg .line { background: linear-gradient(90deg, transparent 8%, rgba(62,213,152,.45) 50%, transparent 92%); box-shadow: 0 0 18px 4px rgba(62,213,152,.12); }
.tr-det[data-running="1"][data-phase="available"] .tr-det-bg .sweep { background: conic-gradient(from 0deg, transparent 0deg 300deg, rgba(62,213,152,.02) 340deg, rgba(62,213,152,.06) 359deg, transparent 360deg); }
.tr-det[data-phase="sold_out"] .tr-det-bg .line { background: linear-gradient(90deg, transparent 8%, rgba(232,178,92,.4) 50%, transparent 92%); box-shadow: 0 0 18px 4px rgba(232,178,92,.10); }
.tr-det[data-running="0"] .tr-det-bg .line, .tr-det[data-running="0"] .tr-det-bg .sweep { display:none; }
/* the room's marks */
.tr-det-tech { display:flex; flex-wrap:wrap; gap:6px 10px; margin:-4px 0 12px; }
.tr-det-tech span { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.2em; text-transform:uppercase; color:var(--tr-text-3); padding:4px 9px; border-radius:6px; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); }
.tr-det[data-running="1"][data-phase="not_released"] .tr-det-tech span:first-child,
.tr-det[data-running="1"][data-phase="available"] .tr-det-tech span:first-child { color:#9FEBC9; border-color:rgba(62,213,152,.3); background:rgba(62,213,152,.07); }
.tr-det[data-phase="sold_out"] .tr-det-tech span:first-child { color:var(--tr-warning); border-color:rgba(232,178,92,.3); }
.tr-det[data-phase="problem"] .tr-det-tech span:first-child, .tr-det[data-phase="blocked"] .tr-det-tech span:first-child, .tr-det[data-phase="error"] .tr-det-tech span:first-child { color:var(--tr-danger); border-color:rgba(255,92,92,.3); }
/* icons that reinforce the words */
.tr-det-sec .tr-eyebrow .tr-ic, .tr-det-sec-head .tr-eyebrow .tr-ic { color:#FF8CA0; margin-right:-2px; }
.tr-det-fact .k { display:flex; align-items:center; gap:6px; }
.tr-det-fact .k .tr-ic { color:var(--tr-text-3); flex:none; }
/* a target still being watched: the small sweep beside its state */
.tr-det-tgt-head .st { display:inline-flex; align-items:center; gap:9px; flex:none; }
.tr-det-tgt-head .tr-radar.watch { width:22px; flex:none; opacity:.85; filter: drop-shadow(0 0 6px rgba(255,51,85,.35)); }
.tr-det-tgt-head .tr-radar.watch .ticks, .tr-det-tgt-head .tr-radar.watch .axis, .tr-det-tgt-head .tr-radar.watch .orbit, .tr-det-tgt-head .tr-radar.watch .blips, .tr-det-tgt-head .tr-radar.watch .dish { display:none; }
.tr-det-tgt-head .tr-radar.watch .ring { stroke-width:8; } .tr-det-tgt-head .tr-radar.watch .r1 { display:none; }
.tr-det-tgt-head .tr-radar.watch .edge { stroke-width:12; } .tr-det-tgt-head .tr-radar.watch .edge-glow { stroke-width:28; }
.tr-det-tgt-head .tr-radar.watch .core { transform:scale(2.2); transform-box:fill-box; transform-origin:center; } .tr-det-tgt-head .tr-radar.watch .core .pulse { display:none; }
.tr-det[data-running="1"] .tr-det-tgt.ok { position:relative; overflow:hidden; }
.tr-det[data-running="1"] .tr-det-tgt.ok::before { content:""; position:absolute; left:0; right:0; top:0; height:10%; pointer-events:none; z-index:0;
  background: linear-gradient(180deg, transparent, rgba(62,213,152,.10) 55%, rgba(62,213,152,.22) 80%, transparent); animation: tr-det-wave 7s linear infinite; }
.tr-det-tgt.ok > * { position:relative; z-index:1; }
.tr-det-eyebrow { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin:-2px 0 12px;
  font-family:var(--tr-mono); font-size:10px; letter-spacing:.3em; text-transform:uppercase; color:#FF8CA0; }
.tr-det-eyebrow em { font-style:normal; letter-spacing:.1em; text-transform:none; color:var(--tr-text-4); }
/* header: poster as a small framed still, the film, the phase */
.tr-det-head { display:flex; align-items:center; gap:18px; padding:0 0 18px; border-bottom:1px solid rgba(255,255,255,.08); }
.tr-det-head .tr-thumb { width:66px; height:94px; border-radius:10px; border:1px solid rgba(255,255,255,.14);
  box-shadow: 0 0 0 1px rgba(255,51,85,.10), 0 18px 40px -18px rgba(255,51,85,.5), 0 14px 30px -16px rgba(0,0,0,1); }
.tr-det.live .tr-thumb { border-color:rgba(62,213,152,.35); box-shadow: 0 0 0 1px rgba(62,213,152,.10), 0 18px 40px -18px rgba(62,213,152,.45), 0 14px 30px -16px rgba(0,0,0,1); }
.tr-det-head .who { min-width:0; flex:1; }
.tr-det-head .title { font-size:22px; font-weight:800; letter-spacing:-.025em; line-height:1.15; color:#fff; overflow-wrap:anywhere; }
.tr-det-head .where { font-size:12.5px; color:var(--tr-text-3); margin-top:6px; }
.tr-det-head .lang { display:flex; align-items:center; gap:6px; font-size:12.5px; color:var(--tr-text-2); margin-top:6px; }
.tr-det-head .tr-pill { flex:none; align-self:flex-start; font-family:var(--tr-mono); letter-spacing:.18em; padding:7px 13px; }
/* panels: the rail card's surface, one restrained accent bar on each eyebrow */
.tr-det-grid { display:grid; grid-template-columns:1fr 1.35fr; gap:16px; margin-top:16px; }
.tr-det-sec { min-width:0; padding:16px 18px 18px; border-radius:18px; border:1px solid var(--tr-border);
  background: linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,0) 42%), rgba(20,20,27,.72);
  box-shadow: var(--tr-hi), 0 18px 40px -30px rgba(0,0,0,1); }
.tr-det-sec.wide { margin-top:16px; }
.tr-det-sec > .tr-eyebrow, .tr-det-sec-head .tr-eyebrow { display:flex; align-items:center; gap:9px; font-size:10px; letter-spacing:.26em; color:var(--tr-text-2); }
.tr-det-sec > .tr-eyebrow::before, .tr-det-sec-head .tr-eyebrow::before { content:""; width:3px; height:11px; border-radius:2px; background:linear-gradient(180deg,#FF6B85,#D4123F); box-shadow:0 0 10px rgba(255,51,85,.6); }
.tr-det-sec > .tr-eyebrow { margin-bottom:12px; }
.tr-det-sec-head { display:flex; align-items:baseline; justify-content:space-between; gap:6px 12px; margin-bottom:12px; flex-wrap:wrap; }
.tr-det-sec-head .tr-eyebrow { margin:0; white-space:nowrap; }
.tr-det-sec-head em { font-style:normal; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.08em; color:var(--tr-text-4); white-space:nowrap; }
/* facts: a mono label, the value in white */
.tr-det-facts { display:grid; grid-template-columns:1fr 1fr; gap:14px 18px; }
.tr-det-fact { min-width:0; padding:9px 0; border-bottom:1px solid rgba(255,255,255,.06); }
.tr-det-fact:last-child { border-bottom:0; padding-bottom:0; }
.tr-det-facts .tr-det-fact { border-bottom:0; padding:0; }
.tr-det-fact .k { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--tr-text-4); }
.tr-det-fact .v { font-size:14.5px; font-weight:700; margin-top:5px; line-height:1.3; color:var(--tr-text); overflow-wrap:anywhere; }
.tr-det-fact .v.mono { font-family:var(--tr-mono); font-weight:500; color:var(--tr-success); }
.tr-det-fact .v.amber { color:var(--tr-warning); }
.tr-det-fact .v.bad { color:var(--tr-danger); }
.tr-det-fact .v.soft { color:var(--tr-text-2); }
/* targets: who is watched, then its answer, then what you can do about it */
.tr-det-tgt { padding:14px 16px 15px; border-radius:15px; border:1px solid var(--tr-border); margin-top:10px; min-width:0;
  background: linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0) 50%), var(--tr-sunken); box-shadow: var(--tr-hi); }
.tr-det-tgt.ok { border-color:rgba(62,213,152,.36);
  background: linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,0) 40%), radial-gradient(520px 200px at 8% 0%, rgba(62,213,152,.14), transparent 70%), #0C1210;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 26px 60px -34px rgba(62,213,152,.55); }
.tr-det-tgt-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.tr-det-tgt-head .who { min-width:0; }
.tr-det-tgt-head .n { font-size:15px; font-weight:800; letter-spacing:-.01em; line-height:1.3; color:#fff; overflow-wrap:anywhere; }
.tr-det-tgt-head .s { font-size:12px; color:var(--tr-text-3); margin-top:3px; overflow-wrap:anywhere; }
.tr-det-tgt-head .s b { font-weight:700; color:var(--tr-text-2); }
.tr-det-state { display:inline-flex; align-items:center; gap:8px; flex:none; padding:6px 11px; border-radius:999px;
  font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em; text-transform:uppercase; white-space:nowrap;
  color:var(--tr-text-2); background:rgba(255,255,255,.05); border:1px solid var(--tr-border-strong); }
.tr-det-state .d { width:6px; height:6px; border-radius:50%; background:var(--tr-text-4); flex:none; }
.tr-det-state.ok { color:var(--tr-success); background:rgba(62,213,152,.12); border-color:rgba(62,213,152,.34); }
.tr-det-state.warn { color:var(--tr-warning); background:rgba(232,178,92,.10); border-color:rgba(232,178,92,.32); }
.tr-det-state.warn .d { background:var(--tr-warning); box-shadow:0 0 8px rgba(232,178,92,.7); }
.tr-det-state.bad { color:var(--tr-danger); background:rgba(255,92,92,.10); border-color:rgba(255,92,92,.36); }
.tr-det-state.bad .d { background:var(--tr-danger); }
.tr-det-times { margin-top:14px; padding-top:12px; border-top:1px solid rgba(255,255,255,.07); }
.tr-det-times .tr-eyebrow { margin-bottom:0; font-size:10px; letter-spacing:.22em; }
.tr-det .tr-chips { margin-top:9px; }
.tr-det .tr-chips .tr-chip { font-size:13px; padding:8px 13px; border-radius:10px; }
.tr-det-book { margin-top:14px; padding:16px; font-size:15px; letter-spacing:.06em; border-radius:14px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.35), inset 0 -1px 0 rgba(0,0,0,.2), 0 22px 50px -22px rgba(62,213,152,.75); }
.tr-det-nobook { margin-top:11px; font-size:12.5px; color:var(--tr-text-3); line-height:1.5; }
.tr-det-tgt-foot { display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; font-family:var(--tr-mono); font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:var(--tr-text-4); }
/* the way out: a finished secondary, quiet next to a green BOOK */
[class*="st-key-close_detail"] { margin-top:16px; }
[class*="st-key-close_detail"] .stButton button { min-height:46px; font-family:var(--tr-mono); font-size:11px; letter-spacing:.2em; text-transform:uppercase; font-weight:500; }
[class*="st-key-close_detail"] .stButton button p { font-weight:500; }
@media (max-width: 768px) {
  /* the pill drops under the title instead of squeezing it into a column */
  .tr-det-head { flex-wrap:wrap; gap:14px; }
  .tr-det-head .who { flex:1 1 180px; }
  .tr-det-head .tr-pill { order:3; }
  .tr-det-head .title { font-size:19px; }
  .tr-det-head .tr-thumb { width:58px; height:82px; }
  .tr-det-bg .tr-reel { width:320px; right:-150px; bottom:-120px; }
  .tr-det-bg .sweep { display:none; }
  .tr-det-grid { grid-template-columns:1fr; gap:12px; }
  .tr-det-facts { grid-template-columns:1fr 1fr; }
  .tr-det-sec { padding:14px; }
  .tr-det-tgt { padding:13px; }
  .tr-det-tgt-head { flex-wrap:wrap; }
  /* one line, thumb-sized: the booking action is the reason to open this on a phone */
  .tr-det-book { padding:17px 12px; font-size:14.5px; letter-spacing:.02em; white-space:nowrap; }
}
@media (prefers-reduced-motion: reduce) {
  .tr-det-bg .sweep, .tr-det-bg .line, .tr-det-bg .tr-reel, .tr-det-head .tr-pill, .tr-det-tgt.ok::before { animation:none !important; }
  .tr-det-bg .line { display:none; }
}
@media (max-width: 480px) {
  .tr-det-facts { grid-template-columns:1fr; gap:10px; }
  .tr-det-sec-head em { white-space:normal; }
}
</style>"""

__all__ = ["OPEN_KEY", "close", "dialog", "markup", "open_detail", "open_id"]
