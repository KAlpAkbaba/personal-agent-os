"""Where a research run executes: the execution_target rule, for the research service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.devices.selection import NoCapableDeviceError
from app.execution import wiring
from app.execution.rule import Decision, JobKind


def choose_research_target(
    db: Session,
    runtime: Any,
    *,
    spoken_target: str | None = None,
    url: str | None = None,
    needs_signed_in_session: bool = False,
) -> Decision:
    """The decision for a research run (read-only: research never acts).

    Raises ``NoCapableDeviceError`` (error class ``no_capable_device``) when there is no
    target to run it on, so callers that already handle it keep working."""
    decision = wiring.choose(
        JobKind.RESEARCH,
        spoken_target=spoken_target,
        url=url,
        needs_signed_in_session=needs_signed_in_session,
        acting=False,
        scheduled=False,
        db=db,
        runtime=runtime,
    )
    if wiring.error_class_of(decision):
        raise NoCapableDeviceError(
            "Araştırmayı çalıştıracak uygun bir hedef yok.",
            capability="browser.chrome",
            target=spoken_target,
            reason=decision.reason,
        )
    return decision
