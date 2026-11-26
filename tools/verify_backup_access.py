#!/usr/bin/env python3
"""Verify that only approved identities can reach the disaster-recovery bucket.

Reads JSON that gcloud printed (nothing here calls Google Cloud) and exits
non-zero on anything outside the allow-list. Three checks, run in order:

  ancestors   ``gcloud projects get-ancestors DR_PROJECT --format=json``
              Pass: the DR project has no folder or organisation above it, or
              ``--allow-ancestor`` names each one whose policy was reviewed.

  bucket      ``gcloud storage buckets get-iam-policy gs://BUCKET --format=json``
              Pass: the bucket policy holds exactly the expected bindings —
              ``roles/storage.objectAdmin`` for the backup administrators and
              ``roles/storage.admin`` for the source project's Firestore
              service agent — and nothing else (no convenience values, no
              public members).

  effective   ``gcloud asset analyze-iam-policy ... --show-response --format=json``
              (``--expand-groups --analyze-service-account-impersonation``)
              Pass: every identity that holds any analysed permission on the
              bucket, *including through project, folder and organisation
              policies, Google groups and service-account impersonation*, is
              on the allow-list; the analysis is fully explored and reports no
              errors.

Usage:
  python3 tools/verify_backup_access.py ancestors ancestors.json [--allow-ancestor folders/123]
  python3 tools/verify_backup_access.py bucket bucket-policy.json --admin user:me@example.com \
      --agent serviceAccount:service-123@gcp-sa-firestore.iam.gserviceaccount.com
  python3 tools/verify_backup_access.py effective analysis.json --admin user:me@example.com \
      --agent serviceAccount:service-123@gcp-sa-firestore.iam.gserviceaccount.com

Exit codes: 0 pass, 1 unexpected access, 2 unusable input, 3 unreviewed ancestor.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ADMIN_ROLE = "roles/storage.objectAdmin"
AGENT_ROLE = "roles/storage.admin"
FIRESTORE_AGENT_SUFFIX = "@gcp-sa-firestore.iam.gserviceaccount.com"


class Unusable(ValueError):
    pass


def _load(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unusable(f"{path}: {type(exc).__name__}") from None


def _validate_members(admins: list[str], agents: list[str]) -> None:
    if not admins:
        raise Unusable("at least one --admin is required")
    for admin in admins:
        if not admin.startswith(("user:", "group:")) or admin.endswith(":"):
            raise Unusable(f"backup administrators must be user: or group: members ({admin})")
    for agent in agents:
        if not (agent.startswith("serviceAccount:service-") and agent.endswith(FIRESTORE_AGENT_SUFFIX)):
            raise Unusable(f"--agent must be the Firestore service agent ({agent})")


def check_ancestors(doc, allowed: list[str]) -> tuple[int, list[str]]:
    if not isinstance(doc, list) or not doc:
        raise Unusable("expected the JSON list printed by gcloud projects get-ancestors")
    lines, unreviewed = [], []
    for entry in doc:
        kind, ident = entry.get("type", ""), str(entry.get("id", ""))
        lines.append(f"{kind} {ident}")
        if kind in ("folder", "organization"):
            name = f"{kind}s/{ident}"
            if name not in allowed:
                unreviewed.append(name)
    if doc[0].get("type") != "project":
        raise Unusable("the first ancestor must be the project itself")
    if unreviewed:
        lines.append("UNREVIEWED ANCESTOR POLICY: " + ", ".join(unreviewed)
                     + " — run the effective check with --organization/--folder scope and review "
                       "gcloud projects get-ancestors-iam-policy before uploading anything")
        return 3, lines
    lines.append("ANCESTORS_OK")
    return 0, lines


def check_bucket(policy, admins: list[str], agents: list[str]) -> tuple[int, list[str]]:
    _validate_members(admins, agents)
    if not isinstance(policy, dict) or "bindings" not in policy:
        raise Unusable("expected the JSON object printed by gcloud storage buckets get-iam-policy")
    expected = {ADMIN_ROLE: set(admins), AGENT_ROLE: set(agents)}
    actual: dict[str, set[str]] = {}
    for binding in policy.get("bindings", []):
        if binding.get("condition"):
            return 1, [f"conditional binding is not allowed on the backup bucket: {binding.get('role')}"]
        actual.setdefault(binding.get("role", ""), set()).update(binding.get("members", []))
    problems = []
    for role in sorted(set(actual) | set(expected)):
        extra = actual.get(role, set()) - expected.get(role, set())
        missing = expected.get(role, set()) - actual.get(role, set())
        for member in sorted(extra):
            problems.append(f"UNEXPECTED {role} {member}")
        for member in sorted(missing):
            problems.append(f"MISSING {role} {member}")
    if problems:
        return 1, problems
    return 0, ["BUCKET_POLICY_OK"]


def _results(doc) -> tuple[list, list[str]]:
    if not isinstance(doc, dict) or "mainAnalysis" not in doc:
        raise Unusable("expected the raw response: rerun with --show-response --format=json")
    errors = []
    main = doc["mainAnalysis"]
    if not main.get("fullyExplored", False):
        errors.append("analysis not fully explored")
    for err in main.get("nonCriticalErrors", []) or []:
        errors.append(f"analysis error: {err.get('cause') or err}")
    results = list(main.get("analysisResults", []) or [])
    for item in doc.get("serviceAccountImpersonationAnalysis", []) or []:
        if not item.get("fullyExplored", True):
            errors.append("impersonation analysis not fully explored")
        results.extend(item.get("analysisResults", []) or [])
    return results, errors


def _own_service_agent(name: str, dr_project_number: str) -> bool:
    """A Google-managed service agent of the DR project itself (never a user-managed account)."""
    if not dr_project_number:
        return False
    n = re.escape(dr_project_number)
    return bool(re.fullmatch(rf"serviceAccount:service-{n}@(gcp-sa-[a-z0-9-]+|gs-project-accounts)"
                             r"\.iam\.gserviceaccount\.com", name))


def check_effective(doc, admins: list[str], agents: list[str],
                    dr_project_number: str = "") -> tuple[int, list[str]]:
    _validate_members(admins, agents)
    if dr_project_number and not dr_project_number.isdigit():
        raise Unusable("--dr-project-number must be the numeric project number")
    results, errors = _results(doc)
    allowed = set(admins) | set(agents)
    seen: dict[str, set[str]] = {}
    for result in results:
        where = result.get("attachedResourceFullName", "?")
        role = (result.get("iamBinding") or {}).get("role", "?")
        for identity in ((result.get("identityList") or {}).get("identities") or []):
            name = identity.get("name", "")
            seen.setdefault(name, set()).add(f"{role} on {where}")
    lines = []
    for name, paths in sorted(seen.items()):
        label = "  [Google-managed agent of the DR project]" if _own_service_agent(name, dr_project_number) else ""
        lines.append(f"{name}  via  {'; '.join(sorted(paths))}{label}")
    unexpected = sorted(name for name in seen
                        if name not in allowed and not _own_service_agent(name, dr_project_number))
    for name in unexpected:
        lines.append(f"UNEXPECTED IDENTITY {name}")
    if not any(a in seen for a in admins):
        errors.append("no approved backup administrator appears in the analysis")
    if not any(a in seen for a in agents):
        errors.append("the Firestore service agent does not appear in the analysis (exports would fail)")
    lines.extend(f"ERROR {e}" for e in errors)
    if unexpected or errors:
        return 1, lines
    lines.append("EFFECTIVE_ACCESS_OK")
    return 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("check", choices=["ancestors", "bucket", "effective"])
    parser.add_argument("json_file")
    parser.add_argument("--admin", action="append", default=[])
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--allow-ancestor", action="append", default=[])
    parser.add_argument("--dr-project-number", default="",
                        help="permit (and label) Google-managed service agents of the DR project itself")
    args = parser.parse_args(argv)
    try:
        doc = _load(args.json_file)
        if args.check == "ancestors":
            code, lines = check_ancestors(doc, args.allow_ancestor)
        elif args.check == "bucket":
            code, lines = check_bucket(doc, args.admin, args.agent)
        else:
            code, lines = check_effective(doc, args.admin, args.agent, args.dr_project_number)
    except Unusable as exc:
        print(f"UNUSABLE INPUT: {exc}")
        return 2
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
