"""The interface description + its bounded fetcher (M24_CAPABILITY_GENESIS_SPEC.md §2).

``InterfaceDescription`` is what the assistant learns about a controllable local
application before it designs anything. Research is a single ``GET
<base_url>/spec`` through :func:`fetch_interface`: 5 s timeout, 64 KiB response
cap, no redirects, ``127.0.0.1``/``localhost`` only. The JSON body then goes
through :meth:`InterfaceDescription.parse`, the CHOKE POINT (ADR-0024/ADR-0025
§3 discipline, same as ``app.evolution.tokens``): every token is re-validated
here, schemas are re-rendered from the parsed model, and nothing of the
description reaches generated source unvalidated. Anything outside the closed
subset is refused as ``validation_error`` naming the offending path — never a
500, never a best-effort guess.

M24 scope is LOCAL applications only: ``base_url`` must be exactly
``http://127.0.0.1:<port>`` or ``http://localhost:<port>``, no path, no query,
no fragment, no userinfo. A description naming any other host is refused at
parse, before any code is designed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.evolution.errors import EvolutionError, EvolutionErrorClass

# A single token: starts alnum, then [a-z0-9_.-], <=64 chars (spec §2 `name`).
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# An operation id: a python-identifier-shaped token (spec §2 `operations[].id`).
OPERATION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# A path segment: a lowercase token. (`{id}` was once accepted here and never
# substituted by the generator; it is refused at parse now.)
PATH_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# A schema field name (spec §2: object of string|integer|boolean|number fields).
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

ALLOWED_METHODS: tuple[str, ...] = ("GET", "POST")
ALLOWED_FIELD_TYPES: tuple[str, ...] = ("string", "integer", "boolean", "number")
ALLOWED_SIDE_EFFECTS: tuple[str, ...] = ("read", "mutate")
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost")

MAX_OPERATIONS = 16
MAX_FIELDS = 16
MAX_TOTAL_BYTES = 8 * 1024
#: The lowest port a described application may live on. Privileged ports below 1024
#: belong to system services, and an owner's small test application never needs one:
#: refusing them keeps a description from pointing the generated adapter at something
#: the owner did not write (M24 security review, 2026-09-08).
MIN_PORT, MAX_PORT = 1024, 65535

#: Loopback ports this system's OWN services listen on. A description naming one of them
#: would turn a generated adapter into a client of the Cloud Core (or the broker, or the
#: object store) speaking from inside the machine — the one loopback origin an adapter
#: must never be pointed at, however honest the rest of its description looks. The
#: single-hardcoded-URL property of the generated module confines an adapter to ONE
#: origin; this is what decides WHICH.
RESERVED_LOOPBACK_PORTS: frozenset[int] = frozenset(
    {
        8000,  # the API in development
        8001,  # the Cloud Core through the tailnet address
        8080,  # the device broker's local surface
        9000,  # MinIO / the object store
        15432,  # PostgreSQL (compose)
        16379,  # Redis (compose)
        17233,  # Temporal (compose)
        19000,  # MinIO (compose)
    }
)

FETCH_TIMEOUT_S = 5.0
FETCH_MAX_BYTES = 64 * 1024
SPEC_PATH = "/spec"


def _fail(path: str, message: str, **details: Any) -> EvolutionError:
    return EvolutionError(
        EvolutionErrorClass.VALIDATION_ERROR,
        f"interface description invalid at {path}: {message}",
        details={"path": path, **details},
    )


def _require_str_token(value: Any, pattern: re.Pattern[str], path: str, label: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise _fail(path, f"is not a valid {label}")
    return value


# ------------------------------------------------------------------- schema


@dataclass(frozen=True, slots=True)
class SchemaField:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class ObjectSchema:
    """The bounded JSON-schema subset (spec §2): an object of scalar fields."""

    fields: tuple[SchemaField, ...] = ()
    required: tuple[str, ...] = ()

    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None

    def to_manifest_schema(self) -> list[dict[str, Any]]:
        required = set(self.required)
        return [
            {"name": f.name, "type": f.type, "required": f.name in required} for f in self.fields
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {f.name: f.type for f in self.fields},
            "required": list(self.required),
        }

    @classmethod
    def parse(cls, raw: Any, *, path: str) -> ObjectSchema:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise _fail(path, "must be an object")
        unknown = set(raw) - {"fields", "required"}
        if unknown:
            raise _fail(path, f"has unknown keys: {sorted(unknown)}")
        raw_fields = raw.get("fields") or {}
        if not isinstance(raw_fields, dict):
            raise _fail(f"{path}.fields", "must be an object of name -> type")
        if len(raw_fields) > MAX_FIELDS:
            raise _fail(f"{path}.fields", f"has more than {MAX_FIELDS} fields")
        fields: list[SchemaField] = []
        for name, kind in raw_fields.items():
            _require_str_token(name, FIELD_NAME_RE, f"{path}.fields.{name}", "field name")
            if kind not in ALLOWED_FIELD_TYPES:
                raise _fail(
                    f"{path}.fields.{name}",
                    f"type must be one of {ALLOWED_FIELD_TYPES}",
                )
            fields.append(SchemaField(name=name, type=str(kind)))
        raw_required = raw.get("required") or []
        if not isinstance(raw_required, list) or len(raw_required) > MAX_FIELDS:
            raise _fail(f"{path}.required", f"must be a list of at most {MAX_FIELDS} names")
        names = {f.name for f in fields}
        required: list[str] = []
        for entry in raw_required:
            if not isinstance(entry, str) or entry not in names:
                raise _fail(f"{path}.required", "names a field the schema does not declare")
            required.append(entry)
        return cls(fields=tuple(fields), required=tuple(required))


# ---------------------------------------------------------------- operation


@dataclass(frozen=True, slots=True)
class Operation:
    id: str
    method: str
    path: str
    input_schema: ObjectSchema
    output_schema: ObjectSchema
    side_effect: str
    idempotent: bool

    @property
    def capability_suffix(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "path": self.path,
            "input_schema": self.input_schema.to_dict(),
            "output_schema": self.output_schema.to_dict(),
            "side_effect": self.side_effect,
            "idempotent": self.idempotent,
        }

    @classmethod
    def parse(cls, raw: Any, *, index: int) -> Operation:
        path = f"operations[{index}]"
        if not isinstance(raw, dict):
            raise _fail(path, "must be an object")
        unknown = set(raw) - {
            "id",
            "method",
            "path",
            "input_schema",
            "output_schema",
            "side_effect",
            "idempotent",
        }
        if unknown:
            raise _fail(path, f"has unknown keys: {sorted(unknown)}")
        op_id = _require_str_token(raw.get("id"), OPERATION_ID_RE, f"{path}.id", "operation id")
        method = raw.get("method")
        if method not in ALLOWED_METHODS:
            raise _fail(f"{path}.method", f"must be one of {ALLOWED_METHODS}")
        op_path = _parse_operation_path(raw.get("path"), field=f"{path}.path")
        input_schema = ObjectSchema.parse(raw.get("input_schema"), path=f"{path}.input_schema")
        output_schema = ObjectSchema.parse(raw.get("output_schema"), path=f"{path}.output_schema")
        side_effect = raw.get("side_effect")
        if side_effect not in ALLOWED_SIDE_EFFECTS:
            raise _fail(f"{path}.side_effect", f"must be one of {ALLOWED_SIDE_EFFECTS}")
        idempotent = raw.get("idempotent")
        if not isinstance(idempotent, bool):
            raise _fail(f"{path}.idempotent", "must be a boolean")
        return cls(
            id=op_id,
            method=str(method),
            path=op_path,
            input_schema=input_schema,
            output_schema=output_schema,
            side_effect=str(side_effect),
            idempotent=idempotent,
        )


def _parse_operation_path(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw.startswith("/") or len(raw) > 200:
        raise _fail(field, "must be a '/'-rooted path of bounded length")
    if "?" in raw or "#" in raw or ".." in raw or "//" in raw:
        raise _fail(field, "must carry no query, fragment or traversal segment")
    segments = raw.split("/")[1:]
    if not segments or not all(segments):
        raise _fail(field, "must be composed of non-empty '/'-separated segments")
    for segment in segments:
        if segment == "{id}":
            # An earlier draft accepted this template and the generator never substituted
            # it, so a description using it produced an adapter that asked the
            # application for a literal "{id}" segment and failed at run time. Refused
            # here, where the reason can be said (M24 security review, 2026-09-08, Low).
            raise _fail(field, "must not use the {id} template: it is never substituted")
        if not PATH_SEGMENT_RE.match(segment):
            raise _fail(field, f"segment {segment!r} is not a valid path token")
    return raw


# ------------------------------------------------------------ base_url + full description


def _parse_base_url(raw: Any, *, field: str = "base_url") -> str:
    if not isinstance(raw, str) or len(raw) > 128:
        raise _fail(field, "must be a bounded string")
    parts = urlsplit(raw)
    if parts.scheme != "http":
        raise _fail(field, "must use the http scheme")
    if parts.username or parts.password:
        raise _fail(field, "must carry no userinfo")
    if parts.path not in ("", "/"):
        raise _fail(field, "must carry no path")
    if parts.query or parts.fragment:
        raise _fail(field, "must carry no query or fragment")
    hostname = parts.hostname
    if hostname not in LOOPBACK_HOSTS:
        raise _fail(field, "must name 127.0.0.1 or localhost only (M24 scope)")
    port = parts.port
    if port is None or not (MIN_PORT <= port <= MAX_PORT):
        raise _fail(field, f"must carry an explicit port between {MIN_PORT} and {MAX_PORT}")
    if port in RESERVED_LOOPBACK_PORTS:
        raise _fail(field, f"must not name port {port}: this system's own services use it")
    return f"http://{hostname}:{port}"


@dataclass(frozen=True, slots=True)
class InterfaceDescription:
    source: dict[str, str]
    name: str
    base_url: str
    operations: tuple[Operation, ...]
    evidence: dict[str, str]

    def operation(self, operation_id: str) -> Operation | None:
        for op in self.operations:
            if op.id == operation_id:
                return op
        return None

    @property
    def read_back_operation(self) -> Operation:
        op = self.operation(self.evidence["read_back"])
        assert op is not None  # guaranteed by parse()
        return op

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": dict(self.source),
            "name": self.name,
            "base_url": self.base_url,
            "operations": [op.to_dict() for op in self.operations],
            "evidence": dict(self.evidence),
        }

    @classmethod
    def parse(cls, raw: Any, *, source: dict[str, str] | None = None) -> InterfaceDescription:
        """The CHOKE POINT. ``source`` records where the description came from
        (an HTTP fetch's url, or ``{"kind": "inline"}`` for tests)."""
        if not isinstance(raw, dict):
            raise _fail("<root>", "must be an object")
        # Bound the total size BEFORE walking it (spec §2 bounds).
        try:
            encoded = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _fail("<root>", "is not JSON-serializable") from exc
        if len(encoded) > MAX_TOTAL_BYTES:
            raise _fail("<root>", f"exceeds {MAX_TOTAL_BYTES} bytes")
        # Control characters anywhere in the raw text are refused outright (the
        # M23 review lesson: a stray control character must never survive into
        # a value that later reaches generated source).
        text = encoded.decode("utf-8")
        if any(ord(ch) < 0x20 and ch not in ("\t",) for ch in text if ch not in ("\n",)):
            raise _fail("<root>", "carries control characters")
        unknown = set(raw) - {"name", "base_url", "operations", "evidence"}
        if unknown:
            raise _fail("<root>", f"has unknown keys: {sorted(unknown)}")
        name = _require_str_token(raw.get("name"), NAME_RE, "name", "interface name")
        base_url = _parse_base_url(raw.get("base_url"))
        raw_ops = raw.get("operations")
        if not isinstance(raw_ops, list) or not raw_ops:
            raise _fail("operations", "must be a non-empty list")
        if len(raw_ops) > MAX_OPERATIONS:
            raise _fail("operations", f"must hold at most {MAX_OPERATIONS} entries")
        operations = tuple(Operation.parse(entry, index=i) for i, entry in enumerate(raw_ops))
        seen_ids = [op.id for op in operations]
        if len(set(seen_ids)) != len(seen_ids):
            raise _fail("operations", "operation ids must be unique")
        evidence_raw = raw.get("evidence")
        if not isinstance(evidence_raw, dict) or set(evidence_raw) != {"read_back"}:
            raise _fail("evidence", "must be an object with exactly one key: read_back")
        read_back_id = evidence_raw.get("read_back")
        if not isinstance(read_back_id, str) or read_back_id not in seen_ids:
            raise _fail("evidence.read_back", "must name a declared operation")
        read_back_op = operations[seen_ids.index(read_back_id)]
        if read_back_op.side_effect != "read":
            raise _fail("evidence.read_back", "must name a read (non-mutating) operation")
        # The read-back has to be able to WITNESS the mutation it verifies: the service
        # compares the two outputs field by field, so an evidence operation sharing no
        # field name with a mutating operation's output would make that comparison
        # vacuous - "nothing disagreed", over nothing. Found by the independent
        # verification pass on 2026-09-08 with a description shaped exactly that way;
        # refused here, before anything is designed, and refused again at the check
        # itself (``app.genesis.service.GenesisService._verify``).
        read_back_fields = {f.name for f in read_back_op.output_schema.fields}
        for op in operations:
            if op.side_effect != "mutate":
                continue
            if not (read_back_fields & {f.name for f in op.output_schema.fields}):
                raise _fail(
                    "evidence.read_back",
                    f"shares no output field with the mutating operation {op.id!r}, so it "
                    "could never witness it",
                )
        return cls(
            source=dict(source or {"kind": "inline"}),
            name=name,
            base_url=base_url,
            operations=operations,
            evidence={"read_back": read_back_id},
        )


# ------------------------------------------------------------------- fetcher


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect: loopback fetches never follow a Location header."""

    def redirect_request(  # type: ignore[override]
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise urllib.error.HTTPError(
            newurl, code, "redirect refused by the genesis loopback fetch policy", headers, fp
        )


def _require_loopback_url(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme != "http":
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "genesis fetch url must use the http scheme"
        )
    hostname = parts.hostname
    if hostname not in LOOPBACK_HOSTS:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            "genesis fetch url must name 127.0.0.1 or localhost only (M24 scope)",
        )
    port = parts.port
    if port is None or not (MIN_PORT <= port <= MAX_PORT):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "genesis fetch url must carry an explicit port"
        )
    if parts.path not in ("", SPEC_PATH):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "genesis fetch url must point at /spec"
        )
    return hostname, port


def fetch_interface(url: str) -> InterfaceDescription:
    """``GET <base_url>/spec`` (spec §2): 5 s timeout, 64 KiB cap, no redirects,
    loopback only. Raises ``dependency_unavailable`` for anything network-shaped
    (refused connection, timeout, non-2xx, a redirect) and ``validation_error``
    for a url outside the loopback subset or a body outside the closed schema.
    """
    hostname, port = _require_loopback_url(url)
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise EvolutionError(
                    EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
                    f"genesis interface fetch returned status {status}",
                )
            body = response.read(FETCH_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "genesis interface fetch refused a redirect",
            ) from exc
        raise EvolutionError(
            EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
            f"genesis interface fetch failed with status {exc.code}",
        ) from exc
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        raise EvolutionError(
            EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
            f"genesis interface fetch could not reach {hostname}:{port}",
        ) from exc
    if len(body) > FETCH_MAX_BYTES:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"genesis interface response exceeds {FETCH_MAX_BYTES} bytes",
        )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "genesis interface response is not utf-8"
        ) from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "genesis interface response is not valid JSON"
        ) from exc
    return InterfaceDescription.parse(raw, source={"kind": "http_spec", "url": url})


__all__ = [
    "ALLOWED_FIELD_TYPES",
    "ALLOWED_METHODS",
    "ALLOWED_SIDE_EFFECTS",
    "FETCH_MAX_BYTES",
    "FETCH_TIMEOUT_S",
    "LOOPBACK_HOSTS",
    "MAX_FIELDS",
    "MAX_OPERATIONS",
    "MAX_TOTAL_BYTES",
    "SPEC_PATH",
    "InterfaceDescription",
    "ObjectSchema",
    "Operation",
    "SchemaField",
    "fetch_interface",
]
