"""Every GitHub workflow, held to the same three rules.

Found sound in the repository-wide audit (Nov 2026) and pinned here so it
stays that way:

1. Each workflow declares its ``permissions`` at the top, least privilege —
   never the default token's broad grant.
2. No workflow input or event field is interpolated into a ``run:`` script.
   ``${{ inputs.x }}`` inside a script is pasted in before the shell parses
   it, so a crafted input runs as code; inputs reach scripts through ``env:``.
3. Every third-party action is pinned to a full commit SHA, so a moved tag
   cannot change what runs with this repository's token.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))
UNTRUSTED = re.compile(r"\$\{\{\s*(inputs|github\.event)\.")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_permissions_are_declared_and_narrow(path):
    text = path.read_text(encoding="utf-8")
    block = re.search(r"^permissions:\n((?:  .+\n)+)", text, re.M)
    assert block, f"{path.name} declares no top-level permissions"
    grants = dict(re.findall(r"^  ([a-z-]+):\s*(\w+)", block.group(1), re.M))
    assert grants and set(grants.values()) <= {"read", "write", "none"}
    assert set(grants) <= {"contents", "actions"}, grants          # nothing beyond what the jobs use


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_input_is_pasted_into_a_script(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    in_run, indent = False, 0
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if re.match(r"-?\s*run:\s*\|?\s*$", stripped) or re.match(r"-?\s*run:\s", stripped):
            in_run, indent = True, len(line) - len(stripped)
            if re.match(r"-?\s*run:\s+\S", stripped) and not stripped.rstrip().endswith("|"):
                assert not UNTRUSTED.search(stripped), f"{path.name}:{number}"
                in_run = False
            continue
        if in_run:
            if stripped and len(line) - len(stripped) <= indent:
                in_run = False
            else:
                assert not UNTRUSTED.search(line), f"{path.name}:{number} interpolates an input into a script"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_a_commit(path):
    for ref in re.findall(r"uses:\s*([^\s#]+)", path.read_text(encoding="utf-8")):
        if ref.startswith("./"):
            continue
        assert re.search(r"@[0-9a-f]{40}$", ref), f"{path.name}: {ref} is not pinned to a commit SHA"
