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

import pytest

from config import firestore as fs
from monitor import state as state_mod

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
