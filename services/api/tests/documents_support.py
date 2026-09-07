"""Shared fixtures for the M20 document-intelligence test suites.

Loads the oracle (``tests/fixtures/documents/truth.json`` + ``expected/*.extract.json``,
ADR-0083 decision 6) and turns it into what a REAL device would answer for
``document.extract`` / ``file.search`` / ``file.inspect`` / ``file.compare`` — never the
real companion, never a real model: fakes only (task brief). ``FakeDocumentDeviceAction``
is a ``DeviceActionPort`` (``tests.alarms_support.FakeDeviceAction`` with capability
handlers wired to this oracle) so every document/voice test drives the exact same fixture
data the Cloud Core answer tests check against truth.json directly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.documents.answers import DocRef
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import FakeDeviceAction

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "documents"
EXPECTED_DIR = FIXTURES_DIR / "expected"

DEVICE_ID = "device:default"


def _expected_filename(path: str) -> str:
    return path.replace("/", "__") + ".extract.json"


def load_truth() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "truth.json").read_text(encoding="utf-8"))


def load_expected(path: str) -> dict[str, Any]:
    return json.loads((EXPECTED_DIR / _expected_filename(path)).read_text(encoding="utf-8"))


def truth_entry(path: str) -> dict[str, Any]:
    for entry in load_truth()["files"]:
        if entry["path"] == path:
            return entry
    raise KeyError(path)


def file_id_for(path: str) -> str:
    """A stable, fake location identity (never the real spec algorithm — that is the
    device's job, out of scope for a Cloud Core test): deterministic per path, so the
    same fixture always resolves to the same id across calls."""
    return "file:" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


def doc_id_for(path: str) -> str:
    """A stable, fake content-version identity, derived from the oracle's own recorded
    sha256 of the real fixture bytes (truth.json) — a genuine content hash, just not
    recomputed from bytes this test suite does not read directly."""
    return "doc:" + truth_entry(path)["sha256"]


def file_record(path: str) -> dict[str, Any]:
    entry = truth_entry(path)
    name = path.rsplit("/", 1)[-1]
    extension = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return {
        "file_id": file_id_for(path),
        "path": path,
        "name": name,
        "extension": extension,
        "size": entry["size"],
        "mtime": "2026-09-08T00:00:00Z",
        "sha256": entry["sha256"],
        "kind": entry["kind"],
    }


def extract_result(path: str) -> dict[str, Any]:
    """Exactly what ``document.extract`` returns for ``path`` (spec §2): the fixture's
    ``expected/*.extract.json`` verbatim, minus the oracle-only bookkeeping keys
    (``path``, per-block ``match``/``contains``) the real device result never carries."""
    expected = load_expected(path)
    blocks = []
    for block in expected["blocks"]:
        clean = {k: v for k, v in block.items() if k not in ("match", "contains")}
        blocks.append(clean)
    return {
        "file": file_record(path),
        "doc_id": doc_id_for(path),
        "kind": expected["kind"],
        "title": expected.get("title"),
        "blocks": blocks,
        "structure": expected.get("structure") or {},
        "truncated": False,
    }


def doc_ref(path: str, *, ambiguous: bool = False) -> DocRef:
    """A :class:`DocRef` built straight from the oracle — what ``app.documents.answers``
    is tested against directly, no database, no device."""
    extract = extract_result(path)
    return DocRef(
        file_id=extract["file"]["file_id"],
        doc_id=extract["doc_id"],
        path=path,
        name=extract["file"]["name"],
        kind=extract["kind"],
        title=extract.get("title"),
        blocks=list(extract["blocks"]),
        structure=dict(extract["structure"]),
        ambiguous=ambiguous,
    )


def _matches_pattern(name: str, path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    from app.documents.retrieval import normalize_word, shares_prefix, tokenize

    needle = normalize_word(pattern)
    haystack = [normalize_word(w) for w in (*tokenize(name), *tokenize(path))]
    return any(shares_prefix(needle, w) for w in haystack)


def _matches_extensions(path: str, extensions: list[str] | None) -> bool:
    if not extensions:
        return True
    return any(path.lower().endswith(ext.lower()) for ext in extensions)


def _matches_roots(path: str, roots: list[str] | None) -> bool:
    """Every fixture lives under one flat root that plays every well-known folder at
    once — a real device resolves a named root ("Masaüstü") to a real path and narrows
    the walk to it; this fake only needs to prove the ``roots`` ARGUMENT reached the
    capability at all (``FakeDeviceAction.payload_for`` is what a test asserts on)."""
    del path, roots
    return True


def _file_search(payload: dict[str, Any]) -> DeviceRunResult:
    pattern = payload.get("pattern")
    extensions = payload.get("extensions")
    roots = payload.get("roots")
    files = [
        file_record(entry["path"])
        for entry in load_truth()["files"]
        if _matches_pattern(entry["path"].rsplit("/", 1)[-1], entry["path"], pattern)
        and _matches_extensions(entry["path"], extensions)
        and _matches_roots(entry["path"], roots)
    ]
    return DeviceRunResult(
        True, result={"files": files, "truncated": False, "searched_roots": roots or []}
    )


def _find_path(payload: dict[str, Any]) -> str | None:
    file_id = payload.get("file_id")
    path = payload.get("path")
    if path:
        return str(path)
    if file_id:
        for entry in load_truth()["files"]:
            if file_id_for(entry["path"]) == file_id:
                return entry["path"]
    return None


#: A secret-bearing path the fake device refuses outright (spec §2's confinement rule:
#: ``.env*``/``*.pem``/... -> ``permission_denied``) — never a real secret, never a real
#: device; the corpus's "Şifre dosyamı oku." negative points a FILE focus straight at this
#: sentinel id so the tool genuinely reaches the device and is genuinely refused.
SECRET_FILE_ID = "file:secret-sentinel"


def _document_extract(payload: dict[str, Any]) -> DeviceRunResult:
    if payload.get("file_id") == SECRET_FILE_ID:
        return DeviceRunResult(False, "permission_denied", "secret_bearing_name")
    path = _find_path(payload)
    if path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    return DeviceRunResult(True, result=extract_result(path))


def _file_inspect(payload: dict[str, Any]) -> DeviceRunResult:
    path = _find_path(payload)
    if path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    expected = load_expected(path)
    result: dict[str, Any] = {"file": file_record(path), "kind": expected["kind"]}
    structure = expected.get("structure") or {}
    if expected["kind"] == "pptx":
        result["slides"] = structure.get("slide_count")
    if expected["kind"] == "pdf":
        result["pages"] = structure.get("pages")
    if expected["kind"] == "xlsx":
        result["sheets"] = [s.get("name") for s in structure.get("sheets") or []]
    if expected["kind"] in ("docx",):
        result["title"] = expected.get("title")
    result["is_text"] = expected["kind"] in ("md", "csv", "json", "source", "txt")
    return DeviceRunResult(True, result=result)


def _file_compare(payload: dict[str, Any]) -> DeviceRunResult:
    a_path = _find_path(payload.get("a") or {})
    b_path = _find_path(payload.get("b") or {})
    if a_path is None or b_path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    from app.documents.answers import compare_blocks

    comparison = compare_blocks(doc_ref(a_path), doc_ref(b_path))
    return DeviceRunResult(
        True,
        result={
            "a": file_record(a_path),
            "b": file_record(b_path),
            "same_content": not comparison["changed_refs"] and not comparison["added_refs"],
            "size_delta": 0,
            "mtime_delta_s": 0,
            "kind": load_expected(a_path)["kind"],
            "changed_refs": comparison["changed_refs"],
            "unchanged_refs": comparison["unchanged_refs"],
            "added_refs": comparison["added_refs"],
            "removed_refs": comparison["removed_refs"],
            "summary": comparison["speech"],
        },
    )


def document_capability_results() -> dict[str, Any]:
    """The capability -> handler map a :class:`tests.alarms_support.FakeDeviceAction`
    needs to serve the whole ``documents`` family from the oracle."""
    return {
        "document.extract": _document_extract,
        "file.search": _file_search,
        "file.inspect": _file_inspect,
        "file.compare": _file_compare,
    }


def build_fake_device_action(extra: dict[str, Any] | None = None) -> FakeDeviceAction:
    results = {**document_capability_results(), **(extra or {})}
    return FakeDeviceAction(results=results)


__all__ = [
    "DEVICE_ID",
    "build_fake_device_action",
    "doc_id_for",
    "doc_ref",
    "document_capability_results",
    "extract_result",
    "file_id_for",
    "file_record",
    "load_expected",
    "load_truth",
    "truth_entry",
]
