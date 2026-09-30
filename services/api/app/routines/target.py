"""Where a scheduled routine action executes: the execution_target rule, for routines."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.devices.selection import NoCapableDeviceError
from app.execution import wiring
from app.execution.rule import Decision, JobKind


def choose_routine_target(
    db: Session, runtime: Any, *, url: str | None = None, job_kind: JobKind = JobKind.SCHEDULED
) -> Decision:
    """The decision for a scheduled routine action. Always read-only: the owner is not
    watching, so ``acting`` is never passed True (ADR-0213 addendum).

    Raises ``NoCapableDeviceError`` (error class ``no_capable_device``) when no target is
    available."""
    decision = wiring.choose(
        job_kind,
        spoken_target=None,
        url=url,
        needs_signed_in_session=False,
        acting=False,
        scheduled=True,
        db=db,
        runtime=runtime,
    )
    if wiring.error_class_of(decision):
        raise NoCapableDeviceError(
            "Zamanlanmış işi çalıştıracak uygun bir hedef yok.",
            capability="browser.chrome",
            reason=decision.reason,
        )
    return decision
