"""Gmail SMTP notifications.

Same transport as the job-tracker (``smtplib`` + an app password over
implicit TLS on 465), because it is dependency-free and already proven in
this account. Credentials come from the environment only — ``GMAIL_ADDRESS``
and ``GMAIL_APP_PASSWORD`` — and are never written to a file, logged, or
included in an error message.

The HTML is a flattened version of the TicketRadar "tickets are live" card.
Mail clients strip most CSS, so everything is inline, table-based, and falls
back to a readable plain-text part.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from config.timezone import fmt_date_code, fmt_datetime, fmt_time, now_ist
from monitor.changes import Change, ChangeKind
from monitor.models import Monitor, describe_date_codes

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT = 30
SENDER_NAME = "Movie Ticket Radar"

# Design tokens, flattened for email clients (DESIGN-SPEC.md §2).
INK = "#08080A"
SURFACE = "#101014"
SUNKEN = "#0A0A0D"
TEXT = "#F2F2F4"
TEXT_2 = "#B0B0BA"
TEXT_3 = "#8E8E98"
ACCENT = "#FF3355"
SUCCESS = "#3ED598"
HAIRLINE = "#22222A"


class NotificationError(RuntimeError):
    """Sending failed. Raised so the caller retries instead of marking as sent."""


# ──────────────────────────────────────────────────────────────────────────
# Credentials
# ──────────────────────────────────────────────────────────────────────────
def credentials() -> tuple[str, str]:
    """Read Gmail credentials from the environment.

    Falls back to Streamlit secrets so "Send test email" works from the UI,
    but there is no file-based or hard-coded path — if neither is configured
    this raises rather than silently not sending.
    """
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not (address and password):
        try:
            import streamlit as st

            address = address or str(st.secrets.get("GMAIL_ADDRESS", "")).strip()
            password = password or str(st.secrets.get("GMAIL_APP_PASSWORD", "")).strip()
        except Exception:
            pass

    if not address or not password:
        raise NotificationError(
            "Email is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "(GitHub Actions secrets for the worker, Streamlit secrets for the app)."
        )
    return address, password


def is_configured() -> bool:
    try:
        credentials()
        return True
    except NotificationError:
        return False


# ──────────────────────────────────────────────────────────────────────────
# Transport
# ──────────────────────────────────────────────────────────────────────────
def send_email(to: str, subject: str, html: str, text: str) -> None:
    to = (to or "").strip()
    if not to:
        raise NotificationError("No recipient address configured for this monitor.")

    address, password = credentials()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, address))
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
            server.login(address, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        # Never echo the exception body — it can contain the credential.
        raise NotificationError(
            "Gmail rejected the login. Check GMAIL_ADDRESS and that "
            "GMAIL_APP_PASSWORD is a 16-character app password."
        ) from None
    except (smtplib.SMTPException, OSError) as exc:
        raise NotificationError(f"Could not send mail: {type(exc).__name__}") from None

    print(f"[email] sent '{subject}' to {_mask(to)}")


def _mask(address: str) -> str:
    """'manoj@gmail.com' -> 'm***@gmail.com' — safe for CI logs."""
    if "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


# ──────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────
def send_change_email(monitor: Monitor, change: Change) -> None:
    """The notification the whole app exists to deliver."""
    subject, html, text = render_change(monitor, change)
    send_email(monitor.notify_email, subject, html, text)


def render_change(monitor: Monitor, change: Change) -> tuple[str, str, str]:
    """Build (subject, html, text). Separated from sending so tests can
    assert on the content without a network or a credential."""
    live = change.kind is ChangeKind.TICKETS_LIVE
    detected = change.detected_at or now_ist()
    # The change is the self-contained payload; the monitor is only a fallback.
    title = change.movie_title or monitor.movie.title
    # Every show date that is actually bookable, not just the first one.
    dates = list(change.date_codes) or ([change.date_code] if change.date_code else [])
    date_label = " · ".join(fmt_date_code(d) for d in dates)
    times = change.new_time_labels if not live and change.new_time_labels else change.time_labels

    subject = (
        f"TICKETS ARE LIVE — {title} at {change.venue_name}"
        if live
        else f"New showtime — {title} at {change.venue_name}"
    )

    eyebrow = "TICKETS ARE LIVE" if live else "NEW SHOWTIME"
    lede = (
        "Booking just opened for the theatre and format you're watching."
        if live
        else "A showtime that wasn't there on the last check has appeared."
    )
    # Every showtime chip links somewhere real: the show-level link when the
    # platform published one, else the date's booking page. Anything that is
    # not a BookMyShow https URL is dropped rather than rendered.
    links = {label: url for label, url in change.time_links if _safe_url(url)}
    booking_url = change.booking_url if _safe_url(change.booking_url) else ""
    for label in times:
        links.setdefault(label, booking_url)

    html = _html(
        eyebrow=eyebrow,
        title=title,
        lede=lede,
        venue=change.venue_name,
        fmt=change.fmt,
        city=monitor.movie.city,
        date_label=date_label,
        times=times,
        links=links,
        detected=detected,
        booking_url=booking_url,
        monitor=monitor,
    )
    text = _text(
        eyebrow=eyebrow,
        title=title,
        lede=lede,
        venue=change.venue_name,
        fmt=change.fmt,
        city=monitor.movie.city,
        date_label=date_label,
        times=times,
        links=links,
        detected=detected,
        booking_url=booking_url,
        monitor=monitor,
    )
    return subject, html, text


def _safe_url(url: str) -> bool:
    """The only links that go into mail are the platform's own https pages."""
    from platforms.bookmyshow import is_bookmyshow_url

    return is_bookmyshow_url(url or "")


def _time_chips(times: list[str], links: dict[str, str] | None = None) -> str:
    """Showtime chips. Each one is an ``<a>`` when it has a verified link."""
    links = links or {}
    if not times:
        return (
            f'<div style="font-size:14px;color:{TEXT_3};">'
            "Showtimes weren't listed individually — open BookMyShow for the full list.</div>"
        )
    chip_style = (
        f"display:block;padding:9px 14px;border-radius:9px;background:#17171C;"
        f"border:1px solid {HAIRLINE};font-family:'Manrope','Segoe UI',Helvetica,Arial,sans-serif;"
        f"font-size:14px;font-weight:700;color:{TEXT};white-space:nowrap;text-decoration:none;"
    )
    cells = []
    for t in times[:12]:
        url = links.get(t, "")
        if url:
            chip = (
                f'<a href="{escape(url, quote=True)}" style="{chip_style}'
                f'border-color:rgba(62,213,152,.45);">{escape(t)} &#8599;</a>'
            )
        else:
            chip = f'<div style="{chip_style}">{escape(t)}</div>'
        cells.append(f'<td style="padding:0 8px 8px 0;">{chip}</td>')
    cells = "".join(cells)
    extra = (
        f'<td style="padding:0 0 8px 0;font-size:13px;color:{TEXT_3};">'
        f"+{len(times) - 12} more</td>"
        if len(times) > 12
        else ""
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>{cells}{extra}</tr></table>'


def _html(*, eyebrow: str, title: str, lede: str, venue: str, fmt: str, city: str,
          date_label: str, times: list[str], detected: datetime, booking_url: str,
          monitor: Monitor, links: dict[str, str] | None = None) -> str:
    """The alert as a premium cinema ticket-stub.

    Tables and inline styles only — Gmail strips <style> blocks and ignores
    flex/grid — and every colour is a plain hex so dark and light clients
    both render it. The poster is shown when the catalogue has an https one;
    it is never a link, so the only anchors in the mail are the verified
    showtime chips and the BOOK button.
    """
    live = eyebrow == "TICKETS ARE LIVE"
    accent = SUCCESS if live else ACCENT
    ink_on_accent = "#04120C" if live else "#FFFFFF"
    font = "'Manrope','Segoe UI',Helvetica,Arial,sans-serif"
    mono = "'DM Mono',Consolas,'Courier New',monospace"

    poster = (monitor.movie.poster_url or "").strip()
    poster_cell = (
        f'<td width="112" valign="top" style="padding:0 18px 0 0;">'
        f'<img src="{escape(poster, quote=True)}" width="112" height="160" alt="" '
        f'style="display:block;width:112px;height:160px;border-radius:12px;border:1px solid {HAIRLINE};'
        f'object-fit:cover;background:#16161C;"></td>'
        if poster.startswith("https://") else ""
    )

    button = ""
    if booking_url:
        # Only rendered when we have a real, derived URL — never a guess. It
        # is the theatre's own page when the checker had one.
        button = (
            f'<tr><td style="padding:24px 30px 4px;">'
            f'<a href="{escape(booking_url, quote=True)}" '
            f'style="display:block;padding:18px;border-radius:14px;background:{accent};'
            f'color:{ink_on_accent};font-family:{font};font-size:16px;font-weight:800;letter-spacing:.06em;'
            f'text-align:center;text-decoration:none;">BOOK ON BOOKMYSHOW &#8599;</a>'
            f'<div style="font-family:{font};font-size:12px;color:{TEXT_3};text-align:center;margin-top:10px;">'
            f"Opens {escape(venue)} on BookMyShow.</div>"
            f"</td></tr>"
        )

    date_block = (
        f'<td valign="top" style="padding:0 24px 0 0;">'
        f'<div style="font-family:{mono};font-size:10px;letter-spacing:.16em;text-transform:uppercase;'
        f'color:{TEXT_3};">{"Dates" if " · " in date_label else "Date"}</div>'
        f'<div style="font-family:{font};font-size:19px;font-weight:800;color:{TEXT};margin-top:6px;'
        f'letter-spacing:-.01em;line-height:1.3;{"white-space:nowrap;" if " · " not in date_label else ""}">'
        f'{escape(date_label)}</div></td>'
        if date_label
        else ""
    )
    watched = describe_date_codes(monitor.date_codes)
    watched_line = f"Watching shows on {escape(watched)} only.<br>" if watched else ""

    dot = (
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:8px;background:{SUCCESS};'
        f'vertical-align:middle;margin-right:8px;"></span>' if live else ""
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"></head>
<body style="margin:0;padding:24px 12px;background:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;
  background:{SURFACE};border:1px solid {HAIRLINE};border-radius:20px;overflow:hidden;font-family:{font};">
  <tr><td style="padding:22px 30px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{font};font-size:15px;font-weight:800;color:{TEXT};letter-spacing:-.02em;">
        Ticket<span style="color:{ACCENT};">Radar</span></td>
      <td align="right" style="font-family:{mono};font-size:10px;letter-spacing:.16em;color:{TEXT_3};text-transform:uppercase;">
        {escape(city)}</td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:22px 30px 0;">
    <div style="display:inline-block;padding:7px 14px;border-radius:999px;
      background:{'#0F2A1F' if live else '#2A0D14'};border:1px solid {accent};font-family:{mono};font-size:11px;
      letter-spacing:.2em;color:{accent};">{dot}{escape(eyebrow)}</div>
  </td></tr>
  <tr><td style="padding:20px 30px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      {poster_cell}
      <td valign="top">
        <div style="font-family:{font};font-size:30px;font-weight:800;letter-spacing:-.03em;color:{TEXT};line-height:1.12;">{escape(title)}</div>
        <div style="font-family:{font};font-size:15px;font-weight:600;color:{TEXT_2};margin-top:12px;line-height:1.5;">
          {escape(venue)}<br>
          <span style="color:{TEXT_3};font-weight:500;">{escape(fmt)} &middot; {escape(city)}</span></div>
        <div style="font-family:{font};font-size:13.5px;color:{TEXT_3};margin-top:12px;line-height:1.55;">{escape(lede)}</div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:22px 30px 0;"><div style="height:1px;background:{HAIRLINE};"></div></td></tr>
  <tr><td style="padding:20px 30px 4px;">
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      {date_block}
      <td valign="top">
        <div style="font-family:{mono};font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:{TEXT_3};">Showtimes</div>
        <div style="margin-top:8px;">{_time_chips(times, links)}</div>
        {_chip_hint(times, links)}
      </td>
    </tr></table>
  </td></tr>
  {button}
  <tr><td style="padding:16px 30px 24px;">
    <div style="font-family:{font};font-size:12.5px;color:{TEXT_3};line-height:1.7;">
      Detected at {escape(fmt_time(detected))} IST.<br>
      {watched_line}The monitor keeps running for your other theatres until
      {escape(fmt_datetime(monitor.monitor_until))} IST.
    </div>
  </td></tr>
  <tr><td style="padding:14px 30px;background:{SUNKEN};border-top:1px solid {HAIRLINE};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{mono};font-size:10.5px;color:#5A5A64;letter-spacing:.14em;">TICKETRADAR &middot; {escape(city.upper())}</td>
      <td align="right" style="font-family:{font};font-size:11px;color:#5A5A64;">Be first in line.</td>
    </tr></table>
  </td></tr>
</table>
</body></html>"""


def _chip_hint(times: list[str], links: dict[str, str] | None) -> str:
    """Say what a showtime click does, so a date page never surprises anyone."""
    links = links or {}
    if not times or not any(links.get(t) for t in times):
        return ""
    return (
        f'<div style="font-size:11.5px;color:{TEXT_3};margin-top:4px;line-height:1.5;">'
        "Tap a showtime to open it on BookMyShow.</div>"
    )


def _text(*, eyebrow: str, title: str, lede: str, venue: str, fmt: str, city: str,
          date_label: str, times: list[str], detected: datetime, booking_url: str,
          monitor: Monitor, links: dict[str, str] | None = None) -> str:
    links = links or {}
    lines = [eyebrow, "", title, f"{venue} · {fmt} · {city}", "", lede, ""]
    if date_label:
        lines += [f"{'Dates' if ' · ' in date_label else 'Date'}: {date_label}"]
    watched = describe_date_codes(monitor.date_codes)
    if watched:
        lines += [f"Watching shows on: {watched}"]
    if times:
        lines += ["Showtimes:"]
        for t in times:
            lines += [f"  {t}" + (f"  {links[t]}" if links.get(t) else "")]
    else:
        lines += ["Showtimes: see BookMyShow"]
    lines += [""]
    if booking_url:
        lines += [f"Book: {booking_url}", ""]
    lines += [
        f"Detected at {fmt_time(detected)} IST.",
        f"Monitoring continues until {fmt_datetime(monitor.monitor_until)} IST.",
        "",
        "— Movie Ticket Radar",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Test mail (Settings page / `python -m notifications.email`)
# ──────────────────────────────────────────────────────────────────────────
def send_test_email(to: str) -> None:
    now = now_ist()
    html = f"""<!doctype html><html><body style="margin:0;padding:28px;background:{INK};
  font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:{TEXT};">
  <div style="max-width:520px;margin:0 auto;background:{SURFACE};border:1px solid {HAIRLINE};
    border-radius:18px;padding:28px;">
    <div style="font-size:11px;letter-spacing:.2em;color:{ACCENT};font-family:Consolas,monospace;">TEST</div>
    <div style="font-size:24px;font-weight:800;letter-spacing:-.02em;margin-top:14px;">Email is working</div>
    <div style="font-size:14px;color:{TEXT_3};margin-top:10px;line-height:1.6;">
      Movie Ticket Radar can reach your inbox. Real alerts only arrive when a theatre
      you're watching actually becomes bookable — not on every check.
    </div>
    <div style="font-size:12px;color:#5A5A64;margin-top:22px;font-family:Consolas,monospace;">
      {escape(fmt_datetime(now))}</div>
  </div></body></html>"""
    text = (
        "Email is working.\n\n"
        "Movie Ticket Radar can reach your inbox. Real alerts only arrive when a "
        "theatre you're watching actually becomes bookable.\n\n"
        f"{fmt_datetime(now)}\n"
    )
    send_email(to, "Movie Ticket Radar — test email", html, text)


if __name__ == "__main__":  # pragma: no cover
    import sys

    recipient = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GMAIL_ADDRESS", "")
    send_test_email(recipient)


__all__ = [
    "NotificationError",
    "credentials",
    "is_configured",
    "render_change",
    "send_change_email",
    "send_email",
    "send_test_email",
]
