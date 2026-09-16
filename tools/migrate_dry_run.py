#!/usr/bin/env python3
"""JSON → Firestore migration: the dry run.

    python tools/migrate_dry_run.py

Reads ``data/*.json``, selects exactly the records that should move, encodes
each one with the *current* Firestore encoder, validates the result and prints
the document paths that a real migration would write — **without writing
anything**. There is no Firestore client here at all: only the pure encoder,
and nothing is opened for writing, so the tool can touch neither the service
nor the JSON.

The selection and every check live in :mod:`tools.migration_plan`, which
``migrate_firestore.py`` uses too — so the migration that runs is the one this
validated, not a second implementation of the same idea.

Exit status is 0 when every check passes and non-zero otherwise, so this can
gate the real migration.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.migration_plan import USERS, build_plan, render  # noqa: E402


def main() -> int:
    print("=" * 78)
    print("JSON -> FIRESTORE MIGRATION — DRY RUN (nothing is written)")
    print("=" * 78)

    plan = build_plan()
    render(plan)

    print("\nDOCUMENT PATHS THAT WOULD BE WRITTEN")
    print("  root: projects/<project>/databases/(default)/documents")
    shown: Counter[str] = Counter()
    for collection, doc_id, _ in plan.writes:
        shown[collection] += 1
        if shown[collection] <= 3:
            # A settings document is named after its owner, so the label
            # stands in for the UID here too.
            visible = plan.labels.get(doc_id, doc_id) if collection == USERS else doc_id
            print(f"    {collection}/{visible}")
    for collection, n in shown.items():
        if n > 3:
            print(f"    … and {n - 3} more in {collection}/")

    print("\n" + "=" * 78)
    if not plan.ok:
        print(f"DRY RUN FAILED — {len(plan.problems)} problem(s); nothing would be migrated")
        print("=" * 78)
        return 1
    print("DRY RUN PASSED — the plan above is safe to execute")
    print("NO FIRESTORE WRITES PERFORMED. NO JSON DATA MODIFIED.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
