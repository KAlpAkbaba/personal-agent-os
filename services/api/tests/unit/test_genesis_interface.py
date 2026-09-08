"""``app.genesis.interface``: ``InterfaceDescription.parse`` (the choke point)
and ``fetch_interface`` (the bounded loopback GET) — M24_CAPABILITY_GENESIS_SPEC.md
§2.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.genesis.interface import (
    FETCH_MAX_BYTES,
    MAX_FIELDS,
    MAX_OPERATIONS,
    InterfaceDescription,
    fetch_interface,
)

VALID = {
    "name": "counterbox",
    "base_url": "http://127.0.0.1:54321",
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
    ],
    "evidence": {"read_back": "read"},
}


def _op(idx: int, **overrides) -> dict:
    base = {
        "id": f"op{idx}",
        "method": "GET",
        "path": f"/op{idx}",
        "input_schema": None,
        "output_schema": {"fields": {"value": "integer"}, "required": ["value"]},
        "side_effect": "read",
        "idempotent": True,
    }
    base.update(overrides)
    return base


class TestParse:
    def test_valid_description_parses(self):
        desc = InterfaceDescription.parse(VALID)
        assert desc.name == "counterbox"
        assert desc.base_url == "http://127.0.0.1:54321"
        assert [op.id for op in desc.operations] == ["read", "increment"]
        assert desc.evidence == {"read_back": "read"}
        assert desc.read_back_operation.id == "read"

    def test_localhost_base_url_also_valid(self):
        raw = {**VALID, "base_url": "http://localhost:8080"}
        desc = InterfaceDescription.parse(raw)
        assert desc.base_url == "http://localhost:8080"

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://example.com:80",  # not loopback
            "https://127.0.0.1:80",  # not http
            "http://127.0.0.1",  # no port
            "http://127.0.0.1:80/path",  # path
            "http://127.0.0.1:80?x=1",  # query
            "http://user:pass@127.0.0.1:80",  # userinfo
            "not a url",
        ],
    )
    def test_hostile_base_url_refused(self, base_url):
        raw = {**VALID, "base_url": base_url}
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse(raw)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR

    def test_path_traversal_in_operation_path_refused(self):
        raw = {**VALID, "operations": [_op(0, path="/counter/../../etc/passwd")]}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_query_string_in_path_refused(self):
        raw = {**VALID, "operations": [_op(0, path="/counter?x=1")]}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_non_token_operation_id_refused(self):
        for bad_id in ("Read", "read-op", "read op", "read!", "_read", "1read", ""):
            raw = {**VALID, "operations": [_op(0, id=bad_id)]}
            with pytest.raises(EvolutionError):
                InterfaceDescription.parse(raw)

    def test_non_token_name_refused(self):
        for bad_name in ("Counterbox", "counter box", "counter/box", ""):
            raw = {**VALID, "name": bad_name}
            with pytest.raises(EvolutionError):
                InterfaceDescription.parse(raw)

    def test_unsupported_method_refused(self):
        raw = {**VALID, "operations": [_op(0, method="PUT")]}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_unsupported_field_type_refused(self):
        raw = {
            **VALID,
            "operations": [
                _op(0, output_schema={"fields": {"value": "object"}, "required": ["value"]})
            ],
        }
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_more_than_max_operations_refused(self):
        raw = {**VALID, "operations": [_op(i) for i in range(MAX_OPERATIONS + 1)]}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_more_than_max_fields_refused(self):
        fields = {f"f{i}": "string" for i in range(MAX_FIELDS + 1)}
        raw = {
            **VALID,
            "operations": [_op(0, output_schema={"fields": fields, "required": []})],
        }
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_oversize_description_refused(self):
        raw = {**VALID, "name": "counterbox", "operations": list(VALID["operations"])}
        # Pad well past the 8 KiB bound with a legal-shaped but huge field set
        # spread across many operations (still under MAX_OPERATIONS but each
        # with enough distinct field names to blow the byte budget).
        big_ops = []
        for i in range(MAX_OPERATIONS):
            fields = {f"field_{i}_{j}": "string" for j in range(MAX_FIELDS)}
            big_ops.append(_op(i, output_schema={"fields": fields, "required": []}))
        raw = {**VALID, "operations": big_ops}
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse(raw)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR

    def test_control_characters_refused(self):
        raw = {**VALID, "name": "counter\x07box"}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_duplicate_operation_ids_refused(self):
        raw = {**VALID, "operations": [_op(0, id="dup"), _op(1, id="dup")]}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_evidence_must_name_a_read_operation(self):
        raw = {
            **VALID,
            "operations": [_op(0, id="mutate_only", side_effect="mutate")],
            "evidence": {"read_back": "mutate_only"},
        }
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_evidence_must_name_a_declared_operation(self):
        raw = {**VALID, "evidence": {"read_back": "nonexistent"}}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_unknown_top_level_key_refused(self):
        raw = {**VALID, "unexpected": "field"}
        with pytest.raises(EvolutionError):
            InterfaceDescription.parse(raw)

    def test_id_template_segment_supported(self):
        raw = {
            **VALID,
            "operations": [_op(0, path="/counter/{id}")],
            "evidence": {"read_back": "op0"},
        }
        desc = InterfaceDescription.parse(raw)
        assert desc.operations[0].path == "/counter/{id}"


# ------------------------------------------------------------------- fetch


class _EchoHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        body = json.dumps(self.server.payload).encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/elsewhere")
        self.end_headers()


class _OversizeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        body = b"x" * (FETCH_MAX_BYTES + 100)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _serve(handler_cls, payload=None):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    if payload is not None:
        server.payload = payload  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_fetch_interface_parses_a_live_response():
    with _serve(_EchoHandler, payload=VALID) as server:
        url = f"http://127.0.0.1:{server.server_port}/spec"
        desc = fetch_interface(url)
        assert desc.name == "counterbox"
        assert desc.source == {"kind": "http_spec", "url": url}


def test_fetch_interface_refuses_a_redirect():
    with _serve(_RedirectHandler) as server:
        url = f"http://127.0.0.1:{server.server_port}/spec"
        with pytest.raises(EvolutionError):
            fetch_interface(url)


def test_fetch_interface_refuses_oversize_response():
    with _serve(_OversizeHandler) as server:
        url = f"http://127.0.0.1:{server.server_port}/spec"
        with pytest.raises(EvolutionError) as excinfo:
            fetch_interface(url)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_fetch_interface_refuses_non_loopback_host():
    with pytest.raises(EvolutionError) as excinfo:
        fetch_interface("http://example.com:80/spec")
    assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_fetch_interface_no_listener_is_dependency_unavailable():
    with pytest.raises(EvolutionError) as excinfo:
        fetch_interface("http://127.0.0.1:1/spec")
    assert excinfo.value.error_class == EvolutionErrorClass.DEPENDENCY_UNAVAILABLE
