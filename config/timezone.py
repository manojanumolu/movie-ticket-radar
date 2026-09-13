"""Everything in this app is Hyderabad time.

GitHub Actions runners are UTC and Streamlit Cloud is UTC, so any naive
``datetime.now()`` would silently drift 5h30m and expire monitors at the wrong
moment. All timestamps are timezone-aware IST, serialised as ISO-8601.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

try:  # pragma: no cover - exercised implicitly by whichever branch runs
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - no tzdata on a bare runner
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(value: datetime) -> datetime:
    """Attach IST to a naive datetime, convert an aware one."""
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return to_ist(value).isoformat()


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_ist(value)
    try:
        return to_ist(datetime.fromisoformat(value))
    except ValueError:
        return None


# ── display helpers (the design spec's exact formats) ────────────────────
def fmt_time(value: datetime | None) -> str:
    """'10:20 PM'"""
    if value is None:
        return "—"
    return to_ist(value).strftime("%I:%M %p").lstrip("0")


def fmt_datetime(value: datetime | None) -> str:
    """'25 Sep 2026, 11:59 PM'"""
    if value is None:
        return "—"
    v = to_ist(value)
    return f"{v.day} {v.strftime('%b %Y')}, {v.strftime('%I:%M %p').lstrip('0')}"


def fmt_long_date(value: datetime | None) -> str:
    """'25 September 2026'"""
    if value is None:
        return "—"
    v = to_ist(value)
    return f"{v.day} {v.strftime('%B %Y')}"


def fmt_date_code(date_code: str) -> str:
    """'20260925' -> '25 September 2026'. Returns the input if unparseable."""
    try:
        return fmt_long_date(datetime.strptime(date_code, "%Y%m%d").replace(tzinfo=IST))
    except (ValueError, TypeError):
        return date_code or "—"


def fmt_countdown(seconds: float) -> str:
    """'07:42'. Clamped at zero — a negative countdown means 'due now'."""
    total = max(0, int(seconds))
    if total >= 3600:
        return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


__all__ = [
    "IST",
    "fmt_countdown",
    "fmt_date_code",
    "fmt_datetime",
    "fmt_long_date",
    "fmt_time",
    "now_ist",
    "parse_iso",
    "to_ist",
    "to_iso",
]
