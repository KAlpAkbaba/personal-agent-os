"""Incident report shape, outbox durability, best-effort API posting."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from recovery_supervisor.incidents import (
    INCIDENT_SCHEMA,
    build_incident_report,
    post_report,
    write_outbox,
)


def make_report() -> dict:
    return build_incident_report(
        component="browser-agent-demo",
        error_class="wrong_error_mapping",
        failing_check="selftest",
        workspace="C:/ws",
        active_version="1.1.0",
        active_manifest_digest="ab" * 32,
        last_known_good="1.0.0",
        rolled_back_to="1.0.0",
        evidence={"selftest": {"expected": "dependency_unavailable", "actual": "internal_bug"}},
        detected_at="2026-08-31T00:00:00+00:00",
        recovered_at="2026-08-31T00:00:05+00:00",
    )


def test_report_shape_carries_fingerprint_material() -> None:
    report = make_report()
    assert report["schema"] == INCIDENT_SCHEMA
    assert report["fingerprint_material"] == {
        "component": "browser-agent-demo",
        "error_class": "wrong_error_mapping",
        "failing_check": "selftest",
    }
    for key in (
        "component",
        "severity",
        "workspace",
        "active_version",
        "active_manifest_digest",
        "last_known_good",
        "rolled_back_to",
        "evidence",
        "detected_at",
        "recovered_at",
    ):
        assert key in report
    assert report["severity"] == "critical"


def test_write_outbox_creates_parseable_file(tmp_path: Path) -> None:
    path = write_outbox(tmp_path / "incidents-outbox", make_report())
    assert path.exists()
    assert path.name.startswith("incident-") and path.suffix == ".json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["fingerprint_material"]["error_class"] == "wrong_error_mapping"


class _IngestHandler(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 201

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        _IngestHandler.received.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(_IngestHandler.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args) -> None:  # silence
        pass


def test_post_report_success_and_failure() -> None:
    server = HTTPServer(("127.0.0.1", 0), _IngestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _IngestHandler.received.clear()
        assert post_report(f"http://127.0.0.1:{port}/ingest", make_report()) is True
        assert _IngestHandler.received[0]["component"] == "browser-agent-demo"
        _IngestHandler.status = 500
        assert post_report(f"http://127.0.0.1:{port}/ingest", make_report()) is False
    finally:
        _IngestHandler.status = 201
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    # API entirely down: never raises, returns False (outbox is source of truth).
    assert post_report(f"http://127.0.0.1:{port}/ingest", make_report(), timeout=0.5) is False
