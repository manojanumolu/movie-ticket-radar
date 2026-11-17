"""Phase 7 — production credentials only reach the jobs that need them.

The Firebase service-account key, the project id and the Gmail credentials
move into two GitHub environments (repository settings, set up by the owner):

* ``production`` — deployment branch ``main`` only, no reviewer — for the
  worker. A run of the worker on any other branch is refused the secrets.
* ``admin`` — deployment branch ``main``, the owner's approval on every run —
  for ``grant-admin``, with its own key that can change custom claims and
  nothing else.

These pin the workflow side: which jobs name an environment, and that no job
outside those two even mentions a production secret, so a workflow added or
edited later cannot quietly pick one up from the repository-level secrets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = {p.name: p for p in sorted(Path(".github/workflows").glob("*.yml"))}
PRODUCTION_SECRETS = ("FIREBASE_SERVICE_ACCOUNT", "FIREBASE_PROJECT_ID", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")


def load(name: str) -> dict:
    return yaml.safe_load(WORKFLOWS[name].read_text(encoding="utf-8"))


def secrets_used(job: dict) -> set[str]:
    text = yaml.safe_dump(job)
    return {s for s in PRODUCTION_SECRETS if f"secrets.{s}" in text}


def test_the_worker_job_runs_in_the_production_environment():
    jobs = load("bookmyshow-monitor.yml")["jobs"]
    assert list(jobs) == ["check"]
    assert jobs["check"]["environment"] == "production"
    assert secrets_used(jobs["check"]) == set(PRODUCTION_SECRETS)


def test_the_admin_job_runs_in_the_admin_environment_with_no_mail_credentials():
    jobs = load("grant-admin.yml")["jobs"]
    assert list(jobs) == ["claim"]
    assert jobs["claim"]["environment"] == "admin"
    assert secrets_used(jobs["claim"]) == {"FIREBASE_SERVICE_ACCOUNT", "FIREBASE_PROJECT_ID"}


def test_the_rules_audit_reads_with_the_worker_key_and_can_do_nothing_else():
    """Read-only audit before the rules are published: the worker's Firebase
    key from `production` (main only), no mail credentials, no write token."""
    doc = load("rules-audit.yml")
    assert set(doc.get("on", doc.get(True))) == {"workflow_dispatch"}
    assert doc["permissions"] == {"contents": "read"}
    (job,) = doc["jobs"].values()
    assert job["environment"] == "production"
    assert secrets_used(job) == {"FIREBASE_SERVICE_ACCOUNT", "FIREBASE_PROJECT_ID"}
    checkout = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False


@pytest.mark.parametrize("name", sorted(set(WORKFLOWS) - {"bookmyshow-monitor.yml", "grant-admin.yml",
                                                          "rules-audit.yml"}))
def test_no_other_workflow_touches_a_production_secret_or_environment(name):
    for job_name, job in load(name)["jobs"].items():
        assert "environment" not in job, (name, job_name)
        assert secrets_used(job) == set(), (name, job_name)


def test_every_job_that_holds_a_production_secret_names_an_environment():
    for name in WORKFLOWS:
        for job_name, job in load(name)["jobs"].items():
            if secrets_used(job):
                assert job.get("environment") in {"production", "admin"}, (name, job_name)


def test_the_environment_lines_change_nothing_else_about_the_worker():
    """Single job, no needs/outputs to break, the same triggers, concurrency
    and permissions as before the environment was added."""
    worker = load("bookmyshow-monitor.yml")
    triggers = worker.get("on", worker.get(True))
    assert set(triggers) == {"push", "schedule", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"], "paths": ["data/monitors.json"]}
    assert worker["concurrency"] == {"group": "bookmyshow-monitor", "cancel-in-progress": False}
    assert worker["permissions"] == {"contents": "write", "actions": "write"}
    job = worker["jobs"]["check"]
    assert "needs" not in job and "outputs" not in job
    admin = load("grant-admin.yml")
    assert set(admin.get("on", admin.get(True))) == {"workflow_dispatch"}
    assert admin["permissions"] == {"contents": "read"}


def test_the_catalogue_workflows_keep_pushing_with_their_own_token():
    for name in ("catalogue-sync.yml", "resolve-movie.yml"):
        doc = load(name)
        assert doc["permissions"] == {"contents": "write"}, name
        assert all("environment" not in job for job in doc["jobs"].values()), name
