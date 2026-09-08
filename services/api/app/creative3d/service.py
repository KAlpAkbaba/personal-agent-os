"""``SceneService`` (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): the owner's rule in
one line — "the assistant creates and changes 3D scenes through the tools' own scripting
interfaces, proves every change by reading the scene back from the tool and rendering it,
and never claims control of an application it cannot actually drive".

Mirrors ``app.appfactory.service.AppFactoryService``'s own device-calling shape (device
selection by capability, an ``ActionReceipt`` per call, a ledger row, a bounded
``scene.activity`` UI-state event) — the same "write -> read-back -> speak" discipline
every mutating capability in this codebase follows (docs/M18_ACTION_CONTRACT.md §5.5).

The device calls, all on the ONE device port every M19-M24 family already shares
(DEVICE_PROTOCOL.md §6l's ``projects`` family, extended by the windows-engineer track
with the two 3D runtimes and ``scene.inspect`` — spec §3):

1. ``project.scaffold`` — writes the fixture project: the plan (``ScenePlan.plan_json()``)
   and the FIXED, sha256-pinned driver file (never generated) under the device's 3D
   root. Re-scaffolding the same ``project_id`` overwrites its own files and deletes
   nothing (the same semantics ``app.appfactory`` already documents for its own
   ``project.scaffold``) — so an ``apply()`` on an existing scene rewrites ONLY
   ``plan.json`` with the NEW incremental operations; the driver loads the scene's own
   ``.blend``/Unity scene file from the PREVIOUS run and applies on top of it, never
   replaying history from this service's side.
2. ``project.run`` — runs the driver through the tool's allowlisted runtime
   (``blender.exe -b`` / ``Unity.exe -batchmode``), to completion (a batch script, never
   a long-lived server — DEVICE_PROTOCOL.md §6l's own ``project.run`` shape is for the
   App Factory's dev servers; the 3D runtimes answer with ``{project_id, slug, exit_code,
   duration_ms, log_path, log_tail}`` instead, the windows-engineer track's own contract
   extension). A Unity run that cannot even start for lack of a valid editor licence
   answers ``dependency_unavailable`` with the licensing client's own words (spec §1, §6)
   — never a crash, never "done": :data:`app.creative3d.models.STATE_DEPENDENCY_UNAVAILABLE`
   is its own state, never confused with a genuine ``failed``.
3. ``scene.inspect`` — the tool's own read-back (``out.json`` + the render PNG, bounded):
   what every receipt and this row actually believe. The PNG is validated by an
   INDEPENDENT reader (``app.creative3d.compare.check_render``, ``PIL``) before it is
   ever handed to the object store — spec §4, §7.

Nothing here touches the filesystem while ``create_app`` builds the application object
(the M24 lesson, ADR-0087 addendum 1, restated for this service by name).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.creative3d.compare import CompareResult, check_render, compare
from app.creative3d.models import (
    STATE_APPLIED,
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_FAILED,
    STATE_PLANNED,
    STATE_RENDERED,
    STATE_SCAFFOLDED,
    SceneRow,
)
from app.creative3d.spec import TOOL_UNITY, ScenePlan
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_SCENE_APPLIED,
    EVENT_TYPE_SCENE_CREATED,
    EVENT_TYPE_SCENE_FAILED,
    EVENT_TYPE_SCENE_INSPECTED,
    EVENT_TYPE_SCENE_LISTED,
    EVENT_TYPE_SCENE_RENDERED,
    EVENT_TYPE_SCENE_UNITY_UNAVAILABLE,
    SUBSYSTEM_CREATIVE3D,
)
from app.logging import get_logger
from app.object_store import ObjectStore, validate_object_key
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_SCENE
from app.routines.dispatch import DeviceActionPort
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.creative3d.service")

CAPABILITY_PROJECT_SCAFFOLD = "project.scaffold"
CAPABILITY_PROJECT_RUN = "project.run"
CAPABILITY_SCENE_INSPECT = "scene.inspect"

RUN_COMMAND_KEY: dict[str, str] = {"blender": "blender_run", "unity": "unity_run"}
DRIVER_PATH_BY_TOOL: dict[str, str] = {
    "blender": "blender_driver.py",
    "unity": "Assets/PagentOS/Editor/SceneDriver.cs",
}
_DRIVERS_DIR = Path(__file__).resolve().parent / "drivers"

SPEECH_NO_DEVICE = "Bu bilgisayarda sahne oluşturma yetkisi yok efendim."
SPEECH_NO_SCENE = "Hangi sahne efendim?"
SPEECH_INVALID_PLAN = "Bu sahne isteğini işleyemedim efendim."

ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_VALIDATION = "validation_error"

#: Two scenes at once is the device's own bound (mirrors the App Factory's "two
#: projects running" cap, DEVICE_PROTOCOL.md §6l) — enforced here, on the Cloud Core's
#: own count of non-terminal rows, since the device has no durable memory of "how many
#: 3D scenes has the assistant ever asked for" the way it does for live processes.
MAX_ACTIVE_SCENES = 2

_PRIMITIVE_TR: dict[str, str] = {
    "cube": "Küp",
    "sphere": "Küre",
    "cylinder": "Silindir",
    "plane": "Düzlem",
    "light_sun": "Güneş ışığı",
    "light_point": "Nokta ışık",
    "camera": "Kamera",
}


def _translate_error(error_class: str, message: str = "") -> tuple[str, str]:
    table = {
        "no_capable_device": (SPEECH_NO_DEVICE, ERROR_CAPABILITY_MISSING),
        "permission_denied": ("Bu işlem izin verilen alanın dışında efendim.", "permission_denied"),
        "validation_error": (SPEECH_INVALID_PLAN, ERROR_VALIDATION),
        "timeout": ("Zaman aşımına uğradım efendim.", "timeout"),
    }
    if error_class in table:
        return table[error_class]
    detail = f" ({message})" if message else ""
    return f"Bunu yapamadım efendim{detail}.", error_class or "device_error"


class SceneService:
    def __init__(self, object_store: ObjectStore | None = None) -> None:
        self._object_store = object_store

    # ------------------------------------------------------------- device plumbing

    def _select_device(
        self, device_action: DeviceActionPort | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        if device_action is None:
            return None, self._capability_missing_receipt()
        return "device:default", None

    def _capability_missing_receipt(
        self, *, capability: str = "scene", session_id: str | None = None
    ) -> dict[str, Any]:
        return self._receipt(
            capability=capability,
            requested_state="none",
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
            record_receipt(db, receipt, SUBSYSTEM_CREATIVE3D)
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
                    subsystem=SUBSYSTEM_CREATIVE3D,
                    action=action,
                    factual_summary=summary,
                    occurred_at=datetime.now(UTC),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("creative3d_ledger_failed", action=action)

    def _publish(self, *, tool: str, scene: str, state: str, objects: int | None = None) -> None:
        metadata: dict[str, Any] = {"tool": tool, "scene": scene[:64], "state": state}
        if objects is not None:
            metadata["objects"] = objects
        publish_ui_state(
            UiState.SCENE_ACTIVITY,
            subsystem=SUBSYSTEM_CREATIVE3D,
            label=scene[:64],
            metadata=metadata,
        )

    def _clarification(self, speech: str) -> dict[str, Any]:
        return {"status": "needs_clarification", "speech": speech, "candidates": []}

    def clarification(self, speech: str) -> dict[str, Any]:
        """Public wrapper for ``_clarification`` — ``tools_scene.py`` needs an honest
        clarification for a few cases this service cannot itself detect (no tool word
        resolved at all, an argument shape the model got wrong before any device call),
        the same "the tool layer may ask a clarifying question through the service's
        own vocabulary" seam other families keep internal to themselves only because
        they never need it from outside."""
        return self._clarification(speech)

    def _invalid_argument(
        self,
        *,
        capability: str,
        requested_state: str,
        speech: str,
        db: Session,
        session_id: str | None,
    ) -> dict[str, Any]:
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

    # ----------------------------------------------------------------- resolution

    def resolve_scene(self, db: Session, target: str | None) -> SceneRow | None:
        """ "current"/None -> the durable ``scene`` focus; a literal row id -> that row
        directly; anything else -> the most recently created row (the same "owner's
        words win, else best effort" fallback ``app.appfactory.service.
        AppFactoryService.resolve_project`` already documents)."""
        if target and target not in ("current", "previous"):
            try:
                row = db.get(SceneRow, uuid.UUID(target))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        entry = (
            focus_module.previous(db, FOCUS_KIND_SCENE)
            if target == "previous"
            else focus_module.current(db, FOCUS_KIND_SCENE)
        )
        if entry is not None:
            try:
                row = db.get(SceneRow, uuid.UUID(entry.object_id))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        return db.execute(
            select(SceneRow).order_by(SceneRow.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    # --------------------------------------------------------------- speech / describe

    def _describe(self, plan: ScenePlan, inspection: dict[str, Any]) -> str:
        objects = inspection.get("objects") or []
        count = len(objects)
        meaningful = [op for op in plan.operations if op.op != "inspect"]
        last = meaningful[-1] if meaningful else None
        if last is None or last.op == "create_scene":
            return (
                f"{plan.tool.capitalize()} içinde yeni bir sahne oluşturdum efendim: "
                f"{plan.project}/{plan.scene}."
            )
        if last.op == "add_primitive":
            obj = next((o for o in objects if o.get("name") == last.name), None)
            loc = obj.get("location") if obj else list(last.location)
            loc_txt = f"({round(loc[0])}, {round(loc[1])}, {round(loc[2])})" if loc else "?"
            kind_word = _PRIMITIVE_TR.get(last.kind, last.kind)
            return f"{kind_word} eklendi: {last.name}, {loc_txt}; sahnede {count} nesne."
        if last.op == "transform":
            obj = next((o for o in objects if o.get("name") == last.name), None)
            loc = obj.get("location") if obj else None
            loc_txt = f"({round(loc[0])}, {round(loc[1])}, {round(loc[2])})" if loc else ""
            return f"{last.name} taşındı: {loc_txt} efendim.".strip()
        if last.op == "set_material":
            return f"{last.name} nesnesinin rengi değiştirildi efendim."
        if last.op == "set_camera":
            if last.look_at:
                return f"Kamera {last.look_at} nesnesine çevrildi efendim."
            return f"{last.name} kamerası ayarlandı efendim."
        if last.op == "set_light":
            return f"{last.name} ışığı {round(last.energy)} olarak ayarlandı efendim."
        if last.op == "render":
            render = inspection.get("render") or {}
            width = render.get("width", last.width)
            height = render.get("height", last.height)
            return f"Render aldım efendim: {width}x{height}."
        return f"Sahne güncellendi efendim; sahnede {count} nesne var."

    # ---------------------------------------------------------------------- dispatch

    def _scaffold_and_run(
        self,
        db: Session,
        device_action: DeviceActionPort,
        row: SceneRow,
        plan: ScenePlan,
        session_id: str | None,
    ) -> dict[str, Any] | None:
        """Runs ``project.scaffold`` then ``project.run`` for ``plan``. Returns an
        early-refusal receipt on failure, or ``None`` to continue to ``scene.inspect``."""
        driver_text = (_DRIVERS_DIR / DRIVER_PATH_BY_TOOL[plan.tool].split("/")[-1]).read_text(
            encoding="utf-8"
        )
        slug = f"{plan.project}-{plan.scene}"[:64]
        scaffold_result = device_action.run(
            capability=CAPABILITY_PROJECT_SCAFFOLD,
            payload={
                "project_id": str(row.id),
                "slug": slug,
                "files": [
                    {"path": "plan.json", "text": plan.plan_json()},
                    {"path": DRIVER_PATH_BY_TOOL[plan.tool], "text": driver_text},
                ],
                "manifest": {
                    "entry": DRIVER_PATH_BY_TOOL[plan.tool],
                    "run": {RUN_COMMAND_KEY[plan.tool]: plan.tool},
                },
            },
            idempotency_key=f"creative3d-scaffold:{row.id}",
            timeout_s=30.0,
        )
        if not scaffold_result.ok:
            speech, error_class = _translate_error(
                scaffold_result.error_class, scaffold_result.message
            )
            row.state = STATE_FAILED
            row.error_class = error_class
            row.error_message = scaffold_result.message
            row.updated_at = datetime.now(UTC)
            db.commit()
            self._ledger(
                db,
                event_type=EVENT_TYPE_SCENE_FAILED,
                action="scene.scaffold",
                summary=f"scene.scaffold -> failed ({scaffold_result.error_class})",
                detail={"scene_id": str(row.id), "error_class": scaffold_result.error_class},
            )
            return self._receipt(
                capability="scene.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": scaffold_result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"scene_id": str(row.id)},
            )
        row.root_path = str((scaffold_result.result or {}).get("root_path") or "") or row.root_path
        row.state = STATE_SCAFFOLDED
        db.commit()

        run_result = device_action.run(
            capability=CAPABILITY_PROJECT_RUN,
            payload={"project_id": str(row.id), "command_key": RUN_COMMAND_KEY[plan.tool]},
            idempotency_key=f"creative3d-run:{row.id}:{uuid.uuid4()}",
            timeout_s=300.0,
        )
        if not run_result.ok:
            if plan.tool == TOOL_UNITY and run_result.error_class == "dependency_unavailable":
                row.state = STATE_DEPENDENCY_UNAVAILABLE
                row.error_class = run_result.error_class
                row.error_message = run_result.message
                row.updated_at = datetime.now(UTC)
                db.commit()
                self._ledger(
                    db,
                    event_type=EVENT_TYPE_SCENE_UNITY_UNAVAILABLE,
                    action="scene.run",
                    summary=f"scene.run -> Unity unavailable: {run_result.message}",
                    detail={"scene_id": str(row.id), "message": run_result.message},
                )
                speech = f"Unity lisansı yok: yapamadım efendim. ({run_result.message})"
                return self._receipt(
                    capability="scene.create",
                    requested_state="applied",
                    execution=EXECUTION_REFUSED,
                    terminal=TERMINAL_FAILED,
                    server={"error_class": run_result.error_class, "message": run_result.message},
                    speech=speech,
                    db=db,
                    error_class="dependency_unavailable",
                    session_id=session_id,
                    extra={"scene_id": str(row.id), "state": STATE_DEPENDENCY_UNAVAILABLE},
                )
            speech, error_class = _translate_error(run_result.error_class, run_result.message)
            row.state = STATE_FAILED
            row.error_class = error_class
            row.error_message = run_result.message
            row.updated_at = datetime.now(UTC)
            db.commit()
            self._ledger(
                db,
                event_type=EVENT_TYPE_SCENE_FAILED,
                action="scene.run",
                summary=f"scene.run -> failed ({run_result.error_class})",
                detail={"scene_id": str(row.id), "error_class": run_result.error_class},
            )
            return self._receipt(
                capability="scene.create",
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": run_result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"scene_id": str(row.id)},
            )
        return None

    def _inspect_device(
        self, device_action: DeviceActionPort, row: SceneRow
    ) -> tuple[dict[str, Any], bytes | None, Any]:
        """Calls ``scene.inspect`` and returns ``(inspection, render_bytes,
        raw_result)``. Raises nothing: a failed inspect still returns an empty
        inspection with the failure recorded on ``raw_result``."""
        result = device_action.run(
            capability=CAPABILITY_SCENE_INSPECT,
            payload={"project_id": str(row.id)},
            idempotency_key=f"creative3d-inspect:{row.id}:{uuid.uuid4()}",
            timeout_s=30.0,
        )
        if not result.ok:
            return (
                {"objects": [], "camera": None, "lights": [], "render": None, "errors": []},
                None,
                result,
            )
        payload = result.result or {}
        inspection = dict(payload.get("inspection") or {})
        png_b64 = payload.get("render_png_base64")
        render_bytes = base64.b64decode(png_b64) if isinstance(png_b64, str) and png_b64 else None
        return inspection, render_bytes, result

    def _store_render(self, row: SceneRow, render_bytes: bytes | None) -> None:
        if render_bytes is None or self._object_store is None:
            return
        mismatch = check_render(render_bytes)
        if mismatch is not None:
            # An independent reader refused it (spec §4, §7): never stored, never
            # claimed as a real render.
            return
        import hashlib

        key = validate_object_key(f"scenes/{row.id}/render.png")
        self._object_store.put(key, render_bytes, content_type="image/png")
        row.render_object_key = key
        row.render_sha256 = hashlib.sha256(render_bytes).hexdigest()
        row.render_bytes = len(render_bytes)

    def _finish(
        self,
        db: Session,
        row: SceneRow,
        plan: ScenePlan,
        *,
        capability: str,
        session_id: str | None,
        device_action: DeviceActionPort,
    ) -> dict[str, Any]:
        inspection, render_bytes, inspect_result = self._inspect_device(device_action, row)
        if not inspect_result.ok:
            speech, error_class = _translate_error(
                inspect_result.error_class, inspect_result.message
            )
            row.state = STATE_FAILED
            row.error_class = error_class
            row.updated_at = datetime.now(UTC)
            db.commit()
            return self._receipt(
                capability=capability,
                requested_state="inspected",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": inspect_result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"scene_id": str(row.id)},
            )
        self._store_render(row, render_bytes)
        cmp_result: CompareResult = compare(plan, inspection, render_bytes=render_bytes)
        row.plan_json = plan.as_dict()
        row.inspection_json = inspection
        row.compare_json = cmp_result.as_dict()
        row.state = STATE_RENDERED if row.render_object_key else STATE_APPLIED
        row.updated_at = datetime.now(UTC)
        db.commit()
        focus_module.set_focus(
            db,
            FOCUS_KIND_SCENE,
            str(row.id),
            label=f"{plan.project}/{plan.scene}",
            source="scene_dispatch",
        )
        speech = self._describe(plan, inspection)
        object_count = len(inspection.get("objects") or [])
        self._publish(tool=plan.tool, scene=plan.scene, state=row.state, objects=object_count)
        event_type = (
            EVENT_TYPE_SCENE_RENDERED if row.state == STATE_RENDERED else EVENT_TYPE_SCENE_APPLIED
        )
        self._ledger(
            db,
            event_type=event_type,
            action=capability,
            summary=f"{capability} -> {plan.project}/{plan.scene} ({object_count} objects)",
            detail={"scene_id": str(row.id), "objects": object_count, "compare_ok": cmp_result.ok},
        )
        return self._receipt(
            capability=capability,
            requested_state=row.state,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"objects": object_count, "compare_ok": cmp_result.ok},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "scene_id": str(row.id),
                "state": row.state,
                "inspection": inspection,
                "compare": cmp_result.as_dict(),
                "objects": object_count,
            },
        )

    # ------------------------------------------------------------------------ create

    def create(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        plan: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        assert device_action is not None

        try:
            scene_plan = ScenePlan.model_validate(plan)
        except ValidationError as exc:
            return self._receipt(
                capability="scene.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "invalid_plan"},
                speech=SPEECH_INVALID_PLAN,
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={"error_class": ERROR_VALIDATION, "detail": str(exc)[:500]},
            )

        active = (
            db.execute(
                select(SceneRow).where(
                    SceneRow.state.in_(
                        (STATE_PLANNED, STATE_SCAFFOLDED, STATE_APPLIED, STATE_RENDERED)
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(active) >= MAX_ACTIVE_SCENES:
            return self._invalid_argument(
                capability="scene.create",
                requested_state="scaffolded",
                speech="Aynı anda en fazla iki sahne üzerinde çalışabilirim efendim.",
                db=db,
                session_id=session_id,
            )

        scene_id = uuid.uuid4()
        now = datetime.now(UTC)
        row = SceneRow(
            id=scene_id,
            tool=scene_plan.tool,
            project=scene_plan.project,
            scene=scene_plan.scene,
            label=scene_plan.label,
            device_id=device_id or "device:default",
            state=STATE_PLANNED,
            plan_json=scene_plan.as_dict(),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_SCENE_CREATED,
            action="scene.create",
            summary=f"scene.create -> {scene_plan.tool}:{scene_plan.project}/{scene_plan.scene}",
            detail={"scene_id": str(scene_id), "tool": scene_plan.tool},
        )

        refusal = self._scaffold_and_run(db, device_action, row, scene_plan, session_id)
        if refusal is not None:
            return refusal
        return self._finish(
            db,
            row,
            scene_plan,
            capability="scene.create",
            session_id=session_id,
            device_action=device_action,
        )

    # ------------------------------------------------------------------------- apply

    def apply(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        operations: list[dict[str, Any]],
        capability: str = "scene.add",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        assert device_action is not None
        row = self.resolve_scene(db, target)
        if row is None:
            return self._clarification(SPEECH_NO_SCENE)
        if row.state == STATE_DEPENDENCY_UNAVAILABLE:
            speech = f"Unity lisansı yok: yapamadım efendim. ({row.error_message or ''})".strip()
            return self._invalid_argument(
                capability=capability,
                requested_state="applied",
                speech=speech,
                db=db,
                session_id=session_id,
            )

        try:
            scene_plan = ScenePlan.model_validate(
                {
                    "tool": row.tool,
                    "project": row.project,
                    "scene": row.scene,
                    "operations": operations,
                }
            )
        except ValidationError as exc:
            return self._receipt(
                capability=capability,
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "invalid_plan"},
                speech=SPEECH_INVALID_PLAN,
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={
                    "error_class": ERROR_VALIDATION,
                    "detail": str(exc)[:500],
                    "scene_id": str(row.id),
                },
            )

        refusal = self._scaffold_and_run(db, device_action, row, scene_plan, session_id)
        if refusal is not None:
            return refusal
        return self._finish(
            db,
            row,
            scene_plan,
            capability=capability,
            session_id=session_id,
            device_action=device_action,
        )

    # ------------------------------------------------------------------------ render

    def render(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        width: int = 320,
        height: int = 240,
        engine: str = "workbench",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self.apply(
            db,
            device_action,
            target=target,
            operations=[{"op": "render", "width": width, "height": height, "engine": engine}],
            capability="scene.render",
            session_id=session_id,
        )

    # ----------------------------------------------------------------------- inspect

    def inspect(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        assert device_action is not None
        row = self.resolve_scene(db, target)
        if row is None:
            return self._clarification(SPEECH_NO_SCENE)
        if row.root_path is None:
            return self._invalid_argument(
                capability="scene.inspect",
                requested_state="inspected",
                speech="Henüz bir sahne oluşturmadım efendim.",
                db=db,
                session_id=session_id,
            )
        inspection, render_bytes, inspect_result = self._inspect_device(device_action, row)
        if not inspect_result.ok:
            speech, error_class = _translate_error(
                inspect_result.error_class, inspect_result.message
            )
            return self._receipt(
                capability="scene.inspect",
                requested_state="inspected",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": inspect_result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"scene_id": str(row.id)},
            )
        self._store_render(row, render_bytes)
        row.inspection_json = inspection
        row.updated_at = datetime.now(UTC)
        db.commit()
        objects = inspection.get("objects") or []
        self._ledger(
            db,
            event_type=EVENT_TYPE_SCENE_INSPECTED,
            action="scene.inspect",
            summary=f"scene.inspect -> {row.project}/{row.scene} ({len(objects)} objects)",
            detail={"scene_id": str(row.id), "objects": len(objects)},
        )
        if not objects:
            speech = f"{row.project}/{row.scene} sahnesinde nesne yok efendim."
        else:
            names = ", ".join(o.get("name", "?") for o in objects[:10])
            speech = f"Sahnede {len(objects)} nesne var efendim: {names}."
        return self._receipt(
            capability="scene.inspect",
            requested_state="inspected",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"objects": len(objects)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"scene_id": str(row.id), "inspection": inspection, "objects": len(objects)},
        )

    # ------------------------------------------------------------------------ status

    def status(
        self, db: Session, *, target: str | None, session_id: str | None = None
    ) -> dict[str, Any]:
        row = self.resolve_scene(db, target)
        if row is None:
            return self._clarification(SPEECH_NO_SCENE)
        objects = len((row.inspection_json or {}).get("objects") or [])
        speech = (
            f"{row.project}/{row.scene} sahnesi {row.state} durumunda efendim; {objects} nesne."
        )
        return self._receipt(
            capability="scene.status",
            requested_state=row.state,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"state": row.state, "objects": objects},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"scene_id": str(row.id), "state": row.state, "objects": objects},
        )

    # -------------------------------------------------------------------------- list

    def list(self, db: Session, *, session_id: str | None = None) -> dict[str, Any]:
        rows = list(
            db.execute(select(SceneRow).order_by(SceneRow.created_at.desc()).limit(20))
            .scalars()
            .all()
        )
        self._ledger(
            db,
            event_type=EVENT_TYPE_SCENE_LISTED,
            action="scene.list",
            summary=f"scene.list -> {len(rows)} sahne",
            detail={"count": len(rows)},
        )
        if not rows:
            speech = "Henüz bir sahne oluşturmadım efendim."
        else:
            names = ", ".join(f"{r.tool}:{r.project}/{r.scene} ({r.state})" for r in rows)
            speech = f"Şu sahneler var efendim: {names}."
        scenes = [
            {
                "scene_id": str(r.id),
                "tool": r.tool,
                "project": r.project,
                "scene": r.scene,
                "state": r.state,
            }
            for r in rows
        ]
        return self._receipt(
            capability="scene.list",
            requested_state="listed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(rows)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"scenes": scenes},
        )


__all__ = ["MAX_ACTIVE_SCENES", "RUN_COMMAND_KEY", "SceneService"]
