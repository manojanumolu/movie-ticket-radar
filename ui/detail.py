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
from ui import components as C

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
def _fact(label: str, value: str, cls: str = "") -> str:
    return (f'<div class="tr-det-fact"><div class="k">{C.e(label)}</div>'
            f'<div class="v{" " + cls if cls else ""}">{C.e(value)}</div></div>')


def _target_block(monitor: Monitor, state: MonitorState, target, finished: bool) -> str:
    ts = state.targets.get(target.key)
    availability = ts.availability if ts else Availability.UNKNOWN
    label, cls, glyph = C.AVAILABILITY_UI.get(availability, ("Watching", "", "◌"))
    live = availability is Availability.AVAILABLE
    url = ts.booking_url if (ts and live and is_bookmyshow_url(ts.booking_url)) else ""

    if live:
        state_line = f'<span class="tr-det-state ok">✓ {C.e(label)}</span>'
    elif finished:
        state_line = f'<span class="tr-det-state">■ Not being checked</span>'
    elif availability is Availability.UNKNOWN and state.last_check_at is None:
        state_line = '<span class="tr-det-state"><span class="tr-spin grey"></span>Waiting for first check</span>'
    elif cls:
        state_line = f'<span class="tr-det-state {cls}">{C.e(glyph)} {C.e(label)}</span>'
    else:
        state_line = f'<span class="tr-det-state"><span class="tr-spin grey"></span>{C.e(label)}</span>'

    where = " · ".join(x for x in (target.area, monitor.movie.city) if x)
    body = (f'<div class="tr-det-tgt-head"><div class="who"><div class="n">{C.e(target.venue_name)}</div>'
            f'<div class="s">{C.e(target.fmt)}{" · " + C.e(where) if where else ""}</div></div>{state_line}</div>')

    if live and ts:
        links = {lbl: u for lbl, u in ts.time_links if is_bookmyshow_url(u)}
        chips = "".join(C._chip(t, links.get(t, url)) for t in ts.time_labels)
        times = (f'<div class="tr-det-times"><div class="tr-eyebrow">Showtimes found</div>'
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

    return f"""{CSS}<div class="tr-det">
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
          <div class="tr-eyebrow">Movie</div>
          {_fact("Title", monitor.movie.title)}
          {_fact("Language", monitor.movie.language or "—")}
          {_fact("Show dates", dates)}
        </section>
        <section class="tr-det-sec">
          <div class="tr-eyebrow">Monitoring</div>
          <div class="tr-det-facts">
            {_fact("Status", phase.label.capitalize())}
            {_fact("Check interval", f"Every {monitor.interval_minutes} min")}
            {_fact("Checks completed", checks)}
            {_fact("Last checked", fmt_time(state.last_check_at))}
            {_fact("Next check", next_value, next_class)}
            {_fact(until_label, until_value)}
          </div>
        </section>
      </div>
      <section class="tr-det-sec wide">
        <div class="tr-det-sec-head"><span class="tr-eyebrow">Theatres &amp; formats</span><em>{C.e(tgt_sub)}</em></div>
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
CSS = """<style>
[data-testid="stDialog"] > div { max-width:900px !important; }
.tr-det { min-width:0; }
.tr-det-head { display:flex; align-items:center; gap:16px; padding:2px 0 16px; border-bottom:1px solid var(--tr-border); }
.tr-det-head .who { min-width:0; flex:1; }
.tr-det-head .title { font-size:20px; font-weight:800; letter-spacing:-.02em; line-height:1.2; overflow-wrap:anywhere; }
.tr-det-head .where { font-size:12.5px; color:var(--tr-text-3); margin-top:5px; }
.tr-det-head .lang { display:flex; align-items:center; gap:6px; font-size:12.5px; color:var(--tr-text-2); margin-top:5px; }
.tr-det-head .tr-pill { flex:none; align-self:flex-start; }
.tr-det-grid { display:grid; grid-template-columns:1fr 1.35fr; gap:18px; margin-top:16px; }
.tr-det-sec { min-width:0; padding:16px; border-radius:16px; background:var(--tr-surface); border:1px solid var(--tr-border); }
.tr-det-sec.wide { margin-top:16px; }
.tr-det-sec .tr-eyebrow { margin-bottom:10px; }
.tr-det-sec-head { display:flex; align-items:baseline; justify-content:space-between; gap:6px 12px; margin-bottom:10px; flex-wrap:wrap; }
.tr-det-sec-head .tr-eyebrow { margin:0; white-space:nowrap; }
.tr-det-sec-head em { font-style:normal; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.08em; color:var(--tr-text-4); white-space:nowrap; }
.tr-det-facts { display:grid; grid-template-columns:1fr 1fr; gap:12px 16px; }
.tr-det-fact { min-width:0; padding:8px 0; border-bottom:1px solid var(--tr-border); }
.tr-det-fact:last-child { border-bottom:0; }
.tr-det-facts .tr-det-fact { border-bottom:0; padding:0; }
.tr-det-fact .k { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--tr-text-4); }
.tr-det-fact .v { font-size:14px; font-weight:700; margin-top:4px; line-height:1.3; overflow-wrap:anywhere; }
.tr-det-fact .v.mono { font-family:var(--tr-mono); font-weight:500; color:var(--tr-success); }
.tr-det-fact .v.amber { color:var(--tr-warning); }
.tr-det-fact .v.bad { color:var(--tr-danger); }
.tr-det-fact .v.soft { color:var(--tr-text-2); }
.tr-det-tgt { padding:13px 14px; border-radius:13px; background:var(--tr-sunken); border:1px solid var(--tr-border); margin-top:8px; min-width:0; }
.tr-det-tgt.ok { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.32); }
.tr-det-tgt-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.tr-det-tgt-head .who { min-width:0; }
.tr-det-tgt-head .n { font-size:14px; font-weight:700; line-height:1.3; overflow-wrap:anywhere; }
.tr-det-tgt-head .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; overflow-wrap:anywhere; }
.tr-det-state { display:inline-flex; align-items:center; gap:7px; flex:none; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.08em; color:var(--tr-text-3); white-space:nowrap; }
.tr-det-state.ok { color:var(--tr-success); font-weight:500; letter-spacing:.1em; }
.tr-det-state.warn { color:var(--tr-warning); }
.tr-det-state.bad { color:var(--tr-danger); }
.tr-det-times { margin-top:12px; }
.tr-det-times .tr-eyebrow { margin-bottom:0; }
.tr-det .tr-chips .tr-chip { font-size:13px; padding:8px 12px; }
.tr-det-book { margin-top:14px; padding:16px; font-size:15px; }
.tr-det-nobook { margin-top:10px; font-size:12px; color:var(--tr-text-3); line-height:1.45; }
.tr-det-tgt-foot { display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--tr-text-4); }
[class*="st-key-close_detail"] { margin-top:14px; }
@media (max-width: 768px) {
  /* the pill drops under the title instead of squeezing it into a column */
  .tr-det-head { flex-wrap:wrap; gap:14px; }
  .tr-det-head .who { flex:1 1 180px; }
  .tr-det-head .tr-pill { order:3; }
  .tr-det-head .title { font-size:18px; }
  .tr-det-grid { grid-template-columns:1fr; gap:12px; }
  .tr-det-facts { grid-template-columns:1fr 1fr; }
  .tr-det-sec { padding:14px; }
  .tr-det-tgt { padding:12px; }
  .tr-det-tgt-head { flex-wrap:wrap; }
  /* one line, thumb-sized: the booking action is the reason to open this on a phone */
  .tr-det-book { padding:17px 12px; font-size:14.5px; letter-spacing:.02em; white-space:nowrap; }
}
@media (max-width: 480px) {
  .tr-det-facts { grid-template-columns:1fr; gap:10px; }
  .tr-det-sec-head em { white-space:normal; }
}
</style>"""

__all__ = ["OPEN_KEY", "close", "dialog", "markup", "open_detail", "open_id"]
