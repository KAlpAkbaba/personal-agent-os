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
from datetime import datetime
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
    if not pattern or pattern in ("*", "*.*"):
        return True
    if "*" in pattern or "?" in pattern:
        # DEVICE_PROTOCOL.md §6j: a glob on the NAME, casefold ("*.pdf").
        import fnmatch

        return fnmatch.fnmatch(name.lower(), pattern.lower())
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


def _search_record(path: str) -> dict[str, Any]:
    """A ``file.search`` hit: the same record :func:`file_record` builds, minus
    ``sha256`` - DEVICE_PROTOCOL.md §6j is explicit that a search never opens a file, so
    its records carry no content hash even when the file is small enough that a
    single-file capability (``file.locate``, this fake's ``_file_inspect``/
    ``_document_extract``) would have carried one."""
    record = dict(file_record(path))
    record.pop("sha256", None)
    return record


def _file_search(payload: dict[str, Any]) -> DeviceRunResult:
    pattern = payload.get("pattern")
    extensions = payload.get("extensions")
    roots = payload.get("roots")
    files = [
        _search_record(entry["path"])
        for entry in load_truth()["files"]
        if _matches_pattern(entry["path"].rsplit("/", 1)[-1], entry["path"], pattern)
        and _matches_extensions(entry["path"], extensions)
        and _matches_roots(entry["path"], roots)
    ]
    return DeviceRunResult(
        True,
        result={
            "files": files,
            "truncated": False,
            "searched_roots": roots or [],
            "entries_walked": len(load_truth()["files"]),
        },
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
    if truth_entry(path).get("kind") == "archive":
        # B32 req 142: the device lists an archive and never extracts it.
        return DeviceRunResult(False, "unsupported_format", "unknown_kind")
    return DeviceRunResult(True, result=extract_result(path))


def _file_locate(payload: dict[str, Any]) -> DeviceRunResult:
    """``file.locate``: the record WITH its sha256 (a single-file capability opens the file)."""
    path = _find_path(payload)
    if path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    return DeviceRunResult(True, result={"file": file_record(path)})


#: B32 req 150: what the fake desktop sent to the Recycle Bin (the test reads it).
TRASHED: list[str] = []


def _file_trash(payload: dict[str, Any]) -> DeviceRunResult:
    path = _find_path(payload)
    if path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    name = path.rsplit("/", 1)[-1]
    if name.startswith(".env") or name.endswith((".pem", ".key")):
        return DeviceRunResult(False, "permission_denied", "secret_bearing_name")
    TRASHED.append(path)
    return DeviceRunResult(
        True,
        result={
            "trashed": True,
            "method": "recycle_bin",
            "file": file_record(path),
            "observed": {"exists": False},
        },
    )


def _file_inspect(payload: dict[str, Any]) -> DeviceRunResult:
    path = _find_path(payload)
    if path is None:
        return DeviceRunResult(False, "not_found", "not_found")
    if path.lower().endswith(".exe"):
        # M28 row 26.16. The device's own reader (PeImageReader) adds a `pe` block when the
        # bytes really are a PE; Cloud Core builds ArtifactFacts out of it and runs
        # validate_against_spec. The fixture corpus is documents, so a native artefact is
        # answered here in the shape the DEVICE returns rather than being looked up as one.
        name = path.replace("/", chr(92)).rsplit(chr(92), 1)[-1]
        return DeviceRunResult(
            True,
            result={
                "file": {
                    "file_id": "file_native",
                    "path": path,
                    "name": name,
                    "extension": ".exe",
                    "size": 162304,
                    "mtime": "2026-09-09T19:07:00.0000000Z",
                    "sha256": "a" * 64,
                },
                "kind": "unknown",
                "is_text": False,
                "pe": {
                    "version": "0.1.0",
                    "architecture": "x64",
                    "subsystem": "windows_gui",
                },
            },
        )
    entry = truth_entry(path)
    if entry.get("kind") == "image":
        # B32 req 139: the picture's own headers, never the pixels.
        return DeviceRunResult(
            True,
            result={
                "file": file_record(path),
                "kind": "image",
                "is_text": False,
                "image": {
                    **dict(entry.get("image") or {}),
                    "format": "PNG",
                    "dpi_x": 96.0,
                    "dpi_y": 96.0,
                    "frames": 1,
                    "metadata": {"application": "System.Drawing"},
                },
            },
        )
    if entry.get("kind") == "archive":
        # B32 req 142: the central directory, never an entry inflated.
        archive = dict(entry.get("archive") or {})
        entries = list(archive.get("entries") or [])
        return DeviceRunResult(
            True,
            result={
                "file": file_record(path),
                "kind": "archive",
                "is_text": False,
                "archive": {
                    "entry_count": archive.get("entry_count", len(entries)),
                    "directory_count": 0,
                    "total_uncompressed": archive.get("total_uncompressed", 0),
                    "total_compressed": sum(int(e.get("compressed_size") or 0) for e in entries),
                    "entries": [{**e, "modified": "1980-01-01T00:00:00Z"} for e in entries],
                    "truncated": False,
                    "over_inflate_bound": False,
                },
            },
        )
    expected = load_expected(path)
    result: dict[str, Any] = {"file": file_record(path), "kind": expected["kind"]}
    structure = expected.get("structure") or {}
    if expected["kind"] in ("pptx", "ppt"):
        result["slides"] = structure.get("slide_count")
    if expected["kind"] == "pdf":
        result["pages"] = structure.get("pages")
    if expected["kind"] in ("xlsx", "xls"):
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

    a_record, b_record = file_record(a_path), file_record(b_path)
    a_mtime = datetime.fromisoformat(a_record["mtime"].replace("Z", "+00:00"))
    b_mtime = datetime.fromisoformat(b_record["mtime"].replace("Z", "+00:00"))
    a_kind = load_expected(a_path)["kind"]
    b_kind = load_expected(b_path)["kind"]
    common = {
        "a": a_record,
        "b": b_record,
        # B minus A (DEVICE_PROTOCOL.md §6j) - both sides, never a signless magnitude.
        "size_delta": b_record["size"] - a_record["size"],
        "mtime_delta_s": round((b_mtime - a_mtime).total_seconds(), 3),
        "truncated": False,
    }
    if a_kind != b_kind:
        # DEVICE_PROTOCOL.md §6j: "different kinds -> kind: '<a>/<b>', empty lists,
        # content identity only" - never a ref-by-ref block diff across two unrelated
        # formats (a PDF has no "s4" to compare against a PPTX's).
        return DeviceRunResult(
            True,
            result={
                **common,
                "same_content": False,
                "kind": f"{a_kind}/{b_kind}",
                "changed_refs": [],
                "unchanged_refs": [],
                "added_refs": [],
                "removed_refs": [],
                "summary": (
                    f"{a_record['name']} bir {a_kind}, {b_record['name']} bir {b_kind} - "
                    "karşılaştırılamaz."
                ),
            },
        )

    from app.documents.answers import compare_blocks

    comparison = compare_blocks(doc_ref(a_path), doc_ref(b_path))
    return DeviceRunResult(
        True,
        result={
            **common,
            "same_content": not comparison["changed_refs"] and not comparison["added_refs"],
            "kind": a_kind,
            "changed_refs": comparison["changed_refs"],
            "unchanged_refs": comparison["unchanged_refs"],
            "added_refs": comparison["added_refs"],
            "removed_refs": comparison["removed_refs"],
            "summary": comparison["speech"],
        },
    )


# --------------------------------------------------------------- B34: the mutable overlay

#: B34 req 153-165: what the fake desktop's files look like AFTER a test mutated them -
#: ``path -> bytes`` for a file written, appended to, renamed or copied; ``None`` for a
#: fixture file moved away or sent to the Recycle Bin. The fixtures on disk are never
#: touched. ``reset_mutations`` clears it between tests (the harness calls it on seed).
MUTATED: dict[str, bytes | None] = {}
#: ``backup_id -> {"path", "bytes", "sha256"}`` - the fake undo store.
BACKUPS: dict[str, dict[str, Any]] = {}
#: How the fake resolves a bucket the Cloud Core names (``documents``, ``desktop/x``).
FOLDER_PREFIXES: dict[str, str] = {"documents": "", "desktop": "Desktop", "downloads": "Downloads"}


def reset_mutations() -> None:
    MUTATED.clear()
    BACKUPS.clear()
    TRASHED.clear()


def _fixture_paths() -> list[str]:
    return [entry["path"] for entry in load_truth()["files"]]


def _exists(path: str) -> bool:
    if path in MUTATED:
        return MUTATED[path] is not None
    return path in _fixture_paths()


def _bytes(path: str) -> bytes:
    if path in MUTATED and MUTATED[path] is not None:
        return MUTATED[path]  # type: ignore[return-value]
    return (FIXTURES_DIR / path).read_bytes()


def _live_record(path: str) -> dict[str, Any]:
    """The record the device would answer for ``path`` NOW (hash of the live bytes)."""
    data = _bytes(path)
    name = path.rsplit("/", 1)[-1]
    extension = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return {
        "file_id": file_id_for(path),
        "path": path,
        "name": name,
        "extension": extension,
        "size": len(data),
        "mtime": "2026-09-15T00:00:00Z",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _mutation_path(payload: dict[str, Any]) -> str | None:
    """``path`` / ``file_id`` (fixture or overlay) / ``folder`` + ``name``."""
    if payload.get("path"):
        return str(payload["path"])
    if payload.get("file_id"):
        for candidate in [*_fixture_paths(), *MUTATED]:
            if file_id_for(candidate) == payload["file_id"]:
                return candidate
        return None
    folder = payload.get("folder")
    name = payload.get("name")
    if folder is not None and name:
        prefix = _folder_prefix(str(folder))
        if prefix is None:
            return None
        return f"{prefix}/{name}" if prefix else str(name)
    return None


def _folder_prefix(bucket: str) -> str | None:
    head, _, rest = bucket.replace("\\", "/").partition("/")
    if head.lower() not in FOLDER_PREFIXES:
        return None
    base = FOLDER_PREFIXES[head.lower()]
    return "/".join(p for p in (base, rest) if p)


def _is_text_like(path: str) -> bool:
    return path.lower().rsplit(".", 1)[-1] in (
        "txt",
        "md",
        "csv",
        "json",
        "py",
        "log",
        "yaml",
        "yml",
    )


def _secret(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(".env") or name.endswith((".pem", ".key"))


def _backup(path: str, reason: str) -> dict[str, Any]:
    data = _bytes(path)
    backup_id = "bak:" + hashlib.sha256(f"{path}|{len(BACKUPS)}".encode()).hexdigest()[:16]
    BACKUPS[backup_id] = {"path": path, "bytes": data, "sha256": hashlib.sha256(data).hexdigest()}
    return {
        "backup_id": backup_id,
        "backup_path": f".pagentos-undo/{backup_id[4:]}.bak",
        "source_path": path,
        "source_name": path.rsplit("/", 1)[-1],
        "sha256": BACKUPS[backup_id]["sha256"],
        "size": len(data),
        "taken_at": "2026-09-15T00:00:00Z",
        "reason": reason,
    }


def _mutation_result(
    verb: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    backup: dict[str, Any] | None,
    **extra: Any,
) -> DeviceRunResult:
    body = {
        verb: True,
        "method": "atomic_replace",
        "before": before,
        "after": after,
        "backup": backup,
        "observed": {"exists": True, "sha256": after.get("sha256")},
        **extra,
    }
    return DeviceRunResult(True, result=body)


def _file_read(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        raw = str(payload.get("path") or "")
        if "\\" in raw or raw.startswith("/"):
            # An absolute path outside the fixture corpus (the native factory's own
            # app log under the executable's folder, B33): the desktop this fake stands in
            # for answers an empty text there, as the unscripted default did before B34.
            name = raw.replace("/", "\\").rsplit("\\", 1)[-1]
            return DeviceRunResult(
                True,
                result={
                    "file": {"file_id": "file:" + name, "path": raw, "name": name},
                    "text": "",
                    "encoding": "utf-8",
                    "truncated": False,
                    "total_chars": 0,
                    "offset": 0,
                },
            )
        return DeviceRunResult(False, "not_found", "not_found")
    if not _is_text_like(path):
        return DeviceRunResult(False, "unsupported_format", "not_text")
    text = _bytes(path).decode("utf-8", errors="replace")
    offset = int(payload.get("offset") or 0)
    length = int(payload.get("length") or 65536)
    chunk = text[offset : offset + length]
    return DeviceRunResult(
        True,
        result={
            "file": _live_record(path),
            "text": chunk,
            "encoding": "utf-8",
            "truncated": offset + len(chunk) < len(text),
            "total_chars": len(text),
            "offset": offset,
        },
    )


def _file_write(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None:
        return DeviceRunResult(
            False, "validation_error", "payload.path or payload.folder+name is required"
        )
    if _secret(path):
        return DeviceRunResult(False, "permission_denied", "secret_bearing_name")
    if not _is_text_like(path):
        return DeviceRunResult(False, "unsupported_format", "not_text")
    existed = _exists(path)
    before = _live_record(path) if existed else None
    expected = payload.get("expected_sha256")
    if expected and (before is None or before["sha256"] != expected):
        return DeviceRunResult(
            False,
            "validation_error",
            "'x' changed since it was read (its hash is no longer the expected one); "
            "nothing was written",
        )
    backup = _backup(path, "write") if existed else None
    MUTATED[path] = str(payload.get("text") or "").encode("utf-8")
    return _mutation_result(
        "written",
        before,
        _live_record(path),
        backup,
        created=not existed,
        chars=len(str(payload.get("text") or "")),
    )


def _file_append(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    if not _is_text_like(path):
        return DeviceRunResult(False, "unsupported_format", "not_text")
    before = _live_record(path)
    expected = payload.get("expected_sha256")
    if expected and before["sha256"] != expected:
        return DeviceRunResult(False, "validation_error", "changed since it was read")
    backup = _backup(path, "append")
    MUTATED[path] = _bytes(path) + str(payload.get("text") or "").encode("utf-8")
    return _mutation_result(
        "appended", before, _live_record(path), backup, chars=len(str(payload.get("text") or ""))
    )


def _file_rename(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    new_name = str(payload.get("new_name") or "")
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return DeviceRunResult(
            False, "validation_error", "payload.new_name must be a plain file name"
        )
    if _secret(new_name):
        return DeviceRunResult(False, "permission_denied", "secret_bearing_name")
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    target = f"{folder}/{new_name}" if folder else new_name
    if _exists(target):
        return DeviceRunResult(
            False, "validation_error", f"'{new_name}' already exists there; nothing was overwritten"
        )
    before = _live_record(path)
    data = _bytes(path)
    MUTATED[path] = None
    MUTATED[target] = data
    return _mutation_result(
        "renamed",
        before,
        _live_record(target),
        None,
        observed={"source_exists": False, "target_exists": True},
    )


def _file_move(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    if "destination_dir" in payload:
        # An absolute (here: root-relative) folder from an undo plan; "" is the root.
        prefix: str | None = str(payload.get("destination_dir") or "").strip("/")
    else:
        destination = payload.get("destination_folder")
        if not destination:
            return DeviceRunResult(
                False,
                "validation_error",
                "payload.destination_dir or payload.destination_folder is required",
            )
        prefix = _folder_prefix(str(destination))
    if prefix is None:
        return DeviceRunResult(
            False, "validation_error", "payload.destination_dir takes an absolute path or a bucket"
        )
    name = path.rsplit("/", 1)[-1]
    target = f"{prefix}/{name}" if prefix else name
    if target == path:
        return DeviceRunResult(False, "validation_error", "the file is already in that folder")
    if _exists(target):
        return DeviceRunResult(
            False, "validation_error", f"'{name}' already exists there; nothing was overwritten"
        )
    before = _live_record(path)
    data = _bytes(path)
    MUTATED[path] = None
    MUTATED[target] = data
    return _mutation_result(
        "moved",
        before,
        _live_record(target),
        None,
        observed={"source_exists": False, "target_exists": True},
    )


def _file_copy(payload: dict[str, Any]) -> DeviceRunResult:
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    destination = payload.get("destination_folder") or payload.get("destination_dir")
    name = str(payload.get("new_name") or path.rsplit("/", 1)[-1])
    if payload.get("destination_path"):
        target = str(payload["destination_path"])
    else:
        if destination:
            prefix = _folder_prefix(str(destination))
            if prefix is None:
                return DeviceRunResult(
                    False,
                    "validation_error",
                    "payload.destination_dir takes an absolute path or a bucket",
                )
        else:
            prefix = path.rsplit("/", 1)[0] if "/" in path else ""
        target = f"{prefix}/{name}" if prefix else name
    if target == path:
        return DeviceRunResult(False, "validation_error", "a file cannot be copied onto itself")
    if _exists(target):
        return DeviceRunResult(
            False, "validation_error", f"'{name}' already exists there; nothing was overwritten"
        )
    before = _live_record(path)
    MUTATED[target] = _bytes(path)
    return _mutation_result(
        "copied",
        before,
        _live_record(target),
        None,
        observed={"source_exists": True, "target_exists": True},
    )


def _file_restore(payload: dict[str, Any]) -> DeviceRunResult:
    backup = BACKUPS.get(str(payload.get("backup_id") or ""))
    if backup is None:
        return DeviceRunResult(
            False, "not_found", "not a backup in any authorised root's undo store"
        )
    target = str(payload.get("target_path") or backup["path"])
    before = _live_record(target) if _exists(target) else None
    displaced = _backup(target, "restore") if before is not None else None
    MUTATED[target] = backup["bytes"]
    after = _live_record(target)
    # The device answers the backup it RESTORED FROM under ``backup`` when nothing was
    # displaced, else the backup of what it displaced (so the restore is itself undoable).
    return _mutation_result(
        "restored",
        before,
        after,
        displaced
        if displaced is not None
        else {"backup_id": payload.get("backup_id"), "sha256": backup["sha256"]},
    )


def _file_trash_b34(payload: dict[str, Any]) -> DeviceRunResult:
    """``file.trash`` with B34's ``backup`` option over the mutable overlay."""
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    if _secret(path):
        return DeviceRunResult(False, "permission_denied", "secret_bearing_name")
    backup = _backup(path, "trash") if payload.get("backup") else None
    record = _live_record(path)
    TRASHED.append(path)
    MUTATED[path] = None
    return DeviceRunResult(
        True,
        result={
            "trashed": True,
            "method": "recycle_bin",
            "file": record,
            "backup": backup,
            "observed": {"exists": False},
        },
    )


def _file_locate_b34(payload: dict[str, Any]) -> DeviceRunResult:
    """``file.locate`` that sees the overlay (a renamed/written file is locatable at once)."""
    path = _mutation_path(payload)
    if path is None or not _exists(path):
        return DeviceRunResult(False, "not_found", "not_found")
    if path in MUTATED:
        return DeviceRunResult(True, result={"file": _live_record(path)})
    return _file_locate(payload)


def mutation_capability_results() -> dict[str, Any]:
    return {
        "file.read": _file_read,
        "file.write": _file_write,
        "file.append": _file_append,
        "file.rename": _file_rename,
        "file.move": _file_move,
        "file.copy": _file_copy,
        "file.restore": _file_restore,
        "file.trash": _file_trash_b34,
        "file.locate": _file_locate_b34,
    }


def document_capability_results() -> dict[str, Any]:
    """The capability -> handler map a :class:`tests.alarms_support.FakeDeviceAction`
    needs to serve the whole ``documents`` family from the oracle."""
    return {
        "document.extract": _document_extract,
        "file.search": _file_search,
        "file.inspect": _file_inspect,
        "file.compare": _file_compare,
        # B32 req 150/151: the hash-bearing single-file read and the Recycle Bin move.
        "file.locate": _file_locate,
        "file.trash": _file_trash,
        # B34 req 153-165: the mutations over the mutable overlay (file.locate/file.trash
        # replaced by the overlay-aware ones).
        **mutation_capability_results(),
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
    "mutation_capability_results",
    "reset_mutations",
    "truth_entry",
]
