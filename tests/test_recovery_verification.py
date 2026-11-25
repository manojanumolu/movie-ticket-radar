import json
from copy import deepcopy

import pytest

from tools.export_auth_backup import snapshot
from tools.verify_recovery import main, verify


@pytest.fixture
def original():
    return {
        "project_id": "demo-ticketradar-source",
        "auth": {"users": [{"localId": "private-owner", "email": "private@example.com",
                           "emailVerified": True, "customAttributes": '{"admin":true,"other":"value"}'}]},
        "collections": {
            "users": {"private-owner": {"owner_uid": "private-owner", "avatar_key": "pref"}},
            "monitors": {"private-monitor": {"id": "private-monitor", "owner_uid": "private-owner",
                                            "status": "STOPPED"}},
            "monitor_state": {"private-monitor": {"owner_uid": "private-owner", "targets": {}}},
            "history": {"private-history": {"owner_uid": "private-owner", "monitor_id": "private-monitor"}},
        },
    }


def test_snapshot_comparison_detects_rekeying_and_owner_changes(original):
    restored = deepcopy(original)
    restored["project_id"] = "demo-ticketradar-restored"
    restored["auth"]["users"][0]["customAttributes"] = '{"other":"value","admin":true}'
    assert all(not row["changed"] for row in verify(original, restored).values())
    restored["collections"]["monitors"]["private-monitor"]["owner_uid"] = "someone-else"
    restored["collections"]["users"]["new-key"] = restored["collections"]["users"].pop("private-owner")
    counts = verify(original, restored)
    assert counts["users"]["missing"] == counts["users"]["unexpected"] == 1
    assert counts["monitors"]["changed"] == counts["monitors"]["new_ownership_issues"] == 1
    assert counts["monitor_state"]["new_ownership_issues"] == 1


def test_cli_reports_counts_and_rejects_live_destination(tmp_path, original, capsys):
    source, target = tmp_path / "source.json", tmp_path / "target.json"
    restored = deepcopy(original)
    restored["project_id"] = "demo-ticketradar-recovery"
    restored["collections"]["monitors"]["private-monitor"]["status"] = "ACTIVE"
    source.write_text(json.dumps(original), encoding="utf-8")
    target.write_text(json.dumps(restored), encoding="utf-8")
    argv = ["--source", str(source), "--restored", str(target)]
    assert main(argv) == 1
    assert "private" not in capsys.readouterr().out
    restored["project_id"] = "ticketradar03"
    target.write_text(json.dumps(restored), encoding="utf-8")
    assert main(argv) == 2
    assert "ticketradar03" not in capsys.readouterr().err


def test_emulator_export_validates_project_before_opening_session():
    with pytest.raises(ValueError):
        snapshot("ticketradar03", session=object())


def test_export_uses_local_pagination_and_excludes_credential_material():
    class Reply:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        pages = iter([{"users": [{"localId": "one", "passwordHash": "synthetic"}], "nextPageToken": "p2"},
                      {"users": [{"localId": "two", "emailVerified": True}]}])

        def get(self, url, **kwargs):
            assert url.startswith("http://127.0.0.1:9099/")
            assert kwargs["allow_redirects"] is False and not self.trust_env
            return Reply(next(self.pages))

        def request(self, method, url, **kwargs):
            assert method == "POST" and url.startswith("http://127.0.0.1:8080/")
            return Reply([])

    data = snapshot("demo-ticketradar-source", session=Session())
    assert data["auth"]["users"] == [{"localId": "one"}, {"localId": "two", "emailVerified": True}]
    assert set(data["collections"]) == {"users", "monitors", "monitor_state", "history"}
