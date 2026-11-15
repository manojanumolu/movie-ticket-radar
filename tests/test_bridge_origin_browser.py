"""The cookie bridge answers its own app and nobody else (audit SEC-02).

The session cookie holds a Firebase refresh token and has to be readable by
the bridge's script. Before the fix the bridge acted on a render message from
*any* window and replied to ``"*"``: a page on another origin could frame
``auth/bridge/index.html``, post it a render, and have it overwrite the
session cookie (reproduced in the audit). Reading the token back failed only
because an unrelated statement threw first.

These run the real app (``tools/fake_firebase_app.py``) in Chromium, sign a
test account in, and then open an attacker page on a different origin that
frames the bridge and posts it render messages — the audit's own attack — and
check that nothing is written and nothing comes back. A page that is the same
*site* but another *origin* (another port) is used as well, because that is
the case SameSite cookies do not protect.

Off by default, like the rest of the browser suite:

    TR_BROWSER_TESTS=1 python -m pytest tests/test_bridge_origin_browser.py
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from urllib.parse import quote

import pytest

from tests.test_auth_bridge_browser import (
    RAVI,
    Server,
    chromium,  # noqa: F401 - a fixture
    in_app,
    server,  # noqa: F401 - a fixture
    sign_in,
    wait_for,
)

pytestmark = pytest.mark.skipif(__import__("os").environ.get("TR_BROWSER_TESTS") != "1",
                                reason="browser tests: set TR_BROWSER_TESTS=1")

ATTACKER_PAGE = """<!doctype html><meta charset="utf-8"><title>attacker</title><body><script>
window.answers = []; window.ready = false;
const params = new URLSearchParams(location.search);
const frame = document.createElement("iframe");
frame.src = params.get("bridge");
document.body.appendChild(frame);
const ops = JSON.parse(params.get("ops") || "[]");
function render() {
  frame.contentWindow.postMessage({type: "streamlit:render", args: {
    names: ["tr_session", "tr_session_started", "tr_oauth_state", "other"],
    ops: ops, nonce: Date.now()}}, "*");
}
window.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type === "streamlit:componentReady") { window.ready = true; render(); }
  if (d.type === "streamlit:setComponentValue") window.answers.push(d.value);
});
// Also push renders blindly, in case the handshake is never seen.
frame.addEventListener("load", () => { for (let i = 0; i < 5; i++) setTimeout(render, 200 * i); });
</script></body>"""


class AttackerSite:
    """A page served from 127.0.0.1 on its own port: a different origin."""

    def __init__(self):
        body = ATTACKER_PAGE.encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # quiet
                pass

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, host: str, bridge: str, ops: list[dict]) -> str:
        return (f"http://{host}:{self.port}/?bridge={quote(bridge, safe='')}"
                f"&ops={quote(json.dumps(ops), safe='')}")

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture(scope="module")
def attacker():
    site = AttackerSite()
    yield site
    site.stop()


def _session_cookie(context, app_server: Server) -> str:
    return next((c["value"] for c in context.cookies(app_server.url) if c["name"] == "tr_session"), "")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"], ids=["same-site-other-origin", "cross-site"])
def test_another_origin_cannot_write_or_read_the_session_through_the_bridge(chromium, server, attacker,  # noqa: F811 - the imported fixtures
                                                                          host):
    context = chromium.new_context(viewport={"width": 1280, "height": 800})
    try:
        app = context.new_page()
        sign_in(app, server, RAVI)
        before = _session_cookie(context, server)
        assert before, "signed in: the session cookie exists"

        bridge = server.url + "component/auth.session.tr_auth_bridge/index.html"
        evil = context.new_page()
        evil.goto(attacker.url(host, bridge, [
            {"name": "tr_session", "value": "ATTACKER-CHOSEN", "max_age": 600},
            {"name": "tr_session_started", "value": "1", "max_age": 600},
            {"name": "some_other_cookie", "value": "x", "max_age": 600},
        ]))
        evil.wait_for_timeout(3000)
        seen = evil.evaluate("({ready: window.ready, answers: window.answers})")

        # It answered nothing to the other origin: no cookie value left.
        assert seen["answers"] == [], seen["answers"]
        # And it wrote nothing: the session cookie is untouched.
        after = {c["name"]: c["value"] for c in context.cookies(server.url)}
        assert after.get("tr_session") == before
        assert "some_other_cookie" not in after

        # The real app still works with the same cookie: a reload restores.
        app.reload()
        wait_for(app, in_app, 60, "the app after a reload")
    finally:
        context.close()


def test_the_app_itself_still_reads_and_writes_through_the_bridge(chromium, server):  # noqa: F811 - the imported fixtures
    """The checks must not cost the legitimate path anything: sign-in writes
    the cookie and a reload restores from it (the full flow is in
    ``test_auth_bridge_browser.py``; this is the shortest proof)."""
    context = chromium.new_context(viewport={"width": 390, "height": 844})
    try:
        tab = context.new_page()
        sign_in(tab, server, RAVI)
        tab.reload()
        wait_for(tab, in_app, 60, "the app after a reload")
    finally:
        context.close()
