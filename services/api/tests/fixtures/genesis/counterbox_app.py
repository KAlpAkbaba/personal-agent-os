"""Sayaç Kutusu — the M24 counter-box fixture application (spec §7).

A stdlib ``http.server`` on ``127.0.0.1:<free port>`` with ``GET /spec`` (its
own ``InterfaceDescription``): ``read`` (GET ``/counter`` -> ``{value}``),
``increment`` (POST ``/counter/increment`` ``{by}`` -> ``{value}``, mutating,
not idempotent), ``reset`` (POST ``/counter/reset`` -> ``{value: 0}``,
mutating, idempotent). A TEST APPLICATION — runs only inside tests and the
voice corpus harness, never a product.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

NAME = "counterbox"

SPEC_TEMPLATE: dict[str, Any] = {
    "name": NAME,
    "operations": [
        {
            "id": "read",
            "method": "GET",
            "path": "/counter",
            "input_schema": None,
            "output_schema": {"fields": {"value": "integer"}, "required": ["value"]},
            "side_effect": "read",
            "idempotent": True,
        },
        {
            "id": "increment",
            "method": "POST",
            "path": "/counter/increment",
            "input_schema": {"fields": {"by": "integer"}, "required": ["by"]},
            "output_schema": {"fields": {"value": "integer"}, "required": ["value"]},
            "side_effect": "mutate",
            "idempotent": False,
        },
        {
            "id": "reset",
            "method": "POST",
            "path": "/counter/reset",
            "input_schema": None,
            "output_schema": {"fields": {"value": "integer"}, "required": ["value"]},
            "side_effect": "mutate",
            "idempotent": True,
        },
    ],
    "evidence": {"read_back": "read"},
}


class _State:
    def __init__(self) -> None:
        self.value = 0
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server_version = "CounterBox/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
        server: CounterBoxServer = self.server  # type: ignore[assignment]
        if self.path == "/spec":
            if not server.spec_enabled:
                self._send_json({"error": "not_found"}, status=404)
                return
            spec = dict(SPEC_TEMPLATE)
            spec["base_url"] = server.base_url
            self._send_json(spec)
            return
        if self.path == "/counter":
            with server.state.lock:
                self._send_json({"value": server.state.value})
            return
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
        server: CounterBoxServer = self.server  # type: ignore[assignment]
        if self.path == "/counter/increment":
            try:
                payload = self._read_json()
                by = payload.get("by")
                if not isinstance(by, int) or isinstance(by, bool):
                    raise ValueError("by must be an integer")
            except ValueError:
                self._send_json({"error": "validation_error"}, status=400)
                return
            with server.state.lock:
                server.state.value += by
                self._send_json({"value": server.state.value})
            return
        if self.path == "/counter/reset":
            with server.state.lock:
                server.state.value = 0
                self._send_json({"value": server.state.value})
            return
        self._send_json({"error": "not_found"}, status=404)


class CounterBoxServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1") -> None:
        super().__init__((host, 0), _Handler)
        self.state = _State()
        self.base_url = f"http://{host}:{self.server_port}"
        #: Toggled off by ``test_genesis_no_shortcut_guard.py`` to prove a
        #: genesis run with no ``/spec`` fails honestly at ``researching``.
        self.spec_enabled = True

    @property
    def value(self) -> int:
        with self.state.lock:
            return self.state.value

    @property
    def spec_url(self) -> str:
        return f"{self.base_url}/spec"


@contextmanager
def serve(host: str = "127.0.0.1") -> Iterator[CounterBoxServer]:
    """Start the counter box on a free port for the duration of the block."""
    server = CounterBoxServer(host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


__all__ = ["NAME", "SPEC_TEMPLATE", "CounterBoxServer", "serve"]
