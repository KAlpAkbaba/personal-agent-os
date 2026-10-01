"""Where a research run executes: the execution_target rule, for the research service."""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.orm import Session

from app.devices.selection import NoCapableDeviceError
from app.execution import wiring
from app.execution.rule import (
    FORCED_UNAVAILABLE,
    Decision,
    JobKind,
    Target,
    forced_target_of,
)

NO_TARGET_TR: Final = "Araştırmayı çalıştıracak uygun bir hedef yok."
#: The owner said "bulutta" and the cloud worker is not up: said as what it is.
CLOUD_UNAVAILABLE_TR: Final = "Bulut şu anda çevrimiçi değil."


def choose_research_target(
    db: Session,
    runtime: Any,
    *,
    spoken_target: str | None = None,
    url: str | None = None,
    needs_signed_in_session: bool = False,
    task_id: uuid.UUID | None = None,
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
        research_job_id=task_id,
        # The run's own PLANNED event carries the decision too (``start_browser_research``):
        # a research the owner asked for is not refused because a ledger row would not write.
        ledger_required=False,
    )
    if wiring.error_class_of(decision):
        forced_cloud = (
            decision.reason == FORCED_UNAVAILABLE
            and forced_target_of(spoken_target) is Target.CLOUD
        )
        raise NoCapableDeviceError(
            CLOUD_UNAVAILABLE_TR if forced_cloud else NO_TARGET_TR,
            capability="browser.chrome",
            target=spoken_target,
            reason=decision.reason,
        )
    return decision
