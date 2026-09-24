"""The PVR INOX diagnostic: four requests at most, a clear verdict, nothing leaked,
and a workflow that only a person can start."""

from __future__ import annotations

from pathlib import Path

import pytest

from platforms.pvr_inox import PvrInoxProvider, sale_dates
from tests.conftest import FakeResponse
from tests.pvr_payloads import (
    FakePostSession,
    block,
    cinema,
    cinemas_payload,
    city_payload,
    city_row,
    nowshowing_payload,
    sessions_payload,
    show,
)
from tests.test_pvr_inox import COMMON, EN_2D, EN_IMAX
from tools import pvr_diagnose

TOKEN = "SECRET(TOKEN)=="


def run(responses) -> tuple[str, FakePostSession]:
    session = FakePostSession(responses)
    provider = PvrInoxProvider(session=session, sleeper=lambda _s: None, clock=lambda: 0.0)
    return pvr_diagnose.run(provider), session


def healthy() -> list:
    today = sale_dates(days=1)[0]
    return [
        city_payload(city_row(count=2)),
        cinemas_payload(cinema("101", "PVR Test Mall", shows=40), cinema("202", "INOX Test", shows=90)),
        nowshowing_payload([EN_2D, EN_IMAX]),
        sessions_payload(block(COMMON, [EN_2D, EN_IMAX], {
            "INSIGNIA": [show("202", 1, "38431", today, "2330", movie_format="3D", screen_type="INSIGNIA",
                              encrypted=TOKEN)],
        })),
    ]


def test_a_healthy_api_is_ok_in_four_requests_and_reads_the_busiest_cinema(capsys):
    verdict, session = run(healthy())
    out = capsys.readouterr().out
    assert verdict == pvr_diagnose.OK
    assert session.operations() == ["content/city", "content/cinemas", "content/nowshowing", "content/csessions"]
    assert session.calls[-1]["json"]["cid"] == "202"
    assert "requests sent: 4" in out and "INSIGNIA / 3D" in out


def test_nothing_sensitive_is_printed(capsys):
    run(healthy())
    out = capsys.readouterr().out
    assert TOKEN not in out and "seatlayout" not in out and "Bearer" not in out and "Authorization" not in out


@pytest.mark.parametrize("status, verdict", [(403, pvr_diagnose.BLOCKED), (401, pvr_diagnose.BLOCKED),
                                             (429, pvr_diagnose.RATE_LIMITED)])
def test_a_refusal_ends_the_run_at_once(status, verdict):
    got, session = run([FakeResponse(status, {})] + healthy())
    assert got == verdict and len(session.calls) == 1


def test_a_refusal_later_on_also_stops_everything():
    got, session = run(healthy()[:2] + [FakeResponse(429, {})])
    assert got == pvr_diagnose.RATE_LIMITED and len(session.calls) == 3


def test_a_changed_shape_is_malformed():
    got, _ = run([city_payload(city_row(count=2)), {"status": 302, "output": {"cinemas": []}}])
    assert got == pvr_diagnose.MALFORMED


def test_a_server_error_is_an_api_error():
    got, _ = run([FakeResponse(502, None)])
    assert got == pvr_diagnose.API_ERROR


def test_a_closed_day_is_still_ok():
    responses = healthy()[:3] + [{"status": 400, "code": 10002, "result": "error", "msg": "No Record", "output": None}]
    got, _ = run(responses)
    assert got == pvr_diagnose.OK


def test_the_workflow_is_manual_read_only_and_secret_free():
    text = Path(".github/workflows/pvr-diagnose.yml").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "on:\n  workflow_dispatch:\n" in code                  # the only trigger
    for trigger in ("schedule:", "push:", "pull_request", "workflow_run", "cron"):
        assert trigger not in code
    assert "permissions:\n  contents: read\n" in code and "write" not in code
    assert "secrets." not in code and "FIREBASE" not in code and "GMAIL" not in code
    assert "timeout-minutes: 5" in code
    assert "python tools/pvr_diagnose.py --city hyderabad" in code
