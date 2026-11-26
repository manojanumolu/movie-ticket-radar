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


# ── G2: deployed Firestore Rules vs the baseline ─────────────────────────
BASELINE_RULES = subprocess.run(["git", "show", "cb9c6a130b1b3f321435c817fabb133d3742f27b:firestore.rules"],
                                cwd=ROOT, capture_output=True, check=True).stdout


def compare(tmp_path, base: bytes, deployed: bytes | None):
    (tmp_path / "base.rules").write_bytes(base)
    if deployed is not None:
        (tmp_path / "deployed.rules").write_bytes(deployed)
    return run(tmp_path, f"rules_equivalent {tmp_path}/base.rules {tmp_path}/deployed.rules")[0]


def test_baseline_rules_are_the_approved_bytes():
    import hashlib
    assert hashlib.sha256(BASELINE_RULES).hexdigest() == \
        "932bdc42ac30b79c48629e9e4420fa2030b6165b157a3ef8016a79ebea2a06ff"
    # The production diagnostic's "deployed" hash is exactly the baseline plus one LF:
    assert hashlib.sha256(BASELINE_RULES + b"\n").hexdigest() == \
        "8f58cd7a6628bf14c8b32087163c46471efe2d5dfc3a9216cc0c21fa1306d218"


@pytest.mark.parametrize("deployed", [
    BASELINE_RULES,                          # byte-identical
    BASELINE_RULES + b"\n",                  # the production case (one extra LF)
    BASELINE_RULES + b"\n\n\n",              # several extra LFs
    BASELINE_RULES.rstrip(b"\n"),            # no final LF at all
])
def test_trailing_line_feed_differences_pass(tmp_path, deployed):
    assert compare(tmp_path, BASELINE_RULES, deployed).returncode == 0


def _edit(old: bytes, new: bytes) -> bytes:
    assert BASELINE_RULES.count(old) >= 1, old
    return BASELINE_RULES.replace(old, new, 1)


@pytest.mark.parametrize("deployed,why", [
    (_edit(b"match /monitors/{id} {", b"match /monitors/{monitorId} {"), "renamed match wildcard"),
    (_edit(b"allow delete: if ownsExisting();", b"allow delete: if true;"), "removed a restriction"),
    (_edit(b"allow update: if false;", b"allow update: if request.auth != null;"), "loosened a denial"),
    (_edit(b"allow delete: if ownsExisting();", b"allow delete: if ownsExisting() && false;"), "added a restriction"),
    (_edit(b"&& requestCost(d) <= 120", b"&& requestCost(d) <= 1200"), "changed a numeric limit"),
    (BASELINE_RULES.replace(b"\n", b"\r\n"), "CRLF line endings"),
    (BASELINE_RULES + b" \n", "trailing space is not a line feed"),
    (BASELINE_RULES + b"\n// comment\n", "appended comment"),
    (b" " + BASELINE_RULES, "leading whitespace"),
    (BASELINE_RULES.rstrip(b"\n") + b"\n}\n", "extra closing brace"),
])
def test_any_real_difference_fails(tmp_path, deployed, why):
    proc = compare(tmp_path, BASELINE_RULES, deployed)
    assert proc.returncode == 1, why


def test_a_rule_difference_is_shown_as_a_diff(tmp_path):
    proc = compare(tmp_path, BASELINE_RULES, _edit(b"allow update: if false;", b"allow update: if true;"))
    assert proc.returncode == 1
    assert "-      allow update: if false;" in proc.stdout and "+      allow update: if true;" in proc.stdout


@pytest.mark.parametrize("deployed", [None, b"", b"\n\n"])
def test_missing_or_empty_deployed_rules_fail_closed(tmp_path, deployed):
    assert compare(tmp_path, BASELINE_RULES, deployed).returncode == 1


@pytest.mark.parametrize("release,ruleset,ok", [
    ({"rulesetName": "projects/ticketradar03/rulesets/r1"},
     {"source": {"files": [{"name": "firestore.rules", "content": "RULES\n"}]}}, True),
    ({"rulesetName": "projects/ticketradar03/rulesets/r1"}, {"source": {"files": []}}, False),
    ({"rulesetName": "projects/ticketradar03/rulesets/r1"},
     {"source": {"files": [{"content": "A"}, {"content": "B"}]}}, False),
    ({"rulesetName": "projects/ticketradar03/rulesets/r1"}, {"source": {"files": [{"name": "x"}]}}, False),
    ({"rulesetName": "projects/ticketradar03/rulesets/r1"}, {"source": {"files": [{"content": ""}]}}, False),
    ({}, {}, False),
    ({"error": {"code": 403}}, {}, False),
])
def test_fetch_extracts_exact_bytes_and_fails_closed(tmp_path, release, ruleset, ok):
    (tmp_path / "release.json").write_text(json.dumps(release))
    (tmp_path / "ruleset.json").write_text(json.dumps(ruleset))
    stubs = f"""
token() {{ echo t; }}
curl() {{ case "$*" in *releases/cloud.firestore*) cat {tmp_path}/release.json ;; *) cat {tmp_path}/ruleset.json ;; esac; }}
fetch_deployed_rules {tmp_path}/out.rules && echo FETCHED
"""
    proc, _ = run(tmp_path, stubs)
    assert ("FETCHED" in proc.stdout) is ok
    if ok:
        assert (tmp_path / "out.rules").read_bytes() == b"RULES\n"       # no extra line feed added


def test_fetch_fails_closed_on_http_error(tmp_path):
    proc, _ = run(tmp_path, "token() { echo t; }\ncurl() { return 22; }\nfetch_deployed_rules "
                            f"{tmp_path}/out.rules && echo FETCHED || echo REFUSED")
    assert "REFUSED" in proc.stdout


@pytest.mark.parametrize("deployed,passes", [
    (BASELINE_RULES + b"\n", True),                                                   # the production case
    (_edit(b"allow update: if false;", b"allow update: if true;"), False),
])
def test_preflight_g2_end_to_end(tmp_path, deployed, passes):
    (tmp_path / "bad.rules").write_bytes(deployed)
    stubs = f"""
gh_get() {{ printf '{{"check_runs":[%s]}}' '{{"name":"pytest","conclusion":"success"}},{{"name":"candidate-pytest","conclusion":"success"}},{{"name":"firebase-emulators","conclusion":"success"}},{{"name":"worker-image","conclusion":"success"}}'; }}
gcloud() {{ case "$*" in *"get-value account"*) echo me@example.com ;; *projectNumber*) echo 123 ;; *locationId*) echo asia-south1 ;; *) echo ok ;; esac; }}
git() {{ case "$*" in *"status --porcelain"*) : ;; *"rev-parse HEAD"*) echo abc ;; *show*) command git "$@" ;; *) command git "$@" ;; esac; }}
fetch_deployed_rules() {{ cp {tmp_path}/bad.rules "$1"; }}
fs_get() {{ echo '{{}}'; }}
phase_preflight
"""
    proc, _ = run(tmp_path, stubs)
    assert proc.returncode == 1                       # stubs end the run at a later gate either way
    if passes:
        assert "STOP [G2]" not in proc.stderr and "deployed Rules = repository" in proc.stdout
        assert "STOP [G1]" in proc.stderr             # preflight continued to the monitor checks
    else:
        assert "STOP [G2]" in proc.stderr and "+      allow update: if true;" in proc.stdout
