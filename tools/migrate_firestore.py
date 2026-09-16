#!/usr/bin/env python3
"""JSON → Firestore migration.

    python tools/migrate_firestore.py              # plan and validate; writes nothing
    python tools/migrate_firestore.py --dry-run    # the same, said out loud
    python tools/migrate_firestore.py --apply      # actually write

**Without ``--apply`` this program cannot write.** The Firestore client is not
even constructed unless ``--apply`` was given, so there is no path from a
plain run to a write.

It shares :mod:`tools.migration_plan` with ``migrate_dry_run.py``, so what is
written here is exactly what that dry run validated: same selection, same
owner stamping, same checks, same reconciliation. And it uses the application's
own ``config.firestore`` — the same client, the same encoder — so nothing about
the documents differs from what the running app would have written itself.

Order of operations, and why:

1. Build the plan and validate it **before touching Firestore**. Any problem
   at all and the program stops, having written nothing.
2. Commit every document in **one** ``:commit``. Firestore applies a commit
   atomically and allows up to 500 writes; this migration is ~35, so either
   all of it lands or none of it does. No half-migrated state exists.
3. Read every document back and check it — count, id, owner, the
   state↔monitor relationship, the round trip, and no nested arrays. A
   failure here exits non-zero and names the document.

Credentials come from the existing worker mechanism and nowhere else:
``FIREBASE_SERVICE_ACCOUNT`` (the JSON, in the environment) and
``FIREBASE_PROJECT_ID``. They are read only on the ``--apply`` path.

The JSON files are never modified. Cleaning up the records left behind is a
separate job, for after the cutover is verified.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import firestore as fs  # noqa: E402
from tools.migration_plan import (  # noqa: E402
    HISTORY,
    MONITORS,
    STATES,
    USERS,
    Plan,
    build_plan,
    decoded,
    encoded,
    nested_arrays,
    owner_of,
    render,
)


def connect() -> fs.FirestoreClient:
    """The worker's own credential path — built only when writing."""
    info = fs.service_account_from_env()
    project = fs.project_from_env()
    if not info:
        raise SystemExit("FIREBASE_SERVICE_ACCOUNT is not set (or is not valid "
                         "service-account JSON) — cannot write.")
    if not project:
        raise SystemExit("FIREBASE_PROJECT_ID is not set — cannot write.")
    print(f"  connecting to project {project} as the migration service account")
    return fs.FirestoreClient(project, fs.service_account_token_getter(info))


def verify_written(client: fs.FirestoreClient, plan: Plan) -> list[str]:
    """Read every migrated document back and prove it is what we meant."""
    failures: list[str] = []
    seen: dict[str, int] = {}

    for collection, doc_id, expected in plan.writes:
        where = f"{collection}/{doc_id}"
        try:
            stored = client.get(collection, doc_id)
        except fs.FirestoreError as exc:
            failures.append(f"{where}: could not be read back ({exc})")
            continue
        if stored is None:
            failures.append(f"{where}: missing after the commit")
            continue

        seen[collection] = seen.get(collection, 0) + 1
        if stored != expected:
            differing = sorted(k for k in set(stored) | set(expected)
                               if stored.get(k) != expected.get(k))
            failures.append(f"{where}: does not match what was sent; fields differ: {differing}")
        if not owner_of(stored):
            failures.append(f"{where}: has no owner_uid in Firestore")
        for path in nested_arrays(encoded(stored), f"{where}."):
            failures.append(f"{where}: array inside array at {path}")
        if decoded(encoded(stored)) != stored:
            failures.append(f"{where}: does not round-trip after the write")
        if collection == MONITORS and stored.get("id") != doc_id:
            failures.append(f"{where}: monitor id was not preserved")
        if collection == STATES and owner_of(stored) != plan.owner_of_monitor.get(doc_id):
            failures.append(f"{where}: owner does not match its monitor")
        if collection == USERS and owner_of(stored) != doc_id:
            failures.append(f"{where}: user document is not named after its owner")

    for collection, expected_n in plan.counts.items():
        if seen.get(collection, 0) != expected_n:
            failures.append(f"{collection}: read back {seen.get(collection, 0)} of {expected_n}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the JSON records into Firestore")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it nothing is written")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the plan (the default)")
    args = parser.parse_args(argv)

    writing = bool(args.apply) and not args.dry_run
    print("=" * 78)
    print("JSON -> FIRESTORE MIGRATION" + ("" if writing else " — DRY RUN (nothing is written)"))
    print("=" * 78)

    plan = build_plan()
    render(plan)

    print("\nPLAN")
    for collection in (MONITORS, STATES, HISTORY, USERS):
        print(f"  {collection:<14} {plan.counts[collection]:>3}")
    print(f"  {'total writes':<14} {len(plan.writes):>3}   (one atomic commit)")

    if not plan.ok:
        print("\n" + "=" * 78)
        print(f"STOPPED — {len(plan.problems)} validation problem(s). Nothing was written.")
        print("=" * 78)
        return 1

    if not writing:
        print("\n" + "=" * 78)
        print("VALIDATION PASSED — re-run with --apply to write.")
        print("NO FIRESTORE WRITES PERFORMED. NO JSON DATA MODIFIED.")
        print("=" * 78)
        return 0

    # ---- from here on we write ------------------------------------------
    print("\nWRITING")
    client = connect()
    try:
        client.commit(list(plan.writes))
    except fs.FirestoreError as exc:
        print(f"  !! the commit was refused: {exc}")
        print("\n" + "=" * 78)
        print("MIGRATION FAILED — a commit is atomic, so nothing was written.")
        print("=" * 78)
        return 1
    print(f"  committed {len(plan.writes)} document(s) atomically")

    print("\nVERIFYING WHAT WAS WRITTEN")
    failures = verify_written(client, plan)
    for failure in failures:
        print(f"  !! {failure}")
    if failures:
        print("\n" + "=" * 78)
        print(f"POST-WRITE VERIFICATION FAILED — {len(failures)} problem(s) above.")
        print("The documents are in Firestore but do not match the plan; do not cut over.")
        print("=" * 78)
        return 1

    for collection in (MONITORS, STATES, HISTORY, USERS):
        print(f"  {collection:<14} {plan.counts[collection]:>3}  read back and verified")
    print("\n" + "=" * 78)
    print("MIGRATION COMPLETE AND VERIFIED.")
    print("JSON data was not modified; the excluded records are still in data/.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
