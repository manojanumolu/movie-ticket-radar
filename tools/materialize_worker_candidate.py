"""Prepare the hardening candidate in a new, detached Git worktree."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = "worker_candidate/overlay/"
PATCH = "worker_candidate/runtime.patch"


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, input=data, check=True, stdout=subprocess.PIPE,
    ).stdout


def materialize(target: Path) -> Path:
    target = target.resolve()
    if target.exists() or ROOT in target.parents or target in ROOT.parents:
        raise ValueError("Choose a new worktree path outside the repository.")
    ref = git(ROOT, "rev-parse", "HEAD").decode("ascii").strip()
    patch = git(ROOT, "show", f"{ref}:{PATCH}")
    overlay = {}
    for entry in git(ROOT, "ls-tree", "-r", "-z", ref, "--", OVERLAY).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.decode("utf-8").split("\t", 1)
        mode, kind, blob = metadata.split()
        if mode != "100644" or kind != "blob":
            raise ValueError("The candidate overlay accepts ordinary tracked files only.")
        overlay[path.removeprefix(OVERLAY)] = git(ROOT, "cat-file", "blob", blob)
    if not overlay or not patch:
        raise ValueError("The commit is missing the worker candidate.")
    git(ROOT, "worktree", "add", "--detach", str(target), ref)
    for relative in overlay:
        if (target / relative).exists():
            raise ValueError(f"Candidate file collides with the tracked tree: {relative}")
    git(target, "apply", "--check", "--whitespace=error", "-", data=patch)
    git(target, "apply", "--whitespace=error", "-", data=patch)
    for relative, source in overlay.items():
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as file:
            file.write(source)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="new worktree path outside the current repository")
    args = parser.parse_args()
    print(materialize(args.target))


if __name__ == "__main__":
    main()
