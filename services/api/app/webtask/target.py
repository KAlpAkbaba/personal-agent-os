"""Where a browser task runs: the execution_target rule, for the web task loop.

The ``browser_task`` kind's chain is cloud -> the owner's Chrome -> a device
(``app.execution.rule``). A target is available when a device of it can serve every
operation the loop sends (``TASK_OPERATIONS``); the decision and the device are read from
ONE registry snapshot, as ``app.research.target`` does.

A task is chosen WITHOUT claiming to act (``acting=False``). Whether it may write is
decided step by step at the gate (rule 9: in the cloud, only on a site the owner listed),
where the page's own address is known - at the start there is no address to ask the
allow-list about, and a task that only reads must not be refused the cloud for it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.orm import Session

from app.devices import selection
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
from app.webtask.service import WebTaskError
from app.webtask.types import CAPABILITY_OF

NO_CAPABLE_DEVICE: Final = wiring.NO_CAPABLE_DEVICE
NO_TARGET_TR: Final = "Görevi çalıştıracak uygun bir hedef yok."
#: The owner said "bulutta" and the cloud worker is not up (``app.research.target``'s words).
CLOUD_UNAVAILABLE_TR: Final = "Bulut şu anda çevrimiçi değil."
CLOUD_CANNOT_SERVE_TR: Final = "Bulut bu işi şu anda yapamıyor."

#: What the loop sends (``app.webtask.device_port``): the session, a tab of its own, the
#: observation, and every acting operation of the vocabulary.
TASK_OPERATIONS: Final[tuple[str, ...]] = (
    "browser.session_open",
    "browser.tab_new",
    "browser.observe",
    *dict.fromkeys(CAPABILITY_OF.values()),
)
#: The ``device`` target is a machine; ``select_device`` picks it by this capability, as
#: the research path does.
_DEVICE_CAPABILITY: Final = "browser.chrome"


@dataclass(frozen=True, slots=True)
class TaskTarget:
    #: ``app.webtask.types.TARGET_*``.
    target: str
    device_id: uuid.UUID
    decision: Decision


def choose_task_target(
    db: Session,
    runtime: Any,
    *,
    spoken_target: str | None,
    allowed_hosts: tuple[str, ...] = (),
    views: list[DeviceView] | None = None,
) -> TaskTarget:
    """The target and the device for a new browser task, or ``WebTaskError``
    (``no_capable_device``, with the Turkish sentence) when there is nowhere to run it.

    ``allowed_hosts`` are the hosts the owner named for the task. They do not move the
    target: no address is known before the first observation, and acting is the gate's.
    The ledger rows are ``execution.selected`` / ``execution.fallback``, without a
    research id; a ledger that will not write does not refuse the owner's task."""
    del allowed_hosts  # named in the signature for the caller; see the docstring
    if views is None:
        views = wiring.list_device_views(db, runtime)
    decision = wiring.choose(
        JobKind.BROWSER_TASK,
        spoken_target=spoken_target,
        url=None,
        needs_signed_in_session=False,
        acting=False,
        scheduled=False,
        db=db,
        runtime=runtime,
        ledger_required=False,
        capabilities=TASK_OPERATIONS,
        views=views,
    )
    if decision.outcome != "selected" or decision.target is None:
        detail = NO_TARGET_TR
        if (
            decision.reason == FORCED_UNAVAILABLE
            and forced_target_of(spoken_target) is Target.CLOUD
        ):
            down = any(s.reason == CLOUD_OFFLINE for s in decision.skipped)
            detail = CLOUD_UNAVAILABLE_TR if down else CLOUD_CANNOT_SERVE_TR
        raise WebTaskError(NO_CAPABLE_DEVICE, detail, decision_reason=decision.reason)
    if decision.target is Target.DEVICE:
        machines = [v for v in views if v.platform != wiring.CLOUD_PLATFORM]
        named = forced_target_of(spoken_target) is Target.DEVICE
        try:
            match = selection.select_device(
                machines, capability=_DEVICE_CAPABILITY, target=spoken_target if named else None
            )
        except selection.NoCapableDeviceError as exc:
            raise WebTaskError(NO_CAPABLE_DEVICE, NO_TARGET_TR, decision_reason=exc.reason) from exc
        return TaskTarget(
            target=decision.target.value, device_id=match.device.id, decision=decision
        )
    view = wiring.device_for(decision, views, capabilities=TASK_OPERATIONS)
    if view is None:
        raise WebTaskError(NO_CAPABLE_DEVICE, NO_TARGET_TR, decision_reason=decision.reason)
    return TaskTarget(target=decision.target.value, device_id=view.id, decision=decision)


__all__ = [
    "CLOUD_CANNOT_SERVE_TR",
    "CLOUD_UNAVAILABLE_TR",
    "NO_TARGET_TR",
    "TASK_OPERATIONS",
    "TaskTarget",
    "choose_task_target",
]
