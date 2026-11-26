"""The five-minute single-pass monitor: one pass per run, no overlap, no
refusal bypass, and the city-listing throttle kept between runs."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import subprocess

from monitor import discovery

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def load(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


# ── the workflow ──────────────────────────────────────────────────────────
def test_one_job_one_pass_and_a_hard_stop_inside_five_minutes():
    wf = load("bookmyshow-monitor.yml")
    assert wf["on"]["schedule"] == [{"cron": "*/5 * * * *"}]
    assert set(wf["on"]["workflow_dispatch"]["inputs"]) == {"force", "dry_run", "monitor_id"}
    assert wf["concurrency"] == {"group": "bookmyshow-monitor", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    [job] = wf["jobs"].values()
    assert job["timeout-minutes"] <= 4
    [run] = [s for s in job["steps"] if "run_monitor.py" in s.get("run", "")]
    assert "python run_monitor.py" in run["run"]
    for forbidden in ("--loop", "--max-minutes", "--poll-seconds", "sleep "):
        assert forbidden not in run["run"]
    env = run["env"]
    assert env["RADAR_DISCOVERY_CLOCK"] == "firestore"
    # The BookMyShow client is the existing one: no request-behaviour switches.
    for removed in ("RADAR_CLIENT", "RADAR_RETRY_REFUSALS", "RADAR_HTTP_ATTEMPTS"):
        assert removed not in env
    assert "GITHUB_TOKEN" not in env                        # nothing to dispatch or push with


def test_no_second_scheduler_for_the_monitor_and_catalogue_unchanged():
    scheduled = {}
    for path in WORKFLOWS.glob("*.yml"):
        wf = load(path.name)
        if "schedule" in (wf["on"] or {}):
            scheduled[path.name] = [c["cron"] for c in wf["on"]["schedule"]]
    assert scheduled == {"bookmyshow-monitor.yml": ["*/5 * * * *"],
                         "catalogue-sync.yml": ["0 1,7,13,19 * * *"]}
    texts = {p.name: p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml")}
    assert [n for n, t in texts.items() if "run_monitor.py" in t] == ["bookmyshow-monitor.yml"]


# ── the discovery clock survives between runs ─────────────────────────────
class Client:
    def __init__(self):
        self.docs = {}

    def get(self, collection, doc):
        return self.docs.get((collection, doc))

    def set(self, collection, doc, fields):
        self.docs[(collection, doc)] = fields


def test_the_clock_round_trips_through_firestore_without_touching_the_file(monkeypatch):
    client = Client()
    monkeypatch.setenv("RADAR_DISCOVERY_CLOCK", "firestore")
    monkeypatch.setattr(discovery, "_clock_client", lambda: client)
    monkeypatch.setattr(discovery, "save_discovery", lambda *a, **k: pytest.fail("file written"))
    monkeypatch.setattr(discovery, "load_discovery", lambda: pytest.fail("file read"))
    clock = {"bookmyshow:hyderabad": {"at": "2026-12-01T10:00:00+05:30", "ok": True}}
    discovery._save_clock(clock, mirror=False)
    assert discovery._load_clock() == clock
    assert client.docs[discovery.CLOCK_DOC] == {"clock": clock}


def test_a_clock_failure_falls_back_to_the_file_and_never_stops_a_check(monkeypatch):
    class Broken:
        def get(self, *a):
            raise RuntimeError("down")

        def set(self, *a):
            raise RuntimeError("down")

    monkeypatch.setattr(discovery, "_clock_client", lambda: Broken())
    saved = []
    monkeypatch.setattr(discovery, "load_discovery", lambda: {"from": "file"})
    monkeypatch.setattr(discovery, "save_discovery", lambda c, mirror: saved.append(c))
    assert discovery._load_clock() == {"from": "file"}
    discovery._save_clock({"x": 1}, mirror=False)
    assert saved == [{"x": 1}]


def test_without_the_flag_the_file_is_used(monkeypatch):
    monkeypatch.delenv("RADAR_DISCOVERY_CLOCK", raising=False)
    assert discovery._clock_client() is None


def test_maintenance_is_manual_dry_run_by_default_and_shares_the_monitor_lock():
    wf = load("monitor-maintenance.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    assert wf["concurrency"] == {"group": "bookmyshow-monitor", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    [job] = wf["jobs"].values()
    run = job["steps"][-1]["run"]
    assert '[ "$INPUT_CONFIRM" = "APPLY" ] && args+=(--apply)' in run
    assert "--extend" not in run and "monitor_until" not in run


# ── the single pass fails loudly on a missing or revoked credential ──────
def _no_checks(monkeypatch):
    import run_monitor
    from monitor import checker
    called = []
    monkeypatch.setattr(run_monitor, "run_once", lambda **k: called.append(k) or pytest.fail("checked anyway"))
    monkeypatch.setattr(checker, "run_once", lambda **k: called.append(k) or pytest.fail("checked anyway"))
    return run_monitor, called


def test_a_missing_credential_inside_actions_fails_the_run(monkeypatch, capsys):
    run_monitor, called = _no_checks(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("TICKETRADAR_ALLOW_JSON_WORKER", raising=False)
    assert run_monitor.main([]) == 1
    out = capsys.readouterr().out
    assert "FATAL" in out and "refusing to fall back to JSON" in out and "::error::" in out
    assert called == []


def test_a_revoked_credential_fails_the_run_instead_of_reporting_nothing_running(monkeypatch, capsys):
    import run_monitor
    from config import firestore as fs
    from monitor import state as state_mod

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-ticketradar")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "worker@test", "type": "service_account"}')
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "revoked"))
    monkeypatch.setattr(state_mod, "_admin", None)
    state_mod.set_scope_provider(None)
    monkeypatch.setattr(state_mod, "_transport",
                        lambda method, url, token, body: (401, {"error": {"message": "UNAUTHENTICATED"}}))
    assert run_monitor.main([]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "checked:" not in out


def test_a_working_store_still_runs_one_pass(monkeypatch, capsys):
    import run_monitor

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)            # local: the JSON store is allowed
    assert run_monitor.main([]) == 0
    out = capsys.readouterr().out
    assert "[worker] backend=json" in out and "checked:" in out


# ── the BookMyShow integration is exactly the production baseline ─────────
def test_the_bookmyshow_adapter_is_byte_identical_to_the_baseline():
    """This release must not change how BookMyShow is requested."""
    root = Path(__file__).resolve().parents[1]
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "1f4650104bc153a8bab794ddd82d55da8cb86e70", "HEAD", "--", "platforms/"],
        cwd=root, encoding="utf-8").split()
    assert changed == []
