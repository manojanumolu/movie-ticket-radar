"""Explicit keyless credentials for trusted worker and operator processes."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import timezone
from pathlib import Path
from threading import Lock

DATASTORE_SCOPES = ("https://www.googleapis.com/auth/datastore",)
IDENTITY_SCOPES = ("https://www.googleapis.com/auth/identitytoolkit",
                   "https://www.googleapis.com/auth/cloud-platform")


class CredentialError(RuntimeError):
    pass


def mode() -> str:
    value = os.environ.get("FIREBASE_CREDENTIAL_MODE", "legacy")
    if value not in {"legacy", "adc", "federated"}:
        raise CredentialError("invalid Firebase credential mode")
    return value


def cached_token(credentials, *, clock=time.time, request=None):
    from google.auth.transport.requests import Request

    lock = Lock()

    def token() -> str:
        with lock:
            try:
                expiry = credentials.expiry
                if expiry is not None and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if not credentials.token or expiry is None or expiry.timestamp() <= clock() + 120:
                    credentials.refresh(request or Request())
                expiry = credentials.expiry
                if expiry is not None and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if not isinstance(credentials.token, str) or not credentials.token:
                    raise CredentialError("empty workload access token")
                if expiry is None or expiry.timestamp() <= clock():
                    raise CredentialError("expired workload access token")
                return credentials.token
            except Exception:
                raise CredentialError("workload access token unavailable") from None

    return token


def configuration_key(project: str) -> tuple[str, ...]:
    return (project, mode(), *(os.environ.get(name, "") for name in (
        "FIREBASE_EXPECTED_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
        "FIREBASE_FEDERATED_ACCOUNT", "FIREBASE_FEDERATED_AUDIENCE",
    )))


def keyless_token(project: str, scopes: tuple[str, ...]):
    """Create the selected keyless credential. Project pinning is mandatory."""
    import google.auth
    from google.auth import compute_engine

    selected = mode()
    expected = os.environ.get("FIREBASE_EXPECTED_PROJECT", "")
    if project != expected or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise CredentialError("Firebase project does not match the workload configuration")

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    try:
        if selected == "adc" and not path:
            credential, detected = google.auth.default(scopes=scopes)
            if not isinstance(credential, compute_engine.Credentials) or detected != project:
                raise CredentialError("attached Google Cloud identity required")
        elif selected == "federated" and path:
            settings = json.loads(Path(path).read_text(encoding="utf-8"))
            email = os.environ.get("FIREBASE_FEDERATED_ACCOUNT", "")
            audience = os.environ.get("FIREBASE_FEDERATED_AUDIENCE", "")
            service_domain = ".iam." + "gserviceaccount.com"
            if not re.fullmatch(r"[a-z0-9-]+@" + re.escape(project + service_domain), email):
                raise CredentialError("expected federated service account is invalid")
            if not re.fullmatch(r"//iam[.]googleapis[.]com/projects/[0-9]+/locations/global/"
                                r"workloadIdentityPools/[a-z0-9-]+/providers/[a-z0-9-]+", audience):
                raise CredentialError("expected federation provider is invalid")
            impersonation = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                             f"{email}:generateAccessToken")
            if (settings.get("type") != "external_account" or settings.get("audience") != audience
                    or settings.get("token_url") != "https://sts.googleapis.com/v1/token"
                    or settings.get("service_account_impersonation_url") != impersonation):
                raise CredentialError("federation configuration mismatch")
            credential, _ = google.auth.load_credentials_from_dict(settings, scopes=scopes)
        else:
            raise CredentialError("keyless mode requires an attached identity or federation")
    except CredentialError:
        raise
    except Exception:
        raise CredentialError("workload credential configuration unavailable") from None
    return cached_token(credential)
