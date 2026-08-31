"""Supervised demo target: a minimal "browser-agent"-shaped service (M6).

On startup it resolves the ACTIVE release through the workspace pointer file
(``<workspace>/current.txt``) and loads ``handler.py`` from that release
directory — never from a fixed source path — so activating a different release
plus a process restart is a complete deployment. Stdlib only.

Endpoints:
    GET /health    liveness: the active release's handler module loaded.
    GET /selftest  synthetic check: runs deterministic browser-agent-shaped
                   operations from the loaded module against known expected
                   outputs (no real browser involved).

The selftest EXPECTATIONS live here (the monitoring side), deliberately outside
the release directory, so a broken release cannot redefine success.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMPONENT = "browser-agent-demo"

# Synthetic browser-agent scenario matrix: (input error message, expected class).
MAP_ERROR_CASES = (
    ("net::ERR_NAME_NOT_RESOLVED at https://example.invalid", "dependency_unavailable"),
    ("Timeout 30000ms exceeded while waiting for selector", "timeout"),
    ("element not found: #submit-button", "element_not_found"),
)
RUN_TASK_INPUT = {
    "op": "extract_title",
    "html": "<html><head><title>PagentOS</title></head><body>ok</body></html>",
}
RUN_TASK_EXPECTED = {"status": "ok", "title": "PagentOS"}


def load_active_handler(workspace: Path) -> tuple[types.ModuleType | None, str | None, str | None]:
    """Returns (module, version, load_error)."""
    pointer = workspace / "current.txt"
    try:
        version = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, None, "no active release pointer (current.txt missing)"
    handler_path = workspace / "releases" / version / "handler.py"
    if not handler_path.is_file():
        return None, version, f"handler.py missing in release {version}"
    try:
        spec = importlib.util.spec_from_file_location(f"release_handler_{version}", handler_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any load failure must surface as unhealthy
        return None, version, f"handler load failed: {type(exc).__name__}: {exc}"
    return module, version, None


def run_selftest(module: types.ModuleType) -> dict[str, object]:
    """Deterministic synthetic checks against the loaded release module."""
    try:
        if module.self_test() is not True:
            return {"status": "fail", "error_class": "self_test_failed", "check": "self_test"}
    except Exception as exc:  # noqa: BLE001 - a raising self_test is a failed check
        return {
            "status": "fail",
            "error_class": "self_test_failed",
            "check": "self_test",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    for message, expected in MAP_ERROR_CASES:
        try:
            actual = module.map_error(message)
        except Exception as exc:  # noqa: BLE001
            actual = f"raised {type(exc).__name__}: {exc}"
        if actual != expected:
            return {
                "status": "fail",
                "error_class": "wrong_error_mapping",
                "check": "map_error",
                "input": message,
                "expected": expected,
                "actual": actual,
            }
    try:
        task_result = module.run_task(dict(RUN_TASK_INPUT))
    except Exception as exc:  # noqa: BLE001
        task_result = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    if task_result != RUN_TASK_EXPECTED:
        return {
            "status": "fail",
            "error_class": "task_output_mismatch",
            "check": "run_task",
            "input": RUN_TASK_INPUT,
            "expected": RUN_TASK_EXPECTED,
            "actual": task_result,
        }
    return {"status": "ok", "checks": ["self_test", "map_error", "run_task"]}


def build_server(workspace: Path, host: str, port: int) -> ThreadingHTTPServer:
    module, version, load_error = load_active_handler(workspace)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            base = {"component": COMPONENT, "version": version}
            if self.path.rstrip("/") == "/health":
                if module is None:
                    self._send(
                        500,
                        {**base, "status": "fail", "error_class": "handler_load_failed",
                         "detail": load_error},
                    )
                else:
                    self._send(200, {**base, "status": "ok"})
            elif self.path.rstrip("/") == "/selftest":
                if module is None:
                    self._send(
                        500,
                        {**base, "status": "fail", "error_class": "handler_load_failed",
                         "detail": load_error},
                    )
                else:
                    result = run_selftest(module)
                    status = 200 if result.get("status") == "ok" else 500
                    self._send(status, {**base, **result})
            else:
                self._send(404, {"status": "fail", "error_class": "not_found"})

        def log_message(self, *args) -> None:  # keep stdout clean/deterministic
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M6 supervised demo target service")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    server = build_server(Path(args.workspace), args.host, args.port)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
