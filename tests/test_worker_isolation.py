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

    class Blocker:
        def find_module(self, name, path=None):
            return self if name == "streamlit" or name.startswith("streamlit.") else None
        def find_spec(self, name, path=None, target=None):
            if name == "streamlit" or name.startswith("streamlit."):
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
