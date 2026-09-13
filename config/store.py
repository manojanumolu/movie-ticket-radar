"""JSON persistence, shared by the Streamlit UI and the GitHub Actions worker.

Design: the repository *is* the database. ``data/*.json`` is the single source
of truth; the worker reads it from its checkout and commits updates back, and
the UI mirrors its writes to GitHub through the API so a Streamlit Cloud
instance (which has an ephemeral filesystem) does not lose them.

This is the same shape as the job-tracker's ``config_store``, adapted: the
mirroring is centralised in one place here rather than repeated per file, and
writes are atomic so a crashed run can't leave a half-written monitor list.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

MONITORS_FILE = DATA_DIR / "monitors.json"
STATE_FILE = DATA_DIR / "state.json"
CATALOGUE_FILE = DATA_DIR / "catalogue.json"
HISTORY_FILE = DATA_DIR / "history.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

GITHUB_REPO = os.environ.get("TICKETRADAR_REPO", "manojanumolu/movie-ticket-radar")

DEFAULTS: dict[str, Any] = {
    "monitors.json": [],
    "state.json": {},
    "catalogue.json": {"movies": [], "updated_at": None},
    "history.json": [],
    "settings.json": {"notify_email": "", "default_interval": 10},
}


# ──────────────────────────────────────────────────────────────────────────
# Local IO
# ──────────────────────────────────────────────────────────────────────────
def _default_for(path: Path) -> Any:
    return json.loads(json.dumps(DEFAULTS.get(path.name, {})))


def read_json(path: Path) -> Any:
    """Read a data file, falling back to its default.

    A corrupted file must not take the app down — a monitor list that fails to
    parse is reported as empty, and the next successful write repairs it.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return _default_for(path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[store] {path.name} unreadable ({exc}); using default")
        return _default_for(path)


def write_json(path: Path, payload: Any, *, mirror: bool = True, message: str = "") -> None:
    """Write atomically, then optionally mirror to GitHub."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    if mirror:
        push_to_github(path, body, message or f"chore: update {path.name}")


# ──────────────────────────────────────────────────────────────────────────
# GitHub mirroring
# ──────────────────────────────────────────────────────────────────────────
def github_token() -> str:
    """Token from the environment, or from Streamlit secrets when in the UI."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token:
        return token.strip()
    try:  # only present when running inside Streamlit
        import streamlit as st

        return str(st.secrets.get("GH_TOKEN", "")).strip()
    except Exception:
        return ""


def running_in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def push_to_github(path: Path, body: str, message: str) -> bool:
    """Mirror one data file into the repo. Best effort — never raises.

    Skipped inside GitHub Actions: the workflow commits the whole data
    directory in one commit at the end of the run, and doing both would race.
    """
    if running_in_actions():
        return False
    token = github_token()
    if not token:
        return False
    try:
        from github import Github, GithubException

        repo = Github(token).get_repo(GITHUB_REPO)
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            existing = repo.get_contents(rel)
            if existing.decoded_content.decode("utf-8") == body:
                return True  # identical; skip the commit
            repo.update_file(rel, message, body, existing.sha)
        except GithubException:
            repo.create_file(rel, message, body)
        return True
    except Exception as exc:  # noqa: BLE001 - mirroring is never fatal
        print(f"[store] GitHub mirror failed for {path.name}: {exc}")
        return False


def github_status() -> tuple[bool, str]:
    """(connected, human message) — surfaced in the UI's Settings page."""
    if running_in_actions():
        return True, "Running inside GitHub Actions."
    if not github_token():
        return False, "No GH_TOKEN configured — monitors are saved locally only."
    try:
        from github import Github

        repo = Github(github_token()).get_repo(GITHUB_REPO)
        return True, f"Connected to {repo.full_name}."
    except Exception as exc:  # noqa: BLE001
        return False, f"GitHub unreachable: {exc}"


def dispatch_workflow(workflow: str, inputs: dict[str, str] | None = None, ref: str = "main") -> tuple[bool, str]:
    """Trigger a workflow_dispatch run. Used to resolve movies when the
    local network cannot reach the platform (see README §Troubleshooting)."""
    token = github_token()
    if not token:
        return False, "No GH_TOKEN configured."
    try:
        from github import Github

        repo = Github(token).get_repo(GITHUB_REPO)
        wf = repo.get_workflow(workflow)
        ok = wf.create_dispatch(ref, inputs or {})
        return bool(ok), "Workflow started." if ok else "GitHub refused the dispatch."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not start workflow: {exc}"


# ──────────────────────────────────────────────────────────────────────────
# Typed accessors
# ──────────────────────────────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    data = read_json(SETTINGS_FILE)
    return {**DEFAULTS["settings.json"], **(data if isinstance(data, dict) else {})}


def save_settings(settings: dict[str, Any], *, mirror: bool = True) -> None:
    write_json(SETTINGS_FILE, settings, mirror=mirror, message="chore: update settings")


def load_catalogue() -> dict[str, Any]:
    data = read_json(CATALOGUE_FILE)
    if not isinstance(data, dict):
        return _default_for(CATALOGUE_FILE)
    data.setdefault("movies", [])
    return data


def save_catalogue(catalogue: dict[str, Any], *, mirror: bool = True) -> None:
    write_json(CATALOGUE_FILE, catalogue, mirror=mirror, message="chore: update movie catalogue")


def load_history() -> list[dict[str, Any]]:
    data = read_json(HISTORY_FILE)
    return data if isinstance(data, list) else []


def save_history(history: list[dict[str, Any]], *, mirror: bool = True) -> None:
    write_json(HISTORY_FILE, history[:50], mirror=mirror, message="chore: update history")


__all__ = [
    "CATALOGUE_FILE",
    "DATA_DIR",
    "GITHUB_REPO",
    "HISTORY_FILE",
    "MONITORS_FILE",
    "REPO_ROOT",
    "SETTINGS_FILE",
    "STATE_FILE",
    "dispatch_workflow",
    "github_status",
    "github_token",
    "load_catalogue",
    "load_history",
    "load_settings",
    "read_json",
    "running_in_actions",
    "save_catalogue",
    "save_history",
    "save_settings",
    "write_json",
]
