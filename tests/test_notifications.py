"""Email rendering, credential hygiene and the catalogue."""

from __future__ import annotations

import pytest

from config.timezone import IST, fmt_countdown, fmt_date_code, fmt_datetime, parse_iso, to_iso
from monitor.changes import Change, ChangeKind
from monitor.models import Availability
from notifications import email as mail


@pytest.fixture
def change(make_monitor, at):
    monitor = make_monitor()
    return monitor, Change(
        kind=ChangeKind.TICKETS_LIVE,
        monitor_id=monitor.id,
        target_key="ALLU::Dolby Cinema",
        venue_name="Allu Cinemas",
        fmt="Dolby Cinema",
        movie_title="Avengers: Endgame Encore",
        previous=Availability.SOLD_OUT,
        current=Availability.AVAILABLE,
        date_code="20260925",
        booking_url="https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925",
        time_labels=["07:30 PM", "09:45 PM"],
        detected_at=at,
    )


# ──────────────────────────────────────────────────────────────────────────
# Rendering  (Test 12)
# ──────────────────────────────────────────────────────────────────────────
def test_live_email_contains_everything_worth_knowing(change):
    monitor, ch = change
    subject, html, text = mail.render_change(monitor, ch)

    assert subject == "TICKETS ARE LIVE — Avengers: Endgame Encore at Allu Cinemas"
    for needle in ("TICKETS ARE LIVE", "Avengers: Endgame Encore", "Allu Cinemas",
                   "Dolby Cinema", "25 September 2026", "07:30 PM", "09:45 PM",
                   ch.booking_url, "BOOK ON BOOKMYSHOW"):
        assert needle in html, needle
    assert "10:20 PM" in html  # detected at
    assert "26 Sep 2026, 11:59 PM" in html  # monitor keeps running until

    assert "Avengers: Endgame Encore" in text
    assert ch.booking_url in text


def test_new_showtime_email_leads_with_the_new_times(change):
    monitor, ch = change
    ch.kind = ChangeKind.NEW_SHOWTIME
    ch.new_time_labels = ["11:15 PM"]

    subject, html, _ = mail.render_change(monitor, ch)
    assert subject.startswith("New showtime")
    assert "NEW SHOWTIME" in html
    assert "11:15 PM" in html


def test_no_booking_button_when_there_is_no_real_url(change):
    monitor, ch = change
    ch.booking_url = ""
    _, html, text = mail.render_change(monitor, ch)
    assert "BOOK ON BOOKMYSHOW" not in html
    assert "Book:" not in text


def test_titles_are_escaped(change):
    monitor, ch = change
    ch.movie_title = 'Evil <script>alert("x")</script>'
    _, html, _ = mail.render_change(monitor, ch)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_long_showtime_list_is_truncated(change):
    monitor, ch = change
    ch.time_labels = [f"{h}:00 PM" for h in range(1, 16)]
    _, html, _ = mail.render_change(monitor, ch)
    assert "more" in html


# ──────────────────────────────────────────────────────────────────────────
# Credentials  (Test 12 — nothing is exposed)
# ──────────────────────────────────────────────────────────────────────────
def test_missing_credentials_raise_rather_than_silently_not_sending(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    with pytest.raises(mail.NotificationError):
        mail.credentials()
    assert mail.is_configured() is False


def test_credentials_come_from_the_environment_only(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    assert mail.credentials() == ("me@gmail.com", "abcd efgh ijkl mnop")


def test_rendered_email_never_contains_the_password(change, monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "supersecretpw123")
    monitor, ch = change
    _, html, text = mail.render_change(monitor, ch)
    assert "supersecretpw123" not in html + text


def test_addresses_are_masked_in_logs():
    assert mail._mask("manoj@gmail.com") == "m***@gmail.com"
    assert mail._mask("nonsense") == "***"


def test_send_without_a_recipient_raises(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    with pytest.raises(mail.NotificationError):
        mail.send_email("", "subject", "<p>x</p>", "x")


def test_auth_failure_message_does_not_leak_the_credential(monkeypatch):
    import smtplib

    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "supersecretpw123")

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            raise smtplib.SMTPAuthenticationError(535, b"Bad credentials supersecretpw123")

        def send_message(self, *a):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: Boom())
    with pytest.raises(mail.NotificationError) as exc:
        mail.send_email("you@example.com", "s", "<p>h</p>", "t")
    assert "supersecretpw123" not in str(exc.value)


# ──────────────────────────────────────────────────────────────────────────
# Time formatting
# ──────────────────────────────────────────────────────────────────────────
def test_display_formats(at):
    assert fmt_datetime(at) == "25 Sep 2026, 10:20 PM"
    assert fmt_date_code("20260925") == "25 September 2026"
    assert fmt_date_code("garbage") == "garbage"
    assert fmt_countdown(462) == "07:42"
    assert fmt_countdown(-5) == "00:00"


def test_iso_round_trip_keeps_ist(at):
    assert parse_iso(to_iso(at)) == at


def test_naive_timestamps_are_read_as_ist():
    from datetime import datetime

    assert parse_iso("2026-09-25T22:20:00").tzinfo is not None
    assert parse_iso("2026-09-25T22:20:00").utcoffset().total_seconds() == 5.5 * 3600
    assert parse_iso("nonsense") is None
    assert parse_iso(None) is None


# ──────────────────────────────────────────────────────────────────────────
# Catalogue
# ──────────────────────────────────────────────────────────────────────────
def test_catalogue_stores_and_finds_a_resolved_movie(provider_factory, listing_url, monkeypatch):
    from monitor import catalogue
    from tests.conftest import ALLU_LIVE, build_payload

    provider = provider_factory([build_payload(ALLU_LIVE)])
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)

    entry = catalogue.resolve_url(listing_url, mirror=False)
    movie = catalogue.movie_from_entry(entry)

    assert movie.id == "bookmyshow:ET00478890"
    assert catalogue.find_entry(movie.id) is not None
    # Venues come back sorted case-insensitively by name.
    assert [v.name for v in catalogue.venues_from_entry(entry)] == ["Allu Cinemas", "AMB Cinemas"]
    assert catalogue.search_entries("avengers")
    assert catalogue.search_entries("nothing-like-this") == []

    catalogue.remove_entry(movie.id, mirror=False)
    assert catalogue.find_entry(movie.id) is None


def test_a_failed_resolve_writes_nothing(provider_factory, listing_url, monkeypatch):
    from monitor import catalogue
    from platforms.base import PlatformBlocked
    from tests.conftest import FakeResponse

    provider = provider_factory([FakeResponse(403)])
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)

    with pytest.raises(PlatformBlocked):
        catalogue.resolve_url(listing_url, mirror=False)
    assert catalogue.list_entries() == []


# ──────────────────────────────────────────────────────────────────────────
# Clickable showtimes — real links only (Tests 13–15)
# ──────────────────────────────────────────────────────────────────────────
DATE_PAGE = "https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00478890/20260925"


def test_showtimes_are_rendered_as_links(change):
    monitor, ch = change
    ch.time_links = [["07:30 PM", DATE_PAGE], ["09:45 PM", DATE_PAGE]]
    _, html, text = mail.render_change(monitor, ch)
    assert html.count(f'<a href="{DATE_PAGE}"') >= 3          # two chips + the button
    assert "07:30 PM &#8599;</a>" in html and "09:45 PM &#8599;</a>" in html
    assert "Tap a showtime to open it on BookMyShow" in html
    assert f"  07:30 PM  {DATE_PAGE}" in text                 # plain-text part links too
    assert 'href="' + DATE_PAGE + '"' in html and "BOOK ON BOOKMYSHOW" in html


def test_a_show_level_link_from_the_platform_is_used_when_present(change):
    monitor, ch = change
    show_page = "https://in.bookmyshow.com/buytickets/x-hyderabad/movie-hyd-ET00478890-MT/20260925?sid=118452"
    ch.time_links = [["07:30 PM", show_page], ["09:45 PM", DATE_PAGE]]
    _, html, text = mail.render_change(monitor, ch)
    assert f'<a href="{show_page}"' in html
    assert f"  07:30 PM  {show_page}" in text


def test_showtimes_without_their_own_link_fall_back_to_the_booking_page(change):
    monitor, ch = change
    ch.time_links = []                     # older state files have none
    _, html, _ = mail.render_change(monitor, ch)
    assert html.count(f'<a href="{ch.booking_url}"') == 3     # 2 chips + button


def test_fabricated_or_foreign_links_are_never_emitted(change):
    monitor, ch = change
    ch.time_links = [
        ["07:30 PM", "javascript:alert(1)"],
        ["09:45 PM", "https://evil.example.com/bookmyshow.com/x"],
    ]
    ch.booking_url = "http://in.bookmyshow.com/insecure"      # not https
    _, html, text = mail.render_change(monitor, ch)
    assert "javascript:" not in html and "evil.example.com" not in html
    assert "insecure" not in html and "BOOK ON BOOKMYSHOW" not in html
    assert "<a " not in html                                   # nothing left to link
    assert "07:30 PM" in html and "09:45 PM" in html           # still listed, just not linked
    assert "Book:" not in text


def test_url_validation_accepts_only_bookmyshow_https():
    from platforms.bookmyshow import is_bookmyshow_url

    assert is_bookmyshow_url("https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET1/20260925")
    assert is_bookmyshow_url("https://bookmyshow.com/x")
    assert not is_bookmyshow_url("http://in.bookmyshow.com/x")
    assert not is_bookmyshow_url("https://in.bookmyshow.com.evil.io/x")
    assert not is_bookmyshow_url("https://notbookmyshow.com/x")
    assert not is_bookmyshow_url("javascript:alert(1)")
    assert not is_bookmyshow_url("")


def test_book_button_opens_the_theatre_page(change):
    """The email's BOOK ON BOOKMYSHOW lands on the monitored theatre."""
    from dataclasses import replace

    monitor, ch = change
    theatre = "https://in.bookmyshow.com/buytickets/avengers-endgame-hyderabad/cinema-hyde-ALLU-MT/20260925"
    _, html, text = mail.render_change(monitor, replace(ch, booking_url=theatre))
    assert f'href="{theatre}"' in html and "BOOK ON BOOKMYSHOW" in html
    assert theatre in text
