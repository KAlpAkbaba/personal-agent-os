"""Fetch + open an artifact render on the owner's machine (M22 spec §4, §5, ADR-0085
decision 4): the ONE place both the voice tool (``artifact.open``) and the REST route
(``POST /v1/artifacts/{id}/open``) reach the device's ``file.fetch`` capability, so the
two surfaces can never drift on what "opened" means or which refusal names what.

Never a second file-open path: the device's own M19 ``file.open`` is what runs inside
``file.fetch``'s own ``open: true`` (DEVICE_PROTOCOL.md §6k step 10) — this module only
picks the render, builds the payload and translates the result.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.artifacts import factory, service
from app.artifacts.models import RENDER_STATE_VALID
from app.artifacts.renderers import EXTENSIONS
from app.artifacts.spec import KIND_FORMATS
from app.routines.dispatch import DeviceActionPort

CAPABILITY_FILE_FETCH = "file.fetch"

#: Refusal codes. Named to match ``apps/web``'s own courtesy table
#: (``ARTIFACT_OPEN_REFUSAL_TR``, ADR-0085 addendum 2) wherever the two tracks overlap,
#: so the Cockpit and the voice answer say the same thing about the same failure —
#: addendum 2 item 6 leaves the exact spellings to this track to choose.
ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_NOT_FOUND = "not_found"
ERROR_NO_VALID_RENDER = "no_valid_render"
ERROR_INVALID_RENDER = "invalid_render"
ERROR_ORIGIN_REFUSED = "origin_refused"
ERROR_HASH_MISMATCH = "hash_mismatch"
ERROR_TOO_LARGE = "too_large"
ERROR_FETCH_FAILED = "fetch_failed"
ERROR_OPEN_FAILED = "open_failed"

_SPEECH: dict[str, str] = {
    ERROR_CAPABILITY_MISSING: "Bu bilgisayardaki ajan dosya getiremiyor efendim.",
    ERROR_NOT_FOUND: "Böyle bir çıktı yok efendim.",
    ERROR_NO_VALID_RENDER: "Doğrulanmış bir çıktı yok efendim; doğrulanmamış bir dosyayı açmam.",
    ERROR_INVALID_RENDER: "Bu format doğrulanamadı efendim; açmadım.",
    ERROR_ORIGIN_REFUSED: "Dosya yalnızca bu sunucunun kendi adresinden getirilir efendim.",
    ERROR_HASH_MISMATCH: "İndirilen dosyanın özeti kayıttakiyle eşleşmedi efendim; tutmadım.",
    ERROR_TOO_LARGE: "Dosya boyut sınırını aşıyor efendim.",
    ERROR_FETCH_FAILED: "Dosyayı cihaza getiremedim efendim.",
    ERROR_OPEN_FAILED: "Dosya cihaza geldi ama açamadım efendim.",
}

#: A real device's ``file.fetch`` error classes (DEVICE_PROTOCOL.md §6k), translated to
#: this module's own refusal codes — the same "no_capable_device -> capability_missing"
#: mapping ``app.documents.service._translate_error`` already uses for the sibling family.
_DEVICE_ERROR_TRANSLATION: dict[str, str] = {
    "no_capable_device": ERROR_CAPABILITY_MISSING,
    "permission_denied": ERROR_ORIGIN_REFUSED,
    "postcondition_failed": ERROR_HASH_MISMATCH,
    "validation_error": ERROR_TOO_LARGE,
    "timeout": ERROR_FETCH_FAILED,
    "dependency_unavailable": ERROR_FETCH_FAILED,
    "cancelled": ERROR_FETCH_FAILED,
}

_UNSAFE_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_NAME_STEM = 80


def _safe_file_name(title: str, fmt: str) -> str:
    """A plain, bounded file name for the device's own ``name`` field. Best-effort only
    — DEVICE_PROTOCOL.md §6k step 2 sanitises (and can still refuse) for real; this pass
    just keeps the common case free of characters no file system accepts."""
    stem = unicodedata.normalize("NFC", title or "artifact").strip()
    stem = _UNSAFE_NAME_CHARS_RE.sub(" ", stem)
    stem = " ".join(stem.split())[:_MAX_NAME_STEM].strip(" .") or "artifact"
    ext = EXTENSIONS.get(fmt, fmt)
    return f"{stem}.{ext}"


@dataclass(frozen=True, slots=True)
class OpenOutcome:
    ok: bool
    #: "opened" | "fetched" | None (never invented: None means the device said nothing
    #: this module could read as either).
    state: str | None
    speech: str
    error_class: str | None = None
    window_title: str | None = None
    artifact_id: str | None = None
    format: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state,
            "speech": self.speech,
            "error_class": self.error_class,
            "window_title": self.window_title,
            "artifact_id": self.artifact_id,
            "format": self.format,
        }


def _pick_render(
    session: Session, *, artifact: Any, version: Any, fmt: str | None
) -> tuple[Any, str | None]:
    """(row, error_class) — the render this open should use: the NAMED format when one
    was given (refused, never silently substituted, when that one render is invalid or
    missing — ADR-0085 §3), else the first valid render in the kind's own format order
    (``KIND_FORMATS``, spec §1's table), else ``no_valid_render``."""
    if fmt:
        row = service.get_render(session, version.id, fmt)
        if row is None:
            return None, ERROR_NOT_FOUND
        if row.state != RENDER_STATE_VALID:
            return None, ERROR_INVALID_RENDER
        return row, None
    rows = {r.format: r for r in service.list_renders(session, version.id)}
    for candidate in KIND_FORMATS.get(artifact.kind, ()):
        row = rows.get(candidate)
        if row is not None and row.state == RENDER_STATE_VALID:
            return row, None
    return None, ERROR_NO_VALID_RENDER


def open_artifact(
    session: Session,
    device_action: DeviceActionPort | None,
    *,
    artifact_id: uuid.UUID,
    fmt: str | None,
    base_url: str,
    idempotency_key: str | None = None,
    timeout_s: float = 30.0,
) -> OpenOutcome:
    """Fetch + open one artifact's render on the owner's machine (spec §4). ``base_url``
    is THIS request's own origin (never a configured URL — ADR-0069's rule, the same one
    the M13 download URL already follows): the device only ever fetches the origin it
    dialled, and the URL built here is that same M13 render-download route."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        return OpenOutcome(False, None, _SPEECH[ERROR_NOT_FOUND], ERROR_NOT_FOUND)
    version = service.get_current_version(session, artifact.id)
    if version is None:
        return OpenOutcome(
            False, None, _SPEECH[ERROR_NOT_FOUND], ERROR_NOT_FOUND, artifact_id=str(artifact_id)
        )
    row, error_class = _pick_render(session, artifact=artifact, version=version, fmt=fmt)
    if row is None:
        assert error_class is not None
        return OpenOutcome(
            False,
            None,
            _SPEECH[error_class],
            error_class,
            artifact_id=str(artifact_id),
            format=fmt,
        )
    if device_action is None:
        return OpenOutcome(
            False,
            None,
            _SPEECH[ERROR_CAPABILITY_MISSING],
            ERROR_CAPABILITY_MISSING,
            artifact_id=str(artifact_id),
            format=row.format,
        )
    name = _safe_file_name(artifact.title, row.format)
    url = f"{base_url.rstrip('/')}{factory.download_path(artifact_id, row.format)}"
    key = idempotency_key or f"artifact-open:{artifact_id}:{row.format}:{uuid.uuid4()}"
    result = device_action.run(
        capability=CAPABILITY_FILE_FETCH,
        payload={
            "url": url,
            "name": name,
            "sha256": row.content_hash,
            "size": row.size_bytes,
            "open": True,
        },
        idempotency_key=key,
        timeout_s=timeout_s,
    )
    if not result.ok:
        translated = _DEVICE_ERROR_TRANSLATION.get(result.error_class, ERROR_FETCH_FAILED)
        return OpenOutcome(
            False,
            None,
            _SPEECH[translated],
            translated,
            artifact_id=str(artifact_id),
            format=row.format,
        )
    payload = result.result or {}
    opened = payload.get("opened")
    if isinstance(opened, dict) and opened.get("opened") is False:
        # DEVICE_PROTOCOL.md §6k step 10: a fetch that succeeded but could not open
        # reports the refusal INSIDE ``opened`` and keeps the file — the fetch DID
        # happen, so this is "fetched", not a failure of the fetch itself.
        return OpenOutcome(
            True,
            "fetched",
            f"{_SPEECH[ERROR_OPEN_FAILED]}",
            ERROR_OPEN_FAILED,
            artifact_id=str(artifact_id),
            format=row.format,
        )
    window_title: str | None = None
    state = "fetched"
    if isinstance(opened, dict) and opened.get("opened"):
        state = "opened"
        observed = opened.get("observed") or {}
        window = observed.get("window") if isinstance(observed, dict) else None
        if isinstance(window, dict):
            window_title = window.get("title")
    speech = "Açtım efendim." if state == "opened" else "Dosyayı cihaza getirdim efendim."
    if window_title:
        speech = f"{speech} Pencere: {window_title}."
    return OpenOutcome(
        True,
        state,
        speech,
        None,
        window_title=window_title,
        artifact_id=str(artifact_id),
        format=row.format,
    )


__all__ = [
    "CAPABILITY_FILE_FETCH",
    "ERROR_CAPABILITY_MISSING",
    "ERROR_FETCH_FAILED",
    "ERROR_HASH_MISMATCH",
    "ERROR_INVALID_RENDER",
    "ERROR_NOT_FOUND",
    "ERROR_NO_VALID_RENDER",
    "ERROR_OPEN_FAILED",
    "ERROR_ORIGIN_REFUSED",
    "ERROR_TOO_LARGE",
    "OpenOutcome",
    "open_artifact",
]
