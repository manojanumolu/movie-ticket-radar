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
from monitor.models import Monitor

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
    date_label = fmt_date_code(change.date_code) if change.date_code else ""
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
        f"display:block;padding:9px 15px;border-radius:9px;background:#17171C;"
        f"border:1px solid {HAIRLINE};font-size:15px;font-weight:600;color:{TEXT};"
        f"white-space:nowrap;text-decoration:none;"
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
    accent = SUCCESS if eyebrow == "TICKETS ARE LIVE" else ACCENT
    ink_on_accent = "#04120C" if eyebrow == "TICKETS ARE LIVE" else "#FFFFFF"

    button = ""
    if booking_url:
        # Only rendered when we have a real, derived URL — never a guess.
        button = (
            f'<tr><td style="padding:26px 30px 4px;">'
            f'<a href="{escape(booking_url, quote=True)}" '
            f'style="display:block;padding:17px;border-radius:13px;background:{accent};'
            f'color:{ink_on_accent};font-size:16px;font-weight:700;letter-spacing:.04em;'
            f'text-align:center;text-decoration:none;">BOOK ON BOOKMYSHOW &#8599;</a>'
            f"</td></tr>"
        )

    date_row = (
        f'<tr><td style="padding:0 30px 18px;">'
        f'<div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;'
        f'color:#6E6E7A;font-family:Consolas,monospace;">Date</div>'
        f'<div style="font-size:19px;font-weight:700;color:{TEXT};margin-top:6px;">'
        f"{escape(date_label)}</div></td></tr>"
        if date_label
        else ""
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px 12px;background:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;
  background:{SURFACE};border:1px solid {HAIRLINE};border-radius:18px;overflow:hidden;
  font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <tr><td style="padding:30px 30px 0;">
    <div style="display:inline-block;padding:6px 12px;border-radius:999px;
      background:rgba(62,213,152,.14);border:1px solid {accent};font-size:11px;
      letter-spacing:.2em;color:{accent};font-family:Consolas,monospace;">{escape(eyebrow)}</div>
    <div style="font-size:30px;font-weight:800;letter-spacing:-.03em;color:{TEXT};
      margin-top:18px;line-height:1.12;">{escape(title)}</div>
    <div style="font-size:15px;color:{TEXT_2};margin-top:10px;">{escape(venue)} &middot; {escape(fmt)} &middot; {escape(city)}</div>
    <div style="font-size:13.5px;color:{TEXT_3};margin-top:8px;line-height:1.55;">{escape(lede)}</div>
  </td></tr>
  <tr><td style="padding:22px 30px 0;"><div style="height:1px;background:{HAIRLINE};"></div></td></tr>
  <tr><td style="height:22px;"></td></tr>
  {date_row}
  <tr><td style="padding:0 30px 4px;">
    <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#6E6E7A;
      font-family:Consolas,monospace;">Showtimes</div>
    <div style="margin-top:10px;">{_time_chips(times, links)}</div>
    {_chip_hint(times, links)}
  </td></tr>
  {button}
  <tr><td style="padding:18px 30px 26px;">
    <div style="font-size:12.5px;color:{TEXT_3};line-height:1.7;">
      Detected at {escape(fmt_time(detected))} IST.<br>
      The monitor keeps running for your other theatres until
      {escape(fmt_datetime(monitor.monitor_until))} IST.
    </div>
  </td></tr>
  <tr><td style="padding:16px 30px;background:{SUNKEN};border-top:1px solid {HAIRLINE};">
    <div style="font-size:11px;color:#5A5A64;font-family:Consolas,monospace;letter-spacing:.1em;">
      MOVIE TICKET RADAR &middot; {escape(monitor.movie.city.upper())}
    </div>
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
        lines += [f"Date: {date_label}"]
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
