"""Narrowing af1711d4 to two targets, and resuming it, change only what they must."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from tools import narrow_monitor_targets as tool

NOW = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
FIVE = [("ALLU Cinemas", "Dolby Cinema"), ("AMB Cinemas", "HDR By Barco"),
        ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "PXL"),
        ("Prasads Multiplex", "PCX"), ("Prasads Multiplex", "HDR By Barco")]
KEEP = ["ALLU Cinemas · Dolby Cinema", "Prasads Multiplex · PCX"]
ID = "af1711d4c0ffee00"


def doc(status="STOPPED", until=NOW + timedelta(days=10)):
    return {"id": ID, "owner_uid": "owner-uid", "notify_email": "owner@example.com",
            "status": status, "created_at": "2026-11-23T16:44:00+05:30",
            "monitor_until": until.isoformat(), "interval_minutes": 5, "stopped_at": "2026-11-25T10:52:00+05:30",
            "stopped_reason": "Stopped manually.", "date_codes": ["20261217"], "categories": [], "show_time": "",
            "movie": {"event_code": "ET00439706", "title": "Avengers: Doomsday",
                      "variants": [["ET00526287", "a"], ["ET00526306", "b"], ["ET00526320", "c"]]},
            "targets": [{"venue_code": f"V{i}", "venue_name": v, "area": "", "fmt": f} for i, (v, f) in enumerate(FIVE)]}


class Client:
    def __init__(self, *docs):
        self.docs = {d["id"]: deepcopy(d) for d in docs}
        self.writes = []
        self.collections = []

    def query(self, collection):
        assert collection == "monitors"
        return {k: deepcopy(v) for k, v in self.docs.items()}

    def get(self, collection, doc_id):
        return deepcopy(self.docs.get(doc_id))

    def set(self, collection, doc_id, fields):
        self.writes.append(doc_id)
        self.collections.append(collection)
        self.docs[doc_id] = deepcopy(fields)


def test_dry_run_writes_nothing_and_shows_both_sides():
    client = Client(doc())
    lines = tool.run(client, "af1711d4", KEEP, resume=False, apply=False, now=NOW)
    assert client.writes == [] and lines[-1] == "DRY_RUN_OK"
    assert sum(1 for l in lines if l.startswith("target")) == 5 + 2
    assert not any("owner@example.com" in l or "owner-uid" in l for l in lines)


def test_apply_keeps_exactly_the_two_targets_and_nothing_else_changes():
    before = doc()
    client = Client(before)
    assert tool.run(client, "af1711d4", KEEP, resume=False, apply=True, now=NOW)[-1] == "APPLIED_AND_VERIFIED"
    after = client.docs[ID]
    assert [tool.label(t) for t in after["targets"]] == KEEP
    assert {k: v for k, v in after.items() if k != "targets"} == {k: v for k, v in before.items() if k != "targets"}
    assert client.writes == [ID]                                  # the same document, once


@pytest.mark.parametrize("keep,why", [
    (["ALLU Cinemas · Dolby Cinema", "INOX · IMAX"], "not a current target"),
    ([], "name the targets"),
    (["ALLU Cinemas · Dolby Cinema", "ALLU Cinemas · Dolby Cinema"], "named twice"),
])
def test_bad_target_lists_are_refused(keep, why):
    client = Client(doc())
    with pytest.raises(tool.Refused, match=why):
        tool.run(client, "af1711d4", keep, resume=False, apply=True, now=NOW)
    assert client.writes == []


def test_an_active_monitor_is_not_edited():
    client = Client(doc(status="ACTIVE"))
    with pytest.raises(tool.Refused, match="ACTIVE"):
        tool.run(client, "af1711d4", KEEP, resume=False, apply=True, now=NOW)


def test_ambiguous_or_missing_ids_are_refused():
    other = {**doc(), "id": "af1711d4ffffffff"}
    with pytest.raises(tool.Refused, match="exactly one"):
        tool.run(Client(doc(), other), "af1711d4", KEEP, resume=False, apply=True, now=NOW)
    with pytest.raises(tool.Refused, match="only maintains af1711d4"):
        tool.run(Client(doc()), "6150dc3b", KEEP, resume=False, apply=True, now=NOW)
    with pytest.raises(tool.Refused, match="8-character"):
        tool.run(Client(doc()), "af17", KEEP, resume=False, apply=True, now=NOW)


def test_the_other_doomsday_monitor_is_never_touched():
    other = {**doc(), "id": "6150dc3b00000000", "targets": doc()["targets"][:1]}
    client = Client(doc(), other)
    tool.run(client, "af1711d4", KEEP, resume=False, apply=True, now=NOW)
    assert client.docs["6150dc3b00000000"] == other and client.writes == [ID]


def test_resume_keeps_the_end_time_exactly():
    before = doc()
    client = Client(before)
    assert tool.run(client, "af1711d4", [], resume=True, apply=True, now=NOW)[-1] == "APPLIED_AND_VERIFIED"
    after = client.docs[ID]
    assert after["status"] == "ACTIVE"
    assert after["monitor_until"] == before["monitor_until"] and after["created_at"] == before["created_at"]
    assert after["targets"] == before["targets"]


def test_resume_refuses_once_the_end_time_has_passed():
    client = Client(doc(until=NOW - timedelta(minutes=1)))
    with pytest.raises(tool.Refused, match="end time has passed"):
        tool.run(client, "af1711d4", [], resume=True, apply=True, now=NOW)
    assert client.writes == []


def test_a_read_back_mismatch_is_reported():
    class Lossy(Client):
        def get(self, collection, doc_id):
            got = super().get(collection, doc_id)
            if self.writes:
                got["interval_minutes"] = 10
            return got

    with pytest.raises(tool.Refused, match="read-back"):
        tool.run(Lossy(doc()), "af1711d4", KEEP, resume=False, apply=True, now=NOW)


PROTECTED = ["created_at", "monitor_until", "owner_uid", "notify_email", "interval_minutes",
             "movie", "date_codes", "categories", "show_time", "id"]


@pytest.mark.parametrize("resume", [False, True])
def test_only_the_monitors_collection_is_written_and_protected_fields_are_byte_identical(resume):
    before = doc()
    client = Client(before)
    tool.run(client, "af1711d4", [] if resume else KEEP, resume=resume, apply=True, now=NOW)
    assert client.collections == ["monitors"] and client.writes == [ID]   # never history or monitor_state
    after = client.docs[ID]
    for field in PROTECTED:
        assert after[field] == before[field], field


@pytest.mark.parametrize("field", PROTECTED + ["status"])
def test_a_change_to_any_protected_field_is_refused_before_writing(monkeypatch, field):
    """Even if the narrowing logic were wrong, the write gate refuses."""
    client = Client(doc())
    real = tool.narrowed
    monkeypatch.setattr(tool, "narrowed", lambda d, k: {**real(d, k), field: "tampered"})
    with pytest.raises(tool.Refused, match="more than allowed"):
        tool.run(client, "af1711d4", KEEP, resume=False, apply=True, now=NOW)
    assert client.writes == []


def test_unrelated_monitors_cannot_even_be_named():
    for prefix in ("6150dc3b", "00000000", "zzzzzzzzzz"):
        with pytest.raises(tool.Refused, match="only maintains"):
            tool.run(Client(doc()), prefix, KEEP, resume=False, apply=False, now=NOW)
