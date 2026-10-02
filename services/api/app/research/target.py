"""Where a research run executes: the execution_target rule, for the research service."""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.orm import Session

from app.devices.selection import NoCapableDeviceError
from app.devices.types import DeviceView
from app.execution import wiring
from app.execution.rule import (
    CLOUD_OFFLINE,
    FORCED_UNAVAILABLE,
    Decision,
    JobKind,
    Target,
    forced_target_of,
)

NO_TARGET_TR: Final = "Araştırmayı çalıştıracak uygun bir hedef yok."
#: The owner said "bulutta" and the cloud worker is not up: said as what it is.
CLOUD_UNAVAILABLE_TR: Final = "Bulut şu anda çevrimiçi değil."
#: ...and when it is up but does not serve research (an operation it does not advertise, or
#: one the owner's policy denies it).
CLOUD_CANNOT_SERVE_TR: Final = "Bulut bu işi şu anda yapamıyor."

#: The operations a research run sends (``app.research.browser_gateway``). A target serves
#: research when a device of it advertises every one - by name, as the cloud worker does
#: (its hello carries no family marker), or through ``browser.chrome``, as the machines do.
RESEARCH_OPERATIONS: Final = (
    "browser.session_open",
    "browser.search",
    "browser.wait",
    "browser.fetch_evidence",
    "browser.session_close",
)


def choose_research_target(
    db: Session,
    runtime: Any,
    *,
    spoken_target: str | None = None,
    url: str | None = None,
    needs_signed_in_session: bool = False,
    task_id: uuid.UUID | None = None,
    views: list[DeviceView] | None = None,
) -> Decision:
    """The decision for a research run (read-only: research never acts).

    A target is available only when a device of it can serve ``RESEARCH_OPERATIONS``; one
    that is up and cannot is skipped for the next. ``views`` is the registry snapshot the
    caller will pick the device from, so the decision and the pick read the same registry.

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
        capabilities=RESEARCH_OPERATIONS,
        views=views,
    )
    if wiring.error_class_of(decision):
        detail = NO_TARGET_TR
        if (
            decision.reason == FORCED_UNAVAILABLE
            and forced_target_of(spoken_target) is Target.CLOUD
        ):
            down = any(s.reason == CLOUD_OFFLINE for s in decision.skipped)
            detail = CLOUD_UNAVAILABLE_TR if down else CLOUD_CANNOT_SERVE_TR
        raise NoCapableDeviceError(
            detail,
            capability="browser.chrome",
            target=spoken_target,
            reason=decision.reason,
        )
    return decision
