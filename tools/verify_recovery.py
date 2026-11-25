"""Compare two private recovery snapshots and report counts only."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

COLLECTIONS = ("users", "monitors", "monitor_state", "history")
REQUIRED = {"auth", *COLLECTIONS}
ISOLATED = re.compile(r"(?:demo-ticketradar|radar-recovery)-[a-z0-9-]+")


def _index(snapshot):
    records = snapshot.get("collections")
    users = snapshot.get("auth", {}).get("users")
    if not isinstance(records, dict) or set(records) != set(COLLECTIONS) or not isinstance(users, list):
        raise ValueError("snapshot omitted required data")
    auth = {}
    for account in users:
        uid = account.get("localId")
        if not isinstance(uid, str) or not uid or uid in auth:
            raise ValueError("invalid or duplicate Firebase UID")
        identity = dict(account)
        claims = identity.get("customAttributes")
        if isinstance(claims, str):
            identity["customAttributes"] = json.loads(claims)
        for key in ("providerUserInfo", "mfaInfo"):
            if key in identity:
                identity[key] = sorted(identity[key], key=lambda item: json.dumps(item, sort_keys=True))
        auth[uid] = identity
    for rows in records.values():
        if not isinstance(rows, dict) or any(not key or not isinstance(item, dict) for key, item in rows.items()):
            raise ValueError("invalid document map")
    return {**records, "auth": auth}


def ownership_problems(data) -> dict[str, set[str]]:
    def unlinked(collection, validate=lambda *_: True):
        return {doc_id for doc_id, doc in data[collection].items()
                if not isinstance(doc.get("owner_uid"), str)
                or doc["owner_uid"] not in data["auth"] or not validate(doc_id, doc)}

    def state_owner(key, doc):
        monitor = data["monitors"].get(key)
        return monitor is not None and monitor.get("owner_uid") == doc.get("owner_uid")

    return {
        "users": unlinked("users", lambda key, row: key == row.get("owner_uid")),
        "monitors": unlinked("monitors", lambda key, row: row.get("id", key) == key),
        "monitor_state": unlinked("monitor_state", state_owner),
        # History can outlive a deleted monitor. Check ownership when it exists.
        "history": unlinked("history", lambda _, row: (
            row.get("monitor_id") not in data["monitors"]
            or row.get("owner_uid") == data["monitors"][row["monitor_id"]].get("owner_uid")
        )),
    }


def verify(source, restored):
    source_project = source.get("project_id")
    target_project = restored.get("project_id")
    if not isinstance(source_project, str) or not source_project:
        raise ValueError("source snapshot project is required")
    if not isinstance(target_project, str) or not ISOLATED.fullmatch(target_project) or target_project == source_project:
        raise ValueError("restored snapshot must come from a distinct isolated project")
    left, right = _index(source), _index(restored)
    prior, after = ownership_problems(left), ownership_problems(right)
    return {
        name: {
            "source": len(left[name]),
            "restored": len(right[name]),
            "missing": len(left[name].keys() - right[name].keys()),
            "unexpected": len(right[name].keys() - left[name].keys()),
            "changed": sum(
                json.dumps(left[name][key], sort_keys=True, allow_nan=False)
                != json.dumps(right[name][key], sort_keys=True, allow_nan=False)
                for key in left[name].keys() & right[name].keys()
            ),
            "source_ownership_issues": len(prior.get(name, ())),
            "new_ownership_issues": len(after.get(name, set()) - prior.get(name, set())),
        }
        for name in sorted(REQUIRED)
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--restored", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.source.open(encoding="utf-8") as left, args.restored.open(encoding="utf-8") as right:
            counts = verify(json.load(left), json.load(right))
    except (OSError, ValueError, TypeError, AttributeError):
        print("Recovery verification rejected the input snapshots.", file=sys.stderr)
        return 2
    print(json.dumps(counts, indent=2))
    return int(any(row[k] for row in counts.values()
                   for k in ("missing", "unexpected", "changed", "new_ownership_issues")))


if __name__ == "__main__":
    raise SystemExit(main())
