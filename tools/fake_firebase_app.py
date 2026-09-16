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


def _simulate_streamlit_cloud() -> None:
    """``TR_SIMULATE_CLOUD=1``: reproduce Streamlit Community Cloud exactly.

    The deployed logs show every server-side reader returning zero on every
    run — ``context_cookies=0 header_cookies=0 runtime_cookies=0``. Blinding
    all three here means only the browser bridge can carry the session, which
    is the condition the fix has to survive.
    """
    if os.environ.get("TR_SIMULATE_CLOUD") != "1":
        return
    from auth import session as auth_session

    if getattr(auth_session, "_cloud_patched", False):
        return
    auth_session._cloud_patched = True
    real_jar = auth_session._cookie_jar

    def server_blind_jar():
        # The bridge's answer still counts; the server readers never do.
        reported = auth_session.bridge_jar()
        if reported:
            return reported
        return {}

    auth_session._cookie_jar = server_blind_jar
    auth_session._cookie_header_from_runtime = lambda: ""
    print("[harness] simulating Streamlit Cloud: no server-side cookies at all", flush=True)


def _simulate_blind_first_run() -> None:
    """``TR_SIMULATE_BLIND_COOKIE=1``: reproduce the deployment symptom.

    Streamlit answers ``st.context.cookies`` with an empty mapping — and no
    error — for a run whose client context it has not resolved. This makes the
    *first* run of every session look like that, which is what signed people
    out on a browser refresh.
    """
    if os.environ.get("TR_SIMULATE_BLIND_COOKIE") != "1":
        return
    from auth import session as auth_session

    # Streamlit re-executes this whole file on every rerun; patch only once so
    # the fault is not re-armed each time.
    if getattr(auth_session, "_blind_patched", False):
        return
    auth_session._blind_patched = True
    real = auth_session._cookie_jar
    # One whole session reads blind — every call in it, the way an unresolved
    # client context behaves — and only the session that would otherwise have
    # restored. Everything after it reads normally, so this is the intermittent
    # blind run, not a permanently broken deployment.
    # One run's worth of reads comes back empty — the gate reads the cookie
    # twice in a run (restore, then the reload hint) — and everything after it
    # reads normally. That is the intermittent blind run, not a permanently
    # broken deployment, so it also proves the recovery actually recovers.
    fault = {"left": 2}

    def blind_one_run():
        jar = real()
        if fault["left"] <= 0 or auth_session.COOKIE not in jar:
            return jar
        fault["left"] -= 1
        print("[harness] unresolved client context: empty cookie jar for this run", flush=True)
        return {}

    auth_session._cookie_jar = blind_one_run


_install_fake()
_simulate_streamlit_cloud()
_simulate_blind_first_run()
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
