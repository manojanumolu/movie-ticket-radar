"""Delete All means every monitor the signed-in account owns — and nothing
of anybody else's.

The store does the deleting (``monitor.state.delete_all_monitors``), scoped
by the Firebase UID the scope provider answers with; the page only asks for
confirmation and reports what happened. Both stores are covered: Firestore
through the rule-enforcing fake in ``test_isolation``, and the JSON
compatibility store.
"""

from __future__ import annotations

import pytest

from config import firestore as fs
from monitor import state as state_mod
from monitor.state import MonitorState
from tests.test_app import run, section, text
from tests.test_isolation import _seed, cloud, monitor_for  # noqa: F401 - fixture


# ──────────────────────────────────────────────────────────────────────────
# Firestore
# ──────────────────────────────────────────────────────────────────────────
def test_delete_all_removes_every_owned_monitor_and_its_state_only(cloud, make_monitor):
    a1 = _seed(cloud, make_monitor, "uid-a", "a@example.com")
    cloud.as_user("uid-a")
    a2 = monitor_for(make_monitor, "uid-a", "a@example.com")
    a2.stop()
    state_mod.upsert_monitor(a2, mirror=False)          # one active, one finished
    b = _seed(cloud, make_monitor, "uid-b", "b@example.com")

    cloud.as_user("uid-a")
    assert state_mod.delete_all_monitors(mirror=False) == 2

    monitors = cloud.docs.get("monitors", {})
    states = cloud.docs.get("monitor_state", {})
    assert a1.id not in monitors and a2.id not in monitors
    assert a1.id not in states                          # its state document went with it
    assert b.id in monitors and b.id in states          # B is whole
    # History is the record of what happened: it stays, as a single Delete leaves it.
    assert [d for d in cloud.docs["history"].values() if d[fs.OWNER] == "uid-a"]
    assert "uid-a" in cloud.docs["users"]               # settings are not monitors
    assert state_mod.load_monitors() == [] and state_mod.load_state() == {}

    cloud.as_user("uid-b")
    assert [m.id for m in state_mod.load_monitors()] == [b.id]


def test_delete_all_is_idempotent_and_safe_with_nothing(cloud, make_monitor):
    cloud.as_user("uid-a")
    assert state_mod.delete_all_monitors(mirror=False) == 0
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    cloud.as_user("uid-a")
    assert state_mod.delete_all_monitors(mirror=False) == 1
    assert state_mod.delete_all_monitors(mirror=False) == 0
    assert state_mod.load_monitors() == []


def test_a_monitor_without_a_state_document_deletes_cleanly(cloud, make_monitor):
    """The rules refuse deleting a state document that was never written, and
    a commit is atomic — so a monitor stopped before its first check must not
    take the whole Delete All down with it."""
    cloud.as_user("uid-a")
    fresh = monitor_for(make_monitor, "uid-a", "a@example.com")
    state_mod.upsert_monitor(fresh, mirror=False)       # no state yet
    checked = _seed(cloud, make_monitor, "uid-a", "a@example.com")
    cloud.as_user("uid-a")
    assert state_mod.delete_all_monitors(mirror=False) == 2
    assert fresh.id not in cloud.docs["monitors"] and checked.id not in cloud.docs["monitors"]


def test_the_worker_view_can_never_delete_all(cloud, make_monitor):
    """Ownerless "all" would be everybody's. Refused before any read."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    cloud.as_worker()
    with pytest.raises(PermissionError):
        state_mod.delete_all_monitors(mirror=False)
    assert cloud.docs["monitors"]


# ──────────────────────────────────────────────────────────────────────────
# JSON compatibility store
# ──────────────────────────────────────────────────────────────────────────
def test_json_store_delete_all_keeps_other_accounts(make_monitor):
    from monitor.state import Scope, load_monitors, upsert_monitor

    for uid in ("uid-a", "uid-a", "uid-b"):
        state_mod.set_scope_provider(lambda uid=uid: Scope("", uid, lambda: ""))
        m = make_monitor(owner_uid=uid)
        upsert_monitor(m, mirror=False)
        state_mod.save_state({m.id: MonitorState(check_count=1)}, mirror=False)
    try:
        state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: ""))
        assert state_mod.delete_all_monitors(mirror=False) == 2
        assert load_monitors() == [] and state_mod.load_state() == {}
        assert state_mod.delete_all_monitors(mirror=False) == 0
        state_mod.set_scope_provider(lambda: Scope("", "uid-b", lambda: ""))
        mine = load_monitors()
        assert len(mine) == 1 and mine[0].owner_uid == "uid-b"
        assert list(state_mod.load_state()) == [mine[0].id]
    finally:
        state_mod.set_scope_provider(None)


# ──────────────────────────────────────────────────────────────────────────
# The page: confirmation first, then everything, then a clean session
# ──────────────────────────────────────────────────────────────────────────
def test_delete_all_asks_first_and_then_deletes_active_and_finished(make_monitor):
    from monitor.state import upsert_monitor

    def load_monitors():
        return state_mod._backend().load_monitors()        # the store itself, never the read cache

    active = [make_monitor() for _ in range(2)]
    stopped = make_monitor()
    stopped.stop()
    for m in active + [stopped]:
        upsert_monitor(m, mirror=False)

    app = run("My Monitors", **{f"show_problem_{active[0].id}": True})
    assert section(text(app), "Active") == "2" and section(text(app), "Finished") == "1"

    app.button(key="m_delete_all").click().run()
    assert not app.exception
    body = text(app)
    assert "Delete all 3 monitor(s)?" in body and "2 active" in body
    assert len(load_monitors()) == 3                    # nothing deleted yet

    app.button(key="m_delete_all_cancel").click().run()
    assert "Delete all 3 monitor(s)?" not in text(app)
    assert len(load_monitors()) == 3

    app.button(key="m_delete_all").click().run()
    app.button(key="m_delete_all_confirm").click().run()
    assert not app.exception
    assert load_monitors() == []
    body = text(app)
    assert "Deleted 3 monitor(s)" in body
    assert section(body, "Active") is None and section(body, "Finished") is None
    assert app.session_state["page"] == "My Monitors"
    assert f"show_problem_{active[0].id}" not in app.session_state
    assert app.session_state.get("m_delete_all_open") is False


def test_delete_all_button_is_absent_with_nothing_to_delete():
    app = run("My Monitors")
    assert not app.exception
    assert "m_delete_all" not in [b.key for b in app.button]
