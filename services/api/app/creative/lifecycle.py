"""The creative run's life after its bytes exist (B43): generation from a prompt (492),
the semantic check (498), undo / redo over the run's own output history (511), "Bu
fotoğrafı düzelt" (512), delivery to the owner's disk as an image ARTIFACT (509, through
B42's provenance and the artifact open path - one delivery mechanism, not a second), and
driving the real application on the delivered file (500, 502, 504).

Module-level functions over the service (the same shape ``app.appfactory.lifecycle_service``
uses): the service owns receipts, the ledger, the focus and the store; this module owns
the sequences.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
)
from app.artifacts import lifecycle as artifact_lifecycle
from app.artifacts import open_service
from app.artifacts.provenance import ACTOR_OWNER_REST, ACTOR_OWNER_VOICE, Actor
from app.creative import drivers
from app.creative.imaging import ERROR_PROVIDER_NOT_CONFIGURED, ImageProviderError
from app.creative.models import STATE_UNVERIFIED, STATE_VERIFIED, CreativeRunRow
from app.creative.spec import MAX_DIMENSION
from app.ledger.vocabulary import (
    EVENT_TYPE_CREATIVE_DELIVERED,
    EVENT_TYPE_CREATIVE_DRIVEN,
    EVENT_TYPE_CREATIVE_GENERATED,
    EVENT_TYPE_CREATIVE_UNDONE,
)
from app.operator.task import STATUS_SUCCEEDED, new_task, run_task
from app.routines.dispatch import DeviceActionPort

ERROR_NOTHING_TO_UNDO = "nothing_to_undo"
ERROR_NOTHING_TO_REDO = "nothing_to_redo"
ERROR_NO_OUTPUT = "no_output"
ERROR_DRIVE_FAILED = "drive_failed"
ERROR_DRIVE_UNSUPPORTED = "drive_unsupported"
MAX_HISTORY = 20
SPEECH_NO_RUN = "Üzerinde çalıştığım bir görsel yok efendim."


# ------------------------------------------------------------------ history (511)


def record_output(row: CreativeRunRow, *, ops: list[str]) -> None:
    """Called by the service after every stored output: the history grows by one entry
    and the pointer moves to it; anything the pointer had undone is dropped (the same
    linear model every editor's undo stack keeps)."""
    history = (
        list(row.history_json or [])[: int(row.history_index or 0) + 1] if row.history_json else []
    )
    if row.output_object_key is None:
        return
    entry = {
        "object_key": row.output_object_key,
        "name": row.output_name,
        "sha256": row.output_sha256,
        "bytes": row.output_bytes,
        "ops": list(ops)[:16],
        "at": datetime.now(UTC).isoformat(),
    }
    if history and history[-1].get("sha256") == entry["sha256"]:
        row.history_json = history
        row.history_index = len(history) - 1
        return
    history.append(entry)
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    row.history_json = history
    row.history_index = len(history) - 1


def _move(
    service: Any, db: Session, *, target: str | None, session_id: str | None, delta: int
) -> dict[str, Any]:
    capability = "creative.undo" if delta < 0 else "creative.redo"
    row = service.resolve_run(db, target)
    if row is None:
        return service.clarification(SPEECH_NO_RUN)
    history = list(row.history_json or [])
    index = int(row.history_index or 0)
    wanted = index + delta
    if not history or wanted < 0 or wanted >= len(history):
        error = ERROR_NOTHING_TO_UNDO if delta < 0 else ERROR_NOTHING_TO_REDO
        speech = (
            "Geri alınacak bir adım yok efendim."
            if delta < 0
            else "Yinelenecek bir adım yok efendim."
        )
        return service._receipt(
            capability=capability,
            requested_state="undone" if delta < 0 else "redone",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": error, "index": index, "length": len(history)},
            speech=speech,
            db=db,
            error_class=error,
            session_id=session_id,
            extra={"run_id": str(row.id)},
        )
    entry = history[wanted]
    row.history_index = wanted
    row.output_object_key = entry.get("object_key")
    row.output_name = entry.get("name")
    row.output_sha256 = entry.get("sha256")
    row.output_bytes = entry.get("bytes")
    row.updated_at = datetime.now(UTC)
    db.commit()
    service._ledger(
        db,
        event_type=EVENT_TYPE_CREATIVE_UNDONE,
        action=capability,
        summary=f"{capability} -> {row.name} step {wanted + 1}/{len(history)}",
        detail={"run_id": str(row.id), "index": wanted, "sha256": row.output_sha256},
    )
    ops = ", ".join(entry.get("ops") or []) or "ilk hâli"
    speech = (
        f"Geri aldım efendim; {row.name} artık {wanted + 1}. adımda ({ops})."
        if delta < 0
        else f"Yineledim efendim; {row.name} artık {wanted + 1}. adımda ({ops})."
    )
    return service._receipt(
        capability=capability,
        requested_state="undone" if delta < 0 else "redone",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={"index": wanted, "length": len(history), "sha256": row.output_sha256},
        speech=speech,
        db=db,
        session_id=session_id,
        extra={"run_id": str(row.id), "index": wanted, "length": len(history)},
    )


def undo(
    service: Any, db: Session, *, target: str | None, session_id: str | None
) -> dict[str, Any]:
    return _move(service, db, target=target, session_id=session_id, delta=-1)


def redo(
    service: Any, db: Session, *, target: str | None, session_id: str | None
) -> dict[str, Any]:
    return _move(service, db, target=target, session_id=session_id, delta=+1)


def history(service: Any, db: Session, *, target: str | None) -> dict[str, Any] | None:
    row = service.resolve_run(db, target)
    if row is None:
        return None
    return {
        "run_id": str(row.id),
        "name": row.name,
        "index": int(row.history_index or 0),
        "entries": [
            {k: v for k, v in entry.items() if k != "object_key"}
            for entry in (row.history_json or [])
        ],
        "semantic": row.semantic_json,
        "artifact_id": str(row.artifact_id) if row.artifact_id else None,
    }


# --------------------------------------------------------------- generation (492)


def generate(
    service: Any,
    db: Session,
    *,
    prompt: str,
    name: str,
    width: int = 1024,
    height: int = 1024,
    tool: str = "paint",
    expectation: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """A run whose first output is the provider's answer to the owner's prompt; the
    local provider refuses by name (``provider_not_configured``), so nothing is ever
    drawn in the prompt's place."""
    width = max(16, min(MAX_DIMENSION, int(width)))
    height = max(16, min(MAX_DIMENSION, int(height)))
    plan = {
        "tool": tool,
        "name": name,
        "source": None,
        "operations": [
            {"op": "generate", "prompt": prompt[:1000], "width": width, "height": height},
            *([{"op": "semantic_check", "expectation": expectation[:200]}] if expectation else []),
            {"op": "export", "format": "png"},
        ],
        "label": prompt[:200],
    }
    receipt = service.create(db, plan=plan, session_id=session_id)
    if receipt.get("execution_status") == EXECUTION_EXECUTED:
        service._ledger(
            db,
            event_type=EVENT_TYPE_CREATIVE_GENERATED,
            action="creative.generate",
            summary=(
                f"creative.generate -> {name} ({width}x{height}) via {service.image_provider.name}"
            ),
            detail={
                "run_id": receipt.get("run_id"),
                "provider": service.image_provider.name,
                "width": width,
                "height": height,
            },
        )
    return receipt


# ------------------------------------------------------------ semantic check (498)


def semantic_check(service: Any, image: bytes, *, expectation: str) -> dict[str, Any]:
    """The vision provider (B29) is asked ONE closed question about the produced bytes;
    without a provider the check is recorded as not run - never as passed."""
    provider = service.vision_provider
    if provider is None:
        return {
            "expectation": expectation,
            "checked": False,
            "ok": None,
            "answer": None,
            "provider": None,
        }
    question = (
        f"Bu görselde şu var mı: {expectation}? Yalnızca 'evet' ya da 'hayır' ile başla, "
        "sonra bir cümleyle ne gördüğünü söyle."
    )
    try:
        answer = provider.describe(image, question=question)
    except Exception as exc:  # noqa: BLE001 - a provider failure is a fact, not a crash
        return {
            "expectation": expectation,
            "checked": False,
            "ok": None,
            "answer": None,
            "provider": provider.name,
            "error": str(exc)[:200],
        }
    text = str(getattr(answer, "text", answer) or "").strip()
    lowered = text.casefold()
    ok = lowered.startswith("evet") or lowered.startswith("yes")
    return {
        "expectation": expectation,
        "checked": True,
        "ok": ok,
        "answer": text[:500],
        "provider": provider.name,
        "model": getattr(answer, "model", None),
    }


def apply_semantic_verdict(row: CreativeRunRow, verdict: dict[str, Any]) -> None:
    row.semantic_json = verdict
    if verdict.get("checked") and verdict.get("ok") is False and row.state == STATE_VERIFIED:
        row.state = STATE_UNVERIFIED


# ---------------------------------------------------------------- photo fix (512)


def enhance(
    service: Any,
    db: Session,
    *,
    target: str | None,
    kind: str = "auto",
    session_id: str | None = None,
) -> dict[str, Any]:
    """ "Bu fotoğrafı düzelt": the local provider's auto enhancement on the run in focus."""
    row = service.resolve_run(db, target)
    if row is None:
        return service.clarification("Hangi fotoğrafı düzelteyim efendim? Önce açalım.")
    before = row.output_sha256
    result = service.apply(
        db,
        target=str(row.id),
        operations=[{"op": "enhance", "kind": kind}, {"op": "export", "format": "png"}],
        capability="creative.enhance",
        session_id=session_id,
    )
    if result.get("execution_status") == "executed":
        # A photo that needs no fix comes back byte-identical, and the history keeps no
        # empty step - so the owner must not hear that it was fixed.
        changed = row.output_sha256 != before
        result["changed"] = changed
        if not changed:
            result["speech"] = (
                f"{row.name} zaten dengeli görünüyor efendim; düzeltecek bir şey bulmadım."
            )
    return result


# ------------------------------------------------------------------ delivery (509)


def deliver(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    base_url: str,
    session_id: str | None,
    application: str | None = None,
    actor_kind: str = ACTOR_OWNER_VOICE,
) -> dict[str, Any]:
    """The run's current output becomes an image artifact (B42: its own copy, provenance
    naming this run) and is fetched onto the owner's disk through the artifact open path
    (``file.fetch``: hash-checked, Downloads, opened - in ``application`` when named)."""
    row = service.resolve_run(db, target)
    if row is None:
        return service.clarification(SPEECH_NO_RUN)
    if not row.output_object_key or not service._object_store.exists(row.output_object_key):
        return service._receipt(
            capability="creative.deliver",
            requested_state="delivered",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NO_OUTPUT},
            speech=f"{row.name} için teslim edilecek bir çıktı yok efendim.",
            db=db,
            error_class=ERROR_NO_OUTPUT,
            session_id=session_id,
            extra={"run_id": str(row.id)},
        )
    actor = Actor(
        actor_kind if actor_kind in (ACTOR_OWNER_VOICE, ACTOR_OWNER_REST) else ACTOR_OWNER_VOICE,
        ref=str(row.id),
        session_id=session_id,
    )
    artifact_id = row.artifact_id
    current_sha = row.output_sha256
    registered = None
    if artifact_id is None or (row.delivery_json or {}).get("sha256") != current_sha:
        registered = artifact_lifecycle.register_image_artifact(
            db,
            service._object_store,
            title=row.label or row.name,
            object_key=row.output_object_key,
            actor=actor,
            sources=[{"creative_run_id": str(row.id), "tool": row.tool, "sha256": current_sha}],
        )
        artifact_id = registered.artifact_id
        row.artifact_id = artifact_id
        row.updated_at = datetime.now(UTC)
        db.commit()
    outcome = open_service.open_artifact(
        db,
        device_action,
        artifact_id=artifact_id,
        fmt=None,
        base_url=base_url,
        idempotency_key=f"creative-deliver:{row.id}:{current_sha}:{uuid.uuid4().hex[:6]}",
        application=application,
    )
    detail = {
        "run_id": str(row.id),
        "artifact_id": str(artifact_id),
        "sha256": current_sha,
        "application": application,
        "state": outcome.state,
        "path": outcome.path,
    }
    if outcome.ok:
        row.delivery_json = {**detail, "at": datetime.now(UTC).isoformat()}
        row.updated_at = datetime.now(UTC)
        db.commit()
        service._ledger(
            db,
            event_type=EVENT_TYPE_CREATIVE_DELIVERED,
            action="creative.deliver",
            summary=f"creative.deliver -> {row.name} as artifact {artifact_id} ({outcome.state})",
            detail=detail,
        )
        speech = (
            f"{row.name} bilgisayarınıza indi ve açıldı efendim."
            if outcome.state == "opened"
            else f"{row.name} bilgisayarınıza indi efendim."
        )
        if outcome.state == "opened" and application == drivers.APP_PAINT:
            speech = f"{row.name} bilgisayarınıza indi ve Paint'te açıldı efendim."
        return service._receipt(
            capability="creative.deliver",
            requested_state="delivered",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED
            if outcome.state in ("opened", "fetched")
            else TERMINAL_UNVERIFIED,
            server=outcome.as_dict(),
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "run_id": str(row.id),
                "artifact_id": str(artifact_id),
                "state": outcome.state,
                "window_title": outcome.window_title,
            },
        )
    return service._receipt(
        capability="creative.deliver",
        requested_state="delivered",
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server=outcome.as_dict(),
        speech=outcome.speech,
        db=db,
        error_class=outcome.error_class,
        session_id=session_id,
        extra={"run_id": str(row.id), "artifact_id": str(artifact_id)},
    )


# ------------------------------------------------------------ driving (500/502/504)


def drive(
    service: Any,
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    target: str | None,
    actions: list[dict[str, Any] | str],
    path: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """The delivered file opened in the run's own application and driven by that
    application's shortcuts; every step's receipt is kept, the first failed step named."""
    row = service.resolve_run(db, target)
    if row is None:
        return service.clarification(SPEECH_NO_RUN)
    path = path or (row.delivery_json or {}).get("path")
    if not path:
        return service._receipt(
            capability="creative.drive",
            requested_state="driven",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NO_OUTPUT},
            speech=f"{row.name} henüz bilgisayarınıza inmedi efendim; önce indireyim.",
            db=db,
            error_class=ERROR_NO_OUTPUT,
            session_id=session_id,
            extra={"run_id": str(row.id)},
        )
    if device_action is None:
        return service._receipt(
            capability="creative.drive",
            requested_state="driven",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "capability_missing"},
            speech="Bu bilgisayarda uygulamayı sürebilecek bir cihaz bağlı değil efendim.",
            db=db,
            error_class="capability_missing",
            session_id=session_id,
            extra={"run_id": str(row.id)},
        )
    try:
        driver = drivers.driver_for(row.tool)
        parsed = [drivers.DriveAction.parse(a) for a in actions]
        steps, drive_session = driver.plan(
            path, parsed, title_hint=str(row.output_name or row.name)
        )
    except drivers.DriverError as exc:
        return service._receipt(
            capability="creative.drive",
            requested_state="driven",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_DRIVE_UNSUPPORTED, "detail": str(exc)[:200]},
            speech=f"{row.tool} bu adımı süremez efendim: {exc}.",
            db=db,
            error_class=ERROR_DRIVE_UNSUPPORTED,
            session_id=session_id,
            extra={"run_id": str(row.id)},
        )
    cap_result = service._capability_result(row.tool)
    if not cap_result.ok:
        return service._invalid_argument(
            capability="creative.drive",
            requested_state="driven",
            speech=service._dependency_unavailable_speech(row.tool, cap_result.detail),
            db=db,
            session_id=session_id,
            error_class="dependency_unavailable",
        )
    task = run_task(
        new_task(goal=f"drive {row.tool}", plan_name="creative.drive", steps=steps), device_action
    )
    receipts = [
        {"step": r.step_name, "capability": r.capability, "ok": r.ok, "error_class": r.error_class}
        for r in task.receipts
    ]
    ok = task.status == STATUS_SUCCEEDED
    failed_step = next((r["step"] for r in receipts if not r["ok"]), None)
    detail = {
        "run_id": str(row.id),
        "tool": row.tool,
        "actions": [a.action for a in parsed],
        "steps": len(steps),
        "ok": ok,
        "failed_step": failed_step,
        "window_title": drive_session.title,
        "captured": drive_session.captured,
    }
    service._ledger(
        db,
        event_type=EVENT_TYPE_CREATIVE_DRIVEN,
        action="creative.drive",
        summary=(
            f"creative.drive -> {row.tool} {len(parsed)} action(s) "
            f"{'ok' if ok else 'failed at ' + str(failed_step)}"
        ),
        detail=detail,
    )
    if ok:
        return service._receipt(
            capability="creative.drive",
            requested_state="driven",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED if drive_session.captured else TERMINAL_UNVERIFIED,
            server={
                "receipts": receipts,
                "window_title": drive_session.title,
                "captured": drive_session.captured,
            },
            speech=(
                f"{row.name} {_tool_tr(row.tool)}'te açıldı ve {len(parsed)} adım "
                "uygulandı efendim; ekranı da yakaladım."
            ),
            db=db,
            session_id=session_id,
            extra={"run_id": str(row.id), "steps": receipts, "window_title": drive_session.title},
        )
    return service._receipt(
        capability="creative.drive",
        requested_state="driven",
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server={"receipts": receipts, "failed_step": failed_step, "error_class": task.error_class},
        speech=(
            f"{_tool_tr(row.tool)} sürülürken '{failed_step}' adımında takıldım efendim; "
            "sonrasını uygulamadım."
        ),
        db=db,
        error_class=task.error_class or ERROR_DRIVE_FAILED,
        session_id=session_id,
        extra={"run_id": str(row.id), "steps": receipts, "failed_step": failed_step},
    )


def _tool_tr(tool: str) -> str:
    return {"paint": "Paint", "photoshop": "Photoshop", "illustrator": "Illustrator"}.get(
        tool, tool
    )


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_capture(encoded: str) -> bytes:
    return base64.b64decode(encoded)


__all__ = [
    "ERROR_DRIVE_FAILED",
    "ERROR_DRIVE_UNSUPPORTED",
    "ERROR_NOTHING_TO_REDO",
    "ERROR_NOTHING_TO_UNDO",
    "ERROR_NO_OUTPUT",
    "ERROR_PROVIDER_NOT_CONFIGURED",
    "ImageProviderError",
    "MAX_HISTORY",
    "apply_semantic_verdict",
    "deliver",
    "drive",
    "enhance",
    "generate",
    "history",
    "record_output",
    "redo",
    "semantic_check",
    "undo",
]
