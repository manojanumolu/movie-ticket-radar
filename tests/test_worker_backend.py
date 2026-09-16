"""Which store the GitHub Actions worker picks, and why.

The worker has no signed-in person, so it does not go through the scope
provider at all: it authenticates to Firestore with a service account handed
to it as ``FIREBASE_SERVICE_ACCOUNT`` and addresses the project named by
``FIREBASE_PROJECT_ID``. Both come from the environment, which on Actions
means the ``env:`` block of the workflow step — and if either one is missing
or unusable the worker silently falls back to the legacy ``data/*.json``
files, finds every monitor there stopped, reports "nothing running" and
exits in under a minute having checked nothing.

That fallback is deliberate (a laptop with no credentials has to work), but
it is indistinguishable from success in a workflow log, so these tests pin
exactly which inputs select which store. No real credentials are involved:
``service_account_token_getter`` is replaced, because what is under test is
the *selection*, not Google's OAuth exchange.

The existing tests in ``test_isolation.py`` inject ``state_mod._admin``
directly to get a worker, so they never exercise this path.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from config import firestore as fs
from config.timezone import now_ist
from monitor import state as state_mod
from monitor import worker as worker_mod
from monitor.checker import run_once
from monitor.models import ANY_FORMAT, CheckOutcome, Monitor, MovieRef, TheatreTarget
from tests.test_isolation import MemoryFirestore, PROJECT

#: Shaped like a real key, with none of the substance. Nothing here is a
#: credential: the private key is absent and the token getter never runs.
FAKE_KEY = json.dumps({
    "type": "service_account",
    "project_id": "tr-test-project",
    "client_email": "worker@tr-test-project.iam.gserviceaccount.com",
})


@pytest.fixture
def worker(monkeypatch):
    """The worker's situation: nobody signed in, and a clean client cache."""
    state_mod.set_scope_provider(None)
    monkeypatch.setattr(state_mod, "_admin", None)
    # The selection is what is under test; minting a Google access token is
    # not, and would need a real private key.
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "token"))
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    yield monkeypatch
    state_mod.set_scope_provider(None)


def test_both_secrets_present_selects_firestore(worker):
    """What the workflow's env block is for. With both values the worker
    reads every owner's monitors out of Firestore."""
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", FAKE_KEY)

    assert state_mod.backend_name() == "firestore"
    store = state_mod._backend()
    assert isinstance(store, state_mod._FirestoreStore)
    assert store.uid is None                       # every owner, not one person
    assert store.client.project_id == "tr-test-project"


def test_no_project_id_falls_back_to_json(worker):
    """The failure this exists to catch: the key is there, the project is
    not, and the worker quietly reads the legacy files instead."""
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", FAKE_KEY)

    assert state_mod.backend_name() == "json"


def test_no_service_account_falls_back_to_json(worker):
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")

    assert state_mod.backend_name() == "json"


def test_neither_secret_falls_back_to_json(worker):
    """A laptop with no credentials — the compatibility path."""
    assert state_mod.backend_name() == "json"


def test_an_empty_project_id_is_not_a_project_id(worker):
    """A GitHub secret that exists but is empty arrives as an empty string,
    not as an absent variable."""
    worker.setenv("FIREBASE_PROJECT_ID", "   ")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", FAKE_KEY)

    assert state_mod.backend_name() == "json"


def test_a_service_account_that_is_not_json_falls_back_to_json(worker, capsys):
    """A truncated or quoted secret. This one says so on the way past."""
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", "not-json-at-all")

    assert state_mod.backend_name() == "json"
    assert "not valid JSON" in capsys.readouterr().out


def test_the_wrong_kind_of_firebase_json_falls_back_to_json(worker):
    """The quietest failure of the lot: valid JSON, plausibly Firebase, but
    the *web app* config rather than a service-account key — so there is no
    ``client_email`` to sign with, and nothing is printed."""
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", json.dumps(
        {"apiKey": "x", "authDomain": "y.firebaseapp.com", "projectId": "y"}))

    assert fs.service_account_from_env() is None
    assert state_mod.backend_name() == "json"


def test_the_workflow_passes_both_variables_to_the_worker():
    """The env block the worker depends on. Without these two names in the
    step that runs ``run_monitor.py``, the repository secrets exist but never
    reach the process, and every run ends "nothing running"."""
    from pathlib import Path

    workflow = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "bookmyshow-monitor.yml"
    text = workflow.read_text(encoding="utf-8")

    step = text.split("Run monitor segment", 1)[1].split("- name:", 1)[0]
    assert "FIREBASE_PROJECT_ID: ${{ secrets.FIREBASE_PROJECT_ID }}" in step
    assert "FIREBASE_SERVICE_ACCOUNT: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}" in step
    # …and the step really is the one that runs the worker.
    assert "run_monitor.py" in step


def test_no_credential_is_ever_committed():
    """The service account belongs in the Actions environment and nowhere
    else: not in the repository, not in the app, not in Streamlit secrets."""
    from pathlib import Path

    here = Path(__file__).resolve()
    root = here.parent.parent
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix not in {".py", ".json", ".toml", ".yml", ".yaml", ".md", ".txt"}:
            continue
        if path.resolve() == here:
            continue                    # this file names the patterns it looks for
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        assert "BEGIN PRIVATE KEY" not in text, f"a private key is committed in {path}"
        assert ".iam.gserviceaccount.com" not in text, \
            f"a service-account address is committed in {path}"


# ──────────────────────────────────────────────────────────────────────────
# Production refuses the legacy store
# ──────────────────────────────────────────────────────────────────────────
def _actions(monkeypatch):
    """Make running_in_actions() true, the way GitHub does."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")


def test_in_actions_a_missing_service_account_is_fatal(worker, capsys):
    """The failure that started this: production monitoring silently against
    data/*.json, where every monitor has long since been stopped."""
    _actions(worker)
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")

    with pytest.raises(worker_mod.WorkerConfigError):
        worker_mod.preflight(in_actions=True)

    out = capsys.readouterr().out
    assert "[worker] backend=json" in out
    assert "[worker] service_account_configured=false" in out
    assert "FATAL: Firestore worker credentials are not configured correctly" in out


def test_in_actions_a_malformed_service_account_is_fatal(worker, capsys):
    _actions(worker)
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", "{ truncated")

    with pytest.raises(worker_mod.WorkerConfigError):
        worker_mod.preflight(in_actions=True)
    assert "not valid JSON" in capsys.readouterr().out


def test_in_actions_a_web_config_mistaken_for_a_key_is_fatal(worker, capsys):
    """Valid JSON, plausibly Firebase, but the web app config — no signing
    identity. This is the one that used to fail in total silence."""
    _actions(worker)
    worker.setenv("FIREBASE_PROJECT_ID", "tr-test-project")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", json.dumps(
        {"apiKey": "x", "authDomain": "y.firebaseapp.com", "projectId": "y"}))

    with pytest.raises(worker_mod.WorkerConfigError):
        worker_mod.preflight(in_actions=True)
    out = capsys.readouterr().out
    assert "web app config rather than a service-account key" in out
    assert "FATAL" in out


def test_in_actions_an_empty_project_id_is_fatal(worker, capsys):
    _actions(worker)
    worker.setenv("FIREBASE_PROJECT_ID", "   ")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", FAKE_KEY)

    with pytest.raises(worker_mod.WorkerConfigError):
        worker_mod.preflight(in_actions=True)
    out = capsys.readouterr().out
    assert "[worker] firebase_project_configured=false" in out
    assert "FIREBASE_PROJECT_ID is not set" in out


def test_outside_actions_the_json_store_is_still_allowed(worker, capsys):
    """A laptop with no credentials has to keep working."""
    name, _ = worker_mod.preflight(in_actions=False)
    assert name == "json"
    assert "FATAL" not in capsys.readouterr().out


def test_actions_can_opt_back_into_json_deliberately(worker, capsys):
    """An escape hatch, so the legacy worker stays reachable if it is ever
    needed. Absent this variable, Actions refuses."""
    _actions(worker)
    worker.setenv(worker_mod.ALLOW_JSON_WORKER, "1")

    name, _ = worker_mod.preflight(in_actions=True)
    assert name == "json"
    assert "FATAL" not in capsys.readouterr().out


# ──────────────────────────────────────────────────────────────────────────
# The real chain: environment → backend → Firestore → running monitor
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def cutover(monkeypatch):
    """Production after the cutover: a monitor written to Firestore by the
    signed-in UI, and JSON left holding only old stopped ones.

    Nothing here bypasses ``_backend()``. The only stub is Google's OAuth
    exchange, which needs a real private key and is not what is under test.
    """
    store = MemoryFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setattr(state_mod, "_owner_of", {})
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "admin"))

    class Cutover:
        docs = store.docs
        calls = store.calls
        owner = "uid-real-person"

        @staticmethod
        def ui_creates(title="Hanuman Ansh", running=True):
            """Exactly what app.start_monitor does: write as the signed-in
            person, through the scope provider."""
            monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
            state_mod.set_scope_provider(
                lambda: state_mod.Scope(PROJECT, Cutover.owner, lambda: f"id.{Cutover.owner}"))
            monitor = _running_monitor(title, owner=Cutover.owner, running=running)
            state_mod.upsert_monitor(monitor, mirror=False)
            state_mod.set_scope_provider(None)
            return monitor

        @staticmethod
        def as_the_actions_worker():
            monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
            monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", FAKE_KEY)
            monkeypatch.setenv("GITHUB_ACTIONS", "true")

    yield Cutover
    state_mod.set_scope_provider(None)


def _running_monitor(title, owner, running=True):
    monitor = Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=10, monitor_until=now_ist() + timedelta(days=2),
        notify_email="w@example.com", owner_uid=owner)
    if not running:
        monitor.stop()
    return monitor


def test_the_worker_sees_the_monitor_the_ui_created(cutover, capsys):
    """End to end, through _backend(): environment → Firestore → RUNNING."""
    created = cutover.ui_creates()
    cutover.as_the_actions_worker()

    name, monitors = worker_mod.preflight(in_actions=True, monitor_id=created.id)

    assert name == "firestore"
    assert [m.id for m in monitors] == [created.id]
    out = capsys.readouterr().out
    assert "[worker] backend=firestore" in out
    assert "[worker] running_monitor_count=1" in out
    assert "[worker] requested_monitor_found=true" in out
    assert "[worker] requested_monitor_status=ACTIVE" in out


def test_the_worker_stays_alive_for_a_running_firestore_monitor(cutover):
    """The bug's signature was seconds_until_next_due() returning None."""
    cutover.ui_creates()
    cutover.as_the_actions_worker()

    assert worker_mod.seconds_until_next_due(now_ist(), 30) is not None


def test_firestore_wins_over_stale_json_monitors(cutover, isolated_data, capsys):
    """JSON holds old stopped monitors; Firestore holds the running one. The
    worker must read Firestore — reading JSON is precisely the production
    failure, and it looks identical from outside."""
    from config import store as store_mod

    stopped = _running_monitor("Old and stopped", owner="", running=False)
    store_mod.write_json(store_mod.MONITORS_FILE, [stopped.to_dict()], mirror=False)

    created = cutover.ui_creates()
    cutover.as_the_actions_worker()

    name, monitors = worker_mod.preflight(in_actions=True)
    assert name == "firestore"
    assert [m.id for m in monitors] == [created.id]
    assert "[worker] running_monitor_count=1" in capsys.readouterr().out


def test_only_stopped_monitors_in_firestore_is_a_legitimate_idle_run(cutover, capsys):
    """Not a failure: Firestore is reachable, there is simply nothing to do."""
    cutover.ui_creates(running=False)
    cutover.as_the_actions_worker()

    name, monitors = worker_mod.preflight(in_actions=True)
    assert name == "firestore" and len(monitors) == 1
    assert "[worker] running_monitor_count=0" in capsys.readouterr().out
    assert worker_mod.seconds_until_next_due(now_ist(), 30) is None


def test_a_dispatched_monitor_id_that_is_absent_is_reported(cutover, capsys):
    """Distinguishable from "nothing running": the UI named a monitor and
    this store does not have it."""
    cutover.ui_creates()
    cutover.as_the_actions_worker()

    worker_mod.preflight(in_actions=True, monitor_id="not-a-real-id")
    out = capsys.readouterr().out
    assert "[worker] requested_monitor_found=false" in out
    assert "[worker] requested_monitor_status=N/A" in out
    assert "::warning::the dispatched monitor was not found" in out


def test_the_forced_first_check_actually_checks_the_dispatched_monitor(cutover, monkeypatch):
    """force=true plus monitor_id — what the UI's workflow_dispatch sends.
    The monitor has never been checked, but force is what makes the first
    check happen now rather than on its interval."""
    created = cutover.ui_creates()
    cutover.as_the_actions_worker()

    checked: list = []

    def fake_check(monitor, at=None):
        checked.append(monitor.id)
        return CheckOutcome(monitor_id=monitor.id, checked_at=at or now_ist(),
                            ok=True, results=[])

    monkeypatch.setattr("monitor.checker.check_monitor", fake_check)
    report = run_once(force=True, monitor_id=created.id, mirror=False,
                      notifier=lambda *a, **k: True)

    assert checked == [created.id], f"the dispatched monitor was not checked: {report.skipped}"
    assert created.id in report.checked


def test_a_firestore_failure_is_an_error_not_an_idle_run(cutover, monkeypatch):
    """A query that fails must reach the workflow as a failure. Swallowing it
    into "nothing running" would be the same invisible green run again."""
    cutover.ui_creates()
    cutover.as_the_actions_worker()

    def broken(method, url, token, body):
        raise fs.FirestoreError("PERMISSION_DENIED", 403)

    monkeypatch.setattr(state_mod, "_transport", broken)
    monkeypatch.setattr(state_mod, "_admin", None)

    with pytest.raises(fs.FirestoreError):
        worker_mod.seconds_until_next_due(now_ist(), 30)


def test_the_summary_says_which_store_was_used(cutover):
    """So a run that checked nothing can be told apart from one that was
    looking in the wrong place."""
    loop = worker_mod.LoopReport(started_at=now_ist(), backend="firestore")
    assert "backend=firestore" in loop.summary()
    assert "checks=0" in loop.summary()


# ──────────────────────────────────────────────────────────────────────────
# Nothing secret is ever printed
# ──────────────────────────────────────────────────────────────────────────
def test_the_diagnostics_never_print_a_secret(cutover, capsys):
    """The project id, the client email, the key and the token all stay out
    of the log; only whether each was usable goes in."""
    cutover.ui_creates()
    cutover.as_the_actions_worker()

    worker_mod.preflight(in_actions=True)
    out = capsys.readouterr().out

    assert PROJECT not in out                       # never the project id itself
    assert "client_email" not in out
    assert "iam.gserviceaccount" not in out
    assert "private_key" not in out
    assert "BEGIN" not in out
    assert "admin" not in out                       # never the access token
    assert "[worker] firebase_project_configured=true" in out
    assert "[worker] service_account_configured=true" in out


def test_a_fatal_message_never_quotes_the_value(worker, capsys):
    """Even when it is complaining about the credential."""
    _actions(worker)
    worker.setenv("FIREBASE_PROJECT_ID", "tr-secret-project-name")
    worker.setenv("FIREBASE_SERVICE_ACCOUNT", json.dumps(
        {"apiKey": "SECRET-KEY-VALUE", "authDomain": "z.firebaseapp.com"}))

    with pytest.raises(worker_mod.WorkerConfigError) as raised:
        worker_mod.preflight(in_actions=True)

    everything = capsys.readouterr().out + str(raised.value)
    assert "SECRET-KEY-VALUE" not in everything
    assert "tr-secret-project-name" not in everything
