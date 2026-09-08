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
    RESERVED_LOOPBACK_PORTS,
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

    def test_read_back_sharing_no_field_with_a_mutation_is_refused(self):
        """The independent verification pass (2026-09-08) drove a description whose
        read-back shared no output field with its mutation: the service's field-by-field
        comparison then had nothing to compare and the run still reported ``verified``.
        Such a description is refused here, before anything is designed."""
        raw = {
            **VALID,
            "operations": [
                _op(
                    1,
                    id="read",
                    path="/z",
                    output_schema={"fields": {"z": "integer"}, "required": ["z"]},
                ),
                _op(
                    2,
                    id="bump",
                    method="POST",
                    path="/w",
                    output_schema={"fields": {"w": "integer"}, "required": ["w"]},
                    side_effect="mutate",
                    idempotent=False,
                ),
            ],
            "evidence": {"read_back": "read"},
        }
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse(raw)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR
        assert "could never witness" in str(excinfo.value)

    def test_read_back_sharing_one_field_with_every_mutation_is_accepted(self):
        """The bound is 'at least one shared field per mutating operation', not
        'identical schemas': a read-back may report more than the mutation does."""
        raw = {
            **VALID,
            "operations": [
                _op(
                    1,
                    id="read",
                    path="/z",
                    output_schema={
                        "fields": {"value": "integer", "extra": "string"},
                        "required": ["value"],
                    },
                ),
                _op(
                    2,
                    id="bump",
                    method="POST",
                    path="/w",
                    output_schema={"fields": {"value": "integer"}, "required": ["value"]},
                    side_effect="mutate",
                    idempotent=False,
                ),
            ],
            "evidence": {"read_back": "read"},
        }
        assert InterfaceDescription.parse(raw).read_back_operation.id == "read"

    def test_localhost_base_url_also_valid(self):
        # 18080, not 8080: the security review's reserved-port rule refuses the ports
        # this system's own services use, and the broker's is one of them. The fact
        # under test is unchanged — "localhost" is a legal loopback host.
        raw = {**VALID, "base_url": "http://localhost:18080"}
        desc = InterfaceDescription.parse(raw)
        assert desc.base_url == "http://localhost:18080"

    @pytest.mark.parametrize("port", sorted(RESERVED_LOOPBACK_PORTS))
    def test_a_port_this_system_serves_is_refused(self, port):
        """A description may not point the generated adapter at the Cloud Core, the
        broker, the object store or a database on this machine — the single-hardcoded-URL
        property of a generated module says an adapter reaches ONE origin, and this is
        what decides which (M24 security review, 2026-09-08)."""
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse({**VALID, "base_url": f"http://127.0.0.1:{port}"})
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR
        assert "own services use it" in str(excinfo.value)

    @pytest.mark.parametrize("port", [1, 22, 80, 443, 1023])
    def test_a_privileged_port_is_refused(self, port):
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse({**VALID, "base_url": f"http://127.0.0.1:{port}"})
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR

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

    def test_id_template_segment_is_refused(self):
        """It was accepted here and never substituted by the generator, so a description
        using it produced an adapter that asked for a literal "{id}" segment and failed
        at run time. Refused at parse now, where the reason can be said (M24 security
        review, 2026-09-08, Low)."""
        raw = {
            **VALID,
            "operations": [_op(0, path="/counter/{id}")],
            "evidence": {"read_back": "op0"},
        }
        with pytest.raises(EvolutionError) as excinfo:
            InterfaceDescription.parse(raw)
        assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR
        assert "never substituted" in str(excinfo.value)


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
    # A high, unused port rather than port 1: the reserved/privileged-port rule would
    # now refuse the latter at parse, and this case is about the FETCH finding nothing
    # listening — it has to reach the socket to prove that.
    with pytest.raises(EvolutionError) as excinfo:
        fetch_interface("http://127.0.0.1:64999/spec")
    assert excinfo.value.error_class == EvolutionErrorClass.DEPENDENCY_UNAVAILABLE
