#!/usr/bin/env python3
"""Set, show or remove the ``admin`` custom claim on one Firebase account.

    python tools/grant_admin.py --email you@example.com            # show current claims
    python tools/grant_admin.py --email you@example.com --grant    # admin: true
    python tools/grant_admin.py --email you@example.com --revoke   # remove it

Why this exists
---------------
The app lifts its active-monitor limit for an account whose Firebase record
carries the custom claim ``{"admin": true}`` (``auth.firebase.admin_claim``).
Custom claims can only be written by an administrator holding the project's
service account — the Admin SDK's ``setCustomUserClaims``, which is the REST
call below. The Firebase console has no page for them, and no client call
can set one, which is precisely what makes the claim trustworthy: a person
cannot give it to themselves, and the app never has the credential that
could.

Where it runs
-------------
Wherever ``FIREBASE_SERVICE_ACCOUNT`` and ``FIREBASE_PROJECT_ID`` are in the
environment — which, by design, is GitHub Actions and nowhere the browser can
reach. ``.github/workflows/grant-admin.yml`` runs this on demand with the
secrets the worker already has, so the key never has to be on a laptop. It
can also run locally with the same two variables set.

The email is only how the account is *found*; the UID is what the claim is
set on, and the app authorises on the claim, never on the address.

Nothing secret is printed: the address you typed, the UID Firebase reports
for it, and the claims before and after.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.firestore import project_from_env, service_account_status  # noqa: E402

ADMIN_API = "https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:{action}"
SCOPES = ("https://www.googleapis.com/auth/identitytoolkit",
          "https://www.googleapis.com/auth/cloud-platform")
CLAIM = "admin"
TIMEOUT = 20.0


def access_token(info: dict[str, Any]) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(info, scopes=list(SCOPES))
    creds.refresh(Request())
    return str(creds.token)


def call(project: str, token: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(ADMIN_API.format(project=project, action=action), json=body,
                             headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        message = ((payload.get("error") or {}).get("message") or f"HTTP {response.status_code}")
        if response.status_code == 403:
            message += ("\n  The service account cannot administer Authentication. In Google Cloud "
                        "IAM give it the role 'Firebase Authentication Admin' (or use the "
                        "firebase-adminsdk service account the console generates), then retry.")
        raise SystemExit(f"error: accounts:{action}: {message}")
    return payload


def lookup(project: str, token: str, email: str) -> dict[str, Any]:
    users = call(project, token, "lookup", {"email": [email]}).get("users") or []
    if not users:
        raise SystemExit(f"error: no Firebase account has the address {email}")
    return users[0]


def claims_of(user: dict[str, Any]) -> dict[str, Any]:
    raw = user.get("customAttributes")
    if not raw:
        return {}
    try:
        claims = json.loads(raw)
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the TicketRadar admin claim on one account")
    parser.add_argument("--email", required=True, help="the account, by its sign-in address")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--grant", action="store_true", help="set admin: true")
    mode.add_argument("--revoke", action="store_true", help="remove the admin claim")
    args = parser.parse_args(argv)

    info, problem = service_account_status()
    project = project_from_env()
    if problem or not project:
        print(f"error: {problem or 'FIREBASE_PROJECT_ID is not set'}", file=sys.stderr)
        return 2

    token = access_token(info or {})
    user = lookup(project, token, args.email)
    uid, before = str(user.get("localId", "")), claims_of(user)
    print(f"account: {args.email}")
    print(f"uid:     {uid}")
    print(f"claims:  {json.dumps(before)}")
    print(f"admin:   {'true' if before.get(CLAIM) is True else 'false'}")

    if not (args.grant or args.revoke):
        return 0

    after = dict(before)
    if args.grant:
        after[CLAIM] = True
    else:
        after.pop(CLAIM, None)
    if after == before:
        print("nothing to change")
        return 0

    # ``customAttributes`` replaces the whole claims object, so the other
    # claims (if any) are carried across rather than dropped.
    call(project, token, "update", {"localId": uid, "customAttributes": json.dumps(after)})
    confirmed = claims_of(lookup(project, token, args.email))
    print(f"claims now: {json.dumps(confirmed)}")
    print(f"admin now:  {'true' if confirmed.get(CLAIM) is True else 'false'}")
    print("The person must sign out and back in — or simply reload the app, which "
          "re-reads the account record — before the app sees the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
