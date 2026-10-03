"""Where a scheduled routine action executes: the execution_target rule, for routines."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy.orm import Session

from app.devices.selection import NoCapableDeviceError
from app.devices.types import DeviceView
from app.execution import rule, wiring
from app.execution.rule import Availability, Decision, ExecutionRequest, JobKind, Target

NO_TARGET_TR: Final = "Zamanlanmış işi çalıştıracak uygun bir hedef yok."


def choose_routine_target(
    db: Session,
    runtime: Any,
    *,
    url: str | None = None,
    job_kind: JobKind = JobKind.SCHEDULED,
    spoken_target: str | None = None,
    capabilities: Sequence[str] | None = None,
    views: list[DeviceView] | None = None,
    ledger_required: bool = True,
) -> Decision:
    """The decision for a scheduled routine action. Always read-only: the owner is not
    watching, so ``acting`` is never passed True (ADR-0213 addendum).

    ``capabilities``, ``views`` and ``ledger_required`` are ``wiring.choose``'s own, handed
    through; ``spoken_target`` is a device word the routine names, which the scheduled chain
    (the cloud alone) refuses unless it is the cloud's.

    Raises ``NoCapableDeviceError`` (error class ``no_capable_device``) when no target is
    available."""
    decision = wiring.choose(
        job_kind,
        spoken_target=spoken_target,
        url=url,
        needs_signed_in_session=False,
        acting=False,
        scheduled=True,
        db=db,
        runtime=runtime,
        capabilities=capabilities,
        views=views,
        ledger_required=ledger_required,
    )
    if wiring.error_class_of(decision):
        raise NoCapableDeviceError(
            NO_TARGET_TR,
            capability="browser.chrome",
            target=spoken_target,
            reason=decision.reason,
        )
    return decision


def _spoken(targets: Sequence[str]) -> str | None:
    """The one word the rule is asked about: a machine's when any of them is a machine's
    (so naming a machine beside the cloud is still refused), else the first."""
    for word in targets:
        if rule.forced_target_of(word) is Target.DEVICE:
            return word
    return targets[0] if targets else None


def select_routine_device(
    db: Session,
    runtime: Any,
    views: list[DeviceView],
    *,
    capability: str,
    url: str | None = None,
    targets: Sequence[str] = (),
) -> DeviceView:
    """The device one scheduled browser operation is sent to: the cloud worker that can serve
    ``capability``, decided over ``views`` and written to the ledger - or
    ``NoCapableDeviceError`` carrying the decision's reason. Every decision that selects
    nothing is that error, the policy refusals ``choose_routine_target`` returns included:
    there is no machine a scheduled browser operation may go to instead.

    The ledger rows are the record of the decision, not the decision: a write that fails is
    logged by ``wiring.choose`` and the operation is sent all the same."""
    spoken = _spoken(targets)
    try:
        decision = choose_routine_target(
            db,
            runtime,
            url=url,
            spoken_target=spoken,
            capabilities=(capability,),
            views=views,
            ledger_required=False,
        )
        reason = decision.reason
    except NoCapableDeviceError as exc:
        decision, reason = None, exc.reason
    device = wiring.device_for(decision, views, capabilities=(capability,)) if decision else None
    if device is None:
        raise NoCapableDeviceError(
            f"{NO_TARGET_TR} ({reason})", capability=capability, target=spoken, reason=reason
        )
    return device


#: What ``wiring.device_for`` is asked for by the probe: "the cloud view that can serve".
_CLOUD_SELECTED: Final = Decision(
    JobKind.SCHEDULED, "selected", Target.CLOUD, (Target.CLOUD,), (), "probe"
)


def probe_routine_device(
    views: list[DeviceView], *, capability: str, targets: Sequence[str] = ()
) -> DeviceView | None:
    """The device ``select_routine_device`` would return over ``views``, or ``None`` - and
    nothing else: pure, no ledger row (a probe is not a run; ``wiring.choose`` writes).

    The same rule (``rule.decide``, the scheduled kind) over the same availability test
    (``wiring.device_for``: online, not revoked, advertised, policy-allowed)."""
    cloud = wiring.device_for(_CLOUD_SELECTED, views, capabilities=(capability,))
    decision = rule.decide(
        ExecutionRequest(
            job_kind=JobKind.SCHEDULED,
            availability=Availability(cloud_online=cloud is not None),
            spoken_target=_spoken(targets),
            acting=False,
        )
    )
    return cloud if decision.outcome == "selected" and decision.target is Target.CLOUD else None
