"""Home and My Monitors agree about deleted monitors.

Manual testing found Delete All emptied My Monitors while the Home rail's
"Recent history" still showed the deleted monitors' lines. The rail was
rendering the raw history list — the permanent record, which a delete
rightly leaves alone — rather than the history of the monitors that exist.
``app.recent_activity`` now filters at the data, by each record's
``monitor_id``; the History page still shows everything.
"""

from __future__ import annotations

from dataclasses import replace

from monitor import state as state_mod
from monitor.state import record_history, upsert_monitor
from tests.test_app import run, text


def _stored_monitors():
    return state_mod._backend().load_monitors()          # the store, never the read cache


def _rail_titles(app) -> list[str]:
    """Movie titles in the Home rail's Recent history rows."""
    import re

    rows = [m.value for m in app.markdown if 'class="tr-history' in m.value]
    return [re.search(r'<div class="t">([^<]+)', r).group(1) for r in rows]


def _seed(make_monitor, count: int):
    monitors = []
    for i in range(count):
        m = make_monitor()
        m.movie = replace(m.movie, title=f"Film {i + 1}")
        upsert_monitor(m, mirror=False)
        record_history(m, "CREATED", "Monitor created.", mirror=False)
        monitors.append(m)
    return monitors


def test_home_recent_activity_follows_the_monitors_that_exist(make_monitor):
    """Steps 1–7 of the manual scenario, end to end."""
    a, b, c = _seed(make_monitor, 3)

    # 2. Home shows recent activity for the monitors that exist (latest first, three at most).
    app = run("Home")
    assert _rail_titles(app) == ["Film 3", "Film 2", "Film 1"]

    # 3–5. Delete one from My Monitors: gone there, and gone from Home.
    app.session_state["page"] = "My Monitors"
    app.run()
    app.button(key=f"m_del_{b.id}").click().run()
    assert not app.exception
    assert [m.id for m in _stored_monitors()] == [c.id, a.id]
    assert "Film 2" not in text(app)
    app.session_state["page"] = "Home"
    app.run()
    assert _rail_titles(app) == ["Film 3", "Film 1"]
    assert b.id not in " ".join(str(k) for k in app.session_state.keys())

    # 6–7. Delete All: Home has no stale activity at all.
    app.session_state["page"] = "My Monitors"
    app.run()
    app.button(key="m_delete_all").click().run()
    app.button(key="m_delete_all_confirm").click().run()
    assert not app.exception and _stored_monitors() == []
    app.session_state["page"] = "Home"
    app.run()
    assert _rail_titles(app) == []
    assert "No active monitors" in text(app)
    for mid in (a.id, b.id, c.id):
        assert mid not in " ".join(str(k) for k in app.session_state.keys())

    # 8. History is the record: every line is still there, newest first.
    app.session_state["page"] = "History"
    app.run()
    body = text(app)
    assert body.count('class="tr-history') == 3
    assert "Film 1" in body and "Film 2" in body and "Film 3" in body


def test_a_single_delete_forgets_that_cards_session_keys(make_monitor):
    a, b = _seed(make_monitor, 2)
    app = run("My Monitors", **{f"show_problem_{a.id}": True, f"show_problem_{b.id}": True})
    app.button(key=f"m_del_{a.id}").click().run()
    assert f"show_problem_{a.id}" not in app.session_state
    assert app.session_state.get(f"show_problem_{b.id}") is True     # the other card is untouched


def test_another_persons_home_and_history_are_untouched_by_a_delete_all(make_monitor, monkeypatch):
    """Step 9: B's monitors, history and Home rail survive A's Delete All."""
    from auth import session
    from monitor.state import Scope, set_scope_provider

    # B's records, written under B's scope.
    set_scope_provider(lambda: Scope("", "uid-b", lambda: ""))
    try:
        b_mon = make_monitor(owner_uid="uid-b")
        b_mon.movie = replace(b_mon.movie, title="B Film")
        upsert_monitor(b_mon, mirror=False)
        record_history(b_mon, "CREATED", "Monitor created.", mirror=False)
    finally:
        set_scope_provider(None)
    # A's records (the signed-in fixture), then A deletes everything.
    _seed(make_monitor, 2)
    app = run("My Monitors")
    app.button(key="m_delete_all").click().run()
    app.button(key="m_delete_all_confirm").click().run()
    assert not app.exception
    survivors = state_mod._JSON.load_monitors()                 # every account's, unscoped
    assert [m.owner_uid for m in survivors] == ["uid-b"]        # A's gone, B's whole

    # Sign in as B: the monitor, its history line on Home, and History are all still there.
    other = session.AuthUser(uid="uid-b", email="b@example.com", display_name="B",
                             id_token="t", refresh_token="r", expires_at=4102444800.0)

    def restore():
        import streamlit as st

        if st.session_state.get("auth_restore_tried"):
            return None
        st.session_state["auth_restore_tried"] = True
        st.session_state[session.USER_KEY] = other
        return other

    monkeypatch.setattr(session, "restore", restore)
    app = run("Home")
    assert _rail_titles(app) == ["B Film"]
    assert "Active monitor" in text(app)
    app.session_state["page"] = "History"
    app.run()
    assert "B Film" in text(app) and "Film 1" not in text(app) and "Film 2" not in text(app)
