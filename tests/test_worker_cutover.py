from copy import deepcopy

import pytest

from monitor import checker
from monitor.catalogue_refresh import CatalogueRefresh
from monitor.execution import OUTBOX
from monitor.models import Monitor
from monitor.runtime import FencedTick, Intent
from monitor.state import MONITORS
from run_worker_service import configured_runtime
from tests.test_monitor_execution import ledger


def test_fenced_mode_rejects_legacy_even_with_authority(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "fenced")
    monkeypatch.setenv("WORKER_AUTHORITY", "new-worker")
    monkeypatch.setenv("FIREBASE_CREDENTIAL_MODE", "legacy")
    with pytest.raises(ValueError, match="workload identity"):
        configured_runtime()


def test_targeted_fenced_tick_uses_checker_and_preserves_stopped_monitor(monkeypatch, ledger):
    store, now, monitor, state, outcome = ledger
    stopped = deepcopy(monitor)
    stopped.id = "stopped-example"
    stopped.stop(now[0])
    store.put(MONITORS, stopped.id, stopped.to_dict())
    baseline = deepcopy(store.rows[MONITORS, stopped.id])
    monkeypatch.setattr(checker, "load_monitors_for_checking",
                        lambda: [Monitor.from_dict(store.fields(MONITORS, monitor.id)), stopped])
    monkeypatch.setattr(checker, "load_state", lambda: {monitor.id: state})
    monkeypatch.setattr(checker, "backend_name", lambda: "fixture")
    monkeypatch.setattr(checker, "discover_siblings", lambda *_args, **_kw: checker.DiscoveryReport())
    monkeypatch.setattr(checker, "outcome_for", lambda *_args, **_kw: outcome)
    fetches = []
    monkeypatch.setattr(checker, "_warn_if_listing_vanished", lambda *_: None)
    monkeypatch.setattr(checker, "fetch_listing", lambda movie, *_: fetches.append(movie.event_code))
    store.query = lambda collection, *, equals, limit: {
        identifier: fields for (name, identifier), (fields, _) in store.rows.items()
        if name == collection and all(fields.get(key) == value for key, value in equals.items())
    }
    accepted = []
    tick = FencedTick(store, "shadow-test", sender=lambda *args: accepted.append(args),
                      catalogue=lambda _: {"movies": []}, clock=lambda: 0, now=lambda: now[0])
    counts = tick(Intent(monitor.id, True), now[0], 1)
    assert counts["checked"] == counts["fetches"] == counts["mail_accepted"] == 1
    assert fetches == [monitor.movie.event_code]
    assert len(accepted) == 1
    assert store.fields(OUTBOX, accepted[0][-1])["status"] == "accepted"
    assert store.rows[MONITORS, stopped.id] == baseline


def test_catalogue_conditional_read_and_unavailable_response():
    calls = []
    at = [1]

    class Reply:
        headers = {"ETag": "fixture"}

        def __init__(self, status):
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield b'{"movies":[]}'

    statuses = iter([200, 304, 503])

    def get(url, **kwargs):
        calls.append(kwargs["headers"])
        return Reply(next(statuses))

    refresh = CatalogueRefresh(get, clock=lambda: at[0])
    assert refresh.read("bookmyshow") == {"movies": []}
    assert refresh.read("bookmyshow") == {"movies": []}
    at[0] += 31
    assert refresh.read("bookmyshow") == {"movies": []}
    at[0] += 31
    with pytest.raises(ValueError, match="unavailable"):
        refresh.read("bookmyshow")
    assert calls == [{}, {"If-None-Match": "fixture"}, {"If-None-Match": "fixture"}]


def test_relay_is_reusable_and_gated_to_main_manual_dispatch():
    from pathlib import Path

    workflow = Path(".github/workflows/monitor-worker-relay.yml").read_text(encoding="utf-8")
    assert "  workflow_call:" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "vars.RADAR_ENABLE_MONITOR_RELAY == 'true'" in workflow
    assert "    environment: monitor-dispatch" in workflow
    assert workflow.count("id-token: write") == 1
    assert "secrets." not in workflow
    assert "monitor-worker-relay.yml" not in Path(
        ".github/workflows/bookmyshow-monitor.yml"
    ).read_text(encoding="utf-8")
