from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import google.auth
import pytest
from google.auth.compute_engine import Credentials

from config import worker_credentials as creds
from monitor import state


@pytest.fixture
def project(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "radar-test-project")
    monkeypatch.setenv("FIREBASE_EXPECTED_PROJECT", "radar-test-project")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(state, "_scope_provider", None)
    monkeypatch.setattr(state, "_admin", None)
    return "radar-test-project"


def test_attached_adc_disallows_key_files_and_project_mismatch(monkeypatch, project):
    monkeypatch.setenv("FIREBASE_CREDENTIAL_MODE", "adc")
    attached = Credentials()
    monkeypatch.setattr(google.auth, "default", lambda **kw: (attached, project))
    assert state.backend_name() == "firestore"
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "unused")
    with pytest.raises(state.StorageUnavailable):
        state.backend_name()
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")
    monkeypatch.setenv("FIREBASE_EXPECTED_PROJECT", "wrong-project")
    with pytest.raises(state.StorageUnavailable):
        state.backend_name()


def test_unknown_mode_refuses_storage(monkeypatch, project):
    monkeypatch.setenv("FIREBASE_CREDENTIAL_MODE", "unexpected")
    with pytest.raises(state.StorageUnavailable):
        state.backend_name()


def test_cached_refresh_and_redaction():
    class Fake:
        expiry = None
        token = None
        calls = 0

        def refresh(self, request):
            self.calls += 1
            self.token = "synthetic"
            self.expiry = datetime.fromtimestamp(700, timezone.utc)

    fake = Fake()
    getter = creds.cached_token(fake, clock=lambda: 50, request=object())
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: getter(), range(16)))
    assert result == ["synthetic"] * 16 and fake.calls == 1

    def denied(_):
        raise ValueError("sensitive detail")

    fake.refresh = denied
    getter = creds.cached_token(fake, clock=lambda: 650, request=object())
    with pytest.raises(creds.CredentialError, match="^workload access token unavailable$"):
        getter()


def test_federation_rejects_non_external_credentials(monkeypatch, tmp_path, project):
    monkeypatch.setenv("FIREBASE_CREDENTIAL_MODE", "federated")
    path = tmp_path / "fixture.json"
    path.write_text('{"type":"service_account"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    domain = ".iam." + "gserviceaccount.com"
    monkeypatch.setenv("FIREBASE_FEDERATED_ACCOUNT", f"worker@{project}{domain}")
    monkeypatch.setenv("FIREBASE_FEDERATED_AUDIENCE",
                       "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/pool/providers/oidc")
    with pytest.raises(creds.CredentialError, match="configuration mismatch"):
        creds.keyless_token(project, creds.DATASTORE_SCOPES)
