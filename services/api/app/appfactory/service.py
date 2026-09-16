"""``AppFactoryService`` (docs/M23_APP_FACTORY_SPEC.md §1-§4, ADR-0086): the owner's rule
in one line — "an app the assistant made exists when it has been scaffolded into a real
project on the owner's machine, run there in a bounded process, exercised through the
browser, and its own tests have passed".

Mirrors ``app.documents.service.DocumentService``'s own device-calling shape (device
selection by capability, an ``ActionReceipt`` per call, a ledger row, a bounded
``app.factory`` UI-state event) — the same "write -> read-back -> speak" discipline every
mutating capability in this codebase follows (docs/M18_ACTION_CONTRACT.md §5.5).

Generation + validation (``app.appfactory.generator`` / ``.validation``) run BEFORE any
device is asked or any ``app_projects`` row is written — a spec that fails either never
reaches the device, and no row exists to clean up.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.appfactory import composed_service, lifecycle_service
from app.appfactory.code_model import CodeModel, ModelAssistedGenerator
from app.appfactory.composer import TEMPLATE_COMPOSED
from app.appfactory.generator import AppGenerator, AppGeneratorError, DeterministicAppGenerator
from app.appfactory.models import (
    STATE_FAILED,
    STATE_PLANNED,
    STATE_RUNNING,
    STATE_SCAFFOLDED,
    STATE_STOPPED,
    STATE_TESTED,
    AppProjectRow,
)
from app.appfactory.oracles import load_oracle
from app.appfactory.spec import KIND_WEB_API, KIND_WEB_STATIC, AppSpec
from app.appfactory.validation import AppValidationError, validate
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_APP_PROJECT_CREATED,
    EVENT_TYPE_APP_PROJECT_EXERCISED,
    EVENT_TYPE_APP_PROJECT_FAILED,
    EVENT_TYPE_APP_PROJECT_FIXED,
    EVENT_TYPE_APP_PROJECT_LISTED,
    EVENT_TYPE_APP_PROJECT_RUN,
    EVENT_TYPE_APP_PROJECT_SCAFFOLDED,
    EVENT_TYPE_APP_PROJECT_STOPPED,
    EVENT_TYPE_APP_PROJECT_TESTED,
    SUBSYSTEM_APPFACTORY,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_PROJECT
from app.research.browser_gateway import BrowserGateway, FetchQuery
from app.routines.dispatch import DeviceActionPort
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

logger = get_logger("app.appfactory.service")

CAPABILITY_PROJECT_SCAFFOLD = "project.scaffold"
CAPABILITY_PROJECT_RUN = "project.run"
CAPABILITY_PROJECT_STATUS = "project.status"
CAPABILITY_PROJECT_STOP = "project.stop"
CAPABILITY_PROJECT_TEST = "project.test"
CAPABILITY_FILE_REVEAL = "file.reveal"

#: The manifest run command KEY every runnable built-in template scaffolds (module
#: docstring of ``app.appfactory.validation``: the device payload names a KEY, never a
#: command line). Only ``web_static``/``web_api`` kinds are runnable at all.
RUN_COMMAND_KEY = "serve"
TEST_COMMAND_KEY = "unit"

SPEECH_NO_DEVICE = "Bu bilgisayarda uygulama oluşturma yetkisi yok efendim."
SPEECH_NO_PROJECT = "Hangi uygulama efendim?"
SPEECH_NOT_SCAFFOLDED = "Önce bir uygulama oluşturmalıyım efendim."
SPEECH_NOT_RUNNABLE = "Bu tür bir uygulama tarayıcıda çalıştırılmaz efendim."
SPEECH_NOT_RUNNING = "Önce uygulamayı çalıştırmalıyım efendim."

ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_VALIDATION = "validation_error"

_RUNNABLE_KINDS = (KIND_WEB_STATIC, KIND_WEB_API)

#: Two projects running at once (device table, spec §3) is the DEVICE's own bound; this
#: constant only names the truthful refusal word this service echoes when a device
#: reports it (never enforced twice — the device is the single source of truth for how
#: many of its own jobs are alive).
ERROR_TOO_MANY_RUNNING = "too_many_running"


def _translate_error(error_class: str) -> tuple[str, str]:
    table = {
        "no_capable_device": (SPEECH_NO_DEVICE, ERROR_CAPABILITY_MISSING),
        "permission_denied": ("Bu işlem izin verilen alanın dışında efendim.", "permission_denied"),
        "validation_error": ("Bu isteği işleyemedim efendim.", ERROR_VALIDATION),
        "timeout": ("Zaman aşımına uğradım efendim.", "timeout"),
        ERROR_TOO_MANY_RUNNING: (
            "Aynı anda en fazla iki uygulama çalıştırabilirim efendim.",
            ERROR_TOO_MANY_RUNNING,
        ),
    }
    return table.get(
        error_class, (f"Bunu yapamadım efendim ({error_class}).", error_class or "device_error")
    )


class AppFactoryService:
    def __init__(
        self,
        generator: AppGenerator | None = None,
        *,
        composed: ModelAssistedGenerator | None = None,
        code_model: CodeModel | None = None,
        max_fix_attempts: int = 3,
        store: Any = None,
    ) -> None:
        self._generator = generator or DeterministicAppGenerator()
        # B40: the composed generator (deterministic, the model's slots under the flag)
        # and the code model the fix loop asks; None means the loop stops at analysis.
        self._composed = composed or ModelAssistedGenerator()
        self._code_model = code_model
        self._max_fix_attempts = max_fix_attempts
        # B41 (req 441/446): the object store a release artifact is written to.
        self._store = store

    # ------------------------------------------------------------- device plumbing

    def _select_device(
        self, device_action: DeviceActionPort | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        if device_action is None:
            return None, self._capability_missing_receipt()
        # The same stable logical bucket app.documents.service._select_device uses:
        # the real BrokerDeviceAction selects the physical device internally per call.
        return "device:default", None

    def _capability_missing_receipt(
        self, *, capability: str = "project", session_id: str | None = None
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
            record_receipt(db, receipt, SUBSYSTEM_APPFACTORY)
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
                    subsystem=SUBSYSTEM_APPFACTORY,
                    action=action,
                    factual_summary=summary,
                    occurred_at=datetime.now(UTC),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("appfactory_ledger_failed", action=action)

    def _publish(self, *, project_name: str, state: str, port: int | None = None) -> None:
        metadata: dict[str, Any] = {"project": project_name[:64], "state": state}
        if port is not None:
            metadata["port"] = port
        publish_ui_state(
            UiState.APP_FACTORY,
            subsystem=SUBSYSTEM_APPFACTORY,
            label=project_name[:64],
            metadata=metadata,
        )

    # ----------------------------------------------------------------- resolution

    def resolve_project(self, db: Session, target: str | None) -> AppProjectRow | None:
        """ "current" (or None) -> the durable ``project`` focus; a literal project id
        string -> that row directly; anything else -> the most recently created row (a
        convenience fallback the same "owner's words win, else best effort" discipline
        ``app.documents.service`` follows for a bare "bu dosya" with no explicit id)."""
        if target and target not in ("current", "previous"):
            try:
                row = db.get(AppProjectRow, uuid.UUID(target))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        kind = FOCUS_KIND_PROJECT
        entry = (
            focus_module.previous(db, kind)
            if target == "previous"
            else focus_module.current(db, kind)
        )
        if entry is not None:
            try:
                row = db.get(AppProjectRow, uuid.UUID(entry.object_id))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        # Fallback: the most recently created project at all (mirrors "current" when no
        # focus row exists yet, e.g. right after a fresh create in a test harness).
        return db.execute(
            select(AppProjectRow).order_by(AppProjectRow.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    # ---------------------------------------------------------------------- create

    def create(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        spec: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing

        built = self._build(spec, db=db, session_id=session_id)
        if isinstance(built, dict):
            return built
        app_spec, files, manifest, composed_build = built

        project_id = uuid.uuid4()
        now = datetime.now(UTC)
        row = AppProjectRow(
            id=project_id,
            name=app_spec.name,
            kind=app_spec.kind,
            template=app_spec.template,
            spec_json=app_spec.model_dump(mode="json"),
            device_id=device_id or "device:default",
            state=STATE_PLANNED,
            created_at=now,
            updated_at=now,
        )
        if composed_build is not None:
            row.plan_json = composed_build.plan.as_dict()
            row.reports_json = composed_build.reports()
            row.oracle_json = composed_build.oracle
        db.add(row)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_CREATED,
            action="app.project.create",
            summary=f"app.project.create -> {app_spec.name} ({app_spec.template})",
            detail={"project_id": str(project_id), "template": app_spec.template},
        )

        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_PROJECT_SCAFFOLD,
            payload={
                "project_id": str(project_id),
                "slug": app_spec.slug(),
                "files": [{"path": f.path, "text": f.text} for f in files.files],
                "manifest": manifest,
            },
            idempotency_key=f"appfactory-scaffold:{project_id}",
            timeout_s=30.0,
        )
        if not result.ok:
            row.state = STATE_FAILED
            row.updated_at = datetime.now(UTC)
            db.commit()
            speech, error_class = _translate_error(result.error_class)
            self._ledger(
                db,
                event_type=EVENT_TYPE_APP_PROJECT_FAILED,
                action="app.project.scaffold",
                summary=f"app.project.scaffold -> failed ({result.error_class})",
                detail={"project_id": str(project_id), "error_class": result.error_class},
            )
            return self._receipt(
                capability="app.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"project_id": str(project_id)},
            )

        root_path = str((result.result or {}).get("root_path") or "")
        row.root_path = root_path or None
        row.state = STATE_SCAFFOLDED
        row.updated_at = datetime.now(UTC)
        db.commit()
        focus_module.set_focus(
            db, FOCUS_KIND_PROJECT, str(project_id), label=app_spec.name, source="app_create"
        )
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_SCAFFOLDED,
            action="app.project.scaffold",
            summary=f"app.project.scaffold -> {app_spec.name} at {root_path}",
            detail={"project_id": str(project_id), "root_path": root_path},
        )
        self._publish(project_name=app_spec.name, state=STATE_SCAFFOLDED)
        return self._receipt(
            capability="app.create",
            requested_state="scaffolded",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"root_path": root_path},
            speech=f"{app_spec.name} uygulamasını oluşturdum efendim."
            + (
                composed_service.unparsed_sentence(composed_build.requirements)
                if composed_build is not None
                else ""
            ),
            db=db,
            session_id=session_id,
            extra={
                "project_id": str(project_id),
                "state": STATE_SCAFFOLDED,
                "root_path": root_path,
                "reports": composed_build.reports() if composed_build is not None else None,
                "template": app_spec.template,
            },
        )

    # ---------------------------------------------------------------- B40: the build

    def _build(self, spec: dict[str, Any], *, db: Session, session_id: str | None) -> Any:
        """The spec -> (AppSpec, files, manifest, composed build | None), or a refusal
        receipt. A composed request (req 422-434) goes through the requirements parser,
        the planners, the composer, the lint and the security scan; a template request
        goes the way it always did."""
        wants_composed = spec.get("template") == TEMPLATE_COMPOSED or (
            "template" not in spec and bool(spec.get("request") or spec.get("requirements"))
        )
        if wants_composed:
            try:
                build = composed_service.build_composed(spec, generator=self._composed)
            except composed_service.ComposedRefusal as exc:
                clarification = exc.code == "clarification_needed"
                return self._receipt(
                    capability="app.create",
                    requested_state="scaffolded",
                    execution=EXECUTION_REFUSED,
                    terminal=TERMINAL_FAILED,
                    server={"reason": exc.code, **exc.detail},
                    speech=exc.speech,
                    db=db,
                    error_class="clarification_needed" if clarification else ERROR_VALIDATION,
                    session_id=session_id,
                    extra={
                        "error_class": "clarification_needed"
                        if clarification
                        else ERROR_VALIDATION,
                        "code": exc.code,
                        **exc.detail,
                    },
                )
            return build.spec, build.files, build.manifest, build

        try:
            app_spec = AppSpec.model_validate(spec)
        except ValidationError as exc:
            return self._receipt(
                capability="app.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "invalid_spec"},
                speech="Bu uygulama tarifini işleyemedim efendim.",
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={"error_class": ERROR_VALIDATION, "detail": str(exc)[:500]},
            )

        try:
            files = self._generator.generate(app_spec)
        except AppGeneratorError as exc:
            return self._receipt(
                capability="app.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "generation_failed"},
                speech="Bu uygulamayı oluşturamadım efendim.",
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={"error_class": ERROR_VALIDATION, "detail": str(exc)[:500]},
            )

        try:
            _report, manifest = validate(files)
        except AppValidationError as exc:
            return self._receipt(
                capability="app.create",
                requested_state="scaffolded",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": exc.code},
                speech="Bu uygulamayı güvenlik denetiminden geçiremedim efendim.",
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={"error_class": ERROR_VALIDATION, "detail": str(exc)[:500], "code": exc.code},
            )

        return app_spec, files, manifest, None

    # ------------------------------------------------------------- B40: plan and fix

    def plan(self, text: str) -> dict[str, Any]:
        """Req 423/424: the plan the owner may read before anything is written."""
        return composed_service.plan_preview(text)

    def _scaffold_version(
        self,
        db: Session,
        device_action: DeviceActionPort,
        parent: AppProjectRow,
        build: Any,
        version: int,
    ) -> AppProjectRow | None:
        """A fixed application is a NEW project version on the device (the device never
        rewrites a project in place); the row carries its parent and its version."""
        project_id = uuid.uuid4()
        now = datetime.now(UTC)
        row = AppProjectRow(
            id=project_id,
            name=parent.name,
            kind=parent.kind,
            template=parent.template,
            spec_json=build.spec.model_dump(mode="json"),
            device_id=parent.device_id,
            state=STATE_PLANNED,
            plan_json=build.plan.as_dict(),
            reports_json=build.reports(),
            oracle_json=build.oracle,
            version=version,
            parent_id=parent.id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        result = device_action.run(
            capability=CAPABILITY_PROJECT_SCAFFOLD,
            payload={
                "project_id": str(project_id),
                "slug": f"{build.spec.slug()}-v{version}",
                "files": [{"path": f.path, "text": f.text} for f in build.files.files],
                "manifest": build.manifest,
            },
            idempotency_key=f"appfactory-scaffold:{project_id}",
            timeout_s=30.0,
        )
        if not result.ok:
            row.state = STATE_FAILED
            row.updated_at = datetime.now(UTC)
            db.commit()
            return None
        row.root_path = str((result.result or {}).get("root_path") or "") or None
        row.state = STATE_SCAFFOLDED
        row.updated_at = datetime.now(UTC)
        db.commit()
        focus_module.set_focus(
            db, FOCUS_KIND_PROJECT, str(project_id), label=parent.name, source="app_fix"
        )
        return row

    def fix(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 435-437: analyse the failed run, then the bounded fix loop through the
        code model - each attempt a new version scaffolded and tested on the device."""
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if project.test_report_json is None:
            return self._invalid_argument(
                capability="app.fix",
                requested_state="fixed",
                speech="Önce testleri çalıştırmalıyım efendim.",
                db=db,
                session_id=session_id,
            )
        record, latest, analysis = composed_service.run_fix_loop(
            db,
            device_action,  # type: ignore[arg-type]
            project,
            generator=self._composed,
            code_model=self._code_model,
            max_attempts=self._max_fix_attempts,
            scaffold_version=self._scaffold_version,
        )
        project.fix_json = {
            "record": record.as_dict(),
            "analysis": analysis.as_dict(),
            "latest": str(latest.id),
        }
        project.updated_at = datetime.now(UTC)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_FIXED,
            action="app.project.fix",
            summary=f"app.project.fix -> {project.name}: {record.status} ({record.reason})",
            detail={
                "project_id": str(project.id),
                "latest": str(latest.id),
                "status": record.status,
                "attempts": len(record.attempts),
            },
        )
        self._publish(project_name=project.name, state=latest.state)
        fixed = record.status == "fixed"
        if fixed:
            speech = (
                f"{project.name} uygulamasını düzelttim efendim: {record.reason}; "
                f"yeni sürüm {latest.version}."
            )
        elif record.status == "not_needed":
            speech = f"{project.name} testleri zaten geçiyor efendim."
        else:
            speech = f"{project.name}: {analysis.summary}. {record.reason}."
        return self._receipt(
            capability="app.fix",
            requested_state="fixed",
            execution=EXECUTION_EXECUTED
            if fixed or record.status == "not_needed"
            else EXECUTION_REFUSED,
            terminal=TERMINAL_VERIFIED
            if fixed or record.status == "not_needed"
            else TERMINAL_FAILED,
            server={
                "status": record.status,
                "attempts": len(record.attempts),
                "latest": str(latest.id),
            },
            speech=speech,
            db=db,
            error_class=None if fixed or record.status == "not_needed" else record.status,
            session_id=session_id,
            extra={
                "project_id": str(project.id),
                "latest_project_id": str(latest.id),
                "state": latest.state,
                "fix": record.as_dict(),
                "analysis": analysis.as_dict(),
                "version": latest.version,
            },
        )

    # -------------------------------------------------------- B41: the lifecycle

    def _translate(self, error_class: str) -> tuple[str, str]:
        return _translate_error(error_class)

    def verify(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 443/444: the oracle replayed in the device's browser, then a restart."""
        return lifecycle_service.verify(
            self, db, device_action, target=target, session_id=session_id
        )

    def log(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 445: the run log the device captured, read back."""
        return lifecycle_service.log(self, db, device_action, target=target, session_id=session_id)

    def package(
        self,
        db: Session,
        device_action: DeviceActionPort | None = None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 441/446: the release artifact in the object store."""
        del device_action
        return lifecycle_service.package(self, db, target=target, session_id=session_id)

    def launch(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 442: the packaged release scaffolded as its own version and run."""
        return lifecycle_service.launch(
            self, db, device_action, target=target, session_id=session_id
        )

    def history(
        self,
        db: Session,
        device_action: DeviceActionPort | None = None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 447: every version and every event of the project."""
        del device_action
        return lifecycle_service.history(self, db, target=target, session_id=session_id)

    def resume(
        self,
        db: Session,
        device_action: DeviceActionPort | None = None,
        *,
        target: str | None,
        name: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 448: back to the latest version of a project, by name or by focus."""
        del device_action
        return lifecycle_service.resume(self, db, target=target, name=name, session_id=session_id)

    def modify(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        request: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Req 449/450: a later request merged into the requirements, a new version."""
        return lifecycle_service.modify(
            self, db, device_action, target=target, request=request, session_id=session_id
        )

    # ------------------------------------------------------------------------ run

    def run(
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
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if project.root_path is None:
            return self._invalid_argument(
                capability="app.run",
                requested_state="running",
                speech=SPEECH_NOT_SCAFFOLDED,
                db=db,
                session_id=session_id,
            )
        if project.kind not in _RUNNABLE_KINDS:
            return self._invalid_argument(
                capability="app.run",
                requested_state="running",
                speech=SPEECH_NOT_RUNNABLE,
                db=db,
                session_id=session_id,
            )

        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_PROJECT_RUN,
            payload={"project_id": str(project.id), "command_key": RUN_COMMAND_KEY},
            idempotency_key=f"appfactory-run:{project.id}:{uuid.uuid4()}",
            timeout_s=30.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability="app.run",
                requested_state="running",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"project_id": str(project.id)},
            )

        port = (result.result or {}).get("port")
        project.run_port = int(port) if port is not None else None
        project.state = STATE_RUNNING
        project.updated_at = datetime.now(UTC)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_RUN,
            action="app.project.run",
            summary=f"app.project.run -> {project.name} on port {port}",
            detail={"project_id": str(project.id), "port": port},
        )
        self._publish(project_name=project.name, state=STATE_RUNNING, port=project.run_port)
        return self._receipt(
            capability="app.run",
            requested_state="running",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"port": port},
            speech=f"{project.name} uygulamasını {port} portunda çalıştırdım efendim.",
            db=db,
            session_id=session_id,
            extra={"project_id": str(project.id), "state": STATE_RUNNING, "port": project.run_port},
        )

    # ------------------------------------------------------------------- exercise

    def exercise(
        self,
        db: Session,
        browser_gateway: BrowserGateway | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Opens the running project's URL through the EXISTING M13 ``BrowserGateway``
        (no new browser path, ADR-0086 decision 4) and records the template's oracle
        alongside what was actually opened. ``BrowserGateway.fetch_evidence`` proves the
        app is reachable and serving content; the oracle's own selector/interaction
        assertions ("add a task, see it, toggle it, reload") are recorded here as what a
        REAL DOM-capable worker independently proves — the device lab's job (spec §6,
        track B). This service never claims a DOM assertion it cannot itself observe."""
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if project.kind not in _RUNNABLE_KINDS:
            return self._invalid_argument(
                capability="app.open",
                requested_state="opened",
                speech=SPEECH_NOT_RUNNABLE,
                db=db,
                session_id=session_id,
            )
        if not project.run_port:
            # ``run_port`` (never ``state``) is the truth of "is a process up right
            # now": ``test()`` moves ``state`` on to "tested"/"failed" as a lifecycle
            # milestone without touching a still-running process's own port.
            return self._invalid_argument(
                capability="app.open",
                requested_state="opened",
                speech=SPEECH_NOT_RUNNING,
                db=db,
                session_id=session_id,
            )
        if browser_gateway is None:
            return self._capability_missing_receipt(capability="app.open", session_id=session_id)

        url = f"http://127.0.0.1:{project.run_port}/"
        # B40 (req 434): a composed application carries its own oracle on the row.
        oracle = project.oracle_json or load_oracle(project.template)
        try:
            records = browser_gateway.fetch_evidence(
                [FetchQuery(query=url, source_class="app_factory", max_results=1)]
            )
        except Exception as exc:  # noqa: BLE001 - the gateway's own failure, not ours
            return self._receipt(
                capability="app.open",
                requested_state="opened",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "browser_error"},
                speech="Uygulamayı tarayıcıda açamadım efendim.",
                db=db,
                error_class="device_error",
                session_id=session_id,
                extra={"project_id": str(project.id), "detail": str(exc)[:300]},
            )
        opened = bool(records and records[0].excerpt)
        if not opened:
            return self._receipt(
                capability="app.open",
                requested_state="opened",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "empty_response"},
                speech="Uygulamayı tarayıcıda açamadım efendim.",
                db=db,
                error_class="device_error",
                session_id=session_id,
                extra={"project_id": str(project.id)},
            )
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_EXERCISED,
            action="app.project.exercise",
            summary=f"app.project.exercise -> {project.name} opened at {url}",
            detail={"project_id": str(project.id), "url": url, "oracle": oracle.get("template")},
        )
        return self._receipt(
            capability="app.open",
            requested_state="opened",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"url": url},
            speech=f"{project.name} uygulamasını tarayıcıda açtım efendim.",
            db=db,
            session_id=session_id,
            extra={"project_id": str(project.id), "url": url, "oracle": oracle},
        )

    # ----------------------------------------------------------------------- test

    def test(
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
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if project.root_path is None:
            return self._invalid_argument(
                capability="app.test",
                requested_state="tested",
                speech=SPEECH_NOT_SCAFFOLDED,
                db=db,
                session_id=session_id,
            )

        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_PROJECT_TEST,
            payload={"project_id": str(project.id), "command_key": TEST_COMMAND_KEY},
            idempotency_key=f"appfactory-test:{project.id}:{uuid.uuid4()}",
            timeout_s=60.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability="app.test",
                requested_state="tested",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"project_id": str(project.id)},
            )

        report = dict(result.result or {})
        passed = int(report.get("passed") or 0)
        failed = int(report.get("failed") or 0)
        exit_code = int(report.get("exit_code") or 0)
        ok = exit_code == 0 and failed == 0
        project.test_report_json = report
        project.state = STATE_TESTED if ok else STATE_FAILED
        project.updated_at = datetime.now(UTC)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_TESTED if ok else EVENT_TYPE_APP_PROJECT_FAILED,
            action="app.project.test",
            summary=f"app.project.test -> {project.name}: {passed} passed, {failed} failed",
            detail={"project_id": str(project.id), "passed": passed, "failed": failed},
        )
        self._publish(project_name=project.name, state=project.state)
        if ok:
            speech = f"{project.name} testleri geçti efendim: {passed} test."
        else:
            tail = str(report.get("report_tail") or "")[:200]
            speech = (
                f"{project.name} testleri başarısız efendim: {failed} test başarısız. {tail}"
            ).strip()
        return self._receipt(
            capability="app.test",
            requested_state="tested",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"passed": passed, "failed": failed, "exit_code": exit_code},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "project_id": str(project.id),
                "state": project.state,
                "passed": passed,
                "failed": failed,
            },
        )

    # ----------------------------------------------------------------------- stop

    def stop(
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
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if not project.run_port:
            # ``run_port`` (never ``state``) is the truth of "is a process up right
            # now" — see ``open()``'s identical comment.
            return self._receipt(
                capability="app.stop",
                requested_state="stopped",
                execution=EXECUTION_NOOP,
                terminal=TERMINAL_ALREADY,
                server={"state": project.state},
                speech=f"{project.name} zaten çalışmıyor efendim.",
                db=db,
                session_id=session_id,
                extra={"project_id": str(project.id), "state": project.state},
            )

        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_PROJECT_STOP,
            payload={"project_id": str(project.id)},
            idempotency_key=f"appfactory-stop:{project.id}:{uuid.uuid4()}",
            timeout_s=15.0,
        )
        if not result.ok:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability="app.stop",
                requested_state="stopped",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"project_id": str(project.id)},
            )

        project.state = STATE_STOPPED
        project.run_port = None
        project.updated_at = datetime.now(UTC)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_STOPPED,
            action="app.project.stop",
            summary=f"app.project.stop -> {project.name}",
            detail={"project_id": str(project.id)},
        )
        self._publish(project_name=project.name, state=STATE_STOPPED)
        return self._receipt(
            capability="app.stop",
            requested_state="stopped",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"stopped": True},
            speech=f"{project.name} uygulamasını durdurdum efendim.",
            db=db,
            session_id=session_id,
            extra={"project_id": str(project.id), "state": STATE_STOPPED},
        )

    # --------------------------------------------------------------------- status

    def status(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)

        # ``run_port`` (never ``state``) is the truth of "is a process up right now" —
        # see ``open()``'s identical comment: ``test()`` moves ``state`` on to
        # "tested"/"failed" as a lifecycle milestone without touching a still-running
        # process's own port.
        live_state = project.state
        live_port = project.run_port
        live_running = bool(project.run_port)
        if device_action is not None and project.run_port:
            result = device_action.run(
                capability=CAPABILITY_PROJECT_STATUS,
                payload={"project_id": str(project.id)},
                idempotency_key=f"appfactory-status:{project.id}:{uuid.uuid4()}",
                timeout_s=10.0,
            )
            if result.ok:
                body = result.result or {}
                live_state = str(body.get("state") or live_state)
                live_port = body.get("port", live_port)
                live_running = live_state == STATE_RUNNING and bool(live_port)

        if live_running:
            speech = f"{project.name} çalışıyor efendim, port {live_port}."
        elif live_state == STATE_TESTED:
            speech = f"{project.name} oluşturuldu ve testleri geçti efendim, şu an çalışmıyor."
        elif live_state == STATE_SCAFFOLDED:
            speech = f"{project.name} oluşturuldu efendim, şu an çalışmıyor."
        elif live_state == STATE_FAILED:
            speech = f"{project.name} son işlemde başarısız oldu efendim."
        else:
            speech = f"{project.name} şu an çalışmıyor efendim."

        return self._receipt(
            capability="app.status",
            requested_state="reported",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"state": live_state, "port": live_port},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"project_id": str(project.id), "state": live_state, "port": live_port},
        )

    # ----------------------------------------------------------------------- open

    def open(
        self,
        db: Session,
        device_action: DeviceActionPort | None,
        browser_gateway: BrowserGateway | None,
        *,
        target: str | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        device_id, missing = self._select_device(device_action)
        if missing is not None:
            return missing
        project = self.resolve_project(db, target)
        if project is None:
            return self._clarification(SPEECH_NO_PROJECT)
        if project.root_path is None:
            return self._invalid_argument(
                capability="app.open",
                requested_state="opened",
                speech=SPEECH_NOT_SCAFFOLDED,
                db=db,
                session_id=session_id,
            )

        result = device_action.run(  # type: ignore[union-attr]
            capability=CAPABILITY_FILE_REVEAL,
            payload={"path": project.root_path},
            idempotency_key=f"appfactory-reveal:{project.id}:{uuid.uuid4()}",
            timeout_s=15.0,
        )
        revealed = bool(result.ok and (result.result or {}).get("revealed"))

        browser_opened = False
        if project.kind in _RUNNABLE_KINDS and project.run_port:
            if browser_gateway is not None:
                try:
                    records = browser_gateway.fetch_evidence(
                        [
                            FetchQuery(
                                query=f"http://127.0.0.1:{project.run_port}/",
                                source_class="app_factory",
                                max_results=1,
                            )
                        ]
                    )
                    browser_opened = bool(records and records[0].excerpt)
                except Exception:  # noqa: BLE001 - best-effort; the reveal already ran
                    browser_opened = False

        if not revealed:
            speech, error_class = _translate_error(result.error_class)
            return self._receipt(
                capability="app.open",
                requested_state="opened",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"error_class": result.error_class},
                speech=speech,
                db=db,
                error_class=error_class,
                session_id=session_id,
                extra={"project_id": str(project.id)},
            )

        speech = f"{project.name} klasörünü açtım efendim."
        if browser_opened:
            speech += " Tarayıcıda da açtım."
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_EXERCISED,
            action="app.project.open",
            summary=f"app.project.open -> {project.name}",
            detail={"project_id": str(project.id), "browser_opened": browser_opened},
        )
        return self._receipt(
            capability="app.open",
            requested_state="opened",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"revealed": revealed, "browser_opened": browser_opened},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"project_id": str(project.id), "browser_opened": browser_opened},
        )

    # ----------------------------------------------------------------------- list

    def list(self, db: Session, *, session_id: str | None = None) -> dict[str, Any]:
        rows = list(
            db.execute(select(AppProjectRow).order_by(AppProjectRow.created_at.desc()).limit(20))
            .scalars()
            .all()
        )
        self._ledger(
            db,
            event_type=EVENT_TYPE_APP_PROJECT_LISTED,
            action="app.project.list",
            summary=f"app.project.list -> {len(rows)} proje",
            detail={"count": len(rows)},
        )
        if not rows:
            speech = "Henüz bir uygulama yapmadım efendim."
        else:
            names = ", ".join(f"{r.name} ({r.state})" for r in rows)
            speech = f"Şunları yaptım efendim: {names}."
        projects = [
            {
                "project_id": str(r.id),
                "name": r.name,
                "template": r.template,
                "state": r.state,
                "port": r.run_port,
            }
            for r in rows
        ]
        return self._receipt(
            capability="app.list",
            requested_state="listed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(rows)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"projects": projects},
        )

    # ----------------------------------------------------------------------- misc

    def _clarification(self, speech: str) -> dict[str, Any]:
        return {"status": "needs_clarification", "speech": speech, "candidates": []}

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


__all__ = ["AppFactoryService", "RUN_COMMAND_KEY", "TEST_COMMAND_KEY"]
