"""HTML fragments for the TicketRadar cards.

Per DESIGN-SPEC §10, status cards are rendered as a single markdown block
rather than composed from widgets: it is both cheaper and far more faithful,
and the mint / amber / crimson variants become one class swap.

Everything user-supplied is escaped. Nothing here decides anything — the
caller passes in state that was already computed, so a card can never
disagree with the monitor it is describing. The one exception is
:func:`phase_for`, which is the single place that turns (monitor, state) into
the word the UI uses for it, so every card agrees on that word.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

from config.timezone import (
    fmt_countdown,
    fmt_date_code,
    fmt_datetime,
    fmt_time,
    now_ist,
)
from monitor.models import Availability, Monitor, MonitorStatus, describe_date_codes
from monitor.state import MonitorState

ASSETS = Path(__file__).parent / "assets"

# ── inline SVG glyphs, lifted from TicketRadar.dc.html ────────────────────
TICKET_GLYPH = (
    '<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="#fff" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1.2'
    'a2.3 2.3 0 0 0 0 4.6v1.2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1.2a2.3 2.3 0 0 0 0-4.6z"/>'
    '<path d="M13 7.5v9" stroke-dasharray="2 2.2"/></svg>'
)
FILM_GLYPH = (
    '<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="rgba(255,255,255,.28)" '
    'stroke-width="1.7" stroke-linecap="round"><rect x="3" y="4.5" width="18" height="15" rx="2.2"/>'
    '<path d="M7.5 4.5v15M16.5 4.5v15"/></svg>'
)
PIN_GLYPH = (
    '<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s6.5-5.6 6.5-10.2A6.5 6.5 0 0 0 5.5 10.8'
    'C5.5 15.4 12 21 12 21z"/><circle cx="12" cy="10.5" r="2.4"/></svg>'
)
CHECK_CIRCLE = (
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#3ED598" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.2 12.3l2.6 2.6 5-5.2"/></svg>'
)

# How each availability reads. (label, css class, glyph)
# Every colour is paired with a word and a glyph so status is never
# colour-only (DESIGN-SPEC.md §10, accessibility).
AVAILABILITY_UI: dict[Availability, tuple[str, str, str]] = {
    Availability.AVAILABLE: ("AVAILABLE", "ok", "✓"),
    Availability.SOLD_OUT: ("Sold out", "warn", "◍"),
    Availability.NOT_BOOKABLE: ("Not released yet", "", "◷"),
    Availability.SHOW_NOT_AVAILABLE: ("Not released yet", "", "◷"),
    Availability.THEATRE_NOT_AVAILABLE: ("Waiting for release", "", "◷"),
    Availability.NOT_FOUND: ("Movie not listed", "bad", "!"),
    Availability.UNKNOWN: ("Watching", "", "◌"),
    Availability.ERROR: ("Couldn't check", "bad", "!"),
    Availability.EXPIRED: ("Expired", "warn", "◷"),
    Availability.STOPPED: ("Stopped", "", "■"),
}

#: If the immediate first check hasn't landed after this long, stop implying
#: it is imminent and say plainly that the schedule will catch it.
FIRST_CHECK_GRACE = timedelta(minutes=8)


def html(markup: str) -> None:
    st.markdown(clean_html(markup), unsafe_allow_html=True)


def scroll_to_top(token: str) -> None:
    """Scroll the page to the top once per ``token`` (a page change).

    Streamlit keeps the scroll position across reruns, so switching to Home
    from the bottom of My Monitors used to land mid-page. A zero-height
    component runs one line of script in the parent document; the token in
    the markup is what makes it run again on the next change, not on every
    rerun.
    """
    import streamlit.components.v1 as components

    components.html(
        "<script>(function(){"
        "var d=window.parent.document;"
        "['[data-testid=\"stAppViewContainer\"]','[data-testid=\"stMain\"]','section.stMain','.stMain']"
        ".forEach(function(s){var el=d.querySelector(s); if(el){el.scrollTo({top:0});}});"
        f"window.parent.scrollTo(0,0);}})();/* {e(token)} */</script>",
        height=0,
    )


def clean_html(markup: str) -> str:
    """Make a multi-line HTML fragment safe for ``st.markdown``.

    Streamlit runs the fragment through a Markdown parser first. A blank (or
    whitespace-only) line ends the HTML block, and any indented line after it
    is rendered as a *code block* — which is how an empty optional slot in a
    card once turned the rest of the card into visible ``</div>`` text. So:
    drop empty lines and leading indentation, and never leave a line that
    starts with four spaces.
    """
    lines = [line.strip() for line in markup.splitlines()]
    return "\n".join(line for line in lines if line)


def e(value: object) -> str:
    return escape(str(value if value is not None else ""))


@lru_cache(maxsize=16)
def asset_uri(name: str) -> str:
    """A bundled image as a data URI (st.markdown can't serve local files).

    Cached: the three platform logos are ~37 KB of PNG that every rerun was
    re-reading and re-encoding.
    """
    path = ASSETS / name
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


# ──────────────────────────────────────────────────────────────────────────
# Chrome
# ──────────────────────────────────────────────────────────────────────────
def logo() -> None:
    html(
        f"""<div class="tr-logo">
          <div class="tr-logo-mark">{TICKET_GLYPH}</div>
          <div><div class="tr-logo-name">Ticket<em>Radar</em></div>
          <div class="tr-logo-sub">Be first in line.</div></div>
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


def step_header(number: int | str, title: str, help_text: str) -> None:
    html(
        f"""<div class="tr-step-head">
          <div class="tr-step-num">{e(number)}</div>
          <div><div class="tr-step-title">{e(title)}</div>
          <div class="tr-step-help">{e(help_text)}</div></div>
        </div>"""
    )


def step_pip(index: int, label: str, state: str) -> None:
    """One cell of the 1–5 progress rail. ``state`` is now · done · todo.

    ``done`` cells are rendered inside a *pick* container by the flow, so the
    rail doubles as a breadcrumb: clicking a completed step goes back to it
    with everything already chosen still chosen.
    """
    mark = "✓" if state == "done" else str(index)
    html(
        f'<div class="tr-step-pip {e(state)}">'
        f'<span class="n">{mark}</span><span class="l">{e(label)}</span></div>'
    )


def step_strip(steps: list[str], current: int, furthest: int) -> None:
    """The whole rail as one block (non-interactive; the flow draws the live one)."""
    cells = []
    for index, label in enumerate(steps, start=1):
        state = "now" if index == current else ("done" if index <= furthest else "todo")
        mark = "✓" if state == "done" else str(index)
        cells.append(
            f'<div class="tr-step-pip {state}">'
            f'<span class="n">{mark}</span><span class="l">{e(label)}</span></div>'
        )
    html(
        f'<div class="tr-steps">{"".join(cells)}</div>'
        f'<div class="tr-step-mobile">Step {current} of {len(steps)} — {e(steps[current - 1])}</div>'
    )


def summary_row(number: int, label: str, value: str) -> None:
    """A collapsed, already-answered step."""
    html(
        f"""<div class="tr-summary">
          <div class="n">✓</div>
          <div class="b"><div class="k">{number} · {e(label)}</div>
          <div class="v">{e(value)}</div></div>
        </div>"""
    )


def summary_strip(items: list[tuple[int, str, str]]) -> None:
    """Everything already answered, as one row of chips: (step, label, value)."""
    if not items:
        return
    chips = "".join(
        f'<div class="i"><span class="n">✓</span><span class="k">{n} · {e(label)}</span>'
        f'<span class="v" title="{escape(value, quote=True)}">{e(value)}</span></div>'
        for n, label, value in items
    )
    html(f'<div class="tr-summary-strip">{chips}</div>')


def flash(kind: str, message: str) -> None:
    """A message in the palette's own voice (success / warning / error / info).

    ``message`` may carry ``**bold**`` markdown; nothing else is interpreted.
    """
    glyph = {"success": "✓", "warning": "◷", "error": "!"}.get(kind, "●")
    parts = e(message).split("**")
    body = "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))
    html(f'<div class="tr-flash {e(kind)}"><span class="g">{glyph}</span><div>{body}</div></div>')


def status_line(kind: str, text: str) -> None:
    """A one-line fact with a glyph: ok · warn · bad · wait · info."""
    glyph = {"ok": "✓", "warn": "⚠", "bad": "!", "wait": "◷", "info": "●"}.get(kind, "●")
    html(f'<div class="tr-status {e(kind)}"><span class="g">{glyph}</span><span>{e(text)}</span></div>')


def platform_selector(platforms, active_slug: str, theatre_count: int, city: str) -> None:
    """The platform lockups: the platform's own logo, never redrawn type."""
    cards = []
    for p in platforms:
        if p.slug == "bookmyshow":
            known = (
                f"{city} · {theatre_count} theatres tracked"
                if theatre_count
                else f"{city} · theatres appear once a movie is synced"
            )
            live = " live" if p.slug == active_slug else ""
            cards.append(
                f"""<div class="tr-platform{live}">
                  <div class="row">
                    <img src="{asset_uri('logo-bookmyshow.png')}" alt="BookMyShow">
                    <span class="tr-pill ok connected">{CHECK_CIRCLE}Connected</span>
                  </div>
                  <div class="meta">{PIN_GLYPH.format(size=15, color='#FF3355')}{e(known)}</div>
                </div>"""
            )
        elif p.slug == "district":
            cards.append(
                f"""<div class="tr-platform">
                  <div class="lockup"><img src="{asset_uri('logo-district.png')}" alt="District by Zomato"
                    style="width:34px;height:34px;border-radius:9px;"><div class="name">District</div></div>
                  <div class="soon">Coming soon</div>
                </div>"""
            )
        elif p.slug == "pvr":
            cards.append(
                f"""<div class="tr-platform">
                  <div class="lockup"><img src="{asset_uri('logo-pvr.png')}" alt="PVR Cinemas"
                    style="height:36px;width:auto;opacity:.88;"><div class="name">Cinemas</div></div>
                  <div class="soon">Coming soon</div>
                </div>"""
            )
        # Anything else registered but not designed for is left off the shelf.
    html(f'<div class="tr-platforms">{"".join(cards)}</div>')


# ──────────────────────────────────────────────────────────────────────────
# Selectable tiles (rendered inside a `pick_` container with an overlay button)
# ──────────────────────────────────────────────────────────────────────────
def location_tile(name: str, sub: str, selected: bool, enabled: bool = True) -> None:
    if not enabled:
        html(
            f'<div class="tr-loc disabled">{PIN_GLYPH.format(size=18, color="#6E6E7A")}'
            f'<div class="n">{e(name)}</div><div class="s">Coming soon</div></div>'
        )
        return
    check = '<div class="tr-check">✓</div>' if selected else ""
    pin = PIN_GLYPH.format(size=18, color="#FF3355" if selected else "#8E8E98")
    html(
        f'<div class="tr-loc{" selected" if selected else ""}">{check}{pin}'
        f'<div class="n">{e(name)}</div><div class="s">{e(sub)}</div></div>'
    )


def theatre_row(name: str, area: str, formats: list[str], selected: bool, abbr: str,
                featured: bool = False, coming: bool = False) -> None:
    """The design's checkbox row: box · abbreviation tile · name / area · formats.

    Long names ("Sai Ranga70MM 4KLaser Dolby7.1 AirCooled") wrap; format
    badges sit on their own line and wrap too, so nothing leaves the card.
    ``coming`` marks a theatre being watched for release: its formats are the
    ones it is known to run, and the row says so.
    """
    shown = formats[:3]
    chips = "".join(f'<span class="tr-badge">{e(f)}</span>' for f in shown)
    if len(formats) > 3:
        chips += f'<span class="tr-badge muted">+{len(formats) - 3} more</span>'
    if not formats:
        chips = '<span class="tr-badge muted">formats not published yet</span>'
    if coming:
        chips = '<span class="tr-badge soon">Coming soon</span>' + chips
    star = '<span class="tr-star" title="Featured theatre">★</span>' if featured else ""
    html(
        f"""<div class="tr-throw{' selected' if selected else ''}{' coming' if coming else ''}">
          <div class="box">{'✓' if selected else ''}</div>
          <div class="ab">{e(abbr)}</div>
          <div class="body">
            <div class="n">{e(name)}{star}</div>
            <div class="a">{e(area or 'Hyderabad')}</div>
            <div class="fmts">{chips}</div>
          </div>
        </div>"""
    )


def featured_tile(name: str, area: str, venue, selected: bool, *, released: bool) -> None:
    """A premium quick-pick, in one of three states.

    *Released* — the movie is listed here now: green badge, its formats.
    *Coming soon* — ``venue`` is the theatre as the catalogue knows it from
    other films, but it hasn't listed this movie yet: grey badge, the formats
    it is known to run, and still selectable — picking it watches the theatre
    until BookMyShow releases tickets there.
    *Unknown* — ``venue`` is None: the catalogue has never seen the theatre,
    so there is nothing to watch; the tile says so and cannot be picked.
    """
    if venue is None:
        html(
            f"""<div class="tr-feat off">
              <div class="k"><span class="tr-badge muted">Not in catalogue</span></div>
              <div class="n">{e(name)}</div>
              <div class="a">{e(area)}</div>
              <div class="s">Not seen on BookMyShow {e(area and 'for any film yet')}</div>
            </div>"""
        )
        return
    check = '<div class="tr-check">✓</div>' if selected else ""
    formats = list(venue.formats)
    if released:
        badge = '<span class="tr-badge live">Now listed</span>'
        sub = (" · ".join(formats[:2]) + (f" +{len(formats) - 2}" if len(formats) > 2 else "")
               if formats else "formats not published yet")
        note = ""
    else:
        badge = '<span class="tr-badge soon">Coming soon</span>'
        sub = ("Expected: " + " · ".join(formats[:2]) + (f" +{len(formats) - 2}" if len(formats) > 2 else "")
               if formats else "Expected format: as listed by the theatre")
        note = '<div class="w">We\'ll watch this theatre until BookMyShow releases tickets.</div>'
    html(
        f"""<div class="tr-feat{' selected' if selected else ''}{'' if released else ' soon'}">{check}
          <div class="k">{badge}</div>
          <div class="n" title="{escape(venue.name, quote=True)}">{e(name)}</div>
          <div class="a">{e(area or venue.area)}</div>
          <div class="s" title="{escape(sub, quote=True)}">{e(sub)}</div>
          {note}
        </div>"""
    )


def format_panel_head(name: str, area: str, badge: str = "", coming: bool = False) -> None:
    badges = ('<span class="tr-badge soon">Coming soon</span>' if coming else "") + (
        f'<span class="tr-badge">{e(badge)}</span>' if badge else "")
    badge_html = f'<div class="b">{badges}</div>' if badges else ""
    note = ('<div class="w">Not listed for this movie yet — pick the format to wait for.</div>'
            if coming else "")
    html(
        f'<div class="tr-fmt-head"><div class="n">{e(name)}</div>'
        f'<div class="a">{e(area or "Hyderabad")}</div>{badge_html}{note}</div>'
    )


def choice_tile(label: str, sub: str, selected: bool) -> None:
    html(
        f'<div class="tr-interval choice{" selected" if selected else ""}">'
        f'<div class="lbl">{e(label)}</div><div class="unit">{e(sub)}</div></div>'
    )


def interval_tile(minutes: int, selected: bool) -> None:
    html(
        f'<div class="tr-interval{" selected" if selected else ""}">'
        f'<div class="num">{int(minutes)}</div><div class="unit">minutes</div></div>'
    )


def poster_tile(title: str, meta: str, selected: bool, poster_url: str = "",
                compact: bool = False) -> None:
    art = (
        f'<img src="{escape(poster_url, quote=True)}" alt="" loading="lazy" decoding="async">'
        if poster_url
        else FILM_GLYPH.format(size=22)
    )
    check = '<div class="tr-check">✓</div>' if selected else ""
    html(
        f"""<div class="tr-poster{' selected' if selected else ''}{' compact' if compact else ''}">
          <div class="art">{art}{check}</div>
          <div class="body"><div class="t" title="{escape(title, quote=True)}">{e(title)}</div>
          <div class="m">{e(meta)}</div></div>
        </div>"""
    )


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
        "BLOCKED": ("bad", "!", "Couldn&rsquo;t reach BookMyShow",
                    "BookMyShow&rsquo;s bot check refused the request &mdash; this is "
                    "<strong>not</strong> a &ldquo;no movies&rdquo; answer."),
        "ERROR": ("bad", "!", "Couldn't load the catalogue",
                  "A network or parsing problem, not a 'no movies' answer."),
        "NEVER": ("", "◷", "Catalogue not built yet",
                  "The background sync hasn't run for this city yet."),
    }
    cls, glyph, title, default_sub = kinds.get(str(status), kinds["NEVER"])
    # `title` and `default_sub` are our own copy and are written as HTML;
    # `message` comes from an exception, so only that gets escaped.
    sub = f"{default_sub} {e(message)}" if (message and str(status) in ("BLOCKED", "ERROR")) \
        else default_sub
    html(
        f"""<div class="tr-banner {cls}">
          <div class="g">{glyph}</div>
          <div><div class="t">{title}</div><div class="s">{sub}</div></div>
        </div>"""
    )


# ──────────────────────────────────────────────────────────────────────────
# Phase — the one word the UI uses for a monitor
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Phase:
    key: str      # waiting · checking · available · not_released · sold_out · error · blocked · stopped · expired
    label: str    # what the pill says
    css: str      # ok · warn · bad · neutral
    live: bool    # pulsing dot / spinner


def phase_for(monitor: Monitor, state: MonitorState, *, at: datetime | None = None) -> Phase:
    """Single decision point for a monitor's current phase."""
    at = at or now_ist()
    if monitor.status is MonitorStatus.STOPPED:
        return Phase("stopped", "STOPPED", "neutral", False)
    if monitor.status is MonitorStatus.EXPIRED or monitor.is_expired(at):
        return Phase("expired", "EXPIRED", "warn", False)
    availabilities = [ts.availability for ts in state.targets.values()]
    if any(a is Availability.AVAILABLE for a in availabilities):
        return Phase("available", "TICKETS AVAILABLE", "ok", True)
    if state.consecutive_errors:
        if state.is_blocked:
            return Phase("blocked", "BLOCKED", "bad", True)
        return Phase("error", "ERROR", "bad", True)
    if state.last_check_at is None:
        if monitor.problem:
            return Phase("problem", "PROBLEM OCCURRED", "bad", False)
        return Phase("waiting", "WAITING FOR FIRST CHECK", "warn", True)
    if availabilities and all(a is Availability.SOLD_OUT for a in availabilities):
        return Phase("sold_out", "SOLD OUT", "warn", True)
    return Phase("not_released", "MONITORING ACTIVE", "ok", True)


def status_for(monitor: Monitor, state: MonitorState) -> str:
    """Which card the monitor should render as."""
    key = phase_for(monitor, state).key
    if key in ("stopped", "expired"):
        return key
    if key == "available":
        return "live"
    if key in ("error", "blocked"):
        return "error"
    return "active"


def problems_for(monitor: Monitor, state: MonitorState, *, at: datetime | None = None) -> list[tuple[str, str]]:
    """Every genuine failure the system knows about, as (title, cause).

    Empty means nothing is wrong. This is the single source for the red
    PROBLEM OCCURRED state, so the rail, My Monitors and the pill agree.
    """
    at = at or now_ist()
    out: list[tuple[str, str]] = []
    if not monitor.is_running(at):
        return out
    if monitor.problem and state.check_count == 0:
        title = {
            "FIRST_CHECK_NOT_STARTED": "The first check could not be started",
            "RETRY_FAILED": "Retry could not be started",
        }.get(str(monitor.problem.get("kind", "")), "Monitoring problem")
        when = fmt_time(_parse(monitor.problem.get("at")))
        out.append((title, f"{monitor.problem.get('message', '')} (at {when})"))
    elif (state.last_check_at is None and monitor.first_check_requested_at
          and at - monitor.first_check_requested_at > FIRST_CHECK_GRACE):
        out.append(("The worker hasn't picked this monitor up",
                    f"A check was requested at {fmt_time(monitor.first_check_requested_at)} but no run "
                    "has reported back. GitHub may be queueing the run; if this persists, the "
                    "workflow itself failed — open the repository's Actions tab."))
    if state.consecutive_errors:
        if state.is_blocked:
            out.append(("BookMyShow blocked the request",
                        f"{state.last_error or 'Bot check refused the request.'} — "
                        f"{state.consecutive_errors} failed attempt(s); last good check "
                        f"{fmt_time(state.last_success_at)}. Not a 'no tickets' answer."))
        else:
            out.append(("Couldn't reach BookMyShow",
                        f"{state.last_error or 'Network error.'} — {state.consecutive_errors} failed "
                        f"attempt(s); last good check {fmt_time(state.last_success_at)}."))
    if state.last_email_error:
        out.append(("Email could not be sent",
                    f"{state.last_email_error} — the alert is retried on every check until it goes out."))
    if not monitor.targets:
        out.append(("Invalid monitor configuration", "This monitor has no theatre/format to watch."))
    return out


def _parse(value):
    from config.timezone import parse_iso

    return parse_iso(value) if isinstance(value, str) else value


def problem_card(problems: list[tuple[str, str]]) -> None:
    """The readable explanation behind PROBLEM OCCURRED."""
    items = "".join(
        f'<div class="box" style="margin-top:10px;"><div style="color:#fff;font-weight:600;font-size:13px;">{e(title)}</div>'
        f'<div style="margin-top:4px;">{e(cause)}</div></div>'
        for title, cause in problems
    )
    html(
        f"""<div class="tr-state error" style="padding:18px;">
          <div class="icon">!</div>
          <div class="h" style="font-size:16px;margin-top:12px;">Problem occurred</div>
          <div class="p">What actually went wrong, most recent first.</div>
          {items}
          <div style="font-size:11.5px;color:#8E8E98;margin-top:14px;">
            Fix the cause, then press Retry now. The monitor itself is saved and is not lost.</div>
        </div>"""
    )


def next_check_text(monitor: Monitor, state: MonitorState, at: datetime) -> tuple[str, str]:
    """(value, css) for the "Next check" metric. Never pretends to be exact."""
    next_at = state.next_check_at(monitor.interval_minutes)
    if next_at is None:
        requested = monitor.first_check_requested_at
        if monitor.problem:
            return "not started", "bad"
        if requested is None:
            return "on next scheduled run", "soft"
        if at - requested > FIRST_CHECK_GRACE:
            return "waiting on schedule", "amber"
        return "any moment now", "amber"
    remaining = (next_at - at).total_seconds()
    if remaining > 0:
        return fmt_countdown(remaining), "mono"
    return "any moment now", "mono"


# ──────────────────────────────────────────────────────────────────────────
# Monitor cards
# ──────────────────────────────────────────────────────────────────────────
def thumb(poster_url: str, small: bool = False) -> str:
    inner = (
        f'<img src="{escape(poster_url, quote=True)}" alt="">'
        if poster_url
        else FILM_GLYPH.format(size=13)
    )
    return f'<div class="tr-thumb{" sm" if small else ""}">{inner}</div>'


def _note(monitor: Monitor, state: MonitorState, phase: Phase, at: datetime) -> str:
    checks = f"checked {state.success_count} time{'s' if state.success_count != 1 else ''}"
    if phase.key == "problem":
        return ('<div class="tr-note bad"><span style="color:#FF6B85;font-size:14px;">!</span>'
                '<div><div class="t">The first check never started</div>'
                '<div class="s">Open PROBLEM OCCURRED below for the reason, then Retry.</div></div></div>')
    if phase.key == "waiting":
        requested = monitor.first_check_requested_at
        if requested is None:
            sub = "It'll be picked up on the next scheduled run."
        elif at - requested > FIRST_CHECK_GRACE:
            sub = (f"Requested at {fmt_time(requested)} — not picked up yet; the scheduled "
                   "worker will catch it.")
        else:
            sub = f"First check requested at {fmt_time(requested)}."
        return (f'<div class="tr-note warn"><span class="tr-spin amber"></span>'
                f'<div><div class="t">Waiting for first check…</div><div class="s">{e(sub)}</div></div></div>')
    if phase.key == "blocked":
        return ('<div class="tr-note bad"><span style="color:#FF6B85;font-size:14px;">!</span>'
                '<div><div class="t">BookMyShow refused the check</div>'
                f'<div class="s">Bot check, not a "no tickets" answer · last good check '
                f"{e(fmt_time(state.last_success_at))} · retrying automatically</div></div></div>")
    if phase.key == "error":
        return ('<div class="tr-note bad"><span style="color:#FF6B85;font-size:14px;">!</span>'
                '<div><div class="t">Couldn\'t check BookMyShow</div>'
                f'<div class="s">Connection problem, not a "no tickets" answer · last good check '
                f"{e(fmt_time(state.last_success_at))}</div></div></div>")
    if phase.key == "available":
        return ('<div class="tr-note ok"><span class="tr-dot ok live"></span>'
                '<div><div class="t">Tickets found</div>'
                f'<div class="s">Still watching your other theatres · {e(checks)}</div></div></div>')
    if phase.key == "sold_out":
        return ('<div class="tr-note warn"><span class="tr-spin amber"></span>'
                '<div><div class="t">Sold out — watching for a reopen</div>'
                f'<div class="s">You\'ll be emailed if seats come back · {e(checks)}</div></div></div>')
    return ('<div class="tr-note"><span class="tr-spin"></span>'
            '<div><div class="t">Checking for tickets…</div>'
            f'<div class="s">Not released yet · {e(checks)}</div></div></div>')


def active_monitor_card(monitor: Monitor, state: MonitorState, *, at: datetime | None = None) -> None:
    """The rail's status card.

    Shows the configured interval, the last *successful* check, the next
    expected one and the current phase as separate facts. They are not the
    same thing, and conflating them is how a UI ends up implying a check
    happened when the backend never ran.
    """
    at = at or now_ist()
    phase = phase_for(monitor, state, at=at)
    next_value, next_class = next_check_text(monitor, state, at)
    card_class = {"waiting": " waiting", "error": " bad", "blocked": " bad", "problem": " bad"}.get(phase.key, "")
    dates = monitor.date_range_label
    dates_metric = (
        f'<div class="tr-metric wide"><div class="k">Show dates</div><div class="v">{e(dates)}</div></div>'
        if dates else ""
    )
    checked = (
        f"{state.success_count} of {state.check_count}" if state.check_count != state.success_count
        else str(state.check_count)
    )

    html(
        f"""<div class="tr-monitor{card_class}">
          <div class="head">
            {thumb(monitor.movie.poster_url)}
            <div style="min-width:0;">
              <div class="title">{e(monitor.movie.title)}</div>
              <div class="where">BookMyShow · {e(monitor.movie.city)}</div>
              <div style="margin-top:10px;"><span class="tr-pill {phase.css}">{e(phase.label)}</span></div>
            </div>
          </div>
          <div class="tr-metrics">
            <div class="tr-metric"><div class="k">Last checked</div>
              <div class="v">{e(fmt_time(state.last_check_at))}</div></div>
            <div class="tr-metric"><div class="k">Next check</div>
              <div class="v {next_class}">{e(next_value)}</div></div>
            <div class="tr-metric"><div class="k">Every</div>
              <div class="v">{monitor.interval_minutes} minutes</div></div>
            <div class="tr-metric"><div class="k">Checks run</div>
              <div class="v">{e(checked)}</div></div>
            <div class="tr-metric wide"><div class="k">Monitoring until</div>
              <div class="v">{e(fmt_datetime(monitor.monitor_until))}</div></div>
            {dates_metric}
          </div>
          {_note(monitor, state, phase, at)}
        </div>"""
    )


def target_row_markup(monitor: Monitor, state: MonitorState, target, finished: str = "") -> str:
    """One theatre/format row. ``finished`` is 'stopped' / 'expired' / ''."""
    ts = state.targets.get(target.key)
    availability = ts.availability if ts else Availability.UNKNOWN
    label, cls, glyph = AVAILABILITY_UI.get(availability, ("Watching", "", "◌"))

    if finished and availability is not Availability.AVAILABLE:
        # A stopped or expired monitor is not "watching" anything.
        word = "Expired" if finished == "expired" else "Stopped"
        last = label.lower() if ts else "never checked"
        sub = f"{target.fmt} · {last}"
        right = (f'<div class="r warn">◷ {word}</div>' if finished == "expired"
                 else f'<div class="r">■ {word}</div>')
        row_cls = ""
    elif availability is Availability.AVAILABLE:
        times = ts.time_labels if ts else []
        sub = f"{target.fmt} · {len(times)} showtime{'s' if len(times) != 1 else ''}"
        right = f'<div class="r ok">✓ {e(label)}</div>'
        row_cls = " ok"
    elif availability is Availability.UNKNOWN:
        sub = f"{target.fmt} · waiting for first check" if state.last_check_at is None \
            else f"{target.fmt} · not released yet"
        right = '<div class="r"><span class="tr-spin grey"></span>Watching</div>'
        row_cls = ""
    elif availability is Availability.THEATRE_NOT_AVAILABLE:
        sub = f"{target.fmt} · waiting for this theatre to release"
        right = '<div class="r"><span class="tr-spin grey"></span>Watching</div>'
        row_cls = ""
    elif availability in (Availability.SHOW_NOT_AVAILABLE, Availability.NOT_BOOKABLE):
        sub = f"{target.fmt} · {label.lower()}"
        right = '<div class="r"><span class="tr-spin grey"></span>Watching</div>'
        row_cls = ""
    else:
        sub = f"{target.fmt} · {label.lower()}"
        right = f'<div class="r {cls}">{e(glyph)} {e(label)}</div>'
        row_cls = f" {cls}" if cls else ""

    return (
        f'<div class="tr-row{row_cls}">'
        f'<div class="body"><div class="n">{e(target.venue_name)}</div><div class="s">{e(sub)}</div></div>'
        f'{right}</div>'
    )


def target_rows(monitor: Monitor, state: MonitorState) -> None:
    """Per-theatre status. One theatre going live never mutes the others."""
    html(f'<div class="tr-eyebrow" style="margin-bottom:9px;">Theatres ({len(monitor.targets)})</div>')
    finished = phase_for(monitor, state).key if not monitor.is_running() else ""
    for target in monitor.targets:
        html(target_row_markup(monitor, state, target, finished))


#: The short word the My Monitors pills use for each phase.
PILL_LABEL = {
    "available": "AVAILABLE", "sold_out": "SOLD OUT", "stopped": "STOPPED", "expired": "EXPIRED",
    "problem": "PROBLEM", "blocked": "BLOCKED", "error": "ERROR", "waiting": "WAITING",
    "not_released": "ACTIVE",
}


def monitor_card(monitor: Monitor, state: MonitorState, *, at: datetime | None = None) -> None:
    """One monitor as a single scannable card, for My Monitors.

    Everything the user asked to know is here as a labelled fact, and every
    timestamp comes from the worker's state — the card never implies a check
    happened that the worker did not report.
    """
    at = at or now_ist()
    phase = phase_for(monitor, state, at=at)
    finished = phase.key if not monitor.is_running(at) else ""
    tone = {"available": "ok", "sold_out": "warn", "waiting": "warn", "expired": "warn",
            "problem": "bad", "blocked": "bad", "error": "bad", "stopped": "muted"}.get(phase.key, "ok")
    next_value, next_class = next_check_text(monitor, state, at) if not finished else ("—", "soft")

    venues = dedupe_names([t.venue_name for t in monitor.targets])
    formats = dedupe_names([t.fmt for t in monitor.targets])
    dates = monitor.date_range_label or "Any date on sale"
    checked = (
        f"{state.success_count} of {state.check_count}" if state.check_count != state.success_count
        else str(state.check_count)
    )
    rows = "".join(target_row_markup(monitor, state, t, finished) for t in monitor.targets)

    if finished == "stopped":
        note = ('<div class="tr-note"><span style="color:#C9C9D2;">■</span>'
                f'<div><div class="t">Monitoring stopped</div><div class="s">You stopped this alert at '
                f'{e(fmt_time(monitor.stopped_at))}. We are no longer checking BookMyShow.</div></div></div>')
    elif finished == "expired":
        note = ('<div class="tr-note warn"><span style="color:#E8B25C;">◷</span>'
                '<div><div class="t">Monitoring expired</div><div class="s">This alert stopped on its own '
                'at the end time you set. Nothing went wrong.</div></div></div>')
    else:
        note = _note(monitor, state, phase, at)

    html(
        f"""<div class="tr-mcard {tone}">
          <div class="top">
            {thumb(monitor.movie.poster_url)}
            <div class="info">
              <div class="title">{e(monitor.movie.title)}</div>
              <div class="where">BookMyShow · {e(monitor.movie.city)}
                {(' · ' + e(monitor.movie.language)) if monitor.movie.language else ''}
                · created {e(fmt_datetime(monitor.created_at))}</div>
              <div class="pills"><span class="tr-pill {phase.css}">{e(PILL_LABEL.get(phase.key, phase.label))}</span>
                <span class="tr-pill neutral">EVERY {monitor.interval_minutes} MIN</span>
                <span class="tr-pill neutral">{len(monitor.targets)} TARGET{'S' if len(monitor.targets) != 1 else ''}</span></div>
            </div>
          </div>
          <div class="grid">
            <div class="tr-metric span2"><div class="k">Theatre{'s' if len(venues) != 1 else ''}</div>
              <div class="v soft">{e(', '.join(venues))}</div></div>
            <div class="tr-metric span2"><div class="k">Format{'s' if len(formats) != 1 else ''}</div>
              <div class="v soft">{e(' · '.join(formats))}</div></div>
            <div class="tr-metric"><div class="k">Show dates</div><div class="v soft">{e(dates)}</div></div>
            <div class="tr-metric"><div class="k">Monitoring until</div>
              <div class="v soft">{e(fmt_datetime(monitor.monitor_until))}</div></div>
            <div class="tr-metric"><div class="k">Last checked</div>
              <div class="v">{e(fmt_time(state.last_check_at))}</div></div>
            <div class="tr-metric"><div class="k">Next check</div>
              <div class="v {next_class}">{e(next_value)}</div></div>
            <div class="tr-metric"><div class="k">Checks run</div><div class="v">{e(checked)}</div></div>
            <div class="tr-metric"><div class="k">Status</div>
              <div class="v soft">{e(phase.label.capitalize())}</div></div>
          </div>
          <div class="targets">{rows}</div>
          {note}
        </div>"""
    )


def dedupe_names(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen


def _chip(label: str, url: str) -> str:
    if url:
        return (f'<a class="tr-chip" href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener">{e(label)} ↗</a>')
    return f'<span class="tr-chip">{e(label)}</span>'


def live_card(monitor: Monitor, state: MonitorState, target_key: str) -> None:
    """The loudest thing on screen when a theatre goes bookable."""
    target = monitor.target(target_key)
    ts = state.targets.get(target_key)
    if target is None or ts is None:
        return

    links = {label: url for label, url in ts.time_links}
    chips = "".join(_chip(t, links.get(t, ts.booking_url)) for t in ts.time_labels) or (
        '<span class="tr-chip" style="font-weight:400;color:#8E8E98;">See BookMyShow for times</span>'
    )
    booked = (
        f'<a class="tr-book" href="{escape(ts.booking_url, quote=True)}" target="_blank" '
        f'rel="noopener">BOOK ON BOOKMYSHOW ↗</a>'
        if ts.booking_url
        else '<span class="tr-chip" style="text-align:center;">Open BookMyShow to book</span>'
    )
    dates = list(ts.date_codes) or ([ts.date_code] if ts.date_code else [])
    date_block = (
        f'<div><div class="tr-eyebrow">{"Dates" if len(dates) > 1 else "Date"}</div>'
        f'<div class="date">{e(describe_date_codes(dates) if len(dates) > 1 else fmt_date_code(dates[0]))}</div></div>'
        if dates
        else ""
    )
    others = sum(1 for t in monitor.targets if t.key != target_key)
    keep = (
        f'<span class="tr-chip" style="font-weight:600;color:#C9C9D2;padding:18px 22px;border-radius:13px;">'
        f'Keep monitoring {others} other{"s" if others != 1 else ""}</span>'
        if others and monitor.is_running() else ""
    )

    html(
        f"""<div class="tr-live">
          <div class="top">
            <div>
              <span class="tr-pill ok" style="font-family:'JetBrains Mono',monospace;letter-spacing:.2em;padding:6px 12px;">
                <span class="tr-dot ok live"></span>TICKETS ARE LIVE</span>
              <h2>{e(monitor.movie.title)}</h2>
              <div class="where">{e(target.venue_name)} · {e(target.fmt)} · {e(target.area or monitor.movie.city)}</div>
            </div>
            <div class="detected">
              <div class="tr-eyebrow">Detected at</div>
              <div class="v">{e(fmt_time(ts.since))}</div>
              <div style="font-size:12px;color:{'#3ED598' if ts.notified_at else '#E8B25C'};margin-top:6px;">
                {'✓ Email sent · ' + e(fmt_time(ts.notified_at)) if ts.notified_at else '◷ Email pending'}</div>
            </div>
          </div>
          <div class="split">
            {date_block}
            <div><div class="tr-eyebrow">Showtimes found</div>
              <div class="tr-chips">{chips}</div></div>
          </div>
          <div class="actions">{booked}{keep}</div>
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
    title = "BookMyShow refused the check" if state.is_blocked else "Couldn't check BookMyShow"
    why = (
        "BookMyShow's bot check turned the request away — not a “no tickets” answer."
        if state.is_blocked
        else "This is a connection problem on our side — not a “no tickets” answer."
    )
    html(
        f"""<div class="tr-state error">
          <div class="icon">!</div>
          <div class="h">{e(title)}</div>
          <div class="p">{e(why)}</div>
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
              {thumb(item.get('poster_url', ''), small=True)}
              <div style="min-width:0;">
                <div class="t">{e(item.get('movie', 'Monitor'))}</div>
                <div class="s">{e(targets)}</div>
                <div class="k" style="color:{colour};">{e(label)}{e(stamp)}</div>
              </div>
            </div>"""
        )


__all__ = [
    "AVAILABILITY_UI",
    "FIRST_CHECK_GRACE",
    "describe_date_codes",
    "Phase",
    "active_monitor_card",
    "asset_uri",
    "catalogue_banner",
    "choice_tile",
    "clean_html",
    "e",
    "empty_card",
    "error_card",
    "expired_card",
    "featured_tile",
    "flash",
    "format_panel_head",
    "hero",
    "history_rows",
    "html",
    "interval_tile",
    "live_card",
    "location_tile",
    "logo",
    "monitor_card",
    "next_check_text",
    "phase_for",
    "problem_card",
    "problems_for",
    "platform_selector",
    "poster_tile",
    "rule",
    "scroll_to_top",
    "status_for",
    "status_line",
    "step_header",
    "step_pip",
    "step_strip",
    "stopped_card",
    "summary_row",
    "summary_strip",
    "target_row_markup",
    "target_rows",
    "theatre_row",
    "thumb",
]
