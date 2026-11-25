"""Fenced checks print each target's verdict (private Cloud Run logs)."""
from datetime import datetime, timezone

from monitor import checker
from monitor.models import Availability, CheckOutcome, Monitor, MovieRef, TargetResult, TheatreTarget


def test_each_target_is_logged_without_the_address(capsys):
    at = datetime(2026, 11, 26, tzinfo=timezone.utc)
    targets = [TheatreTarget("AMB", "AMB Cinemas", fmt="HDR By Barco"),
               TheatreTarget("PRA", "Prasads Multiplex", fmt="PCX")]
    monitor = Monitor(MovieRef("bookmyshow", "ET00439706", "Avengers: Doomsday", "HYD", "hyderabad"),
                      targets, id="af1711d4fixture", owner_uid="owner", notify_email="owner@example.com",
                      created_at=at, monitor_until=at)
    results = [TargetResult(t.key, t.venue_name, t.fmt, a)
               for t, a in zip(targets, (Availability.AVAILABLE, Availability.SHOW_NOT_AVAILABLE))]
    checker._log_fenced_check(monitor, CheckOutcome(monitor.id, at, True, results), [])
    out = capsys.readouterr().out
    assert "[checker] af1711d4 — Avengers: Doomsday (2 target(s))" in out
    assert "AMB Cinemas · HDR By Barco -> AVAILABLE" in out
    assert "Prasads Multiplex · PCX -> SHOW_NOT_AVAILABLE" in out
    assert "owner@example.com" not in out and "af1711d4fixture" not in out


def test_a_failed_check_is_logged(capsys):
    at = datetime(2026, 11, 26, tzinfo=timezone.utc)
    monitor = Monitor(MovieRef("bookmyshow", "ET1", "Film", "HYD", "hyderabad"),
                      [TheatreTarget("A", "A", fmt="2D")], id="abcdefgh12", owner_uid="o",
                      notify_email="o@example.com", created_at=at, monitor_until=at)
    checker._log_fenced_check(monitor, CheckOutcome(monitor.id, at, False, [], error="refused (HTTP 403)"), [])
    assert "check failed: refused (HTTP 403)" in capsys.readouterr().out
