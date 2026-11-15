"""Rollout compatibility: monitors saved before Phase 5's limits.

Phase 5 refuses, at save time, a monitor whose one check would cost more
than ``policy.MAX_REQUESTS_PER_CHECK`` BookMyShow requests. A monitor saved
*before* that limit existed can still be in the store. Every action a person
can take on it from My Monitors must end in a message, never in Streamlit's
exception page — the same courtesy the Create and Extend handlers give.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from config import store
from config.timezone import now_ist
from monitor import policy
from monitor.models import ANY_FORMAT, Monitor, MonitorStatus, MovieRef, TheatreTarget
from monitor.state import get_monitor

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
from tests.conftest import APP_SCRIPT


def _legacy_over_limit_monitor() -> Monitor:
    """41 show dates on a film with two premium-format events: 123 requests
    per check — accepted before Phase 5, over the new 120 limit."""
    first = date(2026, 12, 1)
    m = Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET00512345", title="Legacy Film",
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad",
                       variants=(("ET00512346", "3D"), ("ET00512347", "4DX 3D"))),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        monitor_until=now_ist() + timedelta(days=2), notify_email="tester@example.com",
        owner_uid="uid-test-1",
        date_codes=[(first + timedelta(days=i)).strftime("%Y%m%d") for i in range(41)],
    )
    m.set_problem("FIRST_CHECK_NOT_STARTED", "The worker could not be started.")
    return m


@pytest.fixture
def legacy(monkeypatch):
    m = _legacy_over_limit_monitor()
    assert policy.request_cost(m) == 123 > policy.MAX_REQUESTS_PER_CHECK
    # Written as it would already be in the store — not through today's save.
    store.write_json(store.MONITORS_FILE, [m.to_dict()], mirror=False)
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    monkeypatch.setattr(store, "dispatch_workflow", lambda *a, **k: (True, "Workflow started."))
    return m


def _my_monitors(**session):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state["page"] = "My Monitors"
    for key, value in session.items():
        app.session_state[key] = value
    return app.run()


def test_retry_on_a_pre_limit_monitor_says_so_instead_of_crashing(legacy):
    app = _my_monitors(**{f"show_problem_{legacy.id}": True})
    assert not app.exception
    app.button(key=f"retry_{legacy.id}").click().run()
    assert not app.exception, [str(e.value)[:200] for e in app.exception]
    shown = " ".join(e.value for e in app.error)
    assert "BookMyShow requests every check" in shown


def test_a_pre_limit_monitor_can_still_be_stopped_and_deleted(legacy):
    app = _my_monitors()
    app.button(key=f"m_stop_{legacy.id}").click().run()
    assert not app.exception
    assert get_monitor(legacy.id).status is MonitorStatus.STOPPED


def test_extend_on_a_pre_limit_monitor_says_so_instead_of_reaching_the_rules(legacy):
    """After the Phase 5 rules are published, Firestore refuses to make such a
    monitor running again; the app now says why before trying."""
    stopped = _legacy_over_limit_monitor()
    stopped.id = legacy.id
    stopped.stop()
    store.write_json(store.MONITORS_FILE, [stopped.to_dict()], mirror=False)
    app = _my_monitors()
    app.button(key=f"m_ext_{legacy.id}").click().run()
    assert not app.exception
    assert get_monitor(legacy.id).status is MonitorStatus.STOPPED, "not made running again"
    shown = " ".join(e.value for e in app.error) + " ".join(m.value for m in app.markdown)
    assert "BookMyShow requests every check" in shown


def test_an_ordinary_expired_monitor_still_extends(make_monitor):
    from monitor.state import upsert_monitor

    monitor = make_monitor(until=now_ist() - timedelta(minutes=5))
    upsert_monitor(monitor, mirror=False)
    _my_monitors(page="Home")
    app = _my_monitors()
    app.button(key=f"m_ext_{monitor.id}").click().run()
    assert not app.exception
    assert get_monitor(monitor.id).status is MonitorStatus.ACTIVE
