#!/usr/bin/env python3
"""Set, show or remove the ``admin`` custom claim on one Firebase account.

    python tools/grant_admin.py --uid <firebase-uid>               # show current claims
    python tools/grant_admin.py --uid <firebase-uid> --grant       # admin: true
    python tools/grant_admin.py --uid <firebase-uid> --revoke      # remove it
    python tools/grant_admin.py --email you@example.com ...        # find the account by address
    python tools/grant_admin.py --from-event ...                   # the workflow's input (see below)

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

The UID (or email) is only how the account is *found*; the UID is what
the claim is set on, and the app authorises on the claim, never on the
address.

Nothing secret — and nothing personal — is printed. This repository is
public, and so is every Actions log, so the address is masked to its first
character and domain, the UID to its first four characters, and the only
other output is whether the claim is set before and after.

``--from-event`` is how the workflow passes the account. GitHub prints a
step's ``env:`` block and its command line in the public log, so a
``workflow_dispatch`` input handed over either way is published in full
(that is how an earlier run of this workflow showed an address). Instead
the script reads ``inputs.account`` from the event payload file
(``$GITHUB_EVENT_PATH``), which is never printed, and registers it — and
the account's address once found — as a secret to mask with
``::add-mask::`` before anything else is written.
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


def lookup(project: str, token: str, email: str = "", *, uid: str = "") -> dict[str, Any]:
    body = {"localId": [uid]} if uid else {"email": [email]}
    users = call(project, token, "lookup", body).get("users") or []
    if not users:
        what = f"the UID {mask_uid(uid)}" if uid else f"the address {mask_email(email)}"
        raise SystemExit(f"error: no Firebase account has {what}")
    return users[0]


def mask_in_log(value: str) -> None:
    """Ask GitHub Actions to hide ``value`` wherever it would appear later in
    this job's log. Only inside Actions (elsewhere it would just be noise)."""
    import os

    if value and os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        print(f"::add-mask::{value}", flush=True)


def account_from_event(path: str | None = None) -> str:
    """``inputs.account`` from the ``workflow_dispatch`` event payload."""
    import os

    path = path or os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        raise SystemExit("error: --from-event needs GITHUB_EVENT_PATH (it runs inside Actions)")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("error: the workflow event payload could not be read") from None
    value = str(((event.get("inputs") or {}).get("account")) or "").strip()
    if not value:
        raise SystemExit("error: the workflow was run without an account")
    return value


def mask_email(address: str) -> str:
    """'someone@gmail.com' -> 's***@gmail.com' — the same mask the worker
    uses for recipients, safe in a public log."""
    if "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_uid(uid: str) -> str:
    """Enough to tell two accounts apart when reading a log; never the UID."""
    return f"{uid[:4]}…" if len(uid) > 4 else "…"


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
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--uid", help="the account, by its Firebase UID")
    who.add_argument("--email", help="the account, by its sign-in address")
    who.add_argument("--from-event", action="store_true",
                     help="read the account (a UID or an address) from the workflow_dispatch "
                          "event payload — how the workflow passes it without logging it")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--grant", action="store_true", help="set admin: true")
    mode.add_argument("--revoke", action="store_true", help="remove the admin claim")
    args = parser.parse_args(argv)

    info, problem = service_account_status()
    project = project_from_env()
    if problem or not project:
        print(f"error: {problem or 'FIREBASE_PROJECT_ID is not set'}", file=sys.stderr)
        return 2

    uid_in, email_in = args.uid or "", args.email or ""
    if args.from_event:
        account = account_from_event()
        mask_in_log(account)            # before anything else can print it
        if "@" in account:
            email_in = account
        else:
            uid_in = account

    token = access_token(info or {})
    user = lookup(project, token, email_in, uid=uid_in)
    uid, before = str(user.get("localId", "")), claims_of(user)
    address = str(user.get("email") or email_in)
    mask_in_log(address)
    mask_in_log(uid)
    print(f"account: {mask_email(address)} ({mask_uid(uid)})")
    print(f"admin:   {'true' if before.get(CLAIM) is True else 'false'}")

    if not (args.grant or args.revoke):
        return 0

    after = dict(before)
    if args.grant:
        after[CLAIM] = True
    else:
        after.pop(CLAIM, None)
    if after == before:
        print("Nothing to change.")
        return 0

    # ``customAttributes`` replaces the whole claims object, so the other
    # claims (if any) are carried across rather than dropped.
    call(project, token, "update", {"localId": uid, "customAttributes": json.dumps(after)})
    confirmed = claims_of(lookup(project, token, uid=uid))
    print(f"admin now: {'true' if confirmed.get(CLAIM) is True else 'false'}")
    print("Admin claim updated successfully.")
    print("The person must sign out and back in — or simply reload the app, which "
          "re-reads the account record — before the app sees the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
