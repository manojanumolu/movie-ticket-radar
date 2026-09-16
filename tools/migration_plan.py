"""What moves from the JSON files to Firestore, and the checks that gate it.

The planner is shared by the two migration tools so the real migration cannot
drift from the dry run that was signed off: same selection, same stamping,
same validation, same reconciliation. Only the writing differs.

Nothing here writes anything. It reads ``data/*.json``, builds the list of
documents a migration would commit, and reports every problem it finds.

What moves, per the ownership decision of 2026-09-16:

* **monitors** — the ones carrying an ``owner_uid``. The ownerless ones are
  disposable test monitors and are left behind.
* **monitor_state** — only for migrating monitors, each stamped with that
  monitor's owner. The orphan state whose monitor no longer exists is left.
* **history** — records that already carry an owner, plus records with no
  owner whose ``monitor_id`` points at a migrating monitor, which take their
  owner from that parent. History belonging to a test monitor, or to a monitor
  that no longer exists, stays: there is no owner to recover.
* **users** — the settings document of every owner that has one.

Ownership is never guessed. An owner comes from the record itself or from its
parent monitor, or the record does not move.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import firestore as fs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

#: The collections, spelled as ``monitor.state`` spells them. Kept here so a
#: migration never imports the live store and cannot touch it.
MONITORS, STATES, HISTORY, USERS = "monitors", "monitor_state", "history", "users"
OWNER = fs.OWNER

Write = tuple[str, str, dict[str, Any]]


def _stable_suffix(record: dict[str, Any]) -> str:
    """Six hex characters derived from the record itself.

    The live code mints a random suffix for a *new* history entry, which is
    right when the entry has just happened. A migration is different: it may
    have to be run twice, and a random id would import the same history a
    second time instead of overwriting it. Deriving the suffix from the
    content makes the migration idempotent.
    """
    blob = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:6]


def history_doc_id(item: dict[str, Any], suffix: Callable[[dict], str] = _stable_suffix) -> str:
    """``<stamp>-<monitor prefix>-<suffix>``, the shape the live store uses."""
    monitor_id = str(item.get("monitor_id", "") or "")
    stamp = str(item.get("at", "")).replace(":", "").replace("-", "")[:15] or "0"
    return f"{stamp}-{monitor_id[:8]}-{suffix(item)}"


def owner_of(record: dict[str, Any]) -> str:
    return str(record.get(OWNER, "") or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────
def nested_arrays(fields: dict[str, Any], where: str = "") -> list[str]:
    """Every place an encoded document puts an array directly inside another.
    Firestore refuses such a document with INVALID_ARGUMENT."""
    found: list[str] = []

    def walk(node: Any, path: str, in_array: bool) -> None:
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


def encoded(record: dict[str, Any]) -> dict[str, Any]:
    """The document exactly as the live store would send it."""
    return {k: fs.encode(v, field=k) for k, v in record.items()}


def decoded(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: fs.decode(v, field=k) for k, v in fields.items()}


@dataclass
class Plan:
    """Everything a migration would do, and everything wrong with it."""

    writes: list[Write] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)
    reconciliation: list[tuple[str, int, list[tuple[str, int]], bool]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: owner uid -> a stable label, so counts can be reported without ever
    #: printing a UID or an address.
    labels: dict[str, str] = field(default_factory=dict)
    owner_of_monitor: dict[str, str] = field(default_factory=dict)
    orphan_state: list[str] = field(default_factory=list)
    history_owner_recovered: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def per_owner(self, collection: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for coll, _, record in self.writes:
            if coll == collection:
                who = self.labels.get(owner_of(record), "?")
                out[who] = out.get(who, 0) + 1
        return dict(sorted(out.items()))


def load(name: str, data_dir: Path | None = None) -> Any:
    return json.loads((data_dir or DATA).joinpath(name).read_text(encoding="utf-8"))


def build_plan(*, data_dir: Path | None = None,
               suffix: Callable[[dict], str] = _stable_suffix) -> Plan:
    """Select the records, stamp the recoverable owners, and validate."""
    monitors = load("monitors.json", data_dir)
    state = load("state.json", data_dir)
    history = load("history.json", data_dir)
    settings = load("settings.json", data_dir)
    users = settings.get("users", {}) if isinstance(settings, dict) else {}

    plan = Plan()

    owned = [m for m in monitors if owner_of(m)]
    unowned = [m for m in monitors if not owner_of(m)]
    owned_ids = {m["id"]: owner_of(m) for m in owned}
    unowned_ids = {m["id"] for m in unowned}
    plan.owner_of_monitor = dict(owned_ids)
    plan.labels = {uid: f"owner #{i}" for i, uid in enumerate(sorted(set(owned_ids.values())), 1)}

    # -- monitors: exactly as they stand; the id is the document name -------
    for monitor in owned:
        plan.writes.append((MONITORS, monitor["id"], monitor))

    # -- state: only for migrating monitors, owner taken from the monitor ---
    state_test, state_orphan = [], []
    state_writes: list[Write] = []
    for monitor_id, record in state.items():
        if monitor_id in owned_ids:
            state_writes.append((STATES, monitor_id, {**record, OWNER: owned_ids[monitor_id]}))
        elif monitor_id in unowned_ids:
            state_test.append(monitor_id)
        else:
            state_orphan.append(monitor_id)
    plan.writes.extend(state_writes)
    plan.orphan_state = state_orphan

    # -- history ------------------------------------------------------------
    hist_keep: list[dict[str, Any]] = []
    hist_test = hist_gone = 0
    for item in history:
        monitor_id = str(item.get("monitor_id", "") or "")
        if owner_of(item):
            hist_keep.append(item)
        elif monitor_id in owned_ids:
            # Its parent monitor is migrating and that owner is known.
            hist_keep.append({**item, OWNER: owned_ids[monitor_id]})
            plan.history_owner_recovered += 1
        elif monitor_id in unowned_ids:
            hist_test += 1
        else:
            hist_gone += 1
    for item in hist_keep:
        plan.writes.append((HISTORY, history_doc_id(item, suffix), item))

    # -- users --------------------------------------------------------------
    for uid in sorted(users):
        plan.writes.append((USERS, uid, {**users[uid], OWNER: uid}))

    plan.counts = {
        MONITORS: len(owned),
        STATES: len(state_writes),
        HISTORY: len(hist_keep),
        USERS: len(users),
    }
    plan.excluded = {
        "test monitors": len(unowned),
        "their state": len(state_test),
        "their history": hist_test,
        "history of deleted monitors": hist_gone,
        "orphan state": len(state_orphan),
    }

    _validate(plan, monitors, state, history, users, owned_ids)
    return plan


def _validate(plan: Plan, monitors: list, state: dict, history: list,
              users: dict, owned_ids: dict[str, str]) -> None:
    def fail(message: str) -> None:
        plan.problems.append(message)

    seen: set[tuple[str, str]] = set()
    for collection, doc_id, record in plan.writes:
        where = f"{collection}/{doc_id}"
        fields = encoded(record)

        for path in nested_arrays(fields, f"{where}."):
            fail(f"array inside array at {path} — Firestore would refuse this write")
        if not owner_of(record):
            fail(f"{where} has no {OWNER}")
        if decoded(fields) != record:
            differing = sorted(k for k in set(record) | set(decoded(fields))
                               if decoded(fields).get(k) != record.get(k))
            fail(f"{where} does not round-trip; fields differ: {differing}")
        if (collection, doc_id) in seen:
            fail(f"{where} appears twice in the plan")
        seen.add((collection, doc_id))

    # ids preserved for everything that already had one
    for collection, doc_id, record in plan.writes:
        if collection == MONITORS and doc_id != record.get("id"):
            fail(f"monitor id not preserved: {doc_id} != {record.get('id')}")
        if collection == STATES and doc_id not in owned_ids:
            fail(f"{STATES}/{doc_id} does not belong to a migrating monitor")
        if collection == USERS and doc_id != owner_of(record):
            fail(f"{USERS}/{doc_id} is not named after its owner")

    # state owner must equal its monitor's owner
    for collection, doc_id, record in plan.writes:
        if collection == STATES and owner_of(record) != owned_ids.get(doc_id):
            fail(f"{STATES}/{doc_id} owner does not match its monitor")

    # every settings document belongs to an owner that is actually migrating
    known = set(owned_ids.values())
    for uid in users:
        if uid not in known:
            fail(f"{USERS}/{plan.labels.get(uid, 'unknown')} owns no migrating monitor")

    # reconciliation against the source files
    checks = [
        ("monitors", len(monitors),
         [("migrated", plan.counts[MONITORS]), ("test monitors", plan.excluded["test monitors"])]),
        ("state", len(state),
         [("migrated", plan.counts[STATES]), ("test", plan.excluded["their state"]),
          ("orphan", plan.excluded["orphan state"])]),
        ("history", len(history),
         [("migrated", plan.counts[HISTORY]), ("test-monitor", plan.excluded["their history"]),
          ("deleted-monitor", plan.excluded["history of deleted monitors"])]),
        ("settings", len(users), [("migrated", plan.counts[USERS])]),
    ]
    for name, total, parts in checks:
        ok = sum(n for _, n in parts) == total
        plan.reconciliation.append((name, total, parts, ok))
        if not ok:
            fail(f"{name} does not reconcile: {total} != {sum(n for _, n in parts)}")


def render(plan: Plan, out=print) -> None:
    """The plan, for a human. Never prints a UID or an address."""
    out("\nVALIDATION")
    if plan.ok:
        out("  no nested arrays, every document owned, state owners match,")
        out("  users are known owners, ids preserved, all round-trip.")
    for problem in plan.problems:
        out(f"  !! {problem}")

    out("\nMIGRATING")
    for collection in (MONITORS, STATES, HISTORY, USERS):
        per = ", ".join(f"{k}: {v}" for k, v in plan.per_owner(collection).items())
        out(f"  {collection:<14} {plan.counts[collection]:>3}   {per}")
    out(f"                        ({plan.counts[HISTORY] - plan.history_owner_recovered} already owned"
        f" + {plan.history_owner_recovered} owner recovered from parent monitor)")

    out("\nLEAVING BEHIND (intentional)")
    for what, n in plan.excluded.items():
        extra = f"   {plan.orphan_state}" if what == "orphan state" else ""
        out(f"  {what:<32} {n:>3}{extra}")

    out("\nRECONCILIATION")
    for name, total, parts, ok in plan.reconciliation:
        detail = " + ".join(f"{n} {what}" for what, n in parts)
        out(f"  {'OK ' if ok else 'BAD'} {name:<9} {total:>3} = {detail}")


__all__ = ["HISTORY", "MONITORS", "OWNER", "STATES", "USERS", "Plan", "build_plan",
           "encoded", "decoded", "history_doc_id", "nested_arrays", "owner_of", "render"]
