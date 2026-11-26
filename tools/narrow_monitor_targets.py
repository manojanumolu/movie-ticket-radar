#!/usr/bin/env python3
"""Narrow one existing monitor to a set of its own targets, or resume it, in place.

    python tools/narrow_monitor_targets.py --monitor af1711d4 \
        --keep "ALLU Cinemas · Dolby Cinema" --keep "Prasads Multiplex · PCX"          # dry run
    ... --apply                                                                           # write targets
    python tools/narrow_monitor_targets.py --monitor af1711d4 --resume --apply            # ACTIVE, same end time

Runs with the worker's Firestore credential (FIREBASE_PROJECT_ID +
FIREBASE_SERVICE_ACCOUNT), from the manual "Monitor maintenance" workflow.
Never recreates the document and never touches monitor_state or history.

Narrowing:
  * exactly one document whose ID starts with --monitor, or it refuses;
  * every --keep label must already be one of its targets;
  * it must not be ACTIVE (the worker is not mid-check on it);
  * only ``targets`` changes — created_at, monitor_until, owner, recipient,
    interval, status and everything else stay byte-identical — and the
    document is read back to prove it.

Resume (``--resume``):
  * sets ``status`` ACTIVE and clears ``stopped_at``/``stopped_reason`` only;
  * ``monitor_until`` is NOT changed (unlike the app's Extend by 24 hours),
    so the monitor still stops automatically at its existing end time;
  * refuses if that end time has already passed.

The log prints the ID prefix, status, timestamps, interval and target labels.
Never an address or a UID.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any

#: The only monitor this tool may touch. Anything else is refused outright.
ALLOWED_PREFIXES = ("af1711d4",)

IDENTITY_UNCHANGED = ("id", "movie", "date_codes", "categories", "show_time", "created_at",
                      "monitor_until", "owner_uid", "notify_email", "interval_minutes", "status",
                      "stopped_at", "stopped_reason")


class Refused(RuntimeError):
    pass


def label(target: dict[str, Any]) -> str:
    return f"{target.get('venue_name', '')} · {target.get('fmt', '')}"


def _client():
    from monitor import state

    store = state._backend()
    client = getattr(store, "client", None)
    if client is None or getattr(store, "uid", "signed-in") is not None:
        raise Refused("needs the worker's Firestore credential (not a user scope, not the JSON store)")
    return client


def find(client, prefix: str) -> tuple[str, dict[str, Any]]:
    if len(prefix) < 8:
        raise Refused("give at least the 8-character ID prefix")
    if not prefix.startswith(ALLOWED_PREFIXES):
        raise Refused(f"this tool only maintains {', '.join(ALLOWED_PREFIXES)}")
    ids = [d for d in client.query("monitors") if d.startswith(prefix)]
    if len(ids) != 1:
        raise Refused(f"expected exactly one monitor starting {prefix}, found {len(ids)}")
    doc = client.get("monitors", ids[0])
    if not doc:
        raise Refused("the monitor disappeared while reading it")
    return ids[0], doc


def narrowed(doc: dict[str, Any], keep: list[str]) -> dict[str, Any]:
    targets = list(doc.get("targets") or [])
    labels = [label(t) for t in targets]
    missing = [k for k in keep if k not in labels]
    if missing:
        raise Refused(f"not a current target of this monitor: {missing}")
    if len(set(keep)) != len(keep):
        raise Refused("a target is named twice")
    if doc.get("status") == "ACTIVE":
        raise Refused("stop the monitor first: it is ACTIVE")
    return {**doc, "targets": [t for t in targets if label(t) in keep]}


def resumed(doc: dict[str, Any], now: datetime) -> dict[str, Any]:
    from config.timezone import parse_iso

    until = parse_iso(doc.get("monitor_until"))
    if until is None or until <= now:
        raise Refused("its end time has passed; resuming would need extending, which this never does")
    if doc.get("status") == "ACTIVE":
        raise Refused("already ACTIVE")
    return {**doc, "status": "ACTIVE", "stopped_at": None, "stopped_reason": ""}


def summary(doc_id: str, doc: dict[str, Any]) -> list[str]:
    return [f"monitor   {doc_id[:8]}…",
            f"status    {doc.get('status')}",
            f"created   {doc.get('created_at')}",
            f"until     {doc.get('monitor_until')}",
            f"interval  {doc.get('interval_minutes')} min",
            *[f"target    {label(t)}" for t in doc.get("targets") or []]]


def run(client, prefix: str, keep: list[str], *, resume: bool, apply: bool, now: datetime) -> list[str]:
    doc_id, before = find(client, prefix)
    lines = ["BEFORE", *summary(doc_id, before)]
    if resume:
        after = resumed(before, now)
        allowed = {"status", "stopped_at", "stopped_reason"}
    else:
        if not keep:
            raise Refused("name the targets to keep with --keep")
        after = narrowed(before, keep)
        allowed = {"targets"}
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    if not changed <= allowed:
        raise Refused(f"would change more than allowed: {sorted(changed)}")
    lines += ["AFTER" + ("" if apply else " (dry run — nothing written)"), *summary(doc_id, after)]
    if not apply:
        lines.append("DRY_RUN_OK")
        return lines
    client.set("monitors", doc_id, after)
    stored = client.get("monitors", doc_id)
    if stored != after:
        raise Refused("read-back does not match what was written — investigate before anything else")
    for key in IDENTITY_UNCHANGED:
        if key not in allowed and stored.get(key) != before.get(key):
            raise Refused(f"{key} changed unexpectedly")
    lines.append("APPLIED_AND_VERIFIED")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--monitor", required=True, help="monitor ID or its first 8+ characters")
    parser.add_argument("--keep", action="append", default=[], help='"Venue · Format" to keep (repeat)')
    parser.add_argument("--resume", action="store_true", help="set ACTIVE without changing the end time")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    if args.resume and args.keep:
        print("REFUSED: narrowing and resuming are separate runs")
        return 2
    from config.timezone import now_ist

    try:
        lines = run(_client(), args.monitor, args.keep, resume=args.resume, apply=args.apply, now=now_ist())
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
