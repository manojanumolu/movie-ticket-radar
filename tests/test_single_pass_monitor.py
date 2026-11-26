"""The five-minute single-pass monitor: one pass per run, no overlap, no
refusal bypass, and the city-listing throttle kept between runs."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from monitor import discovery
from platforms import http

#: Captured at import, before the suite's autouse fixture swaps it for an
#: offline stub, so the real selection logic can be tested.
REAL_BUILD_SESSION = http.build_session

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def load(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


# ── the workflow ──────────────────────────────────────────────────────────
def test_one_job_one_pass_and_a_hard_stop_inside_five_minutes():
    wf = load("bookmyshow-monitor.yml")
    assert wf["on"]["schedule"] == [{"cron": "*/5 * * * *"}]
    assert set(wf["on"]["workflow_dispatch"]["inputs"]) == {"force", "dry_run", "monitor_id"}
    assert wf["concurrency"] == {"group": "bookmyshow-monitor", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    [job] = wf["jobs"].values()
    assert job["timeout-minutes"] <= 4
    [run] = [s for s in job["steps"] if "run_monitor.py" in s.get("run", "")]
    assert "python run_monitor.py" in run["run"]
    for forbidden in ("--loop", "--max-minutes", "--poll-seconds", "sleep "):
        assert forbidden not in run["run"]
    env = run["env"]
    assert env["RADAR_DISCOVERY_CLOCK"] == "firestore"
    assert env["RADAR_CLIENT"] == "honest"                 # no browser imitation
    assert env["RADAR_RETRY_REFUSALS"] == "0"
    assert env["RADAR_HTTP_ATTEMPTS"] == "1"
    assert "GITHUB_TOKEN" not in env                        # nothing to dispatch or push with


def test_no_second_scheduler_for_the_monitor_and_catalogue_unchanged():
    scheduled = {}
    for path in WORKFLOWS.glob("*.yml"):
        wf = load(path.name)
        if "schedule" in (wf["on"] or {}):
            scheduled[path.name] = [c["cron"] for c in wf["on"]["schedule"]]
    assert scheduled == {"bookmyshow-monitor.yml": ["*/5 * * * *"],
                         "catalogue-sync.yml": ["0 1,7,13,19 * * *"]}
    texts = {p.name: p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml")}
    assert [n for n, t in texts.items() if "run_monitor.py" in t] == ["bookmyshow-monitor.yml"]


# ── refusals are final ────────────────────────────────────────────────────
class Resp:
    def __init__(self, status):
        self.status_code = status


class FakeCurlSession:
    def __init__(self, script, calls):
        self.script, self.calls = script, calls

    def get(self, url, headers=None, params=None, timeout=20):
        self.calls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Resp(item)

    def close(self):
        pass


def _session(monkeypatch, script, **kwargs):
    calls = []

    class Curl:
        @staticmethod
        def Session(impersonate):
            return FakeCurlSession(script, calls)

    monkeypatch.setattr(http, "_curl", Curl)
    return http.ImpersonatingSession(sleeper=lambda s: None, **kwargs), calls


@pytest.mark.parametrize("status", [401, 403, 429])
def test_with_refusal_retries_off_a_refusal_is_one_request(monkeypatch, status):
    session, calls = _session(monkeypatch, [status, 200, 200], retries=2, retry_refusals=False)
    assert session.get("https://example.invalid").status_code == status
    assert len(calls) == 1


def test_a_network_error_is_retried_once_and_only_once(monkeypatch):
    session, calls = _session(monkeypatch, [OSError("reset"), 200], retries=2, retry_refusals=False)
    assert session.get("https://example.invalid").status_code == 200
    assert len(calls) == 2
    session, calls = _session(monkeypatch, [OSError("a"), OSError("b"), 200], retries=2, retry_refusals=False)
    with pytest.raises(OSError):
        session.get("https://example.invalid")
    assert len(calls) == 2


def test_the_default_is_unchanged_for_everything_else(monkeypatch):
    """Catalogue sync and other callers set nothing: today's behaviour."""
    monkeypatch.delenv("RADAR_RETRY_REFUSALS", raising=False)
    monkeypatch.delenv("RADAR_HTTP_ATTEMPTS", raising=False)
    assert http.session_policy() == (http.BLOCK_RETRIES, True)


def test_the_monitor_environment_turns_refusal_retries_off(monkeypatch):
    monkeypatch.setenv("RADAR_RETRY_REFUSALS", "0")
    monkeypatch.setenv("RADAR_HTTP_ATTEMPTS", "2")
    assert http.session_policy() == (2, False)
    monkeypatch.setenv("RADAR_HTTP_ATTEMPTS", "99")          # never above the old ceiling
    assert http.session_policy() == (http.BLOCK_RETRIES, False)
    monkeypatch.setenv("RADAR_HTTP_ATTEMPTS", "nonsense")
    assert http.session_policy() == (http.BLOCK_RETRIES, False)


# ── the discovery clock survives between runs ─────────────────────────────
class Client:
    def __init__(self):
        self.docs = {}

    def get(self, collection, doc):
        return self.docs.get((collection, doc))

    def set(self, collection, doc, fields):
        self.docs[(collection, doc)] = fields


def test_the_clock_round_trips_through_firestore_without_touching_the_file(monkeypatch):
    client = Client()
    monkeypatch.setenv("RADAR_DISCOVERY_CLOCK", "firestore")
    monkeypatch.setattr(discovery, "_clock_client", lambda: client)
    monkeypatch.setattr(discovery, "save_discovery", lambda *a, **k: pytest.fail("file written"))
    monkeypatch.setattr(discovery, "load_discovery", lambda: pytest.fail("file read"))
    clock = {"bookmyshow:hyderabad": {"at": "2026-12-01T10:00:00+05:30", "ok": True}}
    discovery._save_clock(clock, mirror=False)
    assert discovery._load_clock() == clock
    assert client.docs[discovery.CLOCK_DOC] == {"clock": clock}


def test_a_clock_failure_falls_back_to_the_file_and_never_stops_a_check(monkeypatch):
    class Broken:
        def get(self, *a):
            raise RuntimeError("down")

        def set(self, *a):
            raise RuntimeError("down")

    monkeypatch.setattr(discovery, "_clock_client", lambda: Broken())
    saved = []
    monkeypatch.setattr(discovery, "load_discovery", lambda: {"from": "file"})
    monkeypatch.setattr(discovery, "save_discovery", lambda c, mirror: saved.append(c))
    assert discovery._load_clock() == {"from": "file"}
    discovery._save_clock({"x": 1}, mirror=False)
    assert saved == [{"x": 1}]


def test_without_the_flag_the_file_is_used(monkeypatch):
    monkeypatch.delenv("RADAR_DISCOVERY_CLOCK", raising=False)
    assert discovery._clock_client() is None


def test_maintenance_is_manual_dry_run_by_default_and_shares_the_monitor_lock():
    wf = load("monitor-maintenance.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    assert wf["concurrency"] == {"group": "bookmyshow-monitor", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    [job] = wf["jobs"].values()
    run = job["steps"][-1]["run"]
    assert '[ "$INPUT_CONFIRM" = "APPLY" ] && args+=(--apply)' in run
    assert "--extend" not in run and "monitor_until" not in run


# ── the single pass fails loudly on a missing or revoked credential ──────
def _no_checks(monkeypatch):
    import run_monitor
    from monitor import checker
    called = []
    monkeypatch.setattr(run_monitor, "run_once", lambda **k: called.append(k) or pytest.fail("checked anyway"))
    monkeypatch.setattr(checker, "run_once", lambda **k: called.append(k) or pytest.fail("checked anyway"))
    return run_monitor, called


def test_a_missing_credential_inside_actions_fails_the_run(monkeypatch, capsys):
    run_monitor, called = _no_checks(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("TICKETRADAR_ALLOW_JSON_WORKER", raising=False)
    assert run_monitor.main([]) == 1
    out = capsys.readouterr().out
    assert "FATAL" in out and "refusing to fall back to JSON" in out and "::error::" in out
    assert called == []


def test_a_revoked_credential_fails_the_run_instead_of_reporting_nothing_running(monkeypatch, capsys):
    import run_monitor
    from config import firestore as fs
    from monitor import state as state_mod

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-ticketradar")
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "worker@test", "type": "service_account"}')
    monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "revoked"))
    monkeypatch.setattr(state_mod, "_admin", None)
    state_mod.set_scope_provider(None)
    monkeypatch.setattr(state_mod, "_transport",
                        lambda method, url, token, body: (401, {"error": {"message": "UNAUTHENTICATED"}}))
    assert run_monitor.main([]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "checked:" not in out


def test_a_working_store_still_runs_one_pass(monkeypatch, capsys):
    import run_monitor

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)            # local: the JSON store is allowed
    assert run_monitor.main([]) == 0
    out = capsys.readouterr().out
    assert "[worker] backend=json" in out and "checked:" in out



# ── honest mode: no browser imitation, refusals final (requirement 9) ─────
class Recorder:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def get(self, url, headers=None, params=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "headers": headers, "params": params,
                           "timeout": timeout, "allow_redirects": allow_redirects})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class R:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


BROWSER = {"User-Agent": "Mozilla/5.0 Chrome/140", "sec-ch-ua": '"Chromium"', "sec-ch-ua-mobile": "?0",
           "sec-ch-ua-platform": '"Windows"', "Referer": "https://in.bookmyshow.com/x",
           "Origin": "https://in.bookmyshow.com", "Accept": "application/json", "x-region-code": "HYD"}


def test_the_honest_client_says_what_it_is_and_imitates_no_browser():
    inner = Recorder([R(200)])
    http.HonestSession(session=inner).get("https://in.bookmyshow.com/api", headers=BROWSER)
    sent = inner.calls[0]["headers"]
    assert sent["User-Agent"] == http.HONEST_USER_AGENT and "TicketRadar" in sent["User-Agent"]
    for imitation in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "Referer", "Origin"):
        assert imitation not in sent
    assert sent["Accept"] == "application/json" and sent["x-region-code"] == "HYD"   # what the API needs
    assert inner.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize("status", [401, 403, 429])
def test_the_honest_client_never_retries_a_refusal(status):
    inner = Recorder([R(status), R(200)])
    assert http.HonestSession(attempts=3, sleeper=lambda s: None, session=inner).get("u").status_code == status
    assert len(inner.calls) == 1


def test_the_honest_client_retries_only_a_request_that_did_not_complete():
    inner = Recorder([OSError("reset"), R(200)])
    assert http.HonestSession(attempts=2, sleeper=lambda s: None, session=inner).get("u").status_code == 200
    inner = Recorder([OSError("a"), OSError("b")])
    with pytest.raises(OSError):
        http.HonestSession(attempts=2, sleeper=lambda s: None, session=inner).get("u")
    assert len(inner.calls) == 2


def test_honest_mode_selects_the_honest_client_and_nothing_else_changes(monkeypatch):
    monkeypatch.setenv("RADAR_CLIENT", "honest")
    assert isinstance(REAL_BUILD_SESSION(), http.HonestSession)
    monkeypatch.delenv("RADAR_CLIENT")
    assert not isinstance(REAL_BUILD_SESSION(), http.HonestSession)   # catalogue sync and tools unchanged


def _provider(monkeypatch, script, honest=True):
    from platforms.bookmyshow import BookMyShowProvider

    if honest:
        monkeypatch.setenv("RADAR_CLIENT", "honest")
    else:
        monkeypatch.delenv("RADAR_CLIENT", raising=False)
    session = Recorder(script)
    return BookMyShowProvider(session=session, sleeper=lambda s: None), session


def test_honest_mode_stops_at_a_rate_limit_instead_of_retrying(monkeypatch):
    from platforms.base import PlatformBlocked
    from platforms.bookmyshow import region_for

    provider, session = _provider(monkeypatch, [R(429), R(429), R(429)])
    with pytest.raises(PlatformBlocked):
        provider._get("ET00439706", "20261217", region_for("hyderabad"))
    assert len(session.calls) == 1 and session.calls[0]["timeout"] == 10


def test_honest_mode_never_routes_round_a_refused_city_listing(monkeypatch):
    from platforms.base import PlatformBlocked

    provider, session = _provider(monkeypatch, [R(403), R(200), R(200)])
    with pytest.raises(PlatformBlocked):
        provider.list_movies("hyderabad")
    assert len(session.calls) == 1                                   # no second endpoint tried


def test_honest_mode_bounds_network_retries_per_read(monkeypatch):
    from platforms.base import PlatformError
    from platforms.bookmyshow import region_for
    from platforms.http import request_errors

    err = request_errors()[0]("timed out")
    provider, session = _provider(monkeypatch, [err, err, err, err])
    with pytest.raises(PlatformError):
        provider._get("ET00439706", "20261217", region_for("hyderabad"))
    assert len(session.calls) == 2


def test_without_honest_mode_the_existing_behaviour_is_unchanged(monkeypatch):
    from platforms.bookmyshow import MAX_ATTEMPTS, TIMEOUT, region_for

    provider, session = _provider(monkeypatch, [R(429)] * MAX_ATTEMPTS, honest=False)
    with pytest.raises(Exception):
        provider._get("ET00439706", "20261217", region_for("hyderabad"))
    assert len(session.calls) == MAX_ATTEMPTS and session.calls[0]["timeout"] == TIMEOUT
