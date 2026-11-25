#!/usr/bin/env python3
"""Private worker HTTP interface with an explicit fixture replay mode."""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from monitor.runtime import Intent, Runtime, replay_tick

MAX_BODY = 1024
MONITOR_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def parse_intent(path: str, raw: bytes) -> Intent:
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise ValueError("expected an object")
    if path == "/tick":
        if body:
            raise ValueError("scheduled tick takes no inputs")
        return Intent()
    if set(body) - {"monitor_id", "force"}:
        raise ValueError("unknown check input")
    monitor_id = body.get("monitor_id")
    force = body.get("force", True)
    if not isinstance(monitor_id, str) or not MONITOR_ID.fullmatch(monitor_id):
        raise ValueError("a single monitor ID is required")
    if type(force) is not bool:
        raise ValueError("force must be boolean")
    return Intent(monitor_id=monitor_id, force=force)


def configured_runtime() -> Runtime:
    if os.environ.get("WORKER_MODE") == "shadow":
        return Runtime(replay_tick)
    if os.environ.get("WORKER_MODE") == "fenced":
        from config import worker_credentials
        from monitor.catalogue_refresh import CatalogueRefresh
        from monitor.runtime import FencedTick
        from monitor.state import worker_firestore_client
        from notifications.email import send_outbox_email

        authority = os.environ.get("WORKER_AUTHORITY", "")
        if worker_credentials.mode() == "legacy" or not MONITOR_ID.fullmatch(authority):
            raise ValueError("fenced mode requires workload identity and a named authority")
        client = worker_firestore_client()
        client.token()
        return Runtime(FencedTick(client, authority, sender=send_outbox_email,
                                  catalogue=CatalogueRefresh().read))
    raise ValueError("an explicit worker mode is required")


def handler_for(runtime: Runtime) -> type[BaseHTTPRequestHandler]:
    busy = Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_args):
            # Suppress request metadata in platform logs.
            pass

        def _reply(self, status: int, body: dict):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/healthz":
                self._reply(200, {"ready": True})
            else:
                self._reply(404, {"error": "unknown endpoint"})

        def do_POST(self):
            if self.path not in {"/tick", "/check"}:
                self._reply(404, {"error": "unknown endpoint"})
                return
            try:
                if self.headers.get_all("Transfer-Encoding"):
                    raise ValueError("transfer encoding")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) > 1 or lengths and not lengths[0].isdigit():
                    raise ValueError("content length")
                length = int(lengths[0]) if lengths else 0
                if length > MAX_BODY:
                    self._reply(413, {"error": "body exceeds limit"})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("short body")
                intent = parse_intent(self.path, raw)
            except (OSError, ValueError):
                self._reply(400, {"error": "invalid worker request"})
                return
            if not busy.acquire(blocking=False):
                self._reply(503, {"error": "worker busy"})
                return
            try:
                results = runtime.run(intent, scheduled=self.path == "/tick")
            except Exception as exc:
                print(json.dumps({"worker_result": "failed", "kind": type(exc).__name__}), flush=True)
                self._reply(503, {"error": "worker invocation failed"})
            else:
                print(json.dumps({"worker_result": "complete", "slots": results}), flush=True)
                self._reply(200, {"slots": results})
            finally:
                busy.release()

    return Handler


def main():
    runtime = configured_runtime()
    port = int(os.environ.get("PORT", "8080"))
    with ThreadingHTTPServer(("0.0.0.0", port), handler_for(runtime)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
