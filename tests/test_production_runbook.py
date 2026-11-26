"""The activation runbook, its single name source and its alert files agree."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/audit-reports/2026-11-26-production-readiness-and-activation.md"
ENV = ROOT / "infra/production/env.sh"
METRICS = ROOT / "infra/production/logging-metrics.txt"
ALERTS = ROOT / "infra/production/alerts"

#: Variables the runbook sets itself, in the step that uses them.
LOCAL = {"BUILD_SHA", "PROJECT_NUMBER", "FS_LOCATION", "DR_LOCATION", "TOKEN", "RULES", "RS", "DOOMSDAY_ID",
         "OVERLAP_ID", "EXPORT_PATH", "IMAGE", "IMAGE_DIGEST", "URL", "ID", "CHANNEL_ID", "TEST_ID", "b", "s",
         "f", "sa", "name", "desc", "filter", "strip", "HOME", "PATH",
         "DR_PROJECT_NUMBER", "BILLING", "BACKUP_ADMIN", "FIRESTORE_AGENT", "id"}


def env_names():
    return set(re.findall(r"^export ([A-Z_]+)=", ENV.read_text(), re.M))


def bash_blocks():
    return re.findall(r"```bash\n(.*?)```", RUNBOOK.read_text(), re.S)


def test_every_variable_the_runbook_uses_is_defined():
    used = set()
    for block in bash_blocks():
        used |= set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", block))
    missing = used - env_names() - LOCAL - {"1", "2"}
    assert not missing, missing


def test_names_are_consistent_with_the_single_source():
    env = ENV.read_text()
    assert "export PROJECT=ticketradar03" in env
    assert "export REGION=asia-south1" in env
    assert "export SERVICE=radar-monitor-worker" in env
    assert "export SCHEDULER_JOB=radar-worker-tick" in env
    metrics = METRICS.read_text()
    assert 'resource.labels.service_name="radar-monitor-worker"' in metrics
    assert 'resource.labels.job_id="radar-worker-tick"' in metrics


def test_no_key_creation_and_no_public_service():
    text = RUNBOOK.read_text()
    for block in bash_blocks():
        assert "keys create" not in block
        assert "--allow-unauthenticated" not in block
        assert not re.search(r"add-iam-policy-binding[^\n]*all(Authenticated)?Users", block)
        assert "catalogue-sync" not in block
    assert text.count("--no-allow-unauthenticated") >= 2


def test_alert_policies_reference_defined_metrics():
    defined = {line.split("|")[0] for line in METRICS.read_text().splitlines()
               if line and not line.startswith("#")}
    assert defined == {"radar_worker_failed", "radar_worker_complete", "radar_mail_problem", "radar_scheduler_error"}
    files = sorted(ALERTS.glob("*.json"))
    assert len(files) == 4
    for path in files:
        policy = json.loads(path.read_text())
        assert policy["notificationChannels"] == ["projects/ticketradar03/notificationChannels/CHANNEL_ID"]
        [condition] = policy["conditions"]
        body = condition.get("conditionThreshold") or condition["conditionAbsent"]
        metric = re.search(r'logging.googleapis.com/user/([a-z_]+)', body["filter"]).group(1)
        assert metric in defined


def test_runbook_keeps_the_mandatory_order():
    text = RUNBOOK.read_text()
    order = ["#### A-2", "#### A-5", "#### A-6", "#### A-7", "#### A-8", "#### A-10", "#### A-11"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    # The legacy workflow is disabled before the control document exists and before any fenced deploy.
    assert text.index("Disable workflow") < text.index("worker_runtime/control?currentDocument.exists=false")
    assert text.index("Disable workflow") < text.index("WORKER_MODE=fenced")
    # The original monitor is extended only after the notification test.
    assert text.index("Extend by 24 hours**. Expected") > text.index("#### A-10")


def test_backups_go_to_the_isolated_project_and_are_proven_before_upload():
    text = RUNBOOK.read_text()
    env = ENV.read_text()
    assert "export DR_PROJECT=ticketradar03-dr" in env and "export DR_BUCKET=gs://${DR_PROJECT}-backups" in env
    a2 = text[text.index("#### A-2 "):text.index("#### A-3 ")]
    assert 'gcloud storage buckets create "$DR_BUCKET" --project="$DR_PROJECT"' in a2
    assert "remove-iam-policy-binding" not in a2                 # inherited access is not "removed" on the bucket
    assert "gcloud projects add-iam-policy-binding" not in a2    # no IAM change in ticketradar03 or DR project
    assert "set-iam-policy" in a2
    proof = a2.index("verify_backup_access.py effective")
    assert proof < a2.index("gcloud firestore export")
    assert proof < a2.index("gcloud storage cp /dev/shm/auth")
    assert a2.index("verify_backup_access.py ancestors") < a2.index("gcloud storage buckets create")
    assert "--expand-groups --analyze-service-account-impersonation --show-response" in a2
    assert a2.count("verify_backup_access.py effective") == 2      # before and after upload
    assert "/dev/shm/auth.json" in a2 and "~/auth.json" not in a2
