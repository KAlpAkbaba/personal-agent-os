"""Original-task resumption (M7 acceptance: "original user task resumes and
completes").

Two pieces:

- ``CapabilityDispatcher`` maps ``capability_id -> registered skill entrypoint``
  and executes it. It goes through ``CapabilityRegistry.resolve`` and nothing
  else, so a capability that is not ``production`` with a ``registered`` skill
  version raises ``capability_missing`` — the same failure the original task hit
  before the gap was resolved. Execution is an isolated subprocess speaking
  JSON over stdin/stdout (the generated module's ``main()``), so generated code
  never runs inside the API process.
- ``TaskResumer`` drives the existing ``tasks``/``task_runs`` tables: a task
  parked in ``FAILED_RECOVERABLE`` with ``error_class = "capability_missing"``
  is re-dispatched after registration and moves to ``COMPLETED`` with its result
  recorded on a new ``task_runs`` attempt.

Integration with the task subsystem is deliberately minimal: no new tables, no
change to the task state machine, no ownership of app/artifacts code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.models import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED_RECOVERABLE,
    TASK_STATUS_RUNNING,
    Task,
    TaskRun,
)
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.registry import CapabilityRegistry
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import SRC_DIRNAME, read_manifest
from app.logging import get_logger

logger = get_logger("app.evolution.task_resumption")

SessionFactory = Callable[[], AbstractContextManager[Session]]

CAPABILITY_MISSING_ERROR_CLASS = "capability_missing"
DISPATCH_TIMEOUT_S = 30.0
MAX_PAYLOAD_BYTES = 64 * 1024


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DispatchResult:
    capability_id: str
    version: str
    skill_version_id: str
    output: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "skill_version_id": self.skill_version_id,
            "output": self.output,
            "duration_ms": round(self.duration_ms, 3),
        }


class CapabilityDispatcher:
    """Executes a REGISTERED capability. The only runtime entry to generated code."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        sandbox: SandboxPolicy | None = None,
        python: str = sys.executable,
        timeout_s: float = DISPATCH_TIMEOUT_S,
    ) -> None:
        self.registry = registry
        self.sandbox = sandbox
        self.python = python
        self.timeout_s = timeout_s

    def dispatch(self, capability_id: str, payload: dict[str, Any]) -> DispatchResult:
        resolved = self.registry.resolve(capability_id)
        if resolved is None:
            raise EvolutionError(
                EvolutionErrorClass.CAPABILITY_MISSING,
                f"capability {capability_id!r} is not registered for production dispatch",
                details={"capability_id": capability_id},
            )
        skill_version = resolved.get("skill_version") or {}
        source_ref = skill_version.get("source_ref")
        if not source_ref:
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"capability {capability_id!r} has no source_ref to execute",
            )
        skill_root = Path(source_ref)
        if self.sandbox is not None:
            skill_root = self.sandbox.ensure_within(skill_root, label="registered skill root")
        manifest = read_manifest(skill_root / "manifest.yaml")
        skill_name = manifest.get("skill")
        entrypoint = manifest.get("entrypoint")
        if not skill_name or entrypoint != "run":
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                "registered skill manifest does not declare the run entrypoint",
            )
        module_path = skill_root / SRC_DIRNAME / f"{skill_name}.py"
        if not module_path.is_file():
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"registered skill module is missing: {module_path.name}",
            )
        body = json.dumps(payload or {}, ensure_ascii=False)
        if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"dispatch payload too large (max {MAX_PAYLOAD_BYTES} bytes)",
            )
        env: dict[str, str] = {"PYTHONUTF8": "1"}
        for passthrough in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "COMSPEC"):
            value = os.environ.get(passthrough)
            if value:
                env[passthrough] = value
        started = _utcnow()
        try:
            proc = subprocess.run(  # noqa: S603 - registered, reviewed skill; isolated env
                [self.python, "-I", "-S", "-X", "utf8", str(module_path)],
                input=body,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(skill_root),
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"capability {capability_id!r} timed out after {self.timeout_s}s",
            ) from exc
        if proc.returncode != 0:
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"capability {capability_id!r} exited with code {proc.returncode}",
                details={"stderr_tail": (proc.stderr or "")[-1000:]},
            )
        try:
            output = json.loads(proc.stdout or "{}")
        except ValueError as exc:
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"capability {capability_id!r} did not return JSON",
            ) from exc
        if not isinstance(output, dict):
            raise EvolutionError(
                EvolutionErrorClass.DISPATCH_FAILED,
                f"capability {capability_id!r} returned a non-object result",
            )
        duration_ms = (_utcnow() - started).total_seconds() * 1000.0
        logger.info(
            "capability_dispatched",
            capability_id=capability_id,
            version=resolved["version"],
            duration_ms=round(duration_ms, 3),
        )
        return DispatchResult(
            capability_id=capability_id,
            version=resolved["version"],
            skill_version_id=str(skill_version.get("id")),
            output=output,
            duration_ms=duration_ms,
        )


@dataclass(slots=True)
class ResumptionResult:
    task_id: str
    status: str
    capability_id: str
    output: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "capability_id": self.capability_id,
            "output": self.output,
            "attempt": self.attempt,
            "detail": self.detail,
        }


class TaskResumer:
    """Re-dispatches a task that failed with ``capability_missing``."""

    def __init__(
        self, session_factory: SessionFactory, dispatcher: CapabilityDispatcher
    ) -> None:
        self._session_factory = session_factory
        self.dispatcher = dispatcher

    # ------------------------------------------------------------- bookkeeping

    def record_capability_missing(
        self, intent: str, capability_id: str, *, task_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Park a task in FAILED_RECOVERABLE/capability_missing.

        Used by the original dispatch path (and by the acceptance tests) when a
        request names a capability the registry cannot resolve.
        """
        with self._session_factory() as session:
            row = session.get(Task, task_id) if task_id else None
            if row is None:
                row = Task(id=task_id or uuid.uuid4(), intent=intent)
                session.add(row)
            row.status = TASK_STATUS_FAILED_RECOVERABLE
            row.error_class = CAPABILITY_MISSING_ERROR_CLASS
            row.error_message = f"no registered capability: {capability_id}"
            session.commit()
            logger.info(
                "task_capability_missing", task_id=str(row.id), capability_id=capability_id
            )
            return row.id

    def get_task(self, task_id: uuid.UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(Task, task_id)
            if row is None:
                raise EvolutionError(
                    EvolutionErrorClass.NOT_FOUND, f"task {task_id} not found"
                )
            return {
                "id": str(row.id),
                "intent": row.intent,
                "status": row.status,
                "error_class": row.error_class,
                "error_message": row.error_message,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }

    # ---------------------------------------------------------------- resume

    def resume(
        self, task_id: uuid.UUID, capability_id: str, payload: dict[str, Any]
    ) -> ResumptionResult:
        with self._session_factory() as session:
            row = session.get(Task, task_id)
            if row is None:
                raise EvolutionError(
                    EvolutionErrorClass.NOT_FOUND, f"task {task_id} not found"
                )
            if row.status != TASK_STATUS_FAILED_RECOVERABLE:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    f"task {task_id} is {row.status}; only a FAILED_RECOVERABLE task resumes",
                )
            if row.error_class != CAPABILITY_MISSING_ERROR_CLASS:
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    f"task {task_id} did not fail with {CAPABILITY_MISSING_ERROR_CLASS}",
                )
            attempt = (
                session.execute(
                    select(TaskRun.attempt)
                    .where(TaskRun.task_id == task_id)
                    .order_by(TaskRun.attempt.desc())
                    .limit(1)
                ).scalar()
                or 0
            ) + 1
            row.status = TASK_STATUS_RUNNING
            run = TaskRun(task_id=task_id, attempt=attempt, status=TASK_STATUS_RUNNING)
            session.add(run)
            session.commit()
            run_id = run.id

        try:
            dispatched = self.dispatcher.dispatch(capability_id, payload)
        except EvolutionError as exc:
            with self._session_factory() as session:
                row = session.get(Task, task_id)
                run = session.get(TaskRun, run_id)
                if row is not None:
                    row.status = TASK_STATUS_FAILED_RECOVERABLE
                    row.error_class = (
                        CAPABILITY_MISSING_ERROR_CLASS
                        if exc.error_class == EvolutionErrorClass.CAPABILITY_MISSING
                        else str(exc.error_class)[:64]
                    )
                    row.error_message = exc.message
                if run is not None:
                    run.status = TASK_STATUS_FAILED_RECOVERABLE
                    run.error_class = str(exc.error_class)[:64]
                    run.ended_at = _utcnow()
                    run.telemetry_json = {"error": exc.to_dict()}
                session.commit()
            logger.info(
                "task_resume_failed",
                task_id=str(task_id),
                capability_id=capability_id,
                error_class=str(exc.error_class),
            )
            raise

        with self._session_factory() as session:
            row = session.get(Task, task_id)
            run = session.get(TaskRun, run_id)
            now = _utcnow()
            if row is not None:
                row.status = TASK_STATUS_COMPLETED
                row.error_class = None
                row.error_message = None
                row.ready_at = row.ready_at or now
                row.completed_at = now
            if run is not None:
                run.status = TASK_STATUS_COMPLETED
                run.ended_at = now
                run.telemetry_json = {"dispatch": dispatched.to_dict()}
            session.commit()
        logger.info(
            "task_resumed",
            task_id=str(task_id),
            capability_id=capability_id,
            attempt=attempt,
        )
        return ResumptionResult(
            task_id=str(task_id),
            status=TASK_STATUS_COMPLETED,
            capability_id=capability_id,
            output=dispatched.output,
            attempt=attempt,
            detail="resumed with the newly registered capability",
        )


__all__ = [
    "CAPABILITY_MISSING_ERROR_CLASS",
    "DISPATCH_TIMEOUT_S",
    "MAX_PAYLOAD_BYTES",
    "CapabilityDispatcher",
    "DispatchResult",
    "ResumptionResult",
    "TaskResumer",
]
