"""The worker must run without Streamlit.

The whole point of the architecture is that the background checks happen on a
GitHub Actions runner, independently of whether the UI is deployed or even
installed. If an ``import streamlit`` ever creeps into the engine's import
graph, the workflow would need the UI's dependency tree to do its job — and a
broken Streamlit release would take the monitoring down with it.

This test enforces that boundary by actually blocking the import.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BLOCK_STREAMLIT = textwrap.dedent(
    """
    import sys

    # Every attempt is recorded as well as refused: code that swallows the
    # ImportError (a lazy ``try: import streamlit``) still shows up here.
    sys._streamlit_import_attempts = []

    class Blocker:
        def find_module(self, name, path=None):
            return self if name == "streamlit" or name.startswith("streamlit.") else None
        def find_spec(self, name, path=None, target=None):
            if name == "streamlit" or name.startswith("streamlit."):
                sys._streamlit_import_attempts.append(name)
                raise ImportError("streamlit is not available to the worker")
            return None

    sys.meta_path.insert(0, Blocker())
    """
)


def _run(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", BLOCK_STREAMLIT + textwrap.dedent(body)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_engine_imports_without_streamlit():
    result = _run(
        """
        import monitor.checker, monitor.state, monitor.changes, monitor.catalogue
        import platforms.bookmyshow, notifications.email, config.store
        import run_monitor, resolve_movie
        assert "streamlit" not in sys.modules
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_worker_cli_runs_without_streamlit():
    result = _run(
        """
        import run_monitor
        code = run_monitor.main(["--dry-run"])
        assert code == 0, code
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_email_credentials_do_not_need_streamlit(tmp_path):
    result = _run(
        """
        import os
        os.environ["GMAIL_ADDRESS"] = "me@example.com"
        os.environ["GMAIL_APP_PASSWORD"] = "pw"
        from notifications.email import credentials, is_configured
        assert credentials() == ("me@example.com", "pw")
        assert is_configured() is True
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_a_full_worker_tick_never_touches_streamlit_session_state(tmp_path):
    """A tick that checks a monitor, records history and writes state — the
    paths that used to reach ``st.session_state`` through the read cache's
    ``invalidate_cache`` and log "missing ScriptRunContext" on every write.
    With the import blocked, any such touch is an ImportError; the tick
    must finish, persist, and leave ``streamlit`` unimported."""
    data = tmp_path / "worker-data"          # the autouse fixture already owns tmp_path/data
    data.mkdir()
    result = _run(
        f"""
        import json, os, sys
        from pathlib import Path
        data = Path({str(data)!r})
        from config import store
        from monitor import state as state_mod
        for attr, name in {{"MONITORS_FILE": "monitors.json", "STATE_FILE": "state.json",
                           "HISTORY_FILE": "history.json", "SETTINGS_FILE": "settings.json",
                           "DISCOVERY_FILE": "discovery.json", "CATALOGUE_FILE": "catalogue.json"}}.items():
            setattr(store, attr, data / name)
        store.DATA_DIR = data
        state_mod.MONITORS_FILE = data / "monitors.json"
        state_mod.STATE_FILE = data / "state.json"
        state_mod.SETTINGS_FILE = data / "settings.json"

        from datetime import datetime
        from config.timezone import IST
        from monitor import checker, discovery
        from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget
        from platforms.base import PlatformError

        monitor = Monitor(
            movie=MovieRef(platform="bookmyshow", event_code="ET1", title="T", region_code="HYD",
                           region_slug="hyderabad", city="Hyderabad", source_url="https://x"),
            targets=[TheatreTarget("ALLU", "Allu", "Attapur", ANY_FORMAT)],
            interval_minutes=10, monitor_until=datetime(2099, 1, 1, tzinfo=IST),
            notify_email="w@example.com", owner_uid="")

        class Unreachable:
            slug = "bookmyshow"
            def fetch(self, *a, **k):
                raise PlatformError("blocked in test")
            def list_movies(self, *a, **k):
                raise PlatformError("blocked in test")

        checker.get_provider = lambda slug: Unreachable()
        discovery.get_provider = lambda slug: Unreachable()

        state_mod.upsert_monitor(monitor, mirror=False)
        state_mod.record_history(monitor, "CREATED", "made", mirror=False)
        report = checker.run_once(force=True, mirror=False, notifier=lambda *a, **k: None)
        assert report.failed == [monitor.id], report.summary()
        assert state_mod.get_monitor_state(monitor.id).check_count == 1
        assert [h["kind"] for h in state_mod.load_history()][:2] == ["ERROR", "CREATED"]
        state_mod.invalidate_cache()
        assert "streamlit" not in sys.modules
        assert sys._streamlit_import_attempts == [], sys._streamlit_import_attempts
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
    assert "ScriptRunContext" not in result.stderr and "ScriptRunContext" not in result.stdout
