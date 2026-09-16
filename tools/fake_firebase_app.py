#!/usr/bin/env python3
"""Run the real app in a real browser against an in-memory Firebase.

    streamlit run tools/fake_firebase_app.py --server.port 8601 --server.headless true

Everything is the real ``app.py`` — the same gate, session cookie, login page,
wizard and pages — except that ``auth.firebase._post`` is the suite's
:class:`tests.test_auth.FakeFirebase`, so a browser can sign in, sign out,
switch accounts and reload without a Firebase project or a network. The data
directory is a throwaway copy so nothing here touches ``data/``.

Accounts (all verified; ``newbie`` is not):

    ravi@example.com  / Popcorn2026   (Ravi Teja)
    sita@example.com  / Interval99    (Sita Devi)
    newbie@example.com / Trailer2026  (unverified)

Used by ``tools/auth_browser_audit.py``; never part of the pytest suite.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("FIREBASE_WEB_API_KEY", "fake-web-api-key")
os.environ.pop("FIREBASE_PROJECT_ID", None)
os.environ.pop("GH_TOKEN", None)
os.environ.pop("GITHUB_TOKEN", None)

from auth import firebase  # noqa: E402
from config import store  # noqa: E402
from monitor import state as state_mod  # noqa: E402


def _install_fake() -> None:
    """Once per process: a Streamlit rerun re-executes this file, but the
    modules it patched persist, and so must the fake's accounts."""
    from test_auth import FakeFirebase

    if isinstance(firebase._post, FakeFirebase):
        return
    fake = FakeFirebase()
    # Real Firebase refresh tokens carry characters encodeURIComponent escapes.
    # The harness uses them so a browser refresh exercises the cookie decode.
    fake.token_suffix = "/aB+cD=eF"
    fake.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fake.add("sita@example.com", "Interval99", uid="uid-sita", name="Sita Devi")
    fake.add("newbie@example.com", "Trailer2026", uid="uid-newbie", name="New Person", verified=False)
    # A throwaway account for exercising Delete account in a browser.
    fake.add("disposable@example.com", "Popcorn2026", uid="uid-disposable", name="Disposable One")
    firebase._post = fake

    data = Path(tempfile.mkdtemp(prefix="tr-fake-data-"))
    for name in ("catalogue.json",):
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy(src, data / name)
    for attr, name in {
        "MONITORS_FILE": "monitors.json", "STATE_FILE": "state.json", "CATALOGUE_FILE": "catalogue.json",
        "HISTORY_FILE": "history.json", "SETTINGS_FILE": "settings.json", "DISCOVERY_FILE": "discovery.json",
    }.items():
        setattr(store, attr, data / name)
    store.DATA_DIR = data
    state_mod.MONITORS_FILE = data / "monitors.json"
    state_mod.STATE_FILE = data / "state.json"
    store.push_to_github = lambda *a, **k: False
    store.sync_from_github = lambda *a, **k: None
    print(f"[fake-firebase] accounts ready; data in {data}", flush=True)


_install_fake()
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
