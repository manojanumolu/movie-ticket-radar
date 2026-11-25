#!/usr/bin/env python3
"""Read-only: would any stored monitor be refused by the Phase 5 Firestore rules?

    python tools/rules_audit.py            # needs FIREBASE_SERVICE_ACCOUNT + FIREBASE_PROJECT_ID

Run before publishing ``firestore.rules`` (Phase 7). The rules check every
*client* write of a monitor — the owner creating, stopping, extending or
retrying it, and the app expiring it when its end time has passed. The worker
uses a service account and is not subject to them. So the question before
publication is: is there a stored monitor its owner could no longer stop, or
that the app could no longer expire, once the rules are live?

The Firebase console cannot answer it — it has no filter on the length of a
list or the keys of a map — so this reads every monitor with the worker's
credential and applies the rules' monitor checks in Python, with the numbers
kept in step with ``firestore.rules`` by ``tests/test_rules_audit.py``.

It writes nothing: the whole run is under :func:`config.readonly.read_only`,
which makes any Firestore commit raise. It prints counts and the first eight
characters of the affected document ids — never a title, an address, a UID or
anything else from the documents — because the Actions log is public.

Exit status 0 when the store was read (the verdict lines say whether it is
safe to publish), 1 when it could not be read.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The rules' numbers (firestore.rules); tests/test_rules_audit.py fails if
#: either side changes without the other.
MAX_VARIANTS = 12
MAX_REQUESTS = 120
MAX_TARGETS = 200
MAX_DATE_CODES = 62
MAX_CATEGORIES = 20
MAX_EMAIL = 254
MOVIE_SIZES = {"platform": 40, "event_code": 20, "title": 500}
MOVIE_FIELDS = {"platform", "event_code", "title", "region_code", "region_slug",
                "city", "language", "poster_url", "source_url", "variants"}
MOVIE_REQUIRED = {"platform", "event_code", "title", "region_code", "region_slug"}
MONITOR_FIELDS = {"id", "movie", "targets", "interval_minutes", "monitor_until",
                  "start_immediately", "notify_email", "date_codes", "status",
                  "created_at", "stopped_at", "stopped_reason",
                  "first_check_requested_at", "problem", "owner_uid",
                  "categories", "show_time"}
MONITOR_REQUIRED = {"movie", "targets", "interval_minutes", "monitor_until",
                    "notify_email", "status", "owner_uid"}
STATUSES = {"ACTIVE", "STOPPED", "EXPIRED"}
ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T")


def movie_problems(movie: Any) -> list[str]:
    """``validMovie``: only MovieRef's fields, the five it needs, sane sizes,
    at most twelve siblings. Reasons are categories, never values."""
    if not isinstance(movie, dict):
        return ["movie is not a map"]
    out = []
    if set(movie) - MOVIE_FIELDS:
        out.append("movie has fields the application never writes")
    if MOVIE_REQUIRED - set(movie):
        out.append("movie is missing a required field")
    for key, limit in MOVIE_SIZES.items():
        value = movie.get(key)
        if key in movie and not (isinstance(value, str) and len(value) <= limit):
            out.append(f"movie.{key} is not a string of at most {limit}")
    variants = movie.get("variants")
    if variants is not None and not isinstance(variants, list):
        out.append("movie.variants is not a list")
    elif isinstance(variants, list) and len(variants) > MAX_VARIANTS:
        out.append(f"more than {MAX_VARIANTS} sibling events")
    return out


def shape_problems(doc: dict[str, Any], doc_id: str) -> list[str]:
    """``validShape`` for any status."""
    out = []
    if set(doc) - MONITOR_FIELDS:
        out.append("fields the application never writes")
    if MONITOR_REQUIRED - set(doc):
        out.append("a required field is missing")
    if "id" in doc and doc.get("id") != doc_id:
        out.append("id does not match the document")
    out += movie_problems(doc.get("movie"))
    targets = doc.get("targets")
    if not (isinstance(targets, list) and 0 < len(targets) <= MAX_TARGETS):
        out.append(f"targets is not a list of 1 to {MAX_TARGETS}")
    if not isinstance(doc.get("interval_minutes"), int) or isinstance(doc.get("interval_minutes"), bool):
        out.append("interval is not an integer")
    if doc.get("status") not in STATUSES:
        out.append("status is not ACTIVE, STOPPED or EXPIRED")
    email = doc.get("notify_email")
    if not (isinstance(email, str) and len(email) <= MAX_EMAIL):
        out.append("notification address is not a string of at most 254")
    if not isinstance(doc.get("monitor_until"), str):
        out.append("end time is not a string")
    if "date_codes" in doc and not (isinstance(doc["date_codes"], list) and len(doc["date_codes"]) <= MAX_DATE_CODES):
        out.append(f"more than {MAX_DATE_CODES} dates")
    if "categories" in doc and not (isinstance(doc["categories"], list) and len(doc["categories"]) <= MAX_CATEGORIES):
        out.append(f"more than {MAX_CATEGORIES} categories")
    if "show_time" in doc and not isinstance(doc["show_time"], str):
        out.append("show time is not a string")
    return out


def request_cost(doc: dict[str, Any]) -> int:
    """``requestCost``: the rules count the stored list, as written."""
    dates = doc.get("date_codes") if isinstance(doc.get("date_codes"), list) else []
    movie = doc.get("movie") if isinstance(doc.get("movie"), dict) else {}
    variants = movie.get("variants") if isinstance(movie.get("variants"), list) else []
    return max(1, len(dates)) * (1 + len(variants))


def overdue(doc: dict[str, Any], now: datetime) -> bool:
    until = doc.get("monitor_until")
    if not isinstance(until, str) or not ISO.match(until):
        return False
    try:
        return datetime.fromisoformat(until) <= now
    except ValueError:
        return False


@dataclass
class Audit:
    checked: int = 0
    #: category -> document id prefixes
    found: dict[str, list[str]] = field(default_factory=dict)

    def add(self, category: str, doc_id: str) -> None:
        self.found.setdefault(category, []).append(doc_id[:8])

    def count(self, category: str) -> int:
        return len(self.found.get(category, []))


SHAPE = "fails the Phase 5 shape checks (any status)"
OVERDUE = "running and past its end time, and fails them"
COST = "running, more than 120 requests per check"
SIBLINGS = "running, more than 12 sibling events"
CATEGORIES = (SHAPE, OVERDUE, COST, SIBLINGS)


def audit(documents: dict[str, dict[str, Any]], now: datetime) -> Audit:
    result = Audit(checked=len(documents))
    for doc_id, doc in sorted(documents.items()):
        shape = shape_problems(doc, doc_id)
        running = doc.get("status") == "ACTIVE"
        if shape:
            result.add(SHAPE, doc_id)
            if running and overdue(doc, now):
                result.add(OVERDUE, doc_id)
        if running and request_cost(doc) > MAX_REQUESTS:
            result.add(COST, doc_id)
        if running and any("sibling" in p for p in shape):
            result.add(SIBLINGS, doc_id)
    return result


def report(result: Audit) -> str:
    lines = [f"[rules-audit] monitors checked: {result.checked}"]
    for category in CATEGORIES:
        ids = result.found.get(category, [])
        lines.append(f"[rules-audit] {category}: {len(ids)}" + (f" — {', '.join(ids)}" if ids else ""))
    shape = result.count(SHAPE)
    plain = ("yes" if not shape else
             f"NOT YET — {shape} monitor(s) could not be stopped or expired by their owner "
             f"({result.count(OVERDUE)} already overdue); delete them, or use the candidate with the "
             "stop/expiry exception")
    lines.append(f"[rules-audit] safe to publish Phase 5 rules as written: {plain}")
    lines.append("[rules-audit] safe to publish Phase 5 rules + stop/expiry exception: yes "
                 "(every monitor can still be stopped, expired and deleted)")
    if result.count(COST) or result.count(SIBLINGS):
        lines.append("[rules-audit] note: running monitors above a limit keep running until their end "
                     "time but cannot be extended or retried; the worker already skips those over "
                     "12 siblings or 120 requests")
    return "\n".join(lines)


def main() -> int:
    from config import firestore as fs
    from config import readonly
    from config.timezone import now_ist

    from config import worker_credentials as credential

    project = fs.project_from_env()
    try:
        if credential.mode() == "legacy":
            info, problem = fs.service_account_status()
            if info is None or not project:
                raise credential.CredentialError(problem or "FIREBASE_PROJECT_ID is not set")
            getter = fs.service_account_token_getter(info)
        else:
            getter = credential.keyless_token(project, credential.DATASTORE_SCOPES)
    except credential.CredentialError as exc:
        print(f"[rules-audit] cannot read the store: {exc}")
        return 1
    client = fs.FirestoreClient(project, getter)
    with readonly.read_only():
        try:
            documents = client.query("monitors")
        except fs.FirestoreError as exc:
            print(f"[rules-audit] cannot read the store: HTTP {exc.status}")
            return 1
    result = audit(documents, now_ist())
    print(report(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
