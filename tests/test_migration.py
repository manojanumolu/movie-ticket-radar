"""The JSON → Firestore migration: its planner, its validation and its safety.

Everything here runs against a throwaway ``data/`` directory and the rule-
enforcing in-memory Firestore from :mod:`tests.test_isolation`. No real
credentials, no network, and nothing writes to the repository's own data.

Two things are worth stating plainly, because they are the whole point of the
tool: a record's owner is never guessed, and nothing is written unless
``--apply`` was asked for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from config import firestore as fs
from tests.test_isolation import MemoryFirestore, PROJECT
from tools import migrate_firestore
from tools.migration_plan import (
    HISTORY,
    MONITORS,
    STATES,
    USERS,
    build_plan,
    history_doc_id,
    nested_arrays,
    owner_of,
)

UID_A = "uid-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
UID_B = "uid-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _monitor(mid: str, owner: str, *, variants=()) -> dict:
    return {
        "id": mid,
        "owner_uid": owner,
        "status": "STOPPED",
        "interval_minutes": 10,
        "notify_email": "someone@example.com",
        "monitor_until": "2026-09-26T23:59:00+05:30",
        "created_at": "2026-09-16T10:00:00+05:30",
        "date_codes": ["20260925"],
        "movie": {"platform": "bookmyshow", "event_code": f"ET{mid}", "title": "A Film",
                  "city": "Hyderabad", "language": "English", "region_slug": "hyderabad",
                  "variants": [list(v) for v in variants]},
        "targets": [{"venue_code": "ALLU", "venue_name": "ALLU Cinemas",
                     "area": "Kokapet", "fmt": "Any format"}],
    }


def _state(*, links=()) -> dict:
    return {"check_count": 3, "success_count": 3,
            "targets": {"ALLU::Any format": {
                "availability": "AVAILABLE",
                "time_labels": ["03:40 PM"],
                "time_links": [list(p) for p in links],
                "date_codes": ["20260925"]}}}


def _history(mid: str, owner: str = "", *, at="2026-09-16T17:55:22+05:30") -> dict:
    item = {"monitor_id": mid, "kind": "CREATED", "message": "Monitor created.",
            "movie": "A Film", "at": at, "targets": []}
    if owner:
        item["owner_uid"] = owner
    return item


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A miniature of the real situation: two owners, a disposable test
    monitor, an orphan state and history of every provenance."""
    monitors = [
        _monitor("aaaa11112222", UID_A, variants=(("ET1", "IMAX"), ("ET2", "4DX"))),
        _monitor("bbbb33334444", UID_A),
        _monitor("cccc55556666", UID_B),
        _monitor("dddd77778888", ""),                       # the test monitor
    ]
    state = {
        "aaaa11112222": _state(links=(("03:40 PM", "https://in.bookmyshow.com/a"),)),
        "bbbb33334444": _state(),
        "cccc55556666": _state(),
        "dddd77778888": _state(),                           # test monitor's
        "eeee99990000": _state(),                           # orphan: no monitor
    }
    history = [
        _history("aaaa11112222", UID_A),                    # already owned
        _history("cccc55556666", UID_B),                    # already owned
        _history("bbbb33334444"),                           # owner recoverable
        _history("dddd77778888"),                           # test monitor's
        _history("longgonemonitor"),                        # monitor deleted
    ]
    settings = {"users": {UID_A: {"notify_email": "a@example.com", "default_interval": 15},
                          UID_B: {"notify_email": "b@example.com", "default_interval": 10}}}

    data = tmp_path / "data"
    data.mkdir(exist_ok=True)   # conftest's isolated_data made it already
    for name, payload in (("monitors.json", monitors), ("state.json", state),
                          ("history.json", history), ("settings.json", settings)):
        (data / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    monkeypatch.setattr("tools.migration_plan.DATA", data)
    return data


# ──────────────────────────────────────────────────────────────────────────
# The plan
# ──────────────────────────────────────────────────────────────────────────
def test_the_plan_selects_only_owned_records(data_dir):
    plan = build_plan()
    assert plan.ok, plan.problems
    assert plan.counts == {MONITORS: 3, STATES: 3, HISTORY: 3, USERS: 2}
    assert plan.excluded == {"test monitors": 1, "their state": 1, "their history": 1,
                             "history of deleted monitors": 1, "orphan state": 1}
    assert plan.orphan_state == ["eeee99990000"]
    # every reconciliation line adds up against the source files
    assert all(ok for _, _, _, ok in plan.reconciliation)


def test_the_disposable_test_monitor_and_its_records_are_left_behind(data_dir):
    plan = build_plan()
    written = {(c, d) for c, d, _ in plan.writes}
    assert (MONITORS, "dddd77778888") not in written
    assert (STATES, "dddd77778888") not in written
    assert (STATES, "eeee99990000") not in written          # the orphan
    assert not any(r.get("monitor_id") in ("dddd77778888", "longgonemonitor")
                   for c, _, r in plan.writes if c == HISTORY)


def test_a_recoverable_history_owner_comes_from_the_parent_monitor(data_dir):
    """The one record with no owner whose monitor is migrating takes that
    monitor's owner — and only that."""
    plan = build_plan()
    assert plan.history_owner_recovered == 1
    recovered = [r for c, _, r in plan.writes
                 if c == HISTORY and r["monitor_id"] == "bbbb33334444"]
    assert len(recovered) == 1
    assert owner_of(recovered[0]) == UID_A                  # bbbb… belongs to A
    assert all(owner_of(r) for c, _, r in plan.writes)      # nothing unowned migrates


def test_ownership_is_never_invented(data_dir):
    """Every owner in the plan traces to a record or to a parent monitor."""
    plan = build_plan()
    legitimate = set(plan.owner_of_monitor.values())
    assert {owner_of(r) for _, _, r in plan.writes} <= legitimate


def test_ids_are_preserved(data_dir):
    plan = build_plan()
    for collection, doc_id, record in plan.writes:
        if collection == MONITORS:
            assert doc_id == record["id"]
        if collection == STATES:
            assert doc_id in plan.owner_of_monitor
        if collection == USERS:
            assert doc_id == owner_of(record)


def test_history_ids_are_stable_so_a_rerun_cannot_duplicate(data_dir):
    """The live store mints a random suffix for a new entry; a migration that
    may be run twice derives it from the record instead."""
    first = {d for c, d, _ in build_plan().writes if c == HISTORY}
    second = {d for c, d, _ in build_plan().writes if c == HISTORY}
    assert first == second and len(first) == 3
    item = _history("aaaa11112222", UID_A)
    assert history_doc_id(item) == history_doc_id(dict(item))
    assert history_doc_id(_history("aaaa11112222", UID_A, at="2026-09-01T00:00:00+05:30")) not in first


def test_a_state_owner_that_disagrees_with_its_monitor_is_refused(data_dir):
    """The relationship is checked, not assumed."""
    import tools.migration_plan as plan_mod

    real = plan_mod._validate

    def tamper(plan, *args):
        for i, (collection, doc_id, record) in enumerate(plan.writes):
            if collection == STATES:
                plan.writes[i] = (collection, doc_id, {**record, "owner_uid": "uid-somebody-else"})
                break
        real(plan, *args)

    plan_mod._validate = tamper
    try:
        plan = build_plan()
    finally:
        plan_mod._validate = real
    assert not plan.ok
    assert any("owner does not match its monitor" in p for p in plan.problems)


def test_a_nested_array_stops_the_plan(data_dir, monkeypatch):
    """With the pair encoder disabled the data is unstorable, and the planner
    must say so rather than hand it to Firestore."""
    real_encode = fs.encode
    monkeypatch.setattr(fs, "encode",
                        lambda v, field="": real_encode(v, field="__off__"))
    plan = build_plan()
    assert not plan.ok
    assert any("array inside array" in p for p in plan.problems)


def test_no_document_in_the_plan_contains_an_array_inside_an_array(data_dir):
    from tools.migration_plan import encoded

    for collection, doc_id, record in build_plan().writes:
        assert nested_arrays(encoded(record), f"{collection}/{doc_id}.") == []


# ──────────────────────────────────────────────────────────────────────────
# The tool
# ──────────────────────────────────────────────────────────────────────────
def test_without_apply_the_tool_never_writes(data_dir, capsys, monkeypatch):
    """The safety that matters most: no flag, no write — and it does not even
    build a client, so there is no path to one."""
    def explode(*a, **k):
        raise AssertionError("the migration tried to connect without --apply")

    monkeypatch.setattr(migrate_firestore, "connect", explode)
    assert migrate_firestore.main([]) == 0
    assert migrate_firestore.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "NO FIRESTORE WRITES PERFORMED" in out
    assert "re-run with --apply to write" in out


def test_apply_with_a_failing_plan_writes_nothing(data_dir, capsys, monkeypatch):
    """Validation gates the write, so a bad plan never reaches Firestore."""
    def explode(*a, **k):
        raise AssertionError("a failing plan reached Firestore")

    monkeypatch.setattr(migrate_firestore, "connect", explode)
    real_encode = fs.encode
    monkeypatch.setattr(fs, "encode", lambda v, field="": real_encode(v, field="__off__"))
    assert migrate_firestore.main(["--apply"]) == 1
    assert "Nothing was written" in capsys.readouterr().out


def test_apply_writes_every_document_in_one_commit_and_verifies_it(data_dir, capsys, monkeypatch):
    """The whole path, against the rule-enforcing fake: one atomic commit,
    then every document read back and checked."""
    store = MemoryFirestore()
    client = fs.FirestoreClient(PROJECT, lambda: "admin", transport=store)
    monkeypatch.setattr(migrate_firestore, "connect", lambda: client)

    assert migrate_firestore.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "committed 11 document(s) atomically" in out      # 3 + 3 + 3 + 2
    assert "MIGRATION COMPLETE AND VERIFIED." in out

    # exactly the planned documents exist, under their own ids
    assert set(store.docs[MONITORS]) == {"aaaa11112222", "bbbb33334444", "cccc55556666"}
    assert set(store.docs[STATES]) == {"aaaa11112222", "bbbb33334444", "cccc55556666"}
    assert set(store.docs[USERS]) == {UID_A, UID_B}
    assert len(store.docs[HISTORY]) == 3

    # one commit, not one per document
    commits = [c for c in store.calls if c[1] == ":commit"]
    assert len(commits) == 1

    # ownership survived the trip
    assert store.docs[MONITORS]["aaaa11112222"]["owner_uid"] == UID_A
    assert store.docs[STATES]["cccc55556666"]["owner_uid"] == UID_B
    # …and the pair fields came back exactly as the application writes them
    assert store.docs[MONITORS]["aaaa11112222"]["movie"]["variants"] == [["ET1", "IMAX"], ["ET2", "4DX"]]
    links = store.docs[STATES]["aaaa11112222"]["targets"]["ALLU::Any format"]["time_links"]
    assert links == [["03:40 PM", "https://in.bookmyshow.com/a"]]


def test_a_missing_document_fails_post_write_verification(data_dir, capsys, monkeypatch):
    """If the service loses a document, the tool says which one and exits
    non-zero rather than reporting success."""
    store = MemoryFirestore()
    client = fs.FirestoreClient(PROJECT, lambda: "admin", transport=store)

    def connect_then_lose():
        original = client.commit

        def commit_and_drop(writes):
            original(writes)
            store.docs[MONITORS].pop("bbbb33334444", None)      # something goes missing
        client.commit = commit_and_drop
        return client

    monkeypatch.setattr(migrate_firestore, "connect", connect_then_lose)
    assert migrate_firestore.main(["--apply"]) == 1
    out = capsys.readouterr().out
    assert "POST-WRITE VERIFICATION FAILED" in out
    assert "monitors/bbbb33334444" in out


def test_the_migration_never_touches_the_json(data_dir):
    """The tool reads the files; it must not rewrite them."""
    before = {p.name: p.read_bytes() for p in sorted(data_dir.glob("*.json"))}
    store = MemoryFirestore()
    client = fs.FirestoreClient(PROJECT, lambda: "admin", transport=store)
    import tools.migrate_firestore as tool

    original, tool.connect = tool.connect, lambda: client
    try:
        assert tool.main(["--apply"]) == 0
    finally:
        tool.connect = original
    assert {p.name: p.read_bytes() for p in sorted(data_dir.glob("*.json"))} == before


def test_the_real_data_plan_matches_the_signed_off_counts(tmp_path):
    """The repository's own records, against the numbers that were approved.

    The files are read from the last commit rather than the working tree: the
    suite writes to ``data/`` as it runs, and this assertion is about the
    records as committed, not whatever a previous test happened to leave.
    Reads only — no writes, no Firestore.
    """
    import subprocess

    pristine = tmp_path / "pristine"
    pristine.mkdir()
    for name in ("monitors.json", "state.json", "history.json", "settings.json"):
        try:
            blob = subprocess.run(["git", "show", f"HEAD:data/{name}"],
                                  capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            pytest.skip("git is not available to read the committed data")
        (pristine / name).write_bytes(blob)

    plan = build_plan(data_dir=pristine)
    assert plan.ok, plan.problems
    assert plan.counts == {MONITORS: 8, STATES: 8, HISTORY: 17, USERS: 2}
    assert plan.excluded == {"test monitors": 6, "their state": 6, "their history": 12,
                             "history of deleted monitors": 21, "orphan state": 1}
    assert plan.orphan_state == ["080ddf390dde"]
    assert plan.history_owner_recovered == 7
    assert len(plan.writes) == 35
