"""Phase 7 — the owner can always stop, and the app can always expire, a monitor.

``firestore.rules`` lets a verified owner update a monitor that fails a newer
limit (e.g. more than 12 sibling events) only when the update changes nothing
but ``status`` (to STOPPED or EXPIRED), ``stopped_at`` and ``stopped_reason``.
The emulator test (tests/firestore_rules/rules.test.mjs) proves the rule; these
pin the other half: the app's real stop and expiry writes — the whole document,
from ``Monitor.to_dict`` — change those three fields and nothing else, so the
exception covers them, and the rule itself stays that narrow.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from config import firestore as fs
from config.timezone import now_ist
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget

RULES = Path("firestore.rules").read_text(encoding="utf-8")
NOW = now_ist()
ALLOWED = {"status", "stopped_at", "stopped_reason"}


def legacy(siblings: int = 13) -> Monitor:
    """A running monitor saved before the sibling limit, as read back from the store."""
    m = Monitor(movie=MovieRef(platform="bookmyshow", event_code="ET00000001", title="Film",
                               region_code="HYD", region_slug="hyderabad", city="Hyderabad",
                               variants=tuple((f"ET9{i:07d}", "4DX") for i in range(siblings))),
                targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                interval_minutes=10, monitor_until=NOW + timedelta(days=1),
                notify_email="owner@example.com", owner_uid="uid-owner",
                date_codes=["20261201", "20261202"])
    return Monitor.from_dict(fs.decode(fs.encode(m.to_dict())))


def changed(before: dict, after: dict) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def test_stopping_writes_only_the_three_fields_the_rule_allows():
    m = legacy()
    stored = fs.decode(fs.encode(m.to_dict()))
    m.stop(NOW)
    written = fs.decode(fs.encode(m.to_dict()))
    assert changed(stored, written) == ALLOWED
    assert written["status"] == "STOPPED"
    assert len(written["stopped_at"]) <= 40 and len(written["stopped_reason"]) <= 500


def test_expiring_writes_only_the_three_fields_the_rule_allows():
    m = legacy()
    stored = fs.decode(fs.encode(m.to_dict()))
    m.expire(NOW + timedelta(days=2))
    written = fs.decode(fs.encode(m.to_dict()))
    assert changed(stored, written) == ALLOWED
    assert written["status"] == "EXPIRED"
    assert len(written["stopped_at"]) <= 40 and len(written["stopped_reason"]) <= 500


def test_extending_is_not_covered_by_the_exception():
    m = legacy()
    m.stop(NOW)
    stored = fs.decode(fs.encode(m.to_dict()))
    m.extend(24, NOW)
    written = fs.decode(fs.encode(m.to_dict()))
    assert written["status"] == "ACTIVE"
    assert "monitor_until" in changed(stored, written)


def test_the_exception_is_exactly_the_tested_design():
    body = RULES.split("function isStopOrExpiry()")[1].split("\n    }\n")[0]
    assert "request.resource.data.status in ['STOPPED', 'EXPIRED']" in body
    assert ".hasOnly(['status', 'stopped_at', 'stopped_reason'])" in body
    assert "stopped_reason.size() <= 500" in body and "stopped_at.size() <= 40" in body
    monitors = RULES.split("match /monitors/{id} {")[1].split("\n    }\n")[0]
    update = re.search(r"allow update: if (.*?);", monitors, re.S).group(1)
    assert re.sub(r"\s+", " ", update) == (
        "ownsExisting() && ownsIncoming() && (validMonitor(request.resource.data, id) || isStopOrExpiry())")
    # Creation never uses it: a new monitor must pass every check.
    assert "allow create: if ownsIncoming() && validMonitor(request.resource.data, id);" in monitors
    assert RULES.count("isStopOrExpiry()") == 2
