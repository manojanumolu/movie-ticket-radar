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


def test_production_runtime_files_match_the_baseline():
    changed = git("diff", "--name-only", PRODUCTION, "HEAD", "--", *RUNTIME)
    assert not changed, changed


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
