"""Managed file mutation (B34 req 153-167, 170, 674): the Cloud Core half.

Until this batch the documents family was structurally read-only (ADR-0083 decision 7): no
tool could write, append, rename, move, copy or delete a file, and the corpus proved it.
This module makes every one of those an act with three properties the roadmap names:

* **journaled** - a ``file_mutations`` row exists BEFORE the device is asked (the proposal)
  and is completed from the device's ANSWER (the record before, the record after, both
  hashed, the backup the device kept). The rows for one path are its version history (164);
  the receipt the owner hears is composed from the row (162, 163).
* **reversible** - every applied row carries an inverse plan derived from what the device
  answered, and ``undo`` runs it and reads the result back (160). The device's undo store
  holds the displaced bytes; the Recycle Bin is the owner's own way back for a delete.
* **approved by risk** - a policy (:func:`risk_of`) says which acts apply at once (a new
  file, an append: ``low``) and which wait for the owner's word (overwriting, renaming,
  copying: ``sensitive``; moving, deleting: ``critical``). A proposal goes through the SAME
  read-back + confirmation gate mail drafts and calendar proposals use
  (``app.actions.confirmation_gate``): never applied unless it was spoken to THIS session
  (or listed on the Cockpit) and confirmed after that, and never when the host flag
  ``documents_mutation_enabled`` is off (166, 674).

The DEVICE does the act (``file.write`` / ``file.append`` / ``file.rename`` / ``file.move``
/ ``file.copy`` / ``file.trash {backup}`` / ``file.restore``, DEVICE_PROTOCOL.md §6m):
atomically, inside the owner's authorised roots, with a backup first. Nothing here is a
claim: an act whose answer carries no ``after`` record with a hash is recorded ``failed``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.confirmation_gate import (
    CONFIRM_SOURCE_REST,
    GATE_ALREADY_SENT,
    GATE_NOT_READ_BACK,
    GATE_SEND_DISABLED,
    Confirmation,
    check_gate,
)
from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
)
from app.documents.answers import spoken_file_name
from app.documents.models import (
    MUTATION_KIND_APPEND,
    MUTATION_KIND_COPY,
    MUTATION_KIND_DELETE,
    MUTATION_KIND_EDIT,
    MUTATION_KIND_MOVE,
    MUTATION_KIND_RENAME,
    MUTATION_KIND_RESTORE,
    MUTATION_KIND_WRITE,
    MUTATION_RISK_CRITICAL,
    MUTATION_RISK_LOW,
    MUTATION_RISK_SENSITIVE,
    MUTATION_STATE_APPLIED,
    MUTATION_STATE_DISCARDED,
    MUTATION_STATE_FAILED,
    MUTATION_STATE_PROPOSED,
    MUTATION_STATE_UNDONE,
    FileMutationRow,
)
from app.documents.service import (
    DocumentService,
    _clarification,
    canonical_folder,
)
from app.ledger.vocabulary import (
    EVENT_TYPE_DOCUMENT_MUTATION_APPLIED,
    EVENT_TYPE_DOCUMENT_MUTATION_DISCARDED,
    EVENT_TYPE_DOCUMENT_MUTATION_PROPOSED,
    EVENT_TYPE_DOCUMENT_MUTATION_UNDONE,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_FILE
from app.routines.dispatch import DeviceActionPort

logger = get_logger(__name__)

CAPABILITY_FILE_LOCATE: Final = "file.locate"
CAPABILITY_FILE_READ: Final = "file.read"
CAPABILITY_FILE_WRITE: Final = "file.write"
CAPABILITY_FILE_APPEND: Final = "file.append"
CAPABILITY_FILE_RENAME: Final = "file.rename"
CAPABILITY_FILE_MOVE: Final = "file.move"
CAPABILITY_FILE_COPY: Final = "file.copy"
CAPABILITY_FILE_TRASH: Final = "file.trash"
CAPABILITY_FILE_RESTORE: Final = "file.restore"

#: The kinds a byte-level edit may touch (the device refuses the rest as ``not_text``).
TEXT_LIKE_KINDS: Final[frozenset[str]] = frozenset({"md", "csv", "json", "source", "txt"})
TEXT_LIKE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".cs",
        ".ps1",
        ".sh",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".xml",
        ".html",
        ".css",
        ".sql",
        ".log",
    }
)
DEFAULT_FOLDER: Final = "documents"
MAX_READ_CHARS: Final = 2_000_000
READ_CHUNK: Final = 65536
DEVICE_TIMEOUT_S: Final = 20.0

ERROR_MUTATION_DISABLED = "mutation_disabled"
ERROR_NOT_TEXT = "not_text"
ERROR_NOTHING_PENDING = "nothing_pending"
ERROR_NOTHING_TO_UNDO = "nothing_to_undo"
ERROR_NOT_VERIFIED = "mutation_unverified"
ERROR_TEXT_NOT_FOUND = "text_not_found"

SPEECH_MUTATION_DISABLED: Final = (
    "Dosya değiştirme bu sunucuda kapalı efendim; hiçbir şeye dokunmadım."
)
SPEECH_NO_TEXT: Final = "Ne yazayım efendim?"
SPEECH_NO_NAME: Final = "Dosyaya ne ad vereyim efendim?"
SPEECH_NO_NEW_NAME: Final = "Yeni adı ne olsun efendim?"
SPEECH_NO_FOLDER: Final = "Hangi klasöre efendim?"
SPEECH_UNKNOWN_FOLDER: Final = "Bu klasörü tanımıyorum efendim."
SPEECH_NOTHING_PENDING: Final = "Uygulanacak bekleyen bir değişiklik yok efendim."
SPEECH_NOTHING_TO_UNDO: Final = "Geri alınacak bir değişiklik yok efendim."
SPEECH_NOT_TEXT: Final = (
    "Bu bir metin dosyası değil efendim; Word, Excel ve sunum dosyalarını byte düzeyinde "
    "düzenlemem, bozulurlar. Metin, Markdown, CSV, JSON ve kaynak dosyaları düzenlenir."
)
SPEECH_NOT_READ_BACK: Final = (
    "Bu değişikliği size henüz okumadım efendim; uygulamadan önce ne yapacağımı duymanız gerekir."
)
SPEECH_ALREADY_DONE: Final = "Bu değişiklik zaten sonuçlanmış efendim; bir daha uygulamadım."
SPEECH_NO_VERSIONS: Final = "Bu dosya için kayıtlı bir değişiklik yok efendim."


def risk_of(kind: str, *, existing: bool) -> str:
    """The policy (166): what applies at once and what waits for the owner's word."""
    if kind == MUTATION_KIND_WRITE:
        return MUTATION_RISK_SENSITIVE if existing else MUTATION_RISK_LOW
    if kind == MUTATION_KIND_APPEND:
        return MUTATION_RISK_LOW
    if kind in (MUTATION_KIND_EDIT, MUTATION_KIND_RENAME, MUTATION_KIND_COPY):
        return MUTATION_RISK_SENSITIVE
    if kind in (MUTATION_KIND_MOVE, MUTATION_KIND_DELETE):
        return MUTATION_RISK_CRITICAL
    return MUTATION_RISK_SENSITIVE


def is_text_like_name(name: str) -> bool:
    dot = name.rfind(".")
    return dot >= 0 and name[dot:].lower() in TEXT_LIKE_EXTENSIONS


def _dir_of(path: str) -> str:
    """The folder part of a device path; "" for a bare name (a root-relative fixture)."""
    cut = max(path.rfind("\\"), path.rfind("/"))
    return path[:cut] if cut > 0 else ""


def _name_of(path: str) -> str:
    cut = max(path.rfind("\\"), path.rfind("/"))
    return path[cut + 1 :] if cut >= 0 else path


_DIACRITICS: Final = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiucgiosuaiu")


def _strip_diacritics(text: str) -> str:
    return text.translate(_DIACRITICS)


def replace_spoken(current: str, find: str, replace: str) -> tuple[str, int]:
    """Replace every occurrence of ``find`` in ``current``, matched the way the owner SAID
    it - the router hands the words casefolded (Turkish rules), the file keeps its own
    capitals - and answer the count. The replacement is written as spoken."""
    from app.voice.intents import turkish_casefold

    haystack = turkish_casefold(current)
    needle = turkish_casefold(find)
    if not needle or len(haystack) != len(current):
        count = current.count(find)
        return current.replace(find, replace), count
    if needle not in haystack:
        # ASR drops diacritics ("butce" for "Bütçe"): the letters are compared without
        # them too, still one-to-one so the slices below line up with the original.
        haystack = _strip_diacritics(haystack)
        needle = _strip_diacritics(needle)
    out: list[str] = []
    count = 0
    cursor = 0
    while True:
        at = haystack.find(needle, cursor)
        if at < 0:
            break
        out.append(current[cursor:at])
        out.append(replace)
        cursor = at + len(needle)
        count += 1
    out.append(current[cursor:])
    return "".join(out), count


class MutationService:
    """The journal, the policy and the device calls; the receipts come from
    :class:`DocumentService` so the family speaks with one voice."""

    def __init__(
        self,
        documents: DocumentService,
        *,
        enabled: bool | Callable[[], bool] = True,
    ) -> None:
        self._documents = documents
        self._enabled = enabled

    # ------------------------------------------------------------------ plumbing

    def enabled(self) -> bool:
        return bool(self._enabled() if callable(self._enabled) else self._enabled)

    def _refused(
        self,
        db: Session,
        *,
        capability: str,
        speech: str,
        error_class: str,
        session_id: str | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._documents._receipt(
            capability=capability,
            requested_state="mutated",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": error_class},
            speech=speech,
            db=db,
            error_class=error_class,
            session_id=session_id,
            extra=extra,
        )

    def _disabled(
        self, db: Session, capability: str, session_id: str | None
    ) -> dict[str, Any] | None:
        if self.enabled():
            return None
        return self._refused(
            db,
            capability=capability,
            speech=SPEECH_MUTATION_DISABLED,
            error_class=ERROR_MUTATION_DISABLED,
            session_id=session_id,
        )

    def _locate(
        self, device: DeviceActionPort, *, file_id: str | None = None, path: str | None = None
    ) -> dict[str, Any] | None:
        payload = {"file_id": file_id} if file_id else {"path": path}
        found = device.run(
            capability=CAPABILITY_FILE_LOCATE,
            payload=payload,
            idempotency_key=f"document-mutation-locate:{file_id or path}",
            timeout_s=DEVICE_TIMEOUT_S,
        )
        if not found.ok:
            return None
        record = dict((found.result or {}).get("file") or {})
        return record if record.get("path") else None

    def _read_text(self, device: DeviceActionPort, file_id: str) -> str | None:
        """The whole text through ``file.read`` in 64 KiB pages, bounded."""
        text = ""
        offset = 0
        while len(text) < MAX_READ_CHARS:
            page = device.run(
                capability=CAPABILITY_FILE_READ,
                payload={"file_id": file_id, "offset": offset, "length": READ_CHUNK},
                idempotency_key=f"document-mutation-read:{file_id}:{offset}",
                timeout_s=DEVICE_TIMEOUT_S,
            )
            if not page.ok:
                return None
            body = page.result or {}
            chunk = str(body.get("text") or "")
            text += chunk
            if not body.get("truncated") or not chunk:
                break
            offset += len(chunk)
        return text

    def _resolve_existing(
        self, db: Session, device: DeviceActionPort, target: str, *, session_id: str | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(record with path+sha, clarification): the focused/named file, located by the device."""
        file_id, path, clar = self._documents._resolve_target_file(
            db, device, target, session_id=session_id
        )
        if clar is not None:
            return None, clar
        record = self._locate(device, file_id=file_id, path=path if not file_id else None)
        if record is None:
            return None, _clarification("Dosyayı yerinde bulamadım efendim; adıyla söyler misiniz?")
        return record, None

    def _row(
        self,
        db: Session,
        *,
        device_id: str,
        kind: str,
        risk: str,
        name: str,
        record: dict[str, Any] | None,
        plan: dict[str, Any],
        summary: str,
        session_id: str | None,
    ) -> FileMutationRow:
        now = datetime.now(UTC)
        row = FileMutationRow(
            id=uuid.uuid4(),
            device_id=device_id,
            session_id=session_id,
            kind=kind,
            state=MUTATION_STATE_PROPOSED,
            risk=risk,
            file_id=(record or {}).get("file_id"),
            name=name,
            path_before=(record or {}).get("path"),
            sha_before=(record or {}).get("sha256"),
            size_before=(record or {}).get("size"),
            plan_json=plan,
            summary=summary[:512],
            created_at=now,
            # A proposal the tool returns IS spoken to this session: the read-back.
            read_back_at=now,
            read_back_session_id=session_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _propose_or_apply(
        self,
        db: Session,
        device: DeviceActionPort,
        row: FileMutationRow,
        *,
        capability: str,
        session_id: str | None,
        turn: int | None,
    ) -> dict[str, Any]:
        self._documents._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_MUTATION_PROPOSED,
            action=capability,
            summary=f"{capability} -> {row.kind} {row.name} ({row.risk})",
            detail={
                "mutation_id": str(row.id),
                "kind": row.kind,
                "risk": row.risk,
                "path": row.path_before,
            },
        )
        if row.risk == MUTATION_RISK_LOW:
            row.read_back_turn = turn
            return self._apply_row(
                db,
                device,
                row,
                capability=capability,
                session_id=session_id,
                confirmed_by=f"policy:{MUTATION_RISK_LOW}",
            )
        row.read_back_turn = turn
        db.commit()
        return self._documents._receipt(
            capability=capability,
            requested_state="proposed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"mutation_id": str(row.id), "state": row.state, "risk": row.risk},
            speech=row.summary + " Uygulayayım mı?",
            db=db,
            session_id=session_id,
            extra={
                "mutation_id": str(row.id),
                "state": row.state,
                "risk": row.risk,
                "kind": row.kind,
            },
        )

    # ------------------------------------------------------------- proposals

    def write(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        name: str | None,
        text: str | None,
        folder: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "X adında bir dosya oluştur, içine şunu yaz." (154)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.write", session_id)) is not None:
            return off
        if not name or not name.strip():
            return _clarification(SPEECH_NO_NAME)
        if text is None or not text.strip():
            return _clarification(SPEECH_NO_TEXT)
        name = name.strip()
        if not is_text_like_name(name):
            return self._refused(
                db,
                capability="document.write",
                speech=SPEECH_NOT_TEXT,
                error_class=ERROR_NOT_TEXT,
                session_id=session_id,
            )
        bucket = canonical_folder(folder) if folder else DEFAULT_FOLDER
        if bucket is None:
            return self._refused(
                db,
                capability="document.write",
                speech=SPEECH_UNKNOWN_FOLDER,
                error_class="invalid_argument",
                session_id=session_id,
            )
        plan = {
            "capability": CAPABILITY_FILE_WRITE,
            "payload": {"folder": bucket, "name": name, "text": text},
        }
        summary = (
            f"{spoken_file_name(name)} dosyasını {len(text)} karakterle oluşturacağım efendim."
        )
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_WRITE,
            risk=risk_of(MUTATION_KIND_WRITE, existing=False),
            name=name,
            record=None,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.write", session_id=session_id, turn=turn
        )

    def append(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        text: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyanın sonuna şunu ekle." (155)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.append", session_id)) is not None:
            return off
        if text is None or not text.strip():
            return _clarification(SPEECH_NO_TEXT)
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        if not is_text_like_name(str(record.get("name") or "")):
            return self._refused(
                db,
                capability="document.append",
                speech=SPEECH_NOT_TEXT,
                error_class=ERROR_NOT_TEXT,
                session_id=session_id,
            )
        body = text if text.endswith("\n") else text + "\n"
        plan = {
            "capability": CAPABILITY_FILE_APPEND,
            "payload": {
                "file_id": record["file_id"],
                "text": body,
                "expected_sha256": record.get("sha256"),
            },
        }
        summary = (
            f"{spoken_file_name(str(record['name']))} dosyasının sonuna {len(text)} karakter "
            f"ekleyeceğim efendim."
        )
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_APPEND,
            risk=risk_of(MUTATION_KIND_APPEND, existing=True),
            name=str(record["name"]),
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.append", session_id=session_id, turn=turn
        )

    def edit(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        find: str | None,
        replace: str | None,
        text: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyada X yerine Y yaz." (153, 167) / "Bu belgeyi güncelle ve kaydet" with the
        new text (170) - always a proposal: the file's own words are replaced."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.edit", session_id)) is not None:
            return off
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        name = str(record["name"])
        if not is_text_like_name(name):
            return self._refused(
                db,
                capability="document.edit",
                speech=SPEECH_NOT_TEXT,
                error_class=ERROR_NOT_TEXT,
                session_id=session_id,
            )
        if find:
            current = self._read_text(device, str(record["file_id"]))
            if current is None:
                return self._refused(
                    db,
                    capability="document.edit",
                    speech="Dosyayı okuyamadım efendim; değiştirmedim.",
                    error_class="read_failed",
                    session_id=session_id,
                )
            new_text, count = replace_spoken(current, find, replace or "")
            if count == 0:
                return self._refused(
                    db,
                    capability="document.edit",
                    speech=(
                        f"{spoken_file_name(name)} içinde '{find}' geçmiyor efendim; değiştirmedim."
                    ),
                    error_class=ERROR_TEXT_NOT_FOUND,
                    session_id=session_id,
                )
            summary = (
                f"{spoken_file_name(name)} dosyasında '{find}' yerine '{replace or ''}' "
                f"yazacağım efendim ({count} yerde)."
            )
        elif text is not None and text.strip():
            new_text = text
            summary = (
                f"{spoken_file_name(name)} dosyasının içeriğini {len(text)} karakterlik yeni "
                f"metinle değiştireceğim efendim."
            )
        else:
            return _clarification("Neyi neyle değiştireyim efendim?")
        plan = {
            "capability": CAPABILITY_FILE_WRITE,
            "payload": {
                "file_id": record["file_id"],
                "text": new_text,
                "expected_sha256": record.get("sha256"),
            },
        }
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_EDIT,
            risk=risk_of(MUTATION_KIND_EDIT, existing=True),
            name=name,
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.edit", session_id=session_id, turn=turn
        )

    def rename(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        new_name: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyanın adını X yap." (156)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.rename", session_id)) is not None:
            return off
        if not new_name or not new_name.strip():
            return _clarification(SPEECH_NO_NEW_NAME)
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        name = str(record["name"])
        new_name = new_name.strip()
        if "." not in new_name and "." in name:
            new_name = new_name + name[name.rfind(".") :]
        plan = {
            "capability": CAPABILITY_FILE_RENAME,
            "payload": {"file_id": record["file_id"], "new_name": new_name},
        }
        summary = (
            f"{spoken_file_name(name)} dosyasının adını {spoken_file_name(new_name)} "
            f"yapacağım efendim."
        )
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_RENAME,
            risk=risk_of(MUTATION_KIND_RENAME, existing=True),
            name=name,
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.rename", session_id=session_id, turn=turn
        )

    def move(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        folder: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyayı Masaüstüne taşı." (157)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.move", session_id)) is not None:
            return off
        if not folder:
            return _clarification(SPEECH_NO_FOLDER)
        bucket = canonical_folder(folder)
        if bucket is None:
            return self._refused(
                db,
                capability="document.move",
                speech=SPEECH_UNKNOWN_FOLDER,
                error_class="invalid_argument",
                session_id=session_id,
            )
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        name = str(record["name"])
        plan = {
            "capability": CAPABILITY_FILE_MOVE,
            "payload": {"file_id": record["file_id"], "destination_folder": bucket},
        }
        summary = f"{spoken_file_name(name)} dosyasını {folder} klasörüne taşıyacağım efendim."
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_MOVE,
            risk=risk_of(MUTATION_KIND_MOVE, existing=True),
            name=name,
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.move", session_id=session_id, turn=turn
        )

    def copy(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        new_name: str | None,
        folder: str | None,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyayı kopyala." / "... Masaüstüne kopyala." / "... X adıyla kopyala." (158)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.copy", session_id)) is not None:
            return off
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        name = str(record["name"])
        payload: dict[str, Any] = {"file_id": record["file_id"]}
        if folder:
            bucket = canonical_folder(folder)
            if bucket is None:
                return self._refused(
                    db,
                    capability="document.copy",
                    speech=SPEECH_UNKNOWN_FOLDER,
                    error_class="invalid_argument",
                    session_id=session_id,
                )
            payload["destination_folder"] = bucket
        copy_name = (new_name or "").strip()
        if not copy_name and not folder:
            stem, dot, ext = name.rpartition(".")
            copy_name = f"{stem or ext} - kopya.{ext}" if dot and stem else f"{name} - kopya"
        if copy_name:
            if "." not in copy_name and "." in name:
                copy_name = copy_name + name[name.rfind(".") :]
            payload["new_name"] = copy_name
        where = f" {folder} klasörüne" if folder else ""
        as_name = f" {spoken_file_name(copy_name)} adıyla" if copy_name else ""
        plan = {"capability": CAPABILITY_FILE_COPY, "payload": payload}
        summary = f"{spoken_file_name(name)} dosyasını{where}{as_name} kopyalayacağım efendim."
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_COPY,
            risk=risk_of(MUTATION_KIND_COPY, existing=True),
            name=name,
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.copy", session_id=session_id, turn=turn
        )

    def delete(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        session_id: str | None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """ "Bu dosyayı sil." (159, 161): the Recycle Bin with a backup first - never a
        permanent delete (the shape of that policy is the owner's checkpoint)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.delete", session_id)) is not None:
            return off
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        name = str(record["name"])
        plan = {
            "capability": CAPABILITY_FILE_TRASH,
            "payload": {"file_id": record["file_id"], "backup": True},
        }
        summary = (
            f"{spoken_file_name(name)} dosyasını çöp kutusuna göndereceğim efendim; "
            f"yedeği alınır, geri alınabilir."
        )
        row = self._row(
            db,
            device_id=device_id or "",
            kind=MUTATION_KIND_DELETE,
            risk=risk_of(MUTATION_KIND_DELETE, existing=True),
            name=name,
            record=record,
            plan=plan,
            summary=summary,
            session_id=session_id,
        )
        return self._propose_or_apply(
            db, device, row, capability="document.delete", session_id=session_id, turn=turn
        )

    # ------------------------------------------------------------- the gate: apply

    def current_pending(
        self, db: Session, *, session_id: str | None = None
    ) -> FileMutationRow | None:
        stmt = select(FileMutationRow).where(FileMutationRow.state == MUTATION_STATE_PROPOSED)
        if session_id is not None:
            stmt = stmt.where(FileMutationRow.session_id == session_id)
        stmt = stmt.order_by(FileMutationRow.created_at.desc())
        return db.execute(stmt).scalars().first()

    def pending(self, db: Session, *, limit: int = 50) -> list[FileMutationRow]:
        stmt = (
            select(FileMutationRow)
            .where(FileMutationRow.state == MUTATION_STATE_PROPOSED)
            .order_by(FileMutationRow.created_at.desc())
            .limit(max(1, limit))
        )
        return list(db.execute(stmt).scalars().all())

    def journal(self, db: Session, *, limit: int = 50) -> list[FileMutationRow]:
        stmt = (
            select(FileMutationRow).order_by(FileMutationRow.created_at.desc()).limit(max(1, limit))
        )
        return list(db.execute(stmt).scalars().all())

    def apply(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        mutation_id: str | None,
        confirmation: Confirmation | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        """ "Uygula." / the Cockpit's Onayla: the proposal this session heard, through the
        same gate a mail draft passes (166)."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        row = (
            db.get(FileMutationRow, uuid.UUID(mutation_id))
            if mutation_id
            else self.current_pending(
                db,
                session_id=None
                if (confirmation and confirmation.source == CONFIRM_SOURCE_REST)
                else session_id,
            )
        )
        if row is None:
            return self._refused(
                db,
                capability="document.apply",
                speech=SPEECH_NOTHING_PENDING,
                error_class=ERROR_NOTHING_PENDING,
                session_id=session_id,
            )
        gate = check_gate(
            state=row.state,
            prepared_state="never",
            read_back_state=MUTATION_STATE_PROPOSED,
            read_back_at=row.read_back_at,
            read_back_session_id=row.read_back_session_id,
            read_back_turn=row.read_back_turn,
            confirmation=confirmation,
            host_flag_enabled=self.enabled(),
            provider_available=True,
        )
        if not gate.ok:
            reason = gate.reason or "refused"
            speech = {
                GATE_NOT_READ_BACK: SPEECH_NOT_READ_BACK,
                GATE_ALREADY_SENT: SPEECH_ALREADY_DONE,
                GATE_SEND_DISABLED: SPEECH_MUTATION_DISABLED,
            }.get(
                reason,
                "Bu değişikliği bu şekilde onaylayamam efendim; sesli 'uygula' ya da panelden "
                "onay gerekir.",
            )
            return self._refused(
                db,
                capability="document.apply",
                speech=speech,
                error_class=reason,
                session_id=session_id,
                extra={"mutation_id": str(row.id)},
            )
        row.confirmed_at = datetime.now(UTC)
        row.confirmed_by = (
            f"{confirmation.source}:{confirmation.session_id}" if confirmation is not None else None
        )
        return self._apply_row(
            db,
            device,
            row,
            capability="document.apply",
            session_id=session_id,
            confirmed_by=row.confirmed_by,
        )

    def _apply_row(
        self,
        db: Session,
        device: DeviceActionPort,
        row: FileMutationRow,
        *,
        capability: str,
        session_id: str | None,
        confirmed_by: str | None,
    ) -> dict[str, Any]:
        plan = dict(row.plan_json or {})
        cap = str(plan.get("capability") or "")
        outcome = device.run(
            capability=cap,
            payload=dict(plan.get("payload") or {}),
            idempotency_key=f"document-mutation:{row.id}",
            timeout_s=DEVICE_TIMEOUT_S,
        )
        body = dict(outcome.result or {})
        row.result_json = body
        row.confirmed_by = confirmed_by
        verified, after, backup_id = self._verify(row.kind, outcome.ok, body)
        if not verified:
            row.state = MUTATION_STATE_FAILED
            row.error_class = outcome.error_class or ERROR_NOT_VERIFIED
            row.error_message = (outcome.message or "the device did not answer a verified record")[
                :1024
            ]
            db.commit()
            speech = self._failure_speech(row, outcome.error_class, outcome.message)
            self._documents._ledger(
                db,
                event_type=EVENT_TYPE_DOCUMENT_MUTATION_APPLIED,
                action=capability,
                summary=f"{capability} -> {row.kind} {row.name} FAILED ({row.error_class})",
                detail={"mutation_id": str(row.id), "error_class": row.error_class},
            )
            return self._documents._receipt(
                capability=capability,
                requested_state="mutated",
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                server={
                    "mutation_id": str(row.id),
                    "state": row.state,
                    "device": {"error_class": outcome.error_class, "message": outcome.message},
                },
                speech=speech,
                db=db,
                error_class=row.error_class,
                session_id=session_id,
                extra={"mutation_id": str(row.id), "state": row.state, "kind": row.kind},
            )
        now = datetime.now(UTC)
        row.state = MUTATION_STATE_APPLIED
        row.applied_at = now
        row.path_after = after.get("path") if after else None
        row.sha_after = after.get("sha256") if after else None
        row.size_after = after.get("size") if after else None
        row.backup_id = backup_id
        row.undo_json = self._undo_plan(row, after, backup_id)
        db.commit()
        self._settle_index(db, row, after)
        self._documents._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_MUTATION_APPLIED,
            action=capability,
            summary=f"{capability} -> {row.kind} {row.name} applied",
            detail={
                "mutation_id": str(row.id),
                "kind": row.kind,
                "path_before": row.path_before,
                "path_after": row.path_after,
                "sha_before": row.sha_before,
                "sha_after": row.sha_after,
                "backup_id": backup_id,
            },
        )
        if after and after.get("file_id") and row.kind != MUTATION_KIND_DELETE:
            focus_module.set_focus(
                db,
                FOCUS_KIND_FILE,
                str(after["file_id"]),
                label=str(after.get("name") or row.name),
                source="document_mutation",
            )
        return self._documents._receipt(
            capability=capability,
            requested_state="mutated",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={
                "mutation_id": str(row.id),
                "state": row.state,
                "path_after": row.path_after,
                "sha_before": row.sha_before,
                "sha_after": row.sha_after,
                "backup_id": backup_id,
            },
            speech=self._applied_speech(row),
            db=db,
            session_id=session_id,
            extra={
                "mutation_id": str(row.id),
                "state": row.state,
                "kind": row.kind,
                "sha_before": row.sha_before,
                "sha_after": row.sha_after,
                "backup_id": backup_id,
                "path_after": row.path_after,
            },
        )

    @staticmethod
    def _verify(
        kind: str, ok: bool, body: dict[str, Any]
    ) -> tuple[bool, dict[str, Any] | None, str | None]:
        """What the device OBSERVED, or nothing: a delete verified by ``observed.exists``
        false, everything else by an ``after`` record with a hash and ``observed.exists``."""
        if not ok:
            return False, None, None
        backup = body.get("backup") if isinstance(body.get("backup"), dict) else None
        backup_id = str(backup["backup_id"]) if backup and backup.get("backup_id") else None
        observed = body.get("observed") if isinstance(body.get("observed"), dict) else {}
        if kind == MUTATION_KIND_DELETE:
            gone = body.get("trashed") is True and observed.get("exists") is False
            return gone, dict(body.get("file") or {}), backup_id
        after = body.get("after") if isinstance(body.get("after"), dict) else None
        if not after or not after.get("sha256") or not after.get("path"):
            return False, None, backup_id
        exists = observed.get("exists")
        if exists is False:
            return False, None, backup_id
        return True, after, backup_id

    @staticmethod
    def _undo_plan(
        row: FileMutationRow, after: dict[str, Any] | None, backup_id: str | None
    ) -> dict[str, Any] | None:
        if row.kind in (
            MUTATION_KIND_WRITE,
            MUTATION_KIND_EDIT,
            MUTATION_KIND_APPEND,
            MUTATION_KIND_RESTORE,
        ):
            if backup_id:
                return {
                    "capability": CAPABILITY_FILE_RESTORE,
                    "payload": {"backup_id": backup_id, "target_path": row.path_after},
                }
            # A file that did not exist before: undo = to the Recycle Bin, with a backup.
            return {
                "capability": CAPABILITY_FILE_TRASH,
                "payload": {"path": row.path_after, "backup": True},
            }
        if row.kind == MUTATION_KIND_RENAME:
            return {
                "capability": CAPABILITY_FILE_RENAME,
                "payload": {
                    "path": row.path_after,
                    "new_name": _name_of(row.path_before or row.name),
                },
            }
        if row.kind == MUTATION_KIND_MOVE:
            return {
                "capability": CAPABILITY_FILE_MOVE,
                "payload": {
                    "path": row.path_after,
                    "destination_dir": _dir_of(row.path_before or ""),
                },
            }
        if row.kind == MUTATION_KIND_COPY:
            return {
                "capability": CAPABILITY_FILE_TRASH,
                "payload": {"path": row.path_after, "backup": True},
            }
        if row.kind == MUTATION_KIND_DELETE and backup_id:
            return {"capability": CAPABILITY_FILE_RESTORE, "payload": {"backup_id": backup_id}}
        return None

    def _settle_index(
        self, db: Session, row: FileMutationRow, after: dict[str, Any] | None
    ) -> None:
        """The index stops describing content that is no longer there."""
        if not row.file_id:
            return
        index = self._documents._index
        existing = index.get_by_file_id(db, device_id=row.device_id, file_id=row.file_id)
        if existing is None:
            return
        if row.kind in (
            MUTATION_KIND_WRITE,
            MUTATION_KIND_EDIT,
            MUTATION_KIND_APPEND,
            MUTATION_KIND_DELETE,
            MUTATION_KIND_RESTORE,
        ):
            db.delete(existing)
        elif row.kind in (MUTATION_KIND_RENAME, MUTATION_KIND_MOVE) and after:
            existing.file_id = str(after.get("file_id") or existing.file_id)
            existing.path = str(after.get("path") or existing.path)
            existing.name = str(after.get("name") or existing.name)
        db.commit()

    @staticmethod
    def _applied_speech(row: FileMutationRow) -> str:
        name = spoken_file_name(row.name)
        short = lambda sha: (sha or "")[:8]  # noqa: E731 - one-line helper for the sentence
        if row.kind == MUTATION_KIND_WRITE and row.sha_before is None:
            return (
                f"{name} dosyasını oluşturdum efendim ({row.size_after or 0} bayt, "
                f"özet {short(row.sha_after)}); geri alınabilir."
            )
        if row.kind in (MUTATION_KIND_WRITE, MUTATION_KIND_EDIT):
            return (
                f"{name} dosyasını değiştirdim efendim; özet {short(row.sha_before)} yerine "
                f"{short(row.sha_after)}, eski hâli yedekte, geri alınabilir."
            )
        if row.kind == MUTATION_KIND_APPEND:
            return (
                f"{name} dosyasının sonuna ekledim efendim; özet {short(row.sha_before)} "
                f"yerine {short(row.sha_after)}, geri alınabilir."
            )
        if row.kind == MUTATION_KIND_RENAME:
            return (
                f"{name} dosyasının adını {spoken_file_name(_name_of(row.path_after or ''))} "
                f"yaptım efendim; geri alınabilir."
            )
        if row.kind == MUTATION_KIND_MOVE:
            return f"{name} dosyasını taşıdım efendim; yeni yeri {row.path_after}. Geri alınabilir."
        if row.kind == MUTATION_KIND_COPY:
            return (
                f"{name} dosyasını kopyaladım efendim: "
                f"{spoken_file_name(_name_of(row.path_after or ''))}. Geri alınabilir."
            )
        if row.kind == MUTATION_KIND_DELETE:
            return (
                f"{name} dosyasını çöp kutusuna gönderdim efendim; yedeği duruyor, geri alınabilir."
            )
        if row.kind == MUTATION_KIND_RESTORE:
            return (
                f"Geri aldım efendim: {name} eski hâline döndü "
                f"(özet {short(row.sha_after)} doğrulandı)."
            )
        return f"{name} için değişikliği uyguladım efendim."

    @staticmethod
    def _failure_speech(row: FileMutationRow, error_class: str | None, message: str | None) -> str:
        name = spoken_file_name(row.name)
        if error_class == "validation_error" and message and "changed since" in message:
            return (
                f"{name} dosyası okuduğumdan beri değişmiş efendim; üstüne yazmadım. "
                f"Yeniden okuyup tekrar deneyebilirim."
            )
        if error_class == "validation_error" and message and "already exists" in message:
            return "O adda bir dosya zaten var efendim; üstüne yazmadım."
        if error_class == "permission_denied":
            return f"{name} için bu değişikliğe iznim yok efendim; hiçbir şeye dokunmadım."
        if error_class == "unsupported_format":
            return SPEECH_NOT_TEXT
        if error_class == "not_found":
            return f"{name} dosyasını yerinde bulamadım efendim; değişiklik yapılmadı."
        return (
            f"{name} için değişikliği uygulayamadım efendim; cihaz {error_class or 'bir hata'} "
            f"dedi. Hiçbir şeye dokunulmadı."
        )

    # ---------------------------------------------------------- discard / undo

    def discard(
        self, db: Session, *, mutation_id: str | None, session_id: str | None
    ) -> dict[str, Any]:
        row = (
            db.get(FileMutationRow, uuid.UUID(mutation_id))
            if mutation_id
            else self.current_pending(db, session_id=session_id)
        )
        if row is None or row.state != MUTATION_STATE_PROPOSED:
            return self._refused(
                db,
                capability="document.discard",
                speech=SPEECH_NOTHING_PENDING,
                error_class=ERROR_NOTHING_PENDING,
                session_id=session_id,
            )
        row.state = MUTATION_STATE_DISCARDED
        db.commit()
        self._documents._ledger(
            db,
            event_type=EVENT_TYPE_DOCUMENT_MUTATION_DISCARDED,
            action="document.discard",
            summary=f"document.discard -> {row.kind} {row.name}",
            detail={"mutation_id": str(row.id)},
        )
        return self._documents._receipt(
            capability="document.discard",
            requested_state="discarded",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"mutation_id": str(row.id), "state": row.state},
            speech=f"Vazgeçtim efendim; {spoken_file_name(row.name)} dosyasına dokunmadım.",
            db=db,
            session_id=session_id,
            extra={"mutation_id": str(row.id), "state": row.state, "kind": row.kind},
        )

    def last_applied(self, db: Session) -> FileMutationRow | None:
        stmt = (
            select(FileMutationRow)
            .where(
                FileMutationRow.state == MUTATION_STATE_APPLIED,
                FileMutationRow.kind != MUTATION_KIND_RESTORE,
            )
            .order_by(FileMutationRow.applied_at.desc())
        )
        return db.execute(stmt).scalars().first()

    def undo(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        mutation_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        """ "Son değişikliği geri al." (160): the inverse plan, run and read back; the
        original row becomes ``undone`` and the restore is a row of its own."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        if (off := self._disabled(db, "document.undo", session_id)) is not None:
            return off
        row = (
            db.get(FileMutationRow, uuid.UUID(mutation_id))
            if mutation_id
            else self.last_applied(db)
        )
        if row is None or row.state != MUTATION_STATE_APPLIED or not row.undo_json:
            return self._refused(
                db,
                capability="document.undo",
                speech=SPEECH_NOTHING_TO_UNDO,
                error_class=ERROR_NOTHING_TO_UNDO,
                session_id=session_id,
            )
        undo = dict(row.undo_json)
        restore = FileMutationRow(
            id=uuid.uuid4(),
            device_id=row.device_id,
            session_id=session_id,
            kind=MUTATION_KIND_RESTORE,
            state=MUTATION_STATE_PROPOSED,
            risk=MUTATION_RISK_LOW,
            file_id=None,
            name=row.name,
            path_before=row.path_after,
            sha_before=row.sha_after,
            size_before=row.size_after,
            plan_json=undo,
            summary=f"{spoken_file_name(row.name)} için son değişikliği geri alacağım efendim.",
            undo_of=row.id,
            created_at=datetime.now(UTC),
            read_back_at=datetime.now(UTC),
            read_back_session_id=session_id,
        )
        db.add(restore)
        db.commit()
        db.refresh(restore)
        outcome = self._apply_restore(db, device, restore, undo, session_id=session_id)
        if restore.state == MUTATION_STATE_APPLIED:
            row.state = MUTATION_STATE_UNDONE
            row.undone_at = datetime.now(UTC)
            db.commit()
            self._documents._ledger(
                db,
                event_type=EVENT_TYPE_DOCUMENT_MUTATION_UNDONE,
                action="document.undo",
                summary=f"document.undo -> {row.kind} {row.name}",
                detail={"mutation_id": str(row.id), "restore_id": str(restore.id)},
            )
        return outcome

    def _apply_restore(
        self,
        db: Session,
        device: DeviceActionPort,
        restore: FileMutationRow,
        undo: dict[str, Any],
        *,
        session_id: str | None,
    ) -> dict[str, Any]:
        cap = str(undo.get("capability") or "")
        outcome = device.run(
            capability=cap,
            payload=dict(undo.get("payload") or {}),
            idempotency_key=f"document-mutation-undo:{restore.id}",
            timeout_s=DEVICE_TIMEOUT_S,
        )
        body = dict(outcome.result or {})
        restore.result_json = body
        kind_for_verify = (
            MUTATION_KIND_DELETE if cap == CAPABILITY_FILE_TRASH else MUTATION_KIND_RESTORE
        )
        verified, after, backup_id = self._verify(kind_for_verify, outcome.ok, body)
        if not verified:
            restore.state = MUTATION_STATE_FAILED
            restore.error_class = outcome.error_class or ERROR_NOT_VERIFIED
            restore.error_message = (
                outcome.message or "the device did not answer a verified record"
            )[:1024]
            db.commit()
            return self._documents._receipt(
                capability="document.undo",
                requested_state="undone",
                execution=EXECUTION_FAILED,
                terminal=TERMINAL_FAILED,
                server={
                    "mutation_id": str(restore.undo_of),
                    "restore_id": str(restore.id),
                    "device": {"error_class": outcome.error_class, "message": outcome.message},
                },
                speech=(
                    f"Geri alamadım efendim; cihaz {outcome.error_class or 'bir hata'} dedi. "
                    f"Dosya olduğu gibi duruyor."
                ),
                db=db,
                error_class=restore.error_class,
                session_id=session_id,
                extra={
                    "mutation_id": str(restore.undo_of),
                    "restore_id": str(restore.id),
                    "state": restore.state,
                },
            )
        restore.state = MUTATION_STATE_APPLIED
        restore.applied_at = datetime.now(UTC)
        restore.path_after = after.get("path") if after else None
        restore.sha_after = after.get("sha256") if after else None
        restore.size_after = after.get("size") if after else None
        restore.backup_id = backup_id
        db.commit()
        name = spoken_file_name(restore.name)
        if cap == CAPABILITY_FILE_TRASH:
            speech = f"Geri aldım efendim: {name} çöp kutusuna gönderildi, yedeği duruyor."
        else:
            speech = (
                f"Geri aldım efendim: {name} eski hâline döndü "
                f"(özet {(restore.sha_after or '')[:8]} doğrulandı)."
            )
        return self._documents._receipt(
            capability="document.undo",
            requested_state="undone",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={
                "mutation_id": str(restore.undo_of),
                "restore_id": str(restore.id),
                "sha_after": restore.sha_after,
                "path_after": restore.path_after,
            },
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "mutation_id": str(restore.undo_of),
                "restore_id": str(restore.id),
                "state": restore.state,
                "sha_after": restore.sha_after,
            },
        )

    # --------------------------------------------------------------- versions

    def versions(
        self,
        db: Session,
        device: DeviceActionPort | None,
        *,
        target: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        """ "Bu dosyanın sürüm geçmişi." (164): the journal rows for the file's path,
        newest first."""
        device_id, missing = self._documents._select_device(device)
        if missing is not None:
            return missing
        assert device is not None
        record, clar = self._resolve_existing(db, device, target, session_id=session_id)
        if clar is not None:
            return clar
        assert record is not None
        path = str(record["path"])
        name = str(record["name"])
        stmt = (
            select(FileMutationRow)
            .where((FileMutationRow.path_after == path) | (FileMutationRow.path_before == path))
            .where(FileMutationRow.state.in_((MUTATION_STATE_APPLIED, MUTATION_STATE_UNDONE)))
            .order_by(FileMutationRow.applied_at.desc())
            .limit(20)
        )
        rows = list(db.execute(stmt).scalars().all())
        entries = [self._entry(r) for r in rows]
        if not rows:
            speech = SPEECH_NO_VERSIONS
        else:
            said = "; ".join(
                f"{(r.applied_at or r.created_at).astimezone(UTC).strftime('%d.%m %H:%M')} "
                f"{_kind_word(r.kind)}"
                + (" (geri alındı)" if r.state == MUTATION_STATE_UNDONE else "")
                for r in rows[:5]
            )
            speech = f"{spoken_file_name(name)} için {len(rows)} kayıt var efendim: {said}."
        return self._documents._receipt(
            capability="document.versions",
            requested_state="listed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"path": path, "count": len(rows)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"path": path, "versions": entries},
        )

    @staticmethod
    def _entry(row: FileMutationRow) -> dict[str, Any]:
        return {
            "mutation_id": str(row.id),
            "kind": row.kind,
            "state": row.state,
            "risk": row.risk,
            "name": row.name,
            "path_before": row.path_before,
            "path_after": row.path_after,
            "sha_before": row.sha_before,
            "sha_after": row.sha_after,
            "size_before": row.size_before,
            "size_after": row.size_after,
            "backup_id": row.backup_id,
            "summary": row.summary,
            "error_class": row.error_class,
            "undo_of": str(row.undo_of) if row.undo_of else None,
            "read_back_at": row.read_back_at.isoformat() if row.read_back_at else None,
            "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
            "confirmed_by": row.confirmed_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "undone_at": row.undone_at.isoformat() if row.undone_at else None,
        }

    def entry(self, row: FileMutationRow) -> dict[str, Any]:
        return self._entry(row)


def _kind_word(kind: str) -> str:
    return {
        MUTATION_KIND_WRITE: "yazma",
        MUTATION_KIND_APPEND: "ekleme",
        MUTATION_KIND_EDIT: "düzenleme",
        MUTATION_KIND_RENAME: "yeniden adlandırma",
        MUTATION_KIND_MOVE: "taşıma",
        MUTATION_KIND_COPY: "kopyalama",
        MUTATION_KIND_DELETE: "çöp kutusuna gönderme",
        MUTATION_KIND_RESTORE: "geri alma",
    }.get(kind, kind)


__all__ = [
    "CAPABILITY_FILE_APPEND",
    "CAPABILITY_FILE_COPY",
    "CAPABILITY_FILE_MOVE",
    "CAPABILITY_FILE_RENAME",
    "CAPABILITY_FILE_RESTORE",
    "CAPABILITY_FILE_WRITE",
    "ERROR_MUTATION_DISABLED",
    "ERROR_NOTHING_PENDING",
    "ERROR_NOTHING_TO_UNDO",
    "ERROR_NOT_TEXT",
    "ERROR_NOT_VERIFIED",
    "ERROR_TEXT_NOT_FOUND",
    "MutationService",
    "SPEECH_MUTATION_DISABLED",
    "SPEECH_NOTHING_PENDING",
    "SPEECH_NOTHING_TO_UNDO",
    "SPEECH_NOT_TEXT",
    "is_text_like_name",
    "risk_of",
]
