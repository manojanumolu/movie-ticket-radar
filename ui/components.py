"""HTML fragments for the TicketRadar cards.

Per DESIGN-SPEC §10, status cards are rendered as a single markdown block
rather than composed from widgets: it is both cheaper and far more faithful,
and the mint / amber / crimson variants become one class swap.

Everything user-supplied is escaped. Nothing here decides anything — the
caller passes in state that was already computed, so a card can never
disagree with the monitor it is describing.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from config.timezone import (
    fmt_countdown,
    fmt_date_code,
    fmt_datetime,
    fmt_time,
    now_ist,
)
from monitor.models import Availability, Monitor, MonitorStatus
from monitor.state import MonitorState

# How each availability reads in the rail. (label, css class, glyph)
# Every colour is paired with a word and a glyph so status is never
# colour-only (DESIGN-SPEC §10, accessibility).
AVAILABILITY_UI: dict[Availability, tuple[str, str, str]] = {
    Availability.AVAILABLE: ("AVAILABLE", "ok", "✓"),
    Availability.SOLD_OUT: ("Sold out", "warn", "◍"),
    Availability.NOT_BOOKABLE: ("Not on sale yet", "", "◷"),
    Availability.SHOW_NOT_AVAILABLE: ("No shows yet", "", "◷"),
    Availability.THEATRE_NOT_AVAILABLE: ("Theatre not listed", "", "◷"),
    Availability.NOT_FOUND: ("Movie not listed", "bad", "!"),
    Availability.UNKNOWN: ("Watching", "", "◌"),
    Availability.ERROR: ("Couldn't check", "bad", "!"),
    Availability.EXPIRED: ("Expired", "warn", "◷"),
    Availability.STOPPED: ("Stopped", "", "■"),
}


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def e(value: object) -> str:
    return escape(str(value if value is not None else ""))


# ──────────────────────────────────────────────────────────────────────────
# Chrome
# ──────────────────────────────────────────────────────────────────────────
def step_strip(steps: list[str], current: int, furthest: int) -> None:
    """The 1–5 progress rail.

    ``current`` is where the user is; ``furthest`` is how far they have got,
    so a completed step reads as done even while they are back editing an
    earlier one.
    """
    cells = []
    for index, label in enumerate(steps, start=1):
        if index == current:
            state = "now"
        elif index <= furthest:
            state = "done"
        else:
            state = "todo"
        mark = "✓" if state == "done" else str(index)
        cells.append(
            f'<div class="tr-step-pip {state}">'
            f'<span class="n">{mark}</span><span class="l">{e(label)}</span></div>'
        )
    html(f'<div class="tr-steps">{"".join(cells)}</div>')


def summary_row(number: int, label: str, value: str) -> None:
    """A collapsed, already-answered step."""
    html(
        f"""<div class="tr-summary">
          <div class="n">✓</div>
          <div class="b"><div class="k">{number}. {e(label)}</div>
          <div class="v">{e(value)}</div></div>
        </div>"""
    )


def locked_step(number: int, title: str, reason: str) -> None:
    html(
        f"""<div class="tr-locked">
          <div class="tr-step-head">
            <div class="tr-step-num muted">{number}</div>
            <div><div class="tr-step-title">{e(title)}</div>
            <div class="tr-step-help">{e(reason)}</div></div>
          </div>
        </div>"""
    )


def location_tile(name: str, sub: str, selected: bool, enabled: bool = True) -> None:
    if not enabled:
        html(
            f'<div class="tr-loc disabled"><div class="n">{e(name)}</div>'
            f'<div class="s">Coming soon</div></div>'
        )
        return
    check = '<div class="check">✓</div>' if selected else ""
    html(
        f'<div class="tr-loc{" selected" if selected else ""}">{check}'
        f'<div class="pin">◉</div><div class="n">{e(name)}</div>'
        f'<div class="s">{e(sub)}</div></div>'
    )


def theatre_tile(name: str, area: str, formats: list[str], selected: bool) -> None:
    chips = "".join(f'<span class="f">{e(f)}</span>' for f in formats[:4])
    if len(formats) > 4:
        chips += f'<span class="f more">+{len(formats) - 4}</span>'
    if not formats:
        chips = '<span class="f muted">formats not published yet</span>'
    check = '<div class="check">✓</div>' if selected else ""
    html(
        f"""<div class="tr-theatre{' selected' if selected else ''}">{check}
          <div class="hd"><div class="ab">{e(_abbr(name))}</div>
            <div><div class="n">{e(name)}</div><div class="a">{e(area or 'Hyderabad')}</div></div>
          </div>
          <div class="fl">{chips}</div>
        </div>"""
    )


def _abbr(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w]
    if not words:
        return "???"
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def catalogue_banner(status, message: str, when: str, count: int) -> None:
    """Say exactly why the movie grid looks the way it does.

    An empty grid and a refused request are different facts, and the user is
    told which one they are looking at — never "no movies" for a block.
    """
    kinds = {
        "OK": ("ok", "✓", "Catalogue loaded",
               f"{count} movie(s) from BookMyShow · updated {when}"),
        "EMPTY": ("warn", "◎", "No movies listed right now",
                  f"BookMyShow answered, and had nothing on for this city · checked {when}"),
        "BLOCKED": ("bad", "!", "Couldn't reach BookMyShow",
                    "BookMyShow's bot check refused the request — this is not "
                    "a 'no movies' answer."),
        "ERROR": ("bad", "!", "Couldn't load the catalogue",
                  "A network or parsing problem, not a 'no movies' answer."),
        "NEVER": ("", "◷", "Catalogue not built yet",
                  "The background sync hasn't run for this city yet."),
    }
    cls, glyph, title, default_sub = kinds.get(str(status), kinds["NEVER"])
    sub = message if (message and str(status) in ("BLOCKED", "ERROR")) else default_sub
    html(
        f"""<div class="tr-banner {cls}">
          <div class="g">{e(glyph)}</div>
          <div><div class="t">{e(title)}</div><div class="s">{e(sub)}</div></div>
        </div>"""
    )


def hero(title: str, lede: str, sub: str) -> None:
    html(
        f"""<div class="tr-hero"><div class="tr-hero-inner">
          <div>
            <h1>{e(title)}</h1>
            <div class="tr-hero-lede">{e(lede)}</div>
            <div class="tr-hero-sub">{e(sub)}</div>
          </div>
          <div class="tr-hero-mark"><div>Some stories</div><div>are worth</div><b>the wait</b></div>
        </div></div>"""
    )


def rule(label: str) -> None:
    html(f'<div class="tr-rule"><span class="tr-eyebrow">{e(label)}</span><span class="line"></span></div>')


def step_header(number: int, title: str, help_text: str) -> None:
    html(
        f"""<div class="tr-step-head">
          <div class="tr-step-num">{number}</div>
          <div><div class="tr-step-title">{e(title)}</div>
          <div class="tr-step-help">{e(help_text)}</div></div>
        </div>"""
    )


def platform_selector(platforms, active_slug: str, theatre_count: int, city: str) -> None:
    cards = []
    for p in platforms:
        if not p.enabled:
            cards.append(
                f'<div class="tr-platform"><div class="name">{e(p.name)}</div>'
                f'<div class="soon">Coming soon</div></div>'
            )
            continue
        brand = e(p.name).replace("My", "<em>My</em>") if p.slug == "bookmyshow" else e(p.name)
        known = (
            f"{city} · {theatre_count} theatres known"
            if theatre_count
            else f"{city} · add a movie to discover theatres"
        )
        live = " live" if p.slug == active_slug else ""
        cards.append(
            f"""<div class="tr-platform{live}">
              <div class="row">
                <div class="brand">{brand}</div>
                <span class="tr-pill ok"><span class="tr-dot ok"></span>Connected</span>
              </div>
              <div class="meta"><span class="tr-dot bad"></span>{e(known)}</div>
            </div>"""
        )
    html(f'<div class="tr-platforms">{"".join(cards)}</div>')


def poster_tile(title: str, meta: str, selected: bool, poster_url: str = "") -> None:
    art_style = (
        f' style="background-image:linear-gradient(180deg,rgba(8,8,10,0),rgba(8,8,10,.75)),'
        f'url({escape(poster_url, quote=True)})"'
        if poster_url
        else ""
    )
    caption = "" if poster_url else '<span class="cap">poster</span>'
    check = '<div class="check">✓</div>' if selected else ""
    html(
        f"""<div class="tr-poster{' selected' if selected else ''}">
          <div class="art"{art_style}>{caption}</div>{check}
          <div class="body"><div class="t">{e(title)}</div><div class="m">{e(meta)}</div></div>
        </div>"""
    )


# ──────────────────────────────────────────────────────────────────────────
# Monitor cards
# ──────────────────────────────────────────────────────────────────────────
def _art(poster_url: str, caption: str = "ART") -> str:
    if poster_url:
        return f'<div class="art" style="background-image:url({escape(poster_url, quote=True)})"></div>'
    return f'<div class="art"><span>{e(caption)}</span></div>'


def active_monitor_card(monitor: Monitor, state: MonitorState, *, at: datetime | None = None) -> None:
    """The rail's status card.

    Shows the configured interval, the last *successful* check and the next
    expected one as three separate facts. They are not the same thing, and
    conflating them is how a UI ends up implying a check happened when the
    backend never ran.
    """
    at = at or now_ist()
    next_at = state.next_check_at(monitor.interval_minutes)

    if next_at is None:
        next_value = "on next run"
        next_class = ""
    else:
        remaining = (next_at - at).total_seconds()
        next_value = fmt_countdown(remaining) if remaining > 0 else "due now"
        next_class = " mono" if remaining > 0 else ""

    checks = f"checked {state.success_count} time{'s' if state.success_count != 1 else ''}"
    if state.consecutive_errors:
        note = (
            '<div class="tr-note bad"><span style="color:#FF6B85;font-size:14px;">!</span>'
            f'<div><div class="t">Couldn\'t check BookMyShow</div>'
            f'<div class="s">Connection problem, not a "no tickets" answer · '
            f"last good check {e(fmt_time(state.last_success_at))}</div></div></div>"
        )
    else:
        live = any(
            ts.availability is Availability.AVAILABLE for ts in state.targets.values()
        )
        if live:
            note = (
                '<div class="tr-note"><span class="tr-dot ok live"></span>'
                '<div><div class="t">Tickets found</div>'
                f'<div class="s">Still watching your other theatres · {e(checks)}</div></div></div>'
            )
        else:
            note = (
                '<div class="tr-note"><span class="tr-spin"></span>'
                '<div><div class="t">Checking for tickets…</div>'
                f'<div class="s">Not released yet · {e(checks)}</div></div></div>'
            )

    html(
        f"""<div class="tr-monitor">
          <div class="head">
            {_art(monitor.movie.poster_url)}
            <div>
              <div class="title">{e(monitor.movie.title)}</div>
              <div class="where">BookMyShow · {e(monitor.movie.city)}</div>
              <div style="margin-top:10px;"><span class="tr-pill ok">MONITORING ACTIVE</span></div>
            </div>
          </div>
          <div class="tr-metrics">
            <div class="tr-metric"><div class="k">Last checked</div>
              <div class="v">{e(fmt_time(state.last_success_at))}</div></div>
            <div class="tr-metric"><div class="k">Next check in</div>
              <div class="v{next_class}">{e(next_value)}</div></div>
            <div class="tr-metric wide"><div class="k">Every</div>
              <div class="v">{monitor.interval_minutes} minutes</div></div>
            <div class="tr-metric wide"><div class="k">Monitoring until</div>
              <div class="v">{e(fmt_datetime(monitor.monitor_until))}</div></div>
          </div>
          {note}
        </div>"""
    )


def target_rows(monitor: Monitor, state: MonitorState) -> None:
    """Per-theatre status. One theatre going live never mutes the others."""
    html(f'<div class="tr-eyebrow" style="margin-bottom:9px;">Theatres ({len(monitor.targets)})</div>')
    for target in monitor.targets:
        ts = state.targets.get(target.key)
        availability = ts.availability if ts else Availability.UNKNOWN
        label, cls, glyph = AVAILABILITY_UI.get(availability, ("Watching", "", "◌"))

        if availability is Availability.AVAILABLE:
            times = ts.time_labels if ts else []
            sub = f"{target.fmt} · {len(times)} showtime{'s' if len(times) != 1 else ''}"
            right = f'<div class="r ok">✓ {e(label)}</div>'
            row_cls = " ok"
        elif availability in (Availability.UNKNOWN, Availability.SHOW_NOT_AVAILABLE,
                              Availability.THEATRE_NOT_AVAILABLE, Availability.NOT_BOOKABLE):
            sub = f"{target.fmt} · not released yet"
            right = '<div class="r"><span class="tr-spin grey"></span>Watching</div>'
            row_cls = ""
        else:
            sub = f"{target.fmt} · {label.lower()}"
            right = f'<div class="r">{e(glyph)} {e(label)}</div>'
            row_cls = f" {cls}" if cls else ""

        html(
            f"""<div class="tr-row{row_cls}">
              <div><div class="n">{e(target.venue_name)}</div><div class="s">{e(sub)}</div></div>
              {right}
            </div>"""
        )


def live_card(monitor: Monitor, state: MonitorState, target_key: str) -> None:
    """The loudest thing on screen when a theatre goes bookable."""
    target = monitor.target(target_key)
    ts = state.targets.get(target_key)
    if target is None or ts is None:
        return

    chips = "".join(f'<span class="tr-chip">{e(t)}</span>' for t in ts.time_labels) or (
        '<span class="tr-chip" style="font-weight:400;color:#8E8E98;">See BookMyShow for times</span>'
    )
    booked = (
        f'<a class="tr-book" href="{escape(ts.booking_url, quote=True)}" target="_blank" '
        f'rel="noopener">BOOK ON BOOKMYSHOW ↗</a>'
        if ts.booking_url
        else '<div class="tr-chip" style="text-align:center;">Open BookMyShow to book</div>'
    )
    date_block = (
        f'<div><div class="tr-eyebrow">Date</div>'
        f'<div class="date">{e(fmt_date_code(ts.date_code))}</div></div>'
        if ts.date_code
        else ""
    )

    html(
        f"""<div class="tr-live">
          <div class="top">
            <div>
              <span class="tr-pill ok" style="font-family:'JetBrains Mono',monospace;letter-spacing:.2em;padding:6px 12px;">
                <span class="tr-dot ok live"></span>TICKETS ARE LIVE</span>
              <h2>{e(monitor.movie.title)}</h2>
              <div class="where">{e(target.venue_name)} · {e(target.fmt)} · {e(monitor.movie.city)}</div>
            </div>
            <div class="detected">
              <div class="tr-eyebrow">Detected at</div>
              <div class="v">{e(fmt_time(ts.since))}</div>
              <div style="font-size:12px;color:#3ED598;margin-top:6px;">
                {'✓ Email sent' if ts.notified_at else '◷ Email pending'}</div>
            </div>
          </div>
          <div class="split">
            {date_block}
            <div><div class="tr-eyebrow">Showtimes found</div>
              <div class="tr-chips">{chips}</div></div>
          </div>
          <div style="margin-top:24px;">{booked}</div>
          <div class="foot">
            <span>Found on check #{state.success_count}</span>
            <span>Checked every {monitor.interval_minutes} min</span>
            <span>Monitor live until {e(fmt_datetime(monitor.monitor_until))}</span>
          </div>
        </div>"""
    )


# ──────────────────────────────────────────────────────────────────────────
# The four resting states
# ──────────────────────────────────────────────────────────────────────────
def stopped_card(monitor: Monitor, state: MonitorState) -> None:
    html(
        f"""<div class="tr-state stopped">
          <div class="icon">■</div>
          <div class="h">Monitoring stopped</div>
          <div class="p">You stopped this alert at {e(fmt_time(monitor.stopped_at))}.
            We are no longer checking BookMyShow.</div>
          <div class="box">{e(monitor.movie.title)}<br>
            {len(monitor.targets)} theatre{'s' if len(monitor.targets) != 1 else ''} ·
            {state.success_count} check{'s' if state.success_count != 1 else ''} run</div>
        </div>"""
    )


def expired_card(monitor: Monitor, state: MonitorState) -> None:
    found = any(ts.availability is Availability.AVAILABLE for ts in state.targets.values())
    html(
        f"""<div class="tr-state expired">
          <div class="icon">◷</div>
          <div class="h">Monitoring expired</div>
          <div class="p">This alert stopped on its own at the end time you set. Nothing went wrong.</div>
          <div class="box">Ended {e(fmt_datetime(monitor.monitor_until))}<br>
            {'Tickets were detected before it ended' if found else 'No release detected'}</div>
        </div>"""
    )


def error_card(state: MonitorState, interval_minutes: int) -> None:
    """The one card that must never read as 'no tickets'."""
    next_at = state.next_check_at(interval_minutes)
    attempts = state.consecutive_errors
    html(
        f"""<div class="tr-state error">
          <div class="icon">!</div>
          <div class="h">Couldn't check BookMyShow</div>
          <div class="p">This is a connection problem on our side — not a "no tickets" answer.</div>
          <div class="box">Last successful check<br>
            <span style="color:#fff;font-weight:600;font-size:13px;">
              {e(fmt_time(state.last_success_at)) if state.last_success_at else 'none yet'}</span><br>
            {attempts} failed attempt{'s' if attempts != 1 else ''} ·
            retrying around {e(fmt_time(next_at)) if next_at else 'the next scheduled run'}</div>
          <div style="font-size:11.5px;color:#8E8E98;margin-top:14px;">
            The monitor keeps running. We retry automatically.</div>
        </div>"""
    )


def empty_card(message: str = "Set an alert and we'll watch BookMyShow for you — quietly, in the background.") -> None:
    html(
        f"""<div class="tr-state empty">
          <div class="ring">◎</div>
          <div class="h">No active monitors</div>
          <div class="p">{e(message)}</div>
        </div>"""
    )


def history_rows(history: list[dict], limit: int = 3) -> None:
    kind_style = {
        "TICKETS_LIVE": ("#3ED598", "✓ Tickets found"),
        "NEW_SHOWTIME": ("#3ED598", "✓ New showtime"),
        "STOPPED": ("#9A9AA4", "■ Stopped manually"),
        "EXPIRED": ("#E8B25C", "◷ Expired"),
        "ERROR": ("#FF6B85", "! Check failed"),
        "CREATED": ("#B0B0BA", "● Monitor created"),
    }
    for item in history[:limit]:
        colour, label = kind_style.get(item.get("kind", ""), ("#B0B0BA", item.get("kind", "")))
        when = item.get("at", "")
        stamp = ""
        try:
            stamp = f" · {datetime.fromisoformat(when).strftime('%d %b')}" if when else ""
        except ValueError:
            stamp = ""
        targets = " · ".join(item.get("targets", [])[:2]) or item.get("message", "")
        html(
            f"""<div class="tr-history">
              <div class="art"></div>
              <div style="min-width:0;">
                <div class="t">{e(item.get('movie', 'Monitor'))}</div>
                <div class="s">{e(targets)}</div>
                <div class="k" style="color:{colour};">{e(label)}{e(stamp)}</div>
              </div>
            </div>"""
        )


def status_for(monitor: Monitor, state: MonitorState) -> str:
    """Which card the monitor should render as. Single decision point."""
    if monitor.status is MonitorStatus.STOPPED:
        return "stopped"
    if monitor.status is MonitorStatus.EXPIRED or monitor.is_expired():
        return "expired"
    if any(ts.availability is Availability.AVAILABLE for ts in state.targets.values()):
        return "live"
    if state.consecutive_errors:
        return "error"
    return "active"


__all__ = [
    "AVAILABILITY_UI",
    "active_monitor_card",
    "empty_card",
    "error_card",
    "expired_card",
    "e",
    "hero",
    "history_rows",
    "html",
    "live_card",
    "platform_selector",
    "poster_tile",
    "rule",
    "status_for",
    "step_header",
    "stopped_card",
    "target_rows",
]
