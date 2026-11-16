"""tools/rules_audit.py — the read-only check run before the Phase 5 rules are published.

It applies the rules' monitor checks in Python to every stored monitor, so its
numbers must match ``firestore.rules``; it must never write; and, because the
Actions log is public, it must print nothing but counts and id prefixes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from config import firestore as fs
from config import readonly
from config.timezone import now_ist, to_iso
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget
from tools import rules_audit as ra

RULES = Path("firestore.rules").read_text(encoding="utf-8")
NOW = now_ist()


def stored(status="ACTIVE", *, siblings=0, dates=0, until_days=1, owner="uid-secret-owner",
           email="private.person@example.com", title="A Very Private Watch") -> dict:
    """A monitor document exactly as the app writes it, decoded as the store reads it."""
    m = Monitor(movie=MovieRef(platform="bookmyshow", event_code="ET00000001", title=title,
                               region_code="HYD", region_slug="hyderabad", city="Hyderabad",
                               variants=tuple((f"ET9{i:07d}", "4DX") for i in range(siblings))),
                targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                interval_minutes=10, monitor_until=NOW + timedelta(days=until_days),
                notify_email=email, owner_uid=owner,
                date_codes=[f"202612{i % 28 + 1:02d}" for i in range(dates)])
    if status != "ACTIVE":
        m.stop(NOW)
        if status == "EXPIRED":
            m.expire(NOW)
    return fs.decode(fs.encode(m.to_dict()))


# ── kept in step with firestore.rules ──────────────────────────────────────
def test_the_numbers_match_the_rules():
    assert f"m.variants.size() <= {ra.MAX_VARIANTS}" in RULES
    assert f"requestCost(d) <= {ra.MAX_REQUESTS}" in RULES
    assert f"d.targets.size() <= {ra.MAX_TARGETS}" in RULES
    assert f"d.date_codes.size() <= {ra.MAX_DATE_CODES}" in RULES
    assert f"d.categories.size() <= {ra.MAX_CATEGORIES}" in RULES
    assert f"d.notify_email.size() <= {ra.MAX_EMAIL}" in RULES
    for key, size in ra.MOVIE_SIZES.items():
        assert f"m.{key}.size() <= {size}" in RULES
    for name in ra.MOVIE_FIELDS:
        assert f"'{name}'" in RULES.split("function validMovie")[1].split("}")[0]
    for name in ra.MONITOR_FIELDS:
        assert f"'{name}'" in RULES.split("function monitorFields")[1].split("}")[0]


def test_an_app_written_monitor_passes_every_check():
    for status in ("ACTIVE", "STOPPED", "EXPIRED"):
        doc = stored(status, siblings=12, dates=9)
        assert ra.shape_problems(doc, doc["id"]) == [], status


# ── what it finds ──────────────────────────────────────────────────────────
def test_it_finds_each_category():
    docs = {
        "aaaaaaaa-ok": stored(siblings=3),
        "bbbbbbbb-13": stored(siblings=13),
        "cccccccc-13-stopped": stored("STOPPED", siblings=13),
        "dddddddd-overdue": stored(siblings=13, until_days=-1),
        "eeeeeeee-cost": stored(siblings=2, dates=41),          # 41 x 3 = 123
        "ffffffff-cost-stopped": stored("STOPPED", siblings=2, dates=41),
    }
    docs["gggggggg-movie"] = {**stored(), "movie": {**stored()["movie"], "rating": "U/A"}}
    for d_id, d in docs.items():
        d["id"] = d_id
    result = ra.audit(docs, NOW)
    assert result.checked == 7
    assert sorted(result.found[ra.SHAPE]) == ["bbbbbbbb", "cccccccc", "dddddddd", "gggggggg"]
    assert result.found[ra.OVERDUE] == ["dddddddd"]
    assert result.found[ra.COST] == ["eeeeeeee"]                  # a stopped one costs nothing
    assert sorted(result.found[ra.SIBLINGS]) == ["bbbbbbbb", "dddddddd"]


def test_a_clean_store_is_safe_to_publish_either_way():
    docs = {f"m{i}": stored(siblings=i % 13, dates=1) for i in range(13)}
    for d_id, d in docs.items():
        d["id"] = d_id
    text = ra.report(ra.audit(docs, NOW))
    assert "safe to publish Phase 5 rules as written: yes" in text
    assert "stop/expiry exception: yes" in text


def test_a_blocking_monitor_says_not_yet_for_the_plain_rules():
    doc = stored(siblings=13)
    text = ra.report(ra.audit({doc["id"]: doc}, NOW))
    assert "safe to publish Phase 5 rules as written: NOT YET" in text


# ── public log, read only ──────────────────────────────────────────────────
def test_the_report_carries_no_titles_addresses_or_uids():
    docs = {}
    for i, siblings in enumerate((13, 2, 13)):
        d = stored(siblings=siblings, dates=60 if i == 1 else 0)
        docs[d["id"]] = d
    text = ra.report(ra.audit(docs, NOW))
    for secret in ("A Very Private Watch", "private.person@example.com", "uid-secret-owner", "ET9", "ALLU"):
        assert secret not in text
    for doc_id in docs:
        assert doc_id not in text                                  # only 8-character prefixes
        assert doc_id[:8] in text or ra.SHAPE not in text


def test_main_reads_under_the_write_guard_and_never_commits(monkeypatch, capsys):
    calls = []

    class Client:
        def __init__(self, project, token):
            calls.append(("init", project))

        def query(self, collection, **kw):
            calls.append(("query", collection, readonly.active()))
            return {"m1": stored()}

        def commit(self, writes):                                  # never reached
            raise AssertionError("the audit wrote")

    monkeypatch.setattr(fs, "service_account_status", lambda: ({"client_email": "x"}, ""))
    monkeypatch.setattr(fs, "project_from_env", lambda: "demo-project")
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "t"))
    monkeypatch.setattr(fs, "FirestoreClient", Client)
    assert ra.main() == 0
    assert calls == [("init", "demo-project"), ("query", "monitors", True)]
    assert "monitors checked: 1" in capsys.readouterr().out


def test_a_missing_credential_is_exit_1_and_says_why(monkeypatch, capsys):
    monkeypatch.setattr(fs, "service_account_status", lambda: (None, "FIREBASE_SERVICE_ACCOUNT is not set"))
    assert ra.main() == 1
    assert "FIREBASE_SERVICE_ACCOUNT is not set" in capsys.readouterr().out


def test_an_unreadable_store_is_exit_1_with_the_status_only(monkeypatch, capsys):
    class Client:
        def __init__(self, *a):
            pass

        def query(self, *a, **k):
            raise fs.FirestoreError("denied: some detail", 403)

    monkeypatch.setattr(fs, "service_account_status", lambda: ({"client_email": "x"}, ""))
    monkeypatch.setattr(fs, "project_from_env", lambda: "demo-project")
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "t"))
    monkeypatch.setattr(fs, "FirestoreClient", Client)
    assert ra.main() == 1
    out = capsys.readouterr().out
    assert "HTTP 403" in out and "some detail" not in out


@pytest.mark.parametrize("value, expected", [
    (to_iso(NOW - timedelta(hours=1)), True), (to_iso(NOW + timedelta(hours=1)), False),
    ("not a date", False), (None, False),
])
def test_overdue(value, expected):
    assert ra.overdue({"monitor_until": value}, NOW) is expected
