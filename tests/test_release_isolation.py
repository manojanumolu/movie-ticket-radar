"""Keep the accepted production release at its original executable surface."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = "1f4650104bc153a8bab794ddd82d55da8cb86e70"
# Data legitimately changes in normal catalogue and monitor runs. The guard
# pins the code, dependencies and workflow definitions that operate on it.
RUNTIME = (
    ".streamlit", "app.py", "auth", "config", "content", "explorer", "monitor",
    "notifications", "platforms", "static", "ui", "run_monitor.py", "sync_catalogue.py",
    "resolve_movie.py", "firestore.rules", "requirements.txt", "ci/requirements-worker.lock",
    "tools/grant_admin.py", "tools/rules_audit.py", "tools/bms_diagnose.py", "tools/pvr_diagnose.py",
    ".github/workflows/bookmyshow-monitor.yml", ".github/workflows/catalogue-sync.yml",
    ".github/workflows/grant-admin.yml", ".github/workflows/rules-audit.yml",
    ".github/workflows/bms-diagnose.yml", ".github/workflows/pvr-diagnose.yml",
    ".github/workflows/resolve-movie.yml",
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, encoding="utf-8")


#: The admin-only monitor creation release is the one deliberate change to the
#: executable surface since the baseline: the Firestore rule, the store guard
#: and the page's explanation. Anything else changing is still a failure.
ADMIN_ONLY_CREATION = ["app.py", "firestore.rules", "monitor/state.py"]
#: The single-pass five-minute monitor: the workflow itself, the city-listing
#: clock kept in Firestore between runs, and the refusal policy switch.
SINGLE_PASS_MONITOR = [".github/workflows/bookmyshow-monitor.yml", "monitor/discovery.py",
                       "platforms/http.py"]


def test_production_runtime_files_match_the_baseline():
    changed = git("diff", "--name-only", PRODUCTION, "HEAD", "--", *RUNTIME).split()
    assert changed == sorted(ADMIN_ONLY_CREATION + SINGLE_PASS_MONITOR), changed


def test_worker_entrypoints_are_tracked_only_in_the_candidate():
    for path in ("Dockerfile.worker", "run_worker_service.py", ".github/workflows/monitor-worker-relay.yml"):
        assert git("ls-files", "--", path) == ""
        assert git("ls-files", "--", f"worker_candidate/overlay/{path}").strip()


def test_candidate_preparation_is_only_invoked_by_test_ci():
    workflows = git("ls-files", "--", ".github/workflows").splitlines()
    for path in workflows:
        blob = git("show", f"HEAD:{path}")
        if path != ".github/workflows/tests.yml":
            assert "worker_candidate" not in blob
            assert "run_worker_service.py" not in blob
            assert "monitor-worker-relay.yml" not in blob
        assert not re.search(r"\bterraform\s+apply\b", blob)


def test_only_test_ci_can_change_on_release():
    changed = git("diff", "--name-only", PRODUCTION, "HEAD", "--", ".github/workflows").splitlines()
    assert changed == [".github/workflows/bookmyshow-monitor.yml", ".github/workflows/monitor-maintenance.yml",
                       ".github/workflows/tests.yml"]
    catalogue = git("show", "HEAD:.github/workflows/catalogue-sync.yml")
    assert re.findall(r'cron:\s*"([^"]+)"', catalogue) == ["0 1,7,13,19 * * *"]
    tests = git("show", "HEAD:.github/workflows/tests.yml")
    lines = tests.splitlines()
    event_block = lines.index("on:")
    assert lines[event_block + 1:event_block + 5] == ["  push:", "  pull_request:", "", "permissions:"]


def test_candidate_infrastructure_starts_in_shadow_and_backup_is_opt_in():
    scheduler = git("show", "HEAD:infra/monitoring/main.tf")
    assert scheduler.count('resource "google_cloud_scheduler_job"') == 1
    assert re.search(r"(?m)^  paused\s*=\s*true$", scheduler)
    assert re.search(r'name\s*=\s*"WORKER_MODE"\s+value\s*=\s*"shadow"', scheduler)
    backup = git("show", "HEAD:infra/monitoring/backups.tf")
    assert re.search(r"(?m)^  default\s*=\s*false$", backup)
    assert "count     = var.enable_shadow_backup_schedule ? 1 : 0" in backup
