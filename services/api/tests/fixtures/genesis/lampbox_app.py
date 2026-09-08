"""Test Lambası — the M24 lamp-box fixture application (spec §7), a DIFFERENT
shape than the counter box so ``test_genesis_generalisation.py`` proves
``HttpAdapterGenerator`` is genuinely generic rather than counter-shaped.

``GET /spec`` describes: ``state`` (GET ``/lamp`` -> ``{on, brightness}``,
read, idempotent — TWO output fields, unlike the counter's one), ``set``
(POST ``/lamp`` ``{on, brightness}`` -> ``{on, brightness}``, mutating, TWO
input fields), ``toggle`` (POST ``/lamp/toggle`` -> ``{on, brightness}``,
mutating, no input fields at all). A TEST APPLICATION — runs only inside
tests and the voice corpus harness, never a product.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

NAME = "lampbox"

_STATE_OUTPUT = {
    "fields": {"on": "boolean", "brightness": "integer"},
    "required": ["on", "brightness"],
}

SPEC_TEMPLATE: dict[str, Any] = {
    "name": NAME,
    "operations": [
        {
            "id": "state",
            "method": "GET",
            "path": "/lamp",
            "input_schema": None,
            "output_schema": _STATE_OUTPUT,
            "side_effect": "read",
            "idempotent": True,
        },
        {
            "id": "set",
            "method": "POST",
            "path": "/lamp",
            "input_schema": {
                "fields": {"on": "boolean", "brightness": "integer"},
                "required": ["on", "brightness"],
            },
            "output_schema": _STATE_OUTPUT,
            "side_effect": "mutate",
            "idempotent": True,
        },
        {
            "id": "toggle",
            "method": "POST",
            "path": "/lamp/toggle",
            "input_schema": None,
            "output_schema": _STATE_OUTPUT,
            "side_effect": "mutate",
            "idempotent": False,
        },
    ],
    "evidence": {"read_back": "state"},
}


class _State:
    def __init__(self) -> None:
        self.on = False
        self.brightness = 0
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server_version = "LampBox/1.0"
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

    def _lamp_dict(self, server: LampBoxServer) -> dict[str, Any]:
        return {"on": server.state.on, "brightness": server.state.brightness}

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
        server: LampBoxServer = self.server  # type: ignore[assignment]
        if self.path == "/spec":
            if not server.spec_enabled:
                self._send_json({"error": "not_found"}, status=404)
                return
            spec = dict(SPEC_TEMPLATE)
            spec["base_url"] = server.base_url
            self._send_json(spec)
            return
        if self.path == "/lamp":
            with server.state.lock:
                self._send_json(self._lamp_dict(server))
            return
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
        server: LampBoxServer = self.server  # type: ignore[assignment]
        if self.path == "/lamp":
            payload = self._read_json()
            on = payload.get("on")
            brightness = payload.get("brightness")
            if (
                not isinstance(on, bool)
                or not isinstance(brightness, int)
                or isinstance(brightness, bool)
            ):
                self._send_json({"error": "validation_error"}, status=400)
                return
            with server.state.lock:
                server.state.on = on
                server.state.brightness = brightness
                self._send_json(self._lamp_dict(server))
            return
        if self.path == "/lamp/toggle":
            with server.state.lock:
                server.state.on = not server.state.on
                self._send_json(self._lamp_dict(server))
            return
        self._send_json({"error": "not_found"}, status=404)


class LampBoxServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1") -> None:
        super().__init__((host, 0), _Handler)
        self.state = _State()
        self.base_url = f"http://{host}:{self.server_port}"
        self.spec_enabled = True

    @property
    def on(self) -> bool:
        with self.state.lock:
            return self.state.on

    @property
    def brightness(self) -> int:
        with self.state.lock:
            return self.state.brightness

    @property
    def spec_url(self) -> str:
        return f"{self.base_url}/spec"


@contextmanager
def serve(host: str = "127.0.0.1") -> Iterator[LampBoxServer]:
    """Start the lamp box on a free port for the duration of the block."""
    server = LampBoxServer(host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


__all__ = ["NAME", "SPEC_TEMPLATE", "LampBoxServer", "serve"]
