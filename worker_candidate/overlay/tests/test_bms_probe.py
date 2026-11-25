"""The connectivity probe makes exactly one request and names its verdict."""
import json

import pytest

import run_bms_probe


class Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=20):
        self.calls.append((url, params))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


LISTING = {"moviesData": {"BookMyShow": {"arrEvents": [
    {"EventTitle": "Fixture", "EventCode": "ET00000001", "ChildEvents": [
        {"EventCode": "ET00000002", "EventName": "Fixture (English)", "EventLanguage": "English",
         "EventDimension": "2D", "EventImageCode": "fixture"}]}]}}}


@pytest.mark.parametrize("response,code,verdict", [
    (Response(200, LISTING), 0, "BMS_PROBE_OK"),
    (Response(403), 2, "BMS_PROBE_BLOCKED http=403"),
    (Response(429), 3, "BMS_PROBE_RATE_LIMITED"),
    (Response(500), 4, "BMS_PROBE_HTTP_ERROR http=500"),
    (Response(200, None), 6, "BMS_PROBE_MALFORMED"),
    (OSError("no route"), 5, "BMS_PROBE_NETWORK_ERROR"),
])
def test_exactly_one_request_and_a_verdict(response, code, verdict):
    session = Session(response)
    got, line = run_bms_probe.probe("hyderabad", session=session)
    assert got == code and line.startswith(verdict)
    assert len(session.calls) == 1
    url, params = session.calls[0]
    assert url == "https://in.bookmyshow.com/serv/getData"
    assert params == {"cmd": "QUICKBOOK", "type": "MT", "f": "json"}


def test_a_second_request_is_refused():
    counting = run_bms_probe.CountingSession(Session(Response(200, LISTING)))
    counting.get("https://in.bookmyshow.com/serv/getData")
    with pytest.raises(RuntimeError):
        counting.get("https://in.bookmyshow.com/serv/getData")


def test_probe_is_packaged_in_the_worker_image():
    from pathlib import Path

    ignore = Path(__file__).resolve().parents[1].joinpath(".dockerignore").read_text().splitlines()
    assert "!run_bms_probe.py" in ignore
