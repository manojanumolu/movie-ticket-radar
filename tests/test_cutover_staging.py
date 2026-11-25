"""The staged relay-only cutover keeps the app's dispatch contract and runs no engine."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "worker_candidate/cutover/bookmyshow-monitor.yml"
RELAY = ROOT / "worker_candidate/overlay/.github/workflows/monitor-worker-relay.yml"
LIVE = ROOT / ".github/workflows/bookmyshow-monitor.yml"


def load(path):
    doc = yaml.safe_load(path.read_text())
    doc["on"] = doc.pop(True, doc.get("on"))   # YAML 1.1 reads the key `on` as True
    return doc


def test_staged_workflow_keeps_the_apps_dispatch_inputs():
    staged, live = load(STAGED), load(LIVE)
    assert staged["name"] == live["name"] == "BookMyShow monitor"
    assert set(staged["on"]) == {"workflow_dispatch"}
    assert set(staged["on"]["workflow_dispatch"]["inputs"]) == \
        set(live["on"]["workflow_dispatch"]["inputs"]) == {"force", "dry_run", "monitor_id"}


def test_staged_workflow_runs_no_engine_and_holds_no_secret():
    text = STAGED.read_text()
    staged = load(STAGED)
    assert staged["permissions"] == {"contents": "read"}
    for forbidden in ("secrets.", "run_monitor", "schedule:", "push:", "contents: write",
                      "actions: write", "GMAIL", "FIREBASE_SERVICE_ACCOUNT", "environment: production"):
        assert forbidden not in text
    [job] = staged["jobs"].values()
    assert job["uses"] == "./.github/workflows/monitor-worker-relay.yml"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert "inputs.monitor_id != ''" in job["if"] and "!inputs.dry_run" in job["if"]


def test_relay_is_gated_keyless_and_private():
    relay = load(RELAY)
    assert set(relay["on"]) == {"workflow_call"}
    [job] = relay["jobs"].values()
    for gate in ("refs/heads/main", "workflow_dispatch", "RADAR_ENABLE_MONITOR_RELAY == 'true'"):
        assert gate in job["if"]
    assert job["environment"] == "monitor-dispatch"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    text = RELAY.read_text()
    assert "secrets." not in text and "credentials_json" not in text
    assert "token_format: id_token" in text and "--retry 2" in text


def test_catalogue_schedule_is_untouched():
    catalogue = load(ROOT / ".github/workflows/catalogue-sync.yml")
    assert [c["cron"] for c in catalogue["on"]["schedule"]] == ["0 1,7,13,19 * * *"]


def test_staged_isolation_test_differs_only_for_the_cutover():
    staged = (ROOT / "worker_candidate/cutover/test_release_isolation.py").read_text()
    assert "test_relay_cutover_is_exactly_the_staged_files" in staged
    assert '"0 1,7,13,19 * * *"' in staged
