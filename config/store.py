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
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

MONITORS_FILE = DATA_DIR / "monitors.json"
STATE_FILE = DATA_DIR / "state.json"
CATALOGUE_FILE = DATA_DIR / "catalogue.json"
HISTORY_FILE = DATA_DIR / "history.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
#: When the worker last re-listed a city on its own (see ``monitor.discovery``).
#: Worker-owned, like ``state.json``; the catalogue sync never touches it.
DISCOVERY_FILE = DATA_DIR / "discovery.json"

GITHUB_REPO = os.environ.get("TICKETRADAR_REPO", "manojanumolu/movie-ticket-radar")

DEFAULTS: dict[str, Any] = {
    "monitors.json": [],
    "state.json": {},
    "catalogue.json": {"movies": [], "updated_at": None},
    "history.json": [],
    "settings.json": {"notify_email": "", "default_interval": 10},
    "discovery.json": {},
    # PVR INOX's catalogue, kept apart from BookMyShow's (``catalogue_file``).
    "catalogue_pvr_inox.json": {"movies": [], "updated_at": None},
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


#: Bookkeeping for the UI's read-through (see ``sync_from_github``).
_last_sync_at = 0.0
_mirror_broken = False
#: What the most recent mirror attempt did — the UI reads this right after a
#: write to know whether a commit (and therefore a push-triggered worker run)
#: actually happened. ``{"ok", "committed", "error", "path"}``.
_last_mirror: dict[str, Any] = {"ok": False, "committed": False, "error": "", "path": ""}


def last_mirror() -> dict[str, Any]:
    return dict(_last_mirror)


def push_to_github(path: Path, body: str, message: str) -> bool:
    """Mirror one data file into the repo. Best effort — never raises.

    Skipped inside GitHub Actions: the workflow commits the whole data
    directory in one commit at the end of the run, and doing both would race.
    """
    global _last_sync_at, _mirror_broken, _last_mirror
    _last_mirror = {"ok": False, "committed": False, "error": "", "path": path.name}
    if running_in_actions():
        return False
    token = github_token()
    if not token:
        _last_mirror["error"] = "No GH_TOKEN configured."
        return False
    try:
        from github import Github, GithubException

        repo = Github(token).get_repo(GITHUB_REPO)
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            existing = repo.get_contents(rel)
            if existing.decoded_content.decode("utf-8") == body:
                _mirror_broken = False
                _last_mirror["ok"] = True
                return True  # identical; skip the commit
            repo.update_file(rel, message, body, existing.sha)
        except GithubException:
            repo.create_file(rel, message, body)
        # The repo now matches what we just wrote; don't immediately pull it
        # back over ourselves.
        _last_sync_at = time.monotonic()
        _mirror_broken = False
        _last_mirror.update(ok=True, committed=True)
        return True
    except Exception as exc:  # noqa: BLE001 - mirroring is never fatal
        print(f"[store] GitHub mirror failed for {path.name}: {exc}")
        # A local write that never reached the repo must not be overwritten
        # by the next read-through, or a monitor could vanish from the UI.
        _mirror_broken = True
        _last_mirror["error"] = explain_github_error(exc)
        return False


def explain_github_error(exc: BaseException) -> str:
    """Turn PyGithub's JSON blob into the one sentence a person needs."""
    text = str(exc)
    if "Resource not accessible by personal access token" in text or "403" in text[:5]:
        return ("GitHub refused (403): the GH_TOKEN doesn't have permission for this. "
                "A fine-grained PAT needs Contents: read & write, and Actions: read & write "
                "for on-demand checks.")
    if "Bad credentials" in text or text.startswith("401"):
        return "GitHub rejected the GH_TOKEN (401): it is invalid or expired."
    if "Not Found" in text or text.startswith("404"):
        return "GitHub answered 404: the repository or workflow was not found for this token."
    # Anything else is GitHub's own error body. It goes to the log, where the
    # owner reads it; the page gets a sentence that names the kind of failure
    # and nothing GitHub said about the repository or the request.
    print(f"[github] {type(exc).__name__}: {text[:300]}", flush=True)
    return f"GitHub answered with an error ({type(exc).__name__}). Try again in a moment."


#: The files the UI reads that somebody else writes: the worker owns
#: ``state.json`` and appends to ``history.json``, and flips monitors to
#: EXPIRED in ``monitors.json``.
SYNCED_FILES = ("monitors.json", "state.json", "history.json")


def sync_from_github(*, ttl: float = 20.0, force: bool = False) -> bool:
    """Pull the worker's latest observations into the local data files.

    The worker commits every check to the repository; a Streamlit Cloud
    container only sees those commits when it is redeployed. Reading through
    to the repo (rate-limited by ``ttl``) is what keeps "Last checked" honest
    on any host. Returns True when a fetch actually happened.
    """
    global _last_sync_at
    if running_in_actions() or _mirror_broken:
        return False
    token = github_token()
    if not token:
        return False
    now = time.monotonic()
    if not force and now - _last_sync_at < ttl:
        return False
    _last_sync_at = now
    try:
        from github import Github

        repo = Github(token).get_repo(GITHUB_REPO)
        for name in SYNCED_FILES:
            path = DATA_DIR / name
            rel = path.relative_to(REPO_ROOT).as_posix()
            try:
                body = repo.get_contents(rel).decoded_content.decode("utf-8")
                json.loads(body)  # never replace a good file with a broken one
            except Exception as exc:  # noqa: BLE001
                print(f"[store] read-through skipped {name}: {exc}")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
                os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - reading through is never fatal
        print(f"[store] read-through failed: {exc}")
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


MONITOR_WORKFLOW = "bookmyshow-monitor.yml"


def dispatch_workflow(workflow: str, inputs: dict[str, str] | None = None, ref: str = "") -> tuple[bool, str]:
    """Trigger a workflow_dispatch run. Never raises.

    Used by the UI to start a check the moment a monitor is created, and by
    the worker to hand over to its next segment. Inside Actions the token is
    the run's own ``GITHUB_TOKEN`` (needs ``permissions: actions: write``);
    from the UI it is the configured PAT (needs the *Actions: write* scope —
    without it GitHub answers 403 and the monitor simply waits for the
    schedule, which the caller reports honestly).
    """
    token = github_token()
    if not token:
        return False, "No GH_TOKEN configured."
    ref = ref or os.environ.get("GITHUB_REF_NAME", "") or "main"
    try:
        from github import Github

        repo = Github(token).get_repo(GITHUB_REPO)
        wf = repo.get_workflow(workflow)
        ok = wf.create_dispatch(ref, {k: str(v) for k, v in (inputs or {}).items()})
        return bool(ok), "Workflow started." if ok else "GitHub refused the dispatch."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not start workflow: {explain_github_error(exc)}"


def request_check_now(monitor_id: str = "", *, force: bool = True) -> tuple[bool, str]:
    """Ask the worker to run right now instead of waiting for the schedule.

    ``monitor_id`` narrows the *first* pass of the run to that monitor so a
    brand-new alert is checked within seconds of being saved; the run then
    keeps serving every active monitor at its own interval.
    """
    inputs = {"force": "true" if force else "false", "dry_run": "false",
              "monitor_id": monitor_id or ""}
    return dispatch_workflow(MONITOR_WORKFLOW, inputs)


# ──────────────────────────────────────────────────────────────────────────
# Typed accessors
# ──────────────────────────────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    data = read_json(SETTINGS_FILE)
    return {**DEFAULTS["settings.json"], **(data if isinstance(data, dict) else {})}


def save_settings(settings: dict[str, Any], *, mirror: bool = True) -> None:
    write_json(SETTINGS_FILE, settings, mirror=mirror, message="chore: update settings")


def catalogue_file(platform: str = "bookmyshow") -> Path:
    """Where a platform's catalogue lives. BookMyShow keeps
    ``data/catalogue.json``; any other platform gets a file of its own, so
    one platform's movies, theatres and formats can never appear in the
    other's pickers."""
    if not platform or platform == "bookmyshow":
        return CATALOGUE_FILE
    return DATA_DIR / f"catalogue_{platform}.json"


def load_catalogue(platform: str = "bookmyshow") -> dict[str, Any]:
    path = catalogue_file(platform)
    data = read_json(path)
    if not isinstance(data, dict):
        return _default_for(path)
    data.setdefault("movies", [])
    return data


def save_catalogue(catalogue: dict[str, Any], *, mirror: bool = True, platform: str = "bookmyshow") -> None:
    write_json(catalogue_file(platform), catalogue, mirror=mirror, message="chore: update movie catalogue")


def load_discovery() -> dict[str, Any]:
    data = read_json(DISCOVERY_FILE)
    return data if isinstance(data, dict) else {}


def save_discovery(discovery: dict[str, Any], *, mirror: bool = True) -> None:
    write_json(DISCOVERY_FILE, discovery, mirror=mirror, message="chore: update discovery clock")


def load_history() -> list[dict[str, Any]]:
    data = read_json(HISTORY_FILE)
    return data if isinstance(data, list) else []


def save_history(history: list[dict[str, Any]], *, mirror: bool = True) -> None:
    write_json(HISTORY_FILE, history[:50], mirror=mirror, message="chore: update history")


__all__ = [
    "CATALOGUE_FILE",
    "DATA_DIR",
    "DISCOVERY_FILE",
    "GITHUB_REPO",
    "HISTORY_FILE",
    "MONITORS_FILE",
    "MONITOR_WORKFLOW",
    "REPO_ROOT",
    "SETTINGS_FILE",
    "STATE_FILE",
    "catalogue_file",
    "dispatch_workflow",
    "explain_github_error",
    "github_status",
    "last_mirror",
    "github_token",
    "load_catalogue",
    "load_discovery",
    "load_history",
    "load_settings",
    "read_json",
    "request_check_now",
    "running_in_actions",
    "save_catalogue",
    "save_discovery",
    "save_history",
    "save_settings",
    "sync_from_github",
    "write_json",
]
