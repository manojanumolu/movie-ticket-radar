"""The Cloud Shell activation script stops at every failed gate and never acts past it.

The script is sourced in library mode with Google Cloud, GitHub and Firestore
stubbed as shell functions; every stubbed call is recorded so a test can prove
what did *not* happen after a STOP.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra/production/activate.sh"
TEXT = SCRIPT.read_text()

TARGETS = [("ALLU Cinemas", "Dolby Cinema"), ("AMB Cinemas", "HDR By Barco"),
           ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "PXL"),
           ("Prasads Multiplex", "PCX"), ("Prasads Multiplex", "HDR By Barco")]


def monitor_doc(targets=TARGETS, status="STOPPED"):
    return {"name": "projects/p/databases/(default)/documents/monitors/af1711d4abc",
            "fields": {"status": {"stringValue": status},
                       "targets": {"arrayValue": {"values": [
                           {"mapValue": {"fields": {"venue_name": {"stringValue": v}, "fmt": {"stringValue": f},
                                                    "venue_code": {"stringValue": "X"}}}} for v, f in targets]}}}}


def run(tmp_path, body, stdin=""):
    calls = tmp_path / "calls.log"
    harness = f"""
set -Eeuo pipefail
CALLS={calls}
gcloud() {{ echo "gcloud $*" >> "$CALLS"; return 0; }}
export RADAR_ACTIVATE_LIB_ONLY=1 RADAR_STATE_DIR={tmp_path}/state
source {SCRIPT}
mkdir -p "$STATE_DIR"
{body}
"""
    proc = subprocess.run(["bash", "-c", harness], input=stdin, capture_output=True, text=True, timeout=60)
    return proc, (calls.read_text() if calls.exists() else "")


# ── static safety ────────────────────────────────────────────────────────
def test_syntax_and_shellcheck():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-x", "-S", "style", str(SCRIPT)], check=True, cwd=SCRIPT.parent)


def test_never_writes_monitors_history_users_or_workflows():
    for line in TEXT.replace("\\\n", " ").splitlines():      # join continuation lines
        if re.search(r"-X (PATCH|POST|DELETE)", line):
            assert "worker_runtime/control" in line or ":runQuery" in line or "$URL" in line or "fs_query" in line, line
    assert not re.search(r"\$FS/(monitors|history|users|monitor_state)[^\"']*\" *-d", TEXT)
    assert "keys create" not in TEXT
    assert "--allow-unauthenticated" not in TEXT.replace("--no-allow-unauthenticated", "")
    assert not re.search(r"workflows/[^ ]+/(disable|enable|dispatches)", TEXT)
    assert "catalogue-sync.yml" in TEXT and not re.search(r"catalogue-sync[^\n]*(PUT|PATCH|disable)", TEXT)


def test_holds_no_credential():
    assert not re.search(r"[A-Za-z0-9._%+-]+@gmail\.com", TEXT)
    assert not re.search(r"['\"][a-z]{4} ?[a-z]{4} ?[a-z]{4} ?[a-z]{4}['\"]", TEXT)   # quoted app-password shape
    assert 'read -r -s -p' in TEXT                                             # secrets typed hidden
    assert "ghp_" not in TEXT and "github_pat_" not in TEXT and "private_key" not in TEXT


def test_every_sensitive_action_waits_for_a_typed_confirmation():
    for word, action in [("ISOLATE", "gcloud projects create"), ("BACKUP", "gcloud firestore export"),
                         ("PROBE", "gcloud run jobs create"), ("FENCE", "worker_runtime/control?currentDocument.exists=false"),
                         ("SEND", "--set-secrets="), ("ACTIVATE", "gcloud scheduler jobs resume \"$SCHEDULER_JOB\" --location=\"$REGION\" >/dev/null\n  local since")]:
        assert TEXT.index(f"confirm {word} ") < TEXT.index(action), word


def test_phase_order_matches_the_gates():
    order = re.search(r"PHASES=\(([^)]*)\)", TEXT).group(1).split()
    assert order == ["preflight", "dr_isolation", "backups", "identities", "build", "shadow", "probe",
                     "legacy", "fenced", "alerts", "notify", "activate", "watch"]


# ── behaviour ────────────────────────────────────────────────────────────
def test_wrong_confirmation_stops(tmp_path):
    proc, calls = run(tmp_path, 'confirm BACKUP "Proceed?"; gcloud firestore export x', stdin="yes\n")
    assert proc.returncode == 1 and "STOP [operator]" in proc.stderr
    assert "firestore export" not in calls


def test_state_refuses_unsafe_values(tmp_path):
    proc, _ = run(tmp_path, 'save TEST_ID "abc;rm -rf /"')
    assert proc.returncode == 1 and "STOP [state]" in proc.stderr
    proc, _ = run(tmp_path, 'save TEST_ID abc123; load; echo "id=$TEST_ID"')
    assert proc.returncode == 0 and "id=abc123" in proc.stdout


def test_target_labels_must_be_exactly_the_five(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(monitor_doc()))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(monitor_doc(TARGETS[:4])))
    proc, _ = run(tmp_path, f'[ "$(target_labels {good})" = "$EXPECTED_TARGETS" ] && echo SAME; '
                            f'[ "$(target_labels {bad})" = "$EXPECTED_TARGETS" ] || echo DIFFERENT')
    assert "SAME" in proc.stdout and "DIFFERENT" in proc.stdout


@pytest.mark.parametrize("states,gate", [
    ({"bookmyshow-monitor.yml": "active", "catalogue-sync.yml": "active"}, "G7"),
    ({"bookmyshow-monitor.yml": "disabled_manually", "catalogue-sync.yml": "disabled_manually"}, "G13"),
])
def test_legacy_gate(tmp_path, states, gate):
    stubs = f"""
pause_for() {{ :; }}
gh_get() {{ case "$1" in
  *bookmyshow-monitor.yml) echo '{{"state":"{states["bookmyshow-monitor.yml"]}"}}' ;;
  *catalogue-sync.yml) echo '{{"state":"{states["catalogue-sync.yml"]}"}}' ;;
  *) echo '{{"total_count":0}}' ;; esac; }}
curl() {{ echo 'cron: "0 1,7,13,19 * * *"'; }}
phase_legacy
"""
    proc, _ = run(tmp_path, stubs)
    assert proc.returncode == 1 and f"STOP [{gate}]" in proc.stderr


def test_legacy_gate_waits_for_in_flight_runs_and_catalogue_cron(tmp_path):
    stubs = """
pause_for() { :; }
wait_for() { "$@"; }
gh_get() { case "$1" in
  *catalogue-sync.yml) echo '{"state":"active"}' ;;
  *bookmyshow-monitor.yml) echo '{"state":"disabled_manually"}' ;;
  *in_progress*) echo '{"total_count":1}' ;;
  *) echo '{"total_count":0}' ;; esac; }
curl() { echo 'cron: "0 1,7,13,19 * * *"'; }
phase_legacy
"""
    proc, _ = run(tmp_path, stubs)
    assert proc.returncode == 1 and "STOP [G7]" in proc.stderr and "still in progress" in proc.stderr
    changed_cron = stubs.replace('wait_for() { "$@"; }', 'wait_for() { "$@"; }').replace(
        "cron: \"0 1,7,13,19 * * *\"", "cron: \"0 */2 * * *\"")
    proc, _ = run(tmp_path, changed_cron)
    assert proc.returncode == 1 and "STOP [G13]" in proc.stderr


def test_fenced_and_activate_refuse_if_legacy_is_back(tmp_path):
    for phase in ("phase_fenced", "phase_activate"):
        proc, calls = run(tmp_path, f"""gh_get() {{ echo '{{"state":"active"}}'; }}\n{phase}""")
        assert proc.returncode == 1 and "STOP [G7]" in proc.stderr, phase
        assert "secrets" not in calls and "scheduler jobs resume" not in calls and "run deploy" not in calls


def test_notification_test_requires_no_other_running_monitor(tmp_path):
    proc, calls = run(tmp_path, "active_ids() { echo someone-elses-monitor; }\npause_for() { exit 9; }\nphase_notify")
    assert proc.returncode == 1 and "STOP [G10]" in proc.stderr
    assert "--set-secrets" not in calls


def test_activation_requires_no_unexpected_active_monitor(tmp_path):
    proc, calls = run(tmp_path, """gh_get() { echo '{"state":"disabled_manually"}'; }
active_ids() { echo 6150dc3bxyz; }
phase_activate""")
    assert proc.returncode == 1 and "STOP [G11]" in proc.stderr
    assert "scheduler jobs resume" not in calls


def test_backup_upload_is_blocked_by_an_inherited_reader(tmp_path):
    analysis = {"mainAnalysis": {"fullyExplored": True, "analysisResults": [
        {"attachedResourceFullName": "//storage.googleapis.com/projects/_/buckets/b",
         "iamBinding": {"role": "roles/storage.objectAdmin"}, "identityList": {"identities": [{"name": "user:me@example.com"}]}},
        {"attachedResourceFullName": "//storage.googleapis.com/projects/_/buckets/b",
         "iamBinding": {"role": "roles/storage.admin"},
         "identityList": {"identities": [{"name": "serviceAccount:service-1@gcp-sa-firestore.iam.gserviceaccount.com"}]}},
        {"attachedResourceFullName": "//cloudresourcemanager.googleapis.com/organizations/9",
         "iamBinding": {"role": "roles/storage.objectViewer"}, "identityList": {"identities": [{"name": "user:org@example.com"}]}}]}}
    policy = {"bindings": [{"role": "roles/storage.objectAdmin", "members": ["user:me@example.com"]},
                           {"role": "roles/storage.admin",
                            "members": ["serviceAccount:service-1@gcp-sa-firestore.iam.gserviceaccount.com"]}]}
    (tmp_path / "a.json").write_text(json.dumps(analysis))
    (tmp_path / "p.json").write_text(json.dumps(policy))
    stubs = f"""
gcloud() {{ echo "gcloud $*" >> "$CALLS"; case "$*" in
  *get-iam-policy*) cat {tmp_path}/p.json ;;
  *analyze-iam-policy*) cat {tmp_path}/a.json ;;
  *) echo enforced ;; esac; }}
BACKUP_ADMIN=user:me@example.com FIRESTORE_AGENT=serviceAccount:service-1@gcp-sa-firestore.iam.gserviceaccount.com
DR_PROJECT_NUMBER=5 ANALYSIS_SCOPE=(--project=dr)
verify_access G3b
gcloud storage cp /dev/shm/radar-auth.json.gpg gs://never
"""
    proc, calls = run(tmp_path, stubs)
    assert proc.returncode == 1 and "STOP [G3b]" in proc.stderr
    assert "UNEXPECTED IDENTITY user:org@example.com" in proc.stdout
    assert "storage cp" not in calls


def test_rollback_usage_is_strict(tmp_path):
    proc, _ = run(tmp_path, "rollback reenable-legacy")
    assert proc.returncode == 1 and "STOP [usage]" in proc.stderr
