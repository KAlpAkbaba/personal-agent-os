"""``DocumentService``: search / read / inspect / summarize / answer / compare /
common_points / previous, over the device's ``documents`` capability family
(docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2, §3).

No background crawling (ADR-0083 decision 3): every write to ``document_index`` is the
direct result of an owner-initiated ``search``/``read`` call. Device selection is by
capability (spec §2: the family is advertised together) — ``DeviceActionPort.run``'s own
real implementation (``app.routines.dispatch.BrokerDeviceAction``) already runs
``select_device(..., capability=...)`` internally per call (the SAME M19 pattern
``tools_operator`` relies on), so this service never re-implements selection: it only
distinguishes "no device port on this session at all" (``device_action is None``, refused
before any call) from "no device advertises the capability" (the port's own
``no_capable_device`` error, translated below) — the honest production answer today (the
deployed 0.1.0 agent advertises neither): "Bu bilgisayarda belge okuma yetkisi yok efendim".

Target resolution for deixis (spec §3): "bu/bunu/bu dosya/bu belge" -> the current
``document`` focus (or the current ``file`` focus not yet extracted -> extract it first);
"önceki/bir önceki/az önceki" -> the previous ``document``; a spoken name -> ``file.search``
first. No focus at all -> ``needs_clarification`` "Hangi belge?".
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.documents import answers as answers_module
from app.documents import retrieval
from app.documents.answers import DocRef, spoken_file_name
from app.documents.index import DocumentIndex
from app.documents.models import DocumentIndexRow
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_DOCUMENT_ANSWERED,
    EVENT_TYPE_DOCUMENT_COMPARED,
    EVENT_TYPE_DOCUMENT_READ,
    EVENT_TYPE_DOCUMENT_SEARCHED,
    EVENT_TYPE_DOCUMENT_TRASHED,
    SUBSYSTEM_DOCUMENTS,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT, FOCUS_KIND_FILE, FOCUS_KIND_FOLDER
from app.protocol_files import protocol_file
from app.routines.dispatch import DeviceActionPort
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.documents.service")

CAPABILITY_DOCUMENT_EXTRACT = "document.extract"
CAPABILITY_FILE_SEARCH = "file.search"
CAPABILITY_FILE_INSPECT = "file.inspect"
CAPABILITY_FILE_COMPARE = "file.compare"

SPEECH_NO_DEVICE = "Bu bilgisayarda belge okuma yetkisi yok efendim."
SPEECH_NO_DOCUMENT = "Hangi belge?"
SPEECH_NO_PREVIOUS_DOCUMENT = "Dönebileceğim önceki bir belge yok efendim."
SPEECH_NOT_FOUND = "Aradığınızı bulamadım efendim."
SPEECH_INVALID_FOLDER = "Bu klasörü tanımıyorum efendim."
SPEECH_INVALID_PATTERN = "Bu deseni kullanamam efendim."

ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_INVALID_ARGUMENT = "invalid_argument"

# --------------------------------------------------------------- confinement guard
#
# Single-layer confinement (security review, MEDIUM): the device confines every path
# argument for real (spec §2), but Cloud Core forwarded ``folder``/``pattern`` straight
# through with no check of its own — a second line of defence a model-supplied tool
# argument deserves, since ``file_search``/``document_answer`` accept the model's raw
# ``folder``/``target`` string whenever the router did not resolve one itself
# (``tools_documents.py``'s own docstring: "the owner's WORDS, preferred over the
# model's own argument"). Used by :meth:`DocumentService.search` and every
# named-target resolution path (``_resolve_document``, ``_resolve_target_file``) —
# the one place in this module a caller-supplied string reaches ``file.search``.


def _load_bucket_names() -> tuple[str, ...]:
    """The bucket names ``file.search`` accepts, from the contract BOTH halves read.

    Until 2026-09-12 this list lived here as a literal and the device had no notion of it at
    all: Cloud Core put the name straight into ``payload.roots`` and the device answered
    ``payload.roots must be absolute paths``. The refusal was filed by the defect sink on
    2026-09-09 (ADR-0102) and folder search stayed broken for three days, with both halves'
    suites green, because each restated the shape to itself.

    A missing or malformed contract is not silently survivable - that hedge is how a shared
    file stops being shared - so this raises at import and the packaged tests say so.
    """
    document = json.loads(_FILE_SEARCH_ROOTS.read_text(encoding="utf-8"))
    names = tuple(str(bucket["name"]) for bucket in document["buckets"])
    if not names:
        raise ValueError(f"{_FILE_SEARCH_ROOTS} declares no buckets")
    return names


#: ``packages/protocol/file-search-roots.json`` - the one place the bucket names are written.
#: Read through its run-time copy (app/protocol_files.py): the image carries no packages/.
_FILE_SEARCH_ROOTS: Final[Path] = protocol_file("file-search-roots.json")

#: Root names the DEVICE resolves (spec §1's ``AuthorisedRoots``): Cloud Core cannot name the
#: owner's Documents folder - the owner may have moved it - so it sends the bucket name and
#: the device resolves it, then confines it exactly as it confines an absolute path.
_KNOWN_FOLDER_ROOTS: Final[frozenset[str]] = frozenset(_load_bucket_names())
#: The Turkish words the voice router already resolves to one of the roots above
#: (``app.voice.intents._FOLDER_ALIASES`` keys, duplicated here — not imported — so this
#: module's own second line of defence does not depend on the router's private
#: vocabulary table keeping the same name or shape).
#: The Turkish words the voice router already resolves, mapped to the SAME bucket names the
#: contract declares. A dict rather than a set since 2026-09-12: the value is what actually
#: goes on the wire, so an alias can never reach the device as a word it does not know.
_KNOWN_FOLDER_ALIASES: Final[dict[str, str]] = {
    "masaüstü": "desktop",
    "masaustu": "desktop",
    "belgelerim": "documents",
    "belgelerimde": "documents",
    "indirilenler": "downloads",
}
_UNC_OR_DEVICE_PREFIXES: Final[tuple[str, ...]] = ("\\\\", "//", "\\\\?\\", "\\\\.\\")
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_MAX_PATTERN_CHARS: Final = 200


#: Every Turkish form of the letter I folded onto one, before lowercasing. Python's default
#: casing is not Turkish: ``"İndirilenler".lower()`` is ``"i̇ndirilenler"`` - an ``i`` followed
#: by a COMBINING DOT ABOVE (U+0307) - which matches no alias key, so a folder the owner typed
#: with a capital İ was refused as unknown. ``I``/``ı`` are folded the same way on purpose:
#: this is matching a closed vocabulary of folder words, where being lenient about a dot is
#: right and a false refusal is not.
_TURKISH_I_FORMS: Final[dict[int, str]] = str.maketrans({"İ": "i", "I": "i", "ı": "i", "̇": ""})


def _fold(text: str) -> str:
    """Lowercase for MATCHING, Turkish included."""
    return text.translate(_TURKISH_I_FORMS).lower().replace("̇", "")


def _is_known_folder_name(folder: str) -> bool:
    normalized = _fold(folder.strip())
    return normalized in _KNOWN_FOLDER_ROOTS or normalized in _KNOWN_FOLDER_ALIASES


def canonical_folder(folder: str) -> str | None:
    """The ``roots`` entry to send for a spoken folder, or ``None`` when there is none.

    One of three shapes, and nothing else reaches the device:

    * a bucket name (``documents``) - including through a Turkish alias (``belgelerim``);
    * a bucket name with relative segments (``documents/Faturalar``);
    * ``None``, which the caller turns into "Bu klasörü tanımıyorum efendim."

    A bare relative folder used to be forwarded and the device refused it as a
    ``validation_error`` the owner never saw a reason for. Refusing it HERE, in the owner's
    own words, is the honest answer: Cloud Core genuinely does not know which folder that is.
    """
    normalized = folder.strip().replace("\\", "/").strip("/")
    if not normalized or "\x00" in normalized:
        return None
    segments = [segment for segment in normalized.split("/") if segment]
    if not segments or ".." in segments:
        return None
    head = _fold(segments[0])
    bucket = _KNOWN_FOLDER_ALIASES.get(head, head if head in _KNOWN_FOLDER_ROOTS else None)
    if bucket is None:
        return None
    return "/".join([bucket, *segments[1:]])


def _is_safe_relative_folder(folder: str) -> bool:
    """A folder Cloud Core still forwards to the device, but only when it cannot
    possibly name an absolute location: no drive letter, no leading separator, no
    UNC/device prefix, no ``..`` segment, no embedded NUL. The device still confines
    for real (spec §2's resolve-then-contain); this only keeps an obviously
    out-of-bounds string from ever reaching the device call at all."""
    if not folder or "\x00" in folder:
        return False
    if any(folder.startswith(prefix) for prefix in _UNC_OR_DEVICE_PREFIXES):
        return False
    if folder.startswith(("\\", "/")):
        return False
    if _DRIVE_LETTER_RE.match(folder):
        return False
    parts = re.split(r"[\\/]+", folder)
    return ".." not in parts


def _validate_folder(folder: str) -> bool:
    return _is_known_folder_name(folder) or _is_safe_relative_folder(folder)


def _validate_pattern(pattern: str) -> bool:
    """A bounded name fragment or glob — no path separators, no ``..``, no NUL, at
    most 200 characters (the same bound ``file.search``'s own payload names)."""
    if not pattern or len(pattern) > _MAX_PATTERN_CHARS or "\x00" in pattern:
        return False
    if "\\" in pattern or "/" in pattern:
        return False
    return ".." not in pattern


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """A tool-facing outcome: either a full receipt-shaped dict, or a clarification."""

    payload: dict[str, Any]


def _clarification(speech: str) -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "candidates": []}


def _translate_error(error_class: str) -> tuple[str, str]:
    """(speech, error_class) for a device error (spec §2's error classes)."""
    table = {
        "permission_denied": (
            "Bu dosya izin verilen klasörlerin dışında efendim.",
            "permission_denied",
        ),
        "unsupported_format": ("Bu dosya biçimini okuyamıyorum efendim.", "unsupported_format"),
        "not_found": ("Bu dosyayı bulamadım efendim.", "not_found"),
        "timeout": ("Dosyayı okurken zaman aşımına uğradım efendim.", "timeout"),
        "invalid_argument": ("Bu isteği işleyemedim efendim.", "invalid_argument"),
        "no_capable_device": (SPEECH_NO_DEVICE, ERROR_CAPABILITY_MISSING),
        # B32 req 140: no OCR language pack on the device - the owner's to install.
        "dependency_unavailable": (
            "Cihazda bu görseli okuyacak bir OCR dil paketi yok efendim.",
            "dependency_unavailable",
        ),
    }
    return table.get(
        error_class, (f"Bunu yapamadım efendim ({error_class}).", error_class or "device_error")
    )


# ------------------------------------------------------------------- B32 helpers

#: B32 req 152: how much of a document's text a preview speaks.
PREVIEW_CHARS: Final = 240
#: B32 req 148: how many indexed documents a full-text search reads through.
FULL_TEXT_ROWS: Final = 500
#: B32 req 151: how many search hits a duplicate scan hashes (one file.locate each).
DUPLICATE_SCAN_FILES: Final = 60
#: The device's own rule: a record carries sha256 only when the file is ≤ 8 MiB.
HASHABLE_BYTES: Final = 8 * 1024 * 1024
CAPABILITY_FILE_LOCATE: Final = "file.locate"
CAPABILITY_FILE_TRASH: Final = "file.trash"


def _spoken_bytes(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} megabayt"
    if count >= 1024:
        return f"{count // 1024} kilobayt"
    return f"{count} bayt"


def _preview_facts(doc: DocRef) -> str:
    """The kind's own units: pages, sheets, slides, lines, pixels, entries."""
    structure = dict(doc.structure or {})
    kind = doc.kind
    if kind == "pdf" and structure.get("pages"):
        return f"{structure['pages']} sayfalık PDF"
    if kind in ("xlsx", "xls") and structure.get("sheets"):
        return f"{len(structure['sheets'])} sayfalı Excel tablosu"
    if kind in ("pptx", "ppt") and structure.get("slide_count"):
        return f"{structure['slide_count']} slaytlık sunum"
    if kind in ("docx", "odt", "rtf", "doc"):
        return f"{structure.get('paragraphs') or len(doc.blocks)} paragraflık belge"
    # B52 (req 143): an e-book counts its chapters.
    if kind == "epub":
        return f"{structure.get('chapters') or 1} bölümlük e-kitap"
    if kind == "image":
        w, h = structure.get("width"), structure.get("height")
        lines = structure.get("lines") or 0
        return f"{w}x{h} görsel, {lines} satır metin okundu"
    if kind in ("md", "csv", "json", "source", "txt"):
        count = structure.get("lines") or structure.get("rows") or len(doc.blocks)
        return f"{count} satırlık {kind} dosyası"
    return f"{kind} dosyası"


class DocumentService:
    def __init__(self, index: DocumentIndex | None = None, embedder: Any | None = None) -> None:
        self._index = index or DocumentIndex()
        # B37 req 149: the memory subsystem's embedder, so a question over a document is
        # ranked by meaning as well as by words; None keeps the lexical ranking alone.
        self.embedder = embedder

    # ------------------------------------------------------------- device plumbing

    def _select_device(
        self, device_action: DeviceActionPort | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        """(device_id, error_receipt). ``device_action`` itself is what actually runs a
        command; the device id used for indexing/focus is a stable literal ("device:default")
        when the port carries no device registry of its own — the real ``BrokerDeviceAction``
        selects the physical device internally and the id is not surfaced to this layer, so a
        single logical bucket is used for the index's ``(device_id, file_id)`` key. Tests that
        care about multi-device confinement key their own fake device_action similarly."""
        if device_action is None:
            return None, self._capability_missing_receipt()
        return "device:default", None

    def _invalid_argument_receipt(
        self,
        *,
        capability: str,
        requested_state: str,
        speech: str,
        db: Session,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """A typed refusal for a ``folder``/``pattern`` the confinement guard rejected —
        never echoes the rejected value (the speech names only what kind of argument was
        refused, exactly as the two constant strings above do)."""
        return self._receipt(
            capability=capability,
            requested_state=requested_state,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_INVALID_ARGUMENT},
            speech=speech,
            db=db,
            error_class=ERROR_INVALID_ARGUMENT,
            session_id=session_id,
        )

    def _capability_missing_receipt(
        self, *, capability: str = "documents", session_id: str | None = None
    ) -> dict[str, Any]:
        return self._receipt(
            capability=capability,
            requested_state="read",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "no_device_runtime"},
            speech=SPEECH_NO_DEVICE,
            error_class=ERROR_CAPABILITY_MISSING,
            session_id=session_id,
        )

    def _receipt(
        self,
        *,
        capability: str,
        requested_state: str,
        execution: str,
        terminal: str,
        server: dict[str, Any],
        speech: str,
        db: Session | None = None,
        error_class: str | None = None,
        session_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        receipt = ActionReceipt(
            action_id=str(uuid.uuid4()),
            capability=capability,
            requested_state=requested_state,
            execution_status=execution,
            terminal_status=terminal,
            observed_after={"server": server, "local": {}},
            evidence_refs=[],
            error_class=error_class,
            speech=speech,
            started_at=now,
            completed_at=now,
            session_id=session_id,
            observed_at=now,
        )
        if db is not None:
            record_receipt(db, receipt, SUBSYSTEM_DOCUMENTS)
        out = receipt.as_dict()
        if extra:
            out.update(extra)
        return out

    def _ledger(
        self,
        db: Session | None,
        *,
        event_type: str,
        action: str,
        summary: str,
        detail: dict[str, Any],
    ) -> None:
        if db is None:
            return
        try:
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_DOCUMENTS,
                    action=action,
                    factual_summary=summary,
                    occurred_at=datetime.now(UTC),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("document_ledger_failed", action=action)

    def _publish(
        self,
        *,
        file_label: str,
        part: str | None = None,
        refs: list[dict[str, Any]] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"file": file_label[:64]}
        if part:
            metadata["part"] = part[:64]
        if refs:
            # {ref, path} ONLY (functional gap fix) — no excerpt: "excerpt" is a
            # forbidden metadata-key part (app.uistate.publisher._FORBIDDEN_KEY_PARTS)
            # and must never reach the bus; the publisher's own ``_clean_metadata``
            # re-enforces the {ref, path} shape and the 8-item cap independently, this
            # is only the honest shape the caller intends to send.
            cleaned = [
                {"ref": str(r.get("ref")), "path": str(r.get("path") or "")}
                for r in refs
                if r.get("ref")
            ][:8]
            if cleaned:
                metadata["refs"] = cleaned
        publish_ui_state(
            UiState.DOCUMENT_ANALYSIS,
            subsystem=SUBSYSTEM_DOCUMENTS,
            label=file_label[:64],
            metadata=metadata,
        )

    # ------------------------------------------------------------------- search

    def search(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        pattern: str | None = None,
        folder: str | None = None,
        extensions: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        # The ONE place a spoken folder becomes a wire value. What goes out is a bucket name
        # the device knows (packages/protocol/file-search-roots.json), never the owner's word
        # and never a bare relative path the device would refuse with a class the owner can
        # make nothing of.
        root_entry = canonical_folder(folder) if folder else None
        if folder and root_entry is None:
            return self._invalid_argument_receipt(
                capability=CAPABILITY_FILE_SEARCH,
                requested_state="searched",
                speech=SPEECH_INVALID_FOLDER,
                db=db,
                session_id=session_id,
            )
        if pattern and not _validate_pattern(pattern):
            return self._invalid_argument_receipt(
                capability=CAPABILITY_FILE_SEARCH,
                requested_state="searched",
                speech=SPEECH_INVALID_PATTERN,
                db=db,
                session_id=session_id,
            )
        payload: dict[str, Any] = {}
        if pattern:
            payload["pattern"] = pattern
        if root_entry:
            payload["roots"] = [root_entry]
        if extensions:
            payload["extensions"] = extensions
        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_FILE_SEARCH,
            payload=payload,
            idempotency_key=f"document-search:{uuid.uuid4()}",
            timeout_s=10.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability=CAPABILITY_FILE_SEARCH,
                requested_state="searched",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
            )
        files = list((result.result or {}).get("files") or [])
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_SEARCHED,
            action="document.search",
            summary=f"document.search -> {len(files)} sonuç",
            detail={"pattern": pattern, "folder": folder, "count": len(files)},
        )
        if not files:
            speech = SPEECH_NOT_FOUND
        elif len(files) == 1:
            f = files[0]
            focus_module.set_focus(
                db,
                FOCUS_KIND_FILE,
                str(f.get("file_id")),
                label=str(f.get("name") or ""),
                source="document_search",
            )
            speech = f"{spoken_file_name(str(f.get('name') or ''))} dosyasını buldum efendim."
        else:
            names = {str(f.get("name")) for f in files}
            if len(names) == 1:
                paths = ", ".join(str(f.get("path")) for f in files)
                speech = f"Aynı isimde birden fazla dosya var: {paths}. Hangisini istersiniz?"
            else:
                # A name two hits share cannot tell them apart: those are spoken by path,
                # the rest by name, and the owner is asked rather than left to guess.
                counts = Counter(str(f.get("name")) for f in files)
                spoken = [
                    str(f.get("path")) if counts[str(f.get("name"))] > 1 else str(f.get("name"))
                    for f in files
                ]
                speech = "Şunları buldum: " + ", ".join(spoken) + "."
                if len(names) < len(files):
                    speech += " Aynı isimde olanları yollarıyla söyledim; hangisini istersiniz?"
            if folder:
                # A folder focus is an identity, not a transcript of what the model
                # typed (security review, LOW): persist it only from a name this layer
                # itself already recognises (a router-resolved alias or one of the
                # spec's own root names) or from the DEVICE's own ``searched_roots`` —
                # never the raw ``folder`` argument verbatim when it is neither.
                searched_roots = list((result.result or {}).get("searched_roots") or [])
                focus_folder = folder if _is_known_folder_name(folder) else None
                if focus_folder is None and searched_roots:
                    focus_folder = str(searched_roots[0])
                if focus_folder:
                    focus_module.set_focus(
                        db,
                        FOCUS_KIND_FOLDER,
                        focus_folder,
                        label=focus_folder,
                        source="document_search",
                    )
        return self._receipt(
            capability=CAPABILITY_FILE_SEARCH,
            requested_state="searched",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(files)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"files": files},
        )

    # --------------------------------------------------------------------- read

    def _extract(
        self,
        db: Session,
        device_action: DeviceActionPort,
        *,
        file_id: str | None = None,
        path: str | None = None,
        device_id: str,
        session_id: str | None = None,
    ) -> tuple[DocumentIndexRow | None, dict[str, Any] | None]:
        payload: dict[str, Any] = {}
        if file_id:
            payload["file_id"] = file_id
        if path:
            payload["path"] = path
        result = device_action.run(
            capability=CAPABILITY_DOCUMENT_EXTRACT,
            payload=payload,
            idempotency_key=f"document-extract:{file_id or path}",
            timeout_s=30.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return None, self._receipt(
                capability=CAPABILITY_DOCUMENT_EXTRACT,
                requested_state="read",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
            )
        row = self._index.upsert(db, device_id=device_id, extract=dict(result.result or {}))
        focus_module.set_focus(
            db, FOCUS_KIND_DOCUMENT, row.doc_id, label=row.title or row.name, source="document_read"
        )
        focus_module.set_focus(
            db, FOCUS_KIND_FILE, row.file_id, label=row.name, source="document_read"
        )
        return row, None

    def read(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str = "current",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        row, clar = self._resolve_document(
            db, device_action, device_id, target, extract_if_needed=True, session_id=session_id
        )
        if clar is not None:
            return clar
        assert row is not None
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_READ,
            action="document.read",
            summary=f"document.read -> {row.name}",
            detail={"file_id": row.file_id, "doc_id": row.doc_id},
        )
        self._publish(file_label=row.name, part=None)
        speech = f"{spoken_file_name(row.name)} dosyasını okudum efendim."
        if row.kind == "image":
            # B32 req 141: what the picture SAYS, from the device's OCR lines.
            text = " ".join(str(b.get("text") or "") for b in list(row.blocks or [])).strip()
            speech = (
                f"{spoken_file_name(row.name)} görselinde şu yazıyor efendim: "
                f"{answers_module._bounded_spoken_excerpt(text, limit=PREVIEW_CHARS)}"
                if text
                else f"{spoken_file_name(row.name)} görselinde okunabilir metin bulamadım efendim."
            )
        return self._receipt(
            capability=CAPABILITY_DOCUMENT_EXTRACT,
            requested_state="read",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"file_id": row.file_id, "doc_id": row.doc_id},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"file_id": row.file_id, "doc_id": row.doc_id, "path": row.path},
        )

    # ------------------------------------------------------- B37: semantic search

    def search_indexed(
        self, db: Session, question: str, *, k: int = 5, device_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Req 149: the best blocks across the indexed documents, ranked by words and,
        under an embedder, by meaning; each hit carries its score components."""
        from app.documents import semantic

        rows = self._index.latest(db, device_id=device_id, limit=50)
        hits = semantic.search_rows(question, rows, self.embedder, k=k)
        return [hit.as_dict() for hit in hits]

    # --------------------------------------------------------------- resolution

    def _doc_ref(self, db: Session, row: DocumentIndexRow) -> DocRef:
        ambiguous = self._is_ambiguous_title(db, row)
        return answers_module.from_row(row, ambiguous=ambiguous)

    def _is_ambiguous_title(self, db: Session, row: DocumentIndexRow) -> bool:
        if not row.title:
            return False
        from sqlalchemy import select

        stmt = select(DocumentIndexRow.id).where(
            DocumentIndexRow.title == row.title, DocumentIndexRow.id != row.id
        )
        return db.execute(stmt).first() is not None

    def _resolve_document(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        device_id: str,
        target: str,
        *,
        extract_if_needed: bool,
        session_id: str | None = None,
    ) -> tuple[DocumentIndexRow | None, dict[str, Any] | None]:
        """(row, clarification_or_error). Deixis (module docstring): current/previous/name."""
        target = (target or "current").strip()
        if target in ("", "current"):
            doc_entry = focus_module.current(db, FOCUS_KIND_DOCUMENT)
            if doc_entry is not None:
                row = self._index.get_by_doc_id(db, doc_entry.object_id)
                if row is not None:
                    self._index.touch(db, row)
                    return row, None
            file_entry = focus_module.current(db, FOCUS_KIND_FILE)
            if file_entry is None:
                return None, _clarification(SPEECH_NO_DOCUMENT)
            if not extract_if_needed or device_action is None:
                return None, _clarification(SPEECH_NO_DOCUMENT)
            return self._extract(
                db,
                device_action,
                file_id=file_entry.object_id,
                device_id=device_id,
                session_id=session_id,
            )
        if target == "previous":
            doc_entry = focus_module.previous(db, FOCUS_KIND_DOCUMENT)
            if doc_entry is None:
                return None, _clarification(SPEECH_NO_PREVIOUS_DOCUMENT)
            row = self._index.get_by_doc_id(db, doc_entry.object_id)
            if row is None:
                return None, _clarification(SPEECH_NO_PREVIOUS_DOCUMENT)
            self._index.touch(db, row)
            return row, None
        # A spoken name: search, and read the single hit.
        if device_action is None:
            return None, _clarification(SPEECH_NO_DOCUMENT)
        if not _validate_pattern(target):
            return None, self._invalid_argument_receipt(
                capability=CAPABILITY_FILE_SEARCH,
                requested_state="searched",
                speech=SPEECH_INVALID_PATTERN,
                db=db,
                session_id=session_id,
            )
        found = device_action.run(
            capability=CAPABILITY_FILE_SEARCH,
            payload={"pattern": target},
            idempotency_key=f"document-search-target:{target}",
            timeout_s=10.0,
        )
        files = list((found.result or {}).get("files") or []) if found.ok else []
        if len(files) != 1:
            return None, _clarification(SPEECH_NO_DOCUMENT)
        return self._extract(
            db,
            device_action,
            file_id=str(files[0].get("file_id")),
            device_id=device_id,
            session_id=session_id,
        )

    # ---------------------------------------------------------------- inspect

    def _resolve_target_file(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        target: str,
        *,
        session_id: str | None = None,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """(file_id, path, clarification) for a target, WITHOUT extracting content -
        ``inspect``'s own resolution (spec §3: inspect reads STRUCTURE only, headers-only
        ``file.inspect``, and must never trigger a ``document.extract`` the way
        ``read``/``summarize``/``answer``/``compare`` do for a file not yet indexed)."""
        target = (target or "current").strip()
        if target in ("", "current"):
            doc_entry = focus_module.current(db, FOCUS_KIND_DOCUMENT)
            if doc_entry is not None:
                row = self._index.get_by_doc_id(db, doc_entry.object_id)
                if row is not None:
                    return row.file_id, row.path, None
            file_entry = focus_module.current(db, FOCUS_KIND_FILE)
            if file_entry is None:
                return None, None, _clarification(SPEECH_NO_DOCUMENT)
            return file_entry.object_id, None, None
        if target == "previous":
            doc_entry = focus_module.previous(db, FOCUS_KIND_DOCUMENT)
            if doc_entry is not None:
                row = self._index.get_by_doc_id(db, doc_entry.object_id)
                if row is not None:
                    return row.file_id, row.path, None
            file_entry = focus_module.previous(db, FOCUS_KIND_FILE)
            if file_entry is None:
                return None, None, _clarification(SPEECH_NO_PREVIOUS_DOCUMENT)
            return file_entry.object_id, None, None
        if device_action is None:
            return None, None, _clarification(SPEECH_NO_DOCUMENT)
        if not _validate_pattern(target):
            return (
                None,
                None,
                self._invalid_argument_receipt(
                    capability=CAPABILITY_FILE_SEARCH,
                    requested_state="inspected",
                    speech=SPEECH_INVALID_PATTERN,
                    db=db,
                    session_id=session_id,
                ),
            )
        found = device_action.run(
            capability=CAPABILITY_FILE_SEARCH,
            payload={"pattern": target},
            idempotency_key=f"document-inspect-search:{target}",
            timeout_s=10.0,
        )
        files = list((found.result or {}).get("files") or []) if found.ok else []
        if len(files) != 1:
            return None, None, _clarification(SPEECH_NO_DOCUMENT)
        return str(files[0].get("file_id")), None, None

    # ------------------------------------------------------------ B32: preview

    def preview(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str = "current",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """B32 req 152: what a document IS, in a breath - its kind, its size in the kind's
        own units (pages / sheets / slides / lines / pixels / entries) and the first words of
        its text. An archive (never extracted) previews from ``file.inspect`` instead."""
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        row, clar = self._resolve_document(
            db, device_action, device_id, target, extract_if_needed=True, session_id=session_id
        )
        if clar is not None:
            if clar.get("error_class") == "unsupported_format":
                # An archive, or a kind the device only inspects: preview its headers.
                inspected = self.inspect(db, device_action, target=target, session_id=session_id)
                inspected["preview"] = {"kind": "headers", "text": inspected.get("speech")}
                return inspected
            return clar
        assert row is not None
        doc = self._doc_ref(db, row)
        text = " ".join(str(b.get("text") or "") for b in doc.blocks[:6]).strip()
        snippet = answers_module._bounded_spoken_excerpt(text, limit=PREVIEW_CHARS) if text else ""
        facts = _preview_facts(doc)
        name = spoken_file_name(row.name)
        if snippet:
            speech = f"{name}: {facts}. Başı şöyle: {snippet}"
        else:
            speech = f"{name}: {facts}. Metin çıkmadı efendim."
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_READ,
            action="document.preview",
            summary=f"document.preview -> {row.name}",
            detail={"file_id": row.file_id, "doc_id": row.doc_id},
        )
        self._publish(file_label=row.name, part=doc.blocks[0].get("ref") if doc.blocks else None)
        return self._receipt(
            capability="document.preview",
            requested_state="previewed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"file_id": row.file_id, "doc_id": row.doc_id},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "file_id": row.file_id,
                "doc_id": row.doc_id,
                "path": row.path,
                "kind": row.kind,
                "preview": {"kind": row.kind, "facts": facts, "text": snippet},
            },
        )

    # ---------------------------------------------------------- B32: full text

    def find_text(
        self,
        db: Session,
        *,
        query: str,
        device_id: str | None = None,
        limit: int = 5,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """B32 req 148: the documents whose TEXT carries the owner's words - every indexed
        document's blocks scored with the retrieval module's own rule (exact word 2, shared
        stem 1), best block per document, documents ranked. Reads the index only: no device
        call, no crawl (ADR-0083's "no background crawling" stands - what was never read is
        not searched, and the answer says how many documents it looked through)."""
        words = retrieval.content_words(query)
        rows = self._index.latest(db, device_id=device_id, limit=FULL_TEXT_ROWS)
        hits: list[dict[str, Any]] = []
        if words:
            for row in rows:
                best_score = 0
                best_block: dict[str, Any] | None = None
                for block in list(row.blocks or []):
                    score = retrieval.score_block(words, block)
                    if score > best_score:
                        best_score, best_block = score, block
                if best_block is not None and best_score > 0:
                    hits.append(
                        {
                            "file_id": row.file_id,
                            "doc_id": row.doc_id,
                            "name": row.name,
                            "path": row.path,
                            "kind": row.kind,
                            "ref": best_block.get("ref"),
                            "score": best_score,
                            "excerpt": answers_module._bounded_spoken_excerpt(
                                str(best_block.get("text") or ""), limit=120
                            ),
                        }
                    )
        hits.sort(key=lambda h: (-int(h["score"]), str(h["name"])))
        hits = hits[: max(1, limit)]
        if not words:
            speech = "Neyi arayayım efendim?"
        elif hits:
            named = ", ".join(
                f"{spoken_file_name(h['name'])} "
                f"({answers_module.place_phrase(str(h['ref']), kind=str(h['kind']))})"
                for h in hits
            )
            speech = f"'{query}' geçen {len(hits)} belge buldum efendim: {named}."
        else:
            speech = (
                f"'{query}' geçen bir belge bulamadım efendim; {len(rows)} okunmuş belgeye baktım."
            )
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_SEARCHED,
            action="document.find_text",
            summary=f"document.find_text -> {len(hits)} of {len(rows)}",
            detail={"query": query[:200], "hits": [h["file_id"] for h in hits]},
        )
        if hits:
            self._publish(
                file_label=str(hits[0]["name"]),
                part=str(hits[0]["ref"] or ""),
                refs=[{"ref": h["ref"], "path": h["path"]} for h in hits],
            )
            focus_module.set_focus(
                db,
                FOCUS_KIND_DOCUMENT,
                str(hits[0]["doc_id"]),
                label=str(hits[0]["name"]),
                source="document_find_text",
            )
        return self._receipt(
            capability="document.find_text",
            requested_state="searched",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"query": query[:200], "hits": len(hits), "searched": len(rows)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"hits": hits, "searched": len(rows), "query": query},
        )

    # ---------------------------------------------------------- B32: duplicates

    def duplicates(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        folder: str | None = None,
        pattern: str = "*",
        max_files: int = DUPLICATE_SCAN_FILES,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """B32 req 151: files with the SAME BYTES. ``file.search`` lists, then each hit
        small enough to hash (the device's own 8 MiB rule) is ``file.locate``d for its
        sha256 - a search never opens a file, a locate does - and identical hashes are
        grouped. The answer is a proposal: which copy to keep (the oldest, at the shortest
        path) and which to send to the Recycle Bin; nothing is moved here."""
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        assert device_action is not None
        payload: dict[str, Any] = {"pattern": pattern or "*", "max": max_files}
        roots = [canonical_folder(folder)] if folder and canonical_folder(folder) else None
        if roots:
            payload["roots"] = roots
        found = device_action.run(
            capability=CAPABILITY_FILE_SEARCH,
            payload=payload,
            idempotency_key=f"document-duplicates:{folder or '*'}:{pattern}",
            timeout_s=15.0,
        )
        if not found.ok:
            speech, error_class = _translate_error(found.error_class)
            return self._receipt(
                capability="document.duplicates",
                requested_state="scanned",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": found.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
            )
        files = list((found.result or {}).get("files") or [])[:max_files]
        by_hash: dict[str, list[dict[str, Any]]] = {}
        hashed = 0
        skipped_large = 0
        for record in files:
            size = int(record.get("size") or 0)
            if size > HASHABLE_BYTES:
                skipped_large += 1
                continue
            located = device_action.run(
                capability=CAPABILITY_FILE_LOCATE,
                payload={"file_id": record.get("file_id")},
                idempotency_key=f"document-duplicates-locate:{record.get('file_id')}",
                timeout_s=10.0,
            )
            digest = str(((located.result or {}).get("file") or {}).get("sha256") or "")
            if not located.ok or not digest:
                continue
            hashed += 1
            by_hash.setdefault(digest, []).append(
                {**dict((located.result or {}).get("file") or {}), "sha256": digest}
            )
        groups = []
        for digest, members in by_hash.items():
            if len(members) < 2:
                continue
            ordered = sorted(
                members, key=lambda m: (str(m.get("mtime") or ""), len(str(m.get("path") or "")))
            )
            keep, remove = ordered[0], ordered[1:]
            groups.append(
                {
                    "sha256": digest,
                    "size": int(keep.get("size") or 0),
                    "keep": {
                        "file_id": keep.get("file_id"),
                        "path": keep.get("path"),
                        "name": keep.get("name"),
                    },
                    "remove": [
                        {"file_id": m.get("file_id"), "path": m.get("path"), "name": m.get("name")}
                        for m in remove
                    ],
                }
            )
        groups.sort(key=lambda g: (-g["size"] * len(g["remove"]), str(g["keep"]["name"])))
        removable = sum(len(g["remove"]) for g in groups)
        reclaim = sum(g["size"] * len(g["remove"]) for g in groups)
        if groups:
            named = "; ".join(
                f"{spoken_file_name(str(g['keep']['name']))} {len(g['remove']) + 1} yerde"
                for g in groups[:4]
            )
            speech = (
                f"{len(groups)} yinelenen grup buldum efendim: {named}. {removable} kopya "
                f"çöp kutusuna gönderilebilir ({_spoken_bytes(reclaim)}); isterseniz "
                "'kopyaları çöp kutusuna gönder' deyin."
            )
        else:
            speech = (
                f"Yinelenen dosya bulamadım efendim; {hashed} dosyaya baktım"
                + (f", {skipped_large} büyük dosyayı atladım" if skipped_large else "")
                + "."
            )
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_SEARCHED,
            action="document.duplicates",
            summary=f"document.duplicates -> {len(groups)} groups / {hashed} hashed",
            detail={"groups": len(groups), "hashed": hashed, "skipped_large": skipped_large},
        )
        return self._receipt(
            capability="document.duplicates",
            requested_state="scanned",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"groups": len(groups), "hashed": hashed, "scanned": len(files)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "groups": groups,
                "scanned": len(files),
                "hashed": hashed,
                "skipped_large": skipped_large,
                "removable": removable,
                "reclaim_bytes": reclaim,
                "truncated": bool((found.result or {}).get("truncated")),
            },
        )

    def dedup(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        plan: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """B32 req 150: the proposal, carried out - every ``remove`` file of every group
        sent to the Recycle Bin through ``file.trash`` (never a permanent delete), each
        move read back from the device's own ``observed.exists``. Only a plan
        ``duplicates`` produced in this session is accepted; nothing is re-scanned here,
        so what the owner heard is exactly what moves."""
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        assert device_action is not None
        groups = list(plan.get("groups") or [])
        targets = [m for g in groups for m in list(g.get("remove") or [])]
        if not targets:
            return self._receipt(
                capability="document.dedup",
                requested_state="trashed",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "nothing_to_remove"},
                speech="Gönderilecek kopya yok efendim; önce yinelenen dosyaları bulmamı isteyin.",
                db=db,
                error_class="nothing_to_remove",
                session_id=session_id,
            )
        trashed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for member in targets:
            outcome = device_action.run(
                capability=CAPABILITY_FILE_TRASH,
                payload={"file_id": member.get("file_id")},
                idempotency_key=f"document-dedup-trash:{member.get('file_id')}",
                timeout_s=15.0,
            )
            body = dict(outcome.result or {})
            gone = (
                outcome.ok
                and body.get("trashed") is True
                and not (body.get("observed") or {}).get("exists", True)
            )
            (trashed if gone else failed).append(
                {**member, "error_class": None if gone else (outcome.error_class or "not_trashed")}
            )
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_TRASHED,
            action="document.dedup",
            summary=f"document.dedup -> {len(trashed)} trashed, {len(failed)} failed",
            detail={
                "trashed": [t.get("file_id") for t in trashed],
                "failed": [f.get("file_id") for f in failed],
            },
        )
        if trashed and not failed:
            speech = f"{len(trashed)} kopyayı çöp kutusuna gönderdim efendim; geri alınabilir."
            execution, terminal, error = EXECUTION_EXECUTED, TERMINAL_VERIFIED, None
        elif trashed:
            speech = (
                f"{len(trashed)} kopyayı çöp kutusuna gönderdim, "
                f"{len(failed)} kopyayı gönderemedim efendim."
            )
            execution, terminal, error = EXECUTION_EXECUTED, TERMINAL_UNVERIFIED, "partial"
        else:
            speech = "Kopyaları çöp kutusuna gönderemedim efendim."
            execution, terminal, error = (
                EXECUTION_FAILED,
                TERMINAL_FAILED,
                str(failed[0].get("error_class")),
            )
        return self._receipt(
            capability="document.dedup",
            requested_state="trashed",
            execution=execution,
            terminal=terminal,
            server={"trashed": len(trashed), "failed": len(failed)},
            speech=speech,
            db=db,
            error_class=error,
            session_id=session_id,
            extra={"trashed": trashed, "failed": failed},
        )

    def _inspect_speech(self, name: str, body: dict[str, Any]) -> str:
        name = spoken_file_name(name)
        kind = body.get("kind")
        # B32 req 139/142: a picture's headers, an archive's directory.
        if kind == "image" and isinstance(body.get("image"), dict):
            image = body["image"]
            taken = (image.get("metadata") or {}).get("date_taken")
            camera = (image.get("metadata") or {}).get("camera_model")
            bits = [
                (
                    f"{image.get('width')}x{image.get('height')} {image.get('format') or ''} görsel"
                ).strip()
            ]
            if taken:
                bits.append(f"çekim {taken}")
            if camera:
                bits.append(f"kamera {camera}")
            return f"{name}: {', '.join(bits)} efendim."
        if kind == "archive" and isinstance(body.get("archive"), dict):
            archive = body["archive"]
            entries = list(archive.get("entries") or [])
            names = ", ".join(str(e.get("name")) for e in entries[:5])
            more = " ve daha fazlası" if archive.get("truncated") or len(entries) > 5 else ""
            return (
                f"{name}: {archive.get('entry_count', len(entries))} öğe var efendim: "
                f"{names}{more}; "
                f"toplam {_spoken_bytes(int(archive.get('total_uncompressed') or 0))}."
            )
        if kind == "xlsx" and body.get("sheets"):
            sheets = ", ".join(str(s) for s in body["sheets"])
            return f"{name}: {sheets} sayfalarını içeriyor efendim."
        if kind == "pptx" and body.get("slides"):
            return f"{name}: {body['slides']} slayt var efendim."
        if kind == "pdf" and body.get("pages"):
            return f"{name}: {body['pages']} sayfa var efendim."
        if body.get("title"):
            return f"{name}: {body['title']}."
        if body.get("lines"):
            return f"{name}: {body['lines']} satır."
        return f"{name} hakkında bilgi aldım efendim."

    def inspect(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str = "current",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        # The fast path (ADR-0083 decision 3, "no background crawling"): an already
        # extracted document answers a structure question from the index, no device
        # call at all. Only tried for current/previous - ``_resolve_document``'s own
        # "a spoken name" branch always extracts regardless of ``extract_if_needed``
        # (right for read/summarize/answer/compare, wrong for inspect), so a named
        # target skips straight to ``_resolve_target_file`` below.
        row = None
        if (target or "current").strip() in ("", "current", "previous"):
            row, _clar = self._resolve_document(
                db,
                device_action,
                device_id,
                target,
                extract_if_needed=False,
                session_id=session_id,
            )
        if row is not None:
            doc = self._doc_ref(db, row)
            summary = answers_module.summarize(doc)
            return self._receipt(
                capability="document.inspect",
                requested_state="inspected",
                execution=EXECUTION_EXECUTED,
                terminal=TERMINAL_VERIFIED,
                server={"file_id": row.file_id},
                speech=summary["speech"],
                db=db,
                session_id=session_id,
                extra={"refs": summary["refs"], "structure": row.structure},
            )
        # Not indexed yet: resolve just the file identity - never ``document.extract``,
        # only the lightweight ``file.inspect`` (headers only, no text; spec §2).
        file_id, path, resolve_clar = self._resolve_target_file(
            db, device_action, target, session_id=session_id
        )
        if resolve_clar is not None:
            return resolve_clar
        if device_action is None:
            return self._capability_missing_receipt(
                capability=CAPABILITY_FILE_INSPECT, session_id=session_id
            )
        payload: dict[str, Any] = {"file_id": file_id} if file_id else {"path": path}
        result = device_action.run(
            capability=CAPABILITY_FILE_INSPECT,
            payload=payload,
            idempotency_key=f"document-inspect:{file_id or path}",
            timeout_s=15.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability=CAPABILITY_FILE_INSPECT,
                requested_state="inspected",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
            )
        body = dict(result.result or {})
        file_record = dict(body.get("file") or {})
        name = str(file_record.get("name") or "")
        return self._receipt(
            capability=CAPABILITY_FILE_INSPECT,
            requested_state="inspected",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"file_id": file_record.get("file_id")},
            speech=self._inspect_speech(name, body),
            db=db,
            session_id=session_id,
            extra={"refs": [], "structure": body},
        )

    # -------------------------------------------------------------- summarize

    def summarize(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str = "current",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        row, clar = self._resolve_document(
            db, device_action, device_id, target, extract_if_needed=True, session_id=session_id
        )
        if clar is not None:
            return clar
        assert row is not None
        doc = self._doc_ref(db, row)
        result = answers_module.summarize(doc)
        self._publish(file_label=row.name, part="summary", refs=result["refs"])
        return self._receipt(
            capability="document.summarize",
            requested_state="summarized",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"file_id": row.file_id},
            speech=result["speech"],
            db=db,
            session_id=session_id,
            extra={"refs": result["refs"]},
        )

    # ----------------------------------------------------------------- answer

    def answer(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str = "current",
        question: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        row, clar = self._resolve_document(
            db, device_action, device_id, target, extract_if_needed=True, session_id=session_id
        )
        if clar is not None:
            return clar
        assert row is not None
        doc = self._doc_ref(db, row)
        result = answers_module.answer(doc, question, embedder=self.embedder)
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_ANSWERED,
            action="document.answer",
            summary=f"document.answer -> {row.name}",
            detail={"file_id": row.file_id, "found": result["found"]},
        )
        self._publish(file_label=row.name, part="answer", refs=result["refs"])
        return self._receipt(
            capability="document.answer",
            requested_state="answered",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"file_id": row.file_id, "found": result["found"]},
            speech=result["speech"],
            db=db,
            session_id=session_id,
            extra={"refs": result["refs"], "found": result["found"]},
        )

    # ---------------------------------------------------------------- compare

    def compare(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target_a: str = "current",
        target_b: str = "previous",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        row_a, clar_a = self._resolve_document(
            db, device_action, device_id, target_a, extract_if_needed=True, session_id=session_id
        )
        if clar_a is not None:
            return clar_a
        row_b, clar_b = self._resolve_document(
            db, device_action, device_id, target_b, extract_if_needed=True, session_id=session_id
        )
        if clar_b is not None:
            return clar_b
        assert row_a is not None and row_b is not None
        doc_a, doc_b = self._doc_ref(db, row_a), self._doc_ref(db, row_b)
        device_result: dict[str, Any] | None = None
        if device_action is not None and row_a.device_id == row_b.device_id:
            attempt = device_action.run(
                capability=CAPABILITY_FILE_COMPARE,
                payload={"a": {"file_id": row_a.file_id}, "b": {"file_id": row_b.file_id}},
                idempotency_key=f"document-compare:{row_a.file_id}:{row_b.file_id}",
                timeout_s=15.0,
            )
            if attempt.ok:
                device_result = dict(attempt.result or {})
        result = answers_module.compare(doc_a, doc_b, device_result=device_result)
        self._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_COMPARED,
            action="document.compare",
            summary=f"document.compare -> {row_a.name} / {row_b.name}",
            detail={"a": row_a.file_id, "b": row_b.file_id, "changed": len(result["changed_refs"])},
        )
        return self._receipt(
            capability="document.compare",
            requested_state="compared",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"changed": len(result["changed_refs"])},
            speech=result["speech"],
            db=db,
            session_id=session_id,
            extra={
                "refs": result["refs"],
                "changed_refs": result["changed_refs"],
                "unchanged_refs": result["unchanged_refs"],
                "added_refs": result["added_refs"],
                "removed_refs": result["removed_refs"],
            },
        )

    # --------------------------------------------------------- common_points

    def common_points(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        targets: list[str] | str = "recent",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        rows: list[DocumentIndexRow] = []
        if targets == "recent" or targets == []:
            rows = self._index.latest(db, device_id=device_id, limit=5)
        else:
            for t in targets:  # type: ignore[union-attr]
                row, clar = self._resolve_document(
                    db, device_action, device_id, t, extract_if_needed=True, session_id=session_id
                )
                if clar is not None:
                    return clar
                if row is not None:
                    rows.append(row)
        if len(rows) < 2:
            return _clarification(SPEECH_NO_DOCUMENT)
        docs = [self._doc_ref(db, r) for r in rows]
        result = answers_module.common_points(docs)
        return self._receipt(
            capability="document.common_points",
            requested_state="common_points",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"documents": len(docs), "terms": len(result["terms"])},
            speech=result["speech"],
            db=db,
            session_id=session_id,
            extra={"terms": result["terms"], "found": result["found"]},
        )

    # ------------------------------------------------------------------ previous

    def previous(self, db: Session, *, session_id: str | None = None) -> dict[str, Any]:
        entry = focus_module.previous(db, FOCUS_KIND_DOCUMENT)
        if entry is None:
            return _clarification(SPEECH_NO_PREVIOUS_DOCUMENT)
        row = self._index.get_by_doc_id(db, entry.object_id)
        name = row.name if row is not None else entry.label
        focus_module.set_focus(
            db, FOCUS_KIND_DOCUMENT, entry.object_id, label=entry.label, source="document_previous"
        )
        if row is not None:
            focus_module.set_focus(
                db, FOCUS_KIND_FILE, row.file_id, label=row.name, source="document_previous"
            )
        speech = f"{spoken_file_name(name)} belgesine döndüm efendim."
        return self._receipt(
            capability="document.previous",
            requested_state="switched",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"doc_id": entry.object_id},
            speech=speech,
            db=db,
            session_id=session_id,
        )


__all__ = [
    "CAPABILITY_DOCUMENT_EXTRACT",
    "CAPABILITY_FILE_COMPARE",
    "CAPABILITY_FILE_INSPECT",
    "CAPABILITY_FILE_SEARCH",
    "DocumentService",
    "SPEECH_NO_DEVICE",
    "SPEECH_NO_DOCUMENT",
    "SPEECH_NO_PREVIOUS_DOCUMENT",
]
