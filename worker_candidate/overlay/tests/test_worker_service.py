import http.client
import json
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from monitor.runtime import Intent, Runtime, replay_tick
from run_worker_service import configured_runtime, handler_for, parse_intent


@pytest.mark.parametrize("raw", [b"null", b"[]", b'{"force":1,"monitor_id":"id"}',
                                 b'{"monitor_id":"a/b"}', b'{"monitor_id":"id","extra":true}',
                                 b'{"monitor_id":12}', b'{"force":true}'])
def test_rejects_invalid_requests(raw):
    with pytest.raises(ValueError):
        parse_intent("/check", raw)


def test_shadow_mode_is_explicit(monkeypatch):
    monkeypatch.delenv("WORKER_MODE", raising=False)
    with pytest.raises(ValueError):
        configured_runtime()
    monkeypatch.setenv("WORKER_MODE", "shadow")
    assert configured_runtime().tick is replay_tick


def test_two_slots_share_deadline():
    t = [0]
    calls = []

    def sleep(seconds):
        t[0] += seconds

    def tick(intent, at, deadline):
        calls.append((intent, t[0], deadline))
        return {}

    runtime = Runtime(tick, clock=lambda: t[0], sleep=sleep)
    runtime.run(Intent(), scheduled=True)
    assert calls == [(Intent(), 0, 45), (Intent(), 30, 45)]
    runtime.run(Intent("abc", True), scheduled=False)
    assert calls[-1] == (Intent("abc", True), 30, 75)


def test_http_shadow_health_and_check():
    runtime = Runtime(replay_tick, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(runtime)) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"ready": True}
            conn.request("POST", "/check", '{"monitor_id":"fixture-monitor"}')
            response = conn.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"slots": [{"checked": 1, "changes": 0}]}
            conn.close()
        finally:
            server.shutdown()
            thread.join()


def test_health_reports_the_configured_mode_without_secrets(monkeypatch):
    from run_worker_service import startup_summary

    runtime = Runtime(replay_tick, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(runtime, "fenced")) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request("GET", "/healthz")
            assert json.loads(conn.getresponse().read()) == {"ready": True, "mode": "fenced"}
        finally:
            server.shutdown()
            thread.join()
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "ticketradar03")
    monkeypatch.setenv("FIREBASE_EXPECTED_PROJECT", "ticketradar03")
    monkeypatch.setenv("FIREBASE_CREDENTIAL_MODE", "adc")
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic-secret")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    summary = startup_summary("fenced", "ticketradar03-cloudrun-v1")
    assert summary == {"worker_start": "fenced", "authority": "ticketradar03-cloudrun-v1",
                       "credential_mode": "adc", "project_pinned": True,
                       "sender_configured": True, "legacy_key_present": False}
    assert "synthetic-secret" not in json.dumps(summary)
    assert "sender@example.com" not in json.dumps(summary)
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    assert startup_summary("fenced", "x")["sender_configured"] is False
