"""Export synthetic Auth and Firestore snapshots from loopback emulators."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

from config.firestore import BASE, FirestoreClient, FirestoreError
from tools.verify_recovery import COLLECTIONS

PROJECT = re.compile(r"demo-ticketradar(?:-[a-z0-9-]+)?")
HEADERS = {"Authorization": "Bearer owner"}

# These fields verify identity and login routing. Credential material belongs
# in an approved managed export, never in the comparable drill manifest.
AUTH_FIELDS = ("localId", "email", "emailVerified", "disabled", "customAttributes",
               "providerUserInfo", "mfaInfo", "tenantId", "displayName", "photoUrl")


def snapshot(project: str, *, firestore_port=8080, auth_port=9099, session=None):
    if not PROJECT.fullmatch(project) or any(not 1 <= p <= 65535 for p in (firestore_port, auth_port)):
        raise ValueError("a demo-ticketradar emulator project and loopback ports are required")
    session = session or requests.Session()
    session.trust_env = False

    def transport(method, url, token, body):
        local = url.removeprefix(BASE)
        if not local.startswith("/projects/"):
            raise ValueError("unexpected Firestore endpoint")
        response = session.request(method, f"http://127.0.0.1:{firestore_port}/v1{local}",
                                   headers=HEADERS, json=body, timeout=10, allow_redirects=False)
        return response.status_code, response.json()

    url = f"http://127.0.0.1:{auth_port}/identitytoolkit.googleapis.com/v1/projects/{project}/accounts:batchGet"
    token = ""
    seen = set()
    users = []
    while True:
        params = {"maxResults": 1000}
        if token:
            params["nextPageToken"] = token
        response = session.get(url, params=params, headers=HEADERS, timeout=10, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Auth emulator export failed")
        data = response.json()
        users.extend({key: user[key] for key in AUTH_FIELDS if key in user} for user in data.get("users", []))
        token = data.get("nextPageToken")
        if not token:
            break
        if token in seen:
            raise ValueError("repeated Auth export page")
        seen.add(token)

    client = FirestoreClient(project, lambda: "owner", transport=transport)
    return {"project_id": project, "auth": {"users": users},
            "collections": {name: client.query(name) for name in COLLECTIONS}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--firestore-port", type=int, default=8080)
    parser.add_argument("--auth-port", type=int, default=9099)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        captured = snapshot(args.project, firestore_port=args.firestore_port, auth_port=args.auth_port)
        contents = json.dumps(captured, indent=2, allow_nan=False)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(contents)
    except (OSError, ValueError, TypeError, FirestoreError, requests.RequestException):
        print("Emulator snapshot export failed.", file=sys.stderr)
        return 1
    print(f"Emulator snapshot written for {len(captured['auth']['users'])} account(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
