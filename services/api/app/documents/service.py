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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.documents import answers as answers_module
from app.documents.answers import DocRef, spoken_file_name
from app.documents.index import DocumentIndex
from app.documents.models import DocumentIndexRow
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_DOCUMENT_ANSWERED,
    EVENT_TYPE_DOCUMENT_COMPARED,
    EVENT_TYPE_DOCUMENT_READ,
    EVENT_TYPE_DOCUMENT_SEARCHED,
    SUBSYSTEM_DOCUMENTS,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT, FOCUS_KIND_FILE, FOCUS_KIND_FOLDER
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
_FILE_SEARCH_ROOTS: Final[Path] = (
    Path(__file__).resolve().parents[4] / "packages" / "protocol" / "file-search-roots.json"
)

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
_TURKISH_I_FORMS: Final[dict[int, str]] = str.maketrans(
    {"İ": "i", "I": "i", "ı": "i", "̇": ""}
)


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
    }
    return table.get(
        error_class, (f"Bunu yapamadım efendim ({error_class}).", error_class or "device_error")
    )


class DocumentService:
    def __init__(self, index: DocumentIndex | None = None) -> None:
        self._index = index or DocumentIndex()

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
                speech = "Şunları buldum: " + ", ".join(str(f.get("name")) for f in files) + "."
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
            return None, None, self._invalid_argument_receipt(
                capability=CAPABILITY_FILE_SEARCH,
                requested_state="inspected",
                speech=SPEECH_INVALID_PATTERN,
                db=db,
                session_id=session_id,
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

    def _inspect_speech(self, name: str, body: dict[str, Any]) -> str:
        name = spoken_file_name(name)
        kind = body.get("kind")
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
        result = answers_module.answer(doc, question)
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
