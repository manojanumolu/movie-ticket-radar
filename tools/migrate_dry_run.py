#!/usr/bin/env python3
"""JSON → Firestore migration: the dry run.

    python tools/migrate_dry_run.py

Reads ``data/*.json``, selects exactly the records that should move, encodes
each one with the *current* Firestore encoder, validates the result and prints
the document paths that a real migration would write — **without writing
anything**. There is no Firestore client here at all: only
``config.firestore.encode``, which is a pure function. Nothing is opened for
writing, so the tool cannot alter the JSON either.

What moves, and what does not (the decision recorded 2026-09-16):

* **monitors** — the ones carrying an ``owner_uid``. The ownerless ones are
  disposable test monitors and are left behind.
* **monitor_state** — only for migrated monitors, each stamped with that
  monitor's owner. The orphan state whose monitor no longer exists is left.
* **history** — records that already carry an owner, plus records with no
  owner whose ``monitor_id`` points at a migrated monitor, which take their
  owner from that parent. History belonging to a test monitor, or to a monitor
  that no longer exists, is left behind: there is no owner to recover.
* **users** — the settings document of every owner that has one.

Ownership is never guessed. A record's owner comes from the record itself or
from its parent monitor, and from nowhere else.

Exit status is 0 when every check passes and non-zero on the first failure, so
this can gate the real migration.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import firestore as fs  # noqa: E402

DATA = ROOT / "data"
#: Mirrors monitor.state; kept here so the tool never imports the live store.
MONITORS, STATES, HISTORY, USERS = "monitors", "monitor_state", "history", "users"
OWNER = fs.OWNER

problems: list[str] = []


def fail(message: str) -> None:
    problems.append(message)
    print(f"  !! {message}")


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def owner_of(record: dict) -> str:
    return str(record.get(OWNER, "") or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────
def nested_arrays(fields: dict, where: str = "") -> list[str]:
    """Every place an encoded document puts an array directly inside another.
    Firestore refuses such a document with INVALID_ARGUMENT."""
    found: list[str] = []

    def walk(node, path: str, in_array: bool) -> None:
        if not isinstance(node, dict):
            return
        if "arrayValue" in node:
            if in_array:
                found.append(path)
            for i, item in enumerate(node["arrayValue"].get("values") or []):
                walk(item, f"{path}[{i}]", True)
        elif "mapValue" in node:
            for key, value in (node["mapValue"].get("fields") or {}).items():
                walk(value, f"{path}.{key}", False)

    for key, value in (fields or {}).items():
        walk(value, f"{where}{key}", False)
    return found


def encoded(record: dict) -> dict:
    """The document exactly as the live code would send it."""
    return {k: fs.encode(v, field=k) for k, v in record.items()}


def check_document(collection: str, doc_id: str, record: dict) -> dict:
    """Encode one document and run every rule over it."""
    where = f"{collection}/{doc_id}"
    fields = encoded(record)

    for path in nested_arrays(fields, f"{where}."):
        fail(f"array inside array at {path} — Firestore would refuse this write")

    if not owner_of(record):
        fail(f"{where} has no {OWNER}")

    # The encoder must be lossless: what goes in is what comes back.
    back = {k: fs.decode(v, field=k) for k, v in fields.items()}
    if back != record:
        differing = sorted(k for k in set(back) | set(record) if back.get(k) != record.get(k))
        fail(f"{where} does not round-trip; fields differ: {differing}")
    return fields


# ──────────────────────────────────────────────────────────────────────────
# Selection
# ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    monitors = load("monitors.json")
    state = load("state.json")
    history = load("history.json")
    settings = load("settings.json")
    users = settings.get("users", {}) if isinstance(settings, dict) else {}

    owned = [m for m in monitors if owner_of(m)]
    unowned = [m for m in monitors if not owner_of(m)]
    owned_ids = {m["id"]: owner_of(m) for m in owned}
    unowned_ids = {m["id"] for m in unowned}
    all_ids = set(owned_ids) | unowned_ids

    # Owners get a stable label so counts can be reported without ever
    # printing a UID or an address.
    label = {uid: f"owner #{i}" for i, uid in enumerate(sorted(set(owned_ids.values())), 1)}

    print("=" * 78)
    print("JSON -> FIRESTORE MIGRATION — DRY RUN (nothing is written)")
    print("=" * 78)

    # -- monitors ---------------------------------------------------------
    plan: list[tuple[str, str, dict]] = []
    for m in owned:
        plan.append((MONITORS, m["id"], m))

    # -- state: only for migrated monitors, owner taken from the monitor ---
    state_plan, state_test, state_orphan = [], [], []
    for monitor_id, record in state.items():
        if monitor_id in owned_ids:
            stamped = {**record, OWNER: owned_ids[monitor_id]}
            state_plan.append((STATES, monitor_id, stamped))
        elif monitor_id in unowned_ids:
            state_test.append(monitor_id)
        else:
            state_orphan.append(monitor_id)
    plan.extend(state_plan)

    # -- history ----------------------------------------------------------
    hist_owned, hist_recovered, hist_test, hist_gone = [], [], [], []
    for item in history:
        mid = str(item.get("monitor_id", "") or "")
        if owner_of(item):
            hist_owned.append(item)
        elif mid in owned_ids:
            # Its parent monitor is migrating and its owner is known, so the
            # record takes that owner. Never a guess.
            hist_recovered.append({**item, OWNER: owned_ids[mid]})
        elif mid in unowned_ids:
            hist_test.append(item)
        else:
            hist_gone.append(item)

    for item in hist_owned + hist_recovered:
        mid = str(item.get("monitor_id", "") or "")
        stamp = str(item.get("at", "")).replace(":", "").replace("-", "")[:15] or "0"
        # The live code appends secrets.token_hex(3), minted at write time.
        plan.append((HISTORY, f"{stamp}-{mid[:8]}-<rand>", item))

    # -- users ------------------------------------------------------------
    for uid in sorted(users):
        plan.append((USERS, uid, {**users[uid], OWNER: uid}))

    # -- validate ---------------------------------------------------------
    print("\nVALIDATION")
    for collection, doc_id, record in plan:
        check_document(collection, doc_id, record)

    for collection, monitor_id, record in state_plan:
        if record.get(OWNER) != owned_ids.get(monitor_id):
            fail(f"{collection}/{monitor_id} owner does not match its monitor")

    known = set(owned_ids.values())
    for uid in users:
        if uid not in known:
            fail(f"users/{label.get(uid, 'unknown owner')} does not own any migrating monitor")

    for _, doc_id, record in plan:
        if _ == MONITORS and doc_id != record.get("id"):
            fail(f"monitor id not preserved for {doc_id}")

    ids = [d for c, d, _ in plan if c == HISTORY]
    if len(set(ids)) != len(ids):
        fail("two history documents would collide on the same id prefix")

    if not problems:
        print("  no nested arrays, every document owned, state owners match,")
        print("  users are known owners, monitor ids preserved, all round-trip.")

    # -- counts, per owner, no identifiers --------------------------------
    print("\nMIGRATING")
    per_owner = Counter(label[owned_ids[m['id']]] for m in owned)
    print(f"  {MONITORS:<14} {len(owned):>3}   " + ", ".join(f"{k}: {v}" for k, v in sorted(per_owner.items())))
    print(f"  {STATES:<14} {len(state_plan):>3}   "
          + ", ".join(f"{k}: {v}" for k, v in sorted(Counter(label[r[OWNER]] for _, _, r in state_plan).items())))
    hist_all = hist_owned + hist_recovered
    print(f"  {HISTORY:<14} {len(hist_all):>3}   "
          + ", ".join(f"{k}: {v}" for k, v in sorted(Counter(label[owner_of(h)] for h in hist_all).items())))
    print(f"                        ({len(hist_owned)} already owned + {len(hist_recovered)} owner recovered from parent monitor)")
    print(f"  {USERS:<14} {len(users):>3}   " + ", ".join(f"{label[u]}: 1" for u in sorted(users)))

    print("\nLEAVING BEHIND (intentional)")
    print(f"  ownerless test monitors            {len(unowned):>3}")
    print(f"  their state records                {len(state_test):>3}")
    print(f"  their history records              {len(hist_test):>3}")
    print(f"  history of monitors long deleted   {len(hist_gone):>3}")
    print(f"  orphan state (no monitor exists)   {len(state_orphan):>3}   {state_orphan}")

    # -- reconciliation ---------------------------------------------------
    print("\nRECONCILIATION")
    checks = [
        ("monitors", len(monitors), [("migrated", len(owned)), ("test monitors", len(unowned))]),
        ("state", len(state), [("migrated", len(state_plan)), ("test", len(state_test)), ("orphan", len(state_orphan))]),
        ("history", len(history), [("migrated", len(hist_all)), ("test-monitor", len(hist_test)), ("deleted-monitor", len(hist_gone))]),
        ("settings", len(users), [("migrated", len(users))]),
    ]
    for name, total, parts in checks:
        summed = sum(n for _, n in parts)
        ok = summed == total
        detail = " + ".join(f"{n} {what}" for what, n in parts)
        print(f"  {'OK ' if ok else 'BAD'} {name:<9} {total:>3} = {detail}")
        if not ok:
            fail(f"{name} does not reconcile: {total} != {summed}")

    # -- the paths that would be written ----------------------------------
    print("\nDOCUMENT PATHS THAT WOULD BE WRITTEN")
    print("  root: projects/<project>/databases/(default)/documents")
    shown = Counter()
    for collection, doc_id, _ in plan:
        shown[collection] += 1
        if shown[collection] <= 3:
            # A settings document is named after its owner, so the label
            # stands in for the UID here too.
            visible = label.get(doc_id, doc_id) if collection == USERS else doc_id
            print(f"    {collection}/{visible}")
    for collection, n in shown.items():
        if n > 3:
            print(f"    … and {n - 3} more in {collection}/")

    print("\n" + "=" * 78)
    if problems:
        print(f"DRY RUN FAILED — {len(problems)} problem(s); nothing would be migrated")
        print("=" * 78)
        return 1
    print("DRY RUN PASSED — the plan above is safe to execute")
    print("NO FIRESTORE WRITES PERFORMED. NO JSON DATA MODIFIED.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
