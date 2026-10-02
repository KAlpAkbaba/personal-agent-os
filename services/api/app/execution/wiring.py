"""The execution_target rule, wired to the device registry and the ledger (ADR-0213 PR 1b).

``app.execution.rule`` is pure. This module is the one place that feeds it: it reads the
registry for what is up, calls ``decide``, writes the decision's events to the activity
ledger and hands the ``Decision`` back. The research, routine and web-task services call it
through the thin adapters (``app.research.target``, ``app.routines.target``); none of them
reads the registry or writes these events itself.

Registry conventions read here (nothing new is stored):

* a device whose ``platform`` is ``cloud`` IS the cloud worker; it is never the ``device``
  target;
* a device carrying the label ``owner_chrome`` is the machine whose own Chrome the owner
  enrolled (``enroll-owner-chrome.ps1``); the Chrome is usable while that device is online.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.devices import selection
from app.devices.presence import PRESENCE_ONLINE
from app.devices.service import list_device_views
from app.devices.types import DeviceView
from app.execution import rule
from app.execution import vocabulary as vocab
from app.execution.rule import Availability, Decision, ExecutionRequest, JobKind, Skip, Target
from app.ledger import service as ledger
from app.ledger import vocabulary as ledger_vocab
from app.logging import get_logger

logger = get_logger("app.execution.wiring")

CLOUD_PLATFORM: Final = "cloud"
OWNER_CHROME_LABEL: Final = "owner_chrome"
#: Capability the device target is asked for when an alias is resolved.
_DEVICE_CAPABILITY: Final = "browser.chrome"

#: ``select_device``'s reasons for a device that is up and still cannot serve. A skip for one
#: of them is written as ``<target>_<reason>`` (``cloud_capability_missing``, ...).
_PRECISE_REASONS: Final = ("capability_missing", "policy_denied")
#: The rule's "it is down" skips - the only ones a more precise reason may replace.
_DOWN_REASONS: Final = frozenset(
    {rule.CLOUD_OFFLINE, rule.CHROME_DEVICE_OFFLINE, rule.DEVICE_OFFLINE}
)

NOT_ON_OWNER_ALLOW_LIST: Final = "not_on_owner_allow_list"
#: The error class every reader of a device failure already knows.
NO_CAPABLE_DEVICE: Final = "no_capable_device"

#: Refusal reasons that mean "there is nowhere to run this" - the existing
#: ``no_capable_device`` class. Payment, deny-list and allow-list refusals are policy and
#: stay their own reasons.
_NO_TARGET_REASONS: Final = frozenset(
    {
        rule.NO_TARGET_AVAILABLE,
        rule.NO_ELIGIBLE_TARGET,
        rule.FORCED_UNAVAILABLE,
        rule.FORCED_NOT_ALLOWED,
    }
)

_SUBSYSTEM: Final = {
    JobKind.RESEARCH: ledger_vocab.SUBSYSTEM_RESEARCH,
    JobKind.SCHEDULED: ledger_vocab.SUBSYSTEM_ROUTINE,
}
_EVENT_STATUS: Final = {
    vocab.EXECUTION_SELECTED: ledger_vocab.STATUS_COMPLETED,
    vocab.EXECUTION_FALLBACK: ledger_vocab.STATUS_INFO,
    vocab.EXECUTION_REFUSED: ledger_vocab.STATUS_FAILED,
}


def error_class_of(decision: Decision) -> str | None:
    """``no_capable_device`` when the decision is a refusal for want of a target."""
    if decision.outcome == "refused" and decision.reason in _NO_TARGET_REASONS:
        return NO_CAPABLE_DEVICE
    return None


def _why_not(view: DeviceView, capabilities: Sequence[str]) -> str | None:
    """Why this one device cannot serve ``capabilities`` right now - ``offline``,
    ``capability_missing`` or ``policy_denied``, in ``select_device``'s own words because it
    IS ``select_device``'s answer for that device - or ``None`` when it can. With nothing
    asked, it is presence alone."""
    if view.status == "revoked" or view.presence != PRESENCE_ONLINE:
        return "offline"
    for capability in capabilities:
        try:
            selection.select_device([view], capability=capability)
        except selection.NoCapableDeviceError as exc:
            return exc.reason
    return None


def _target_state(
    target: Target, views: list[DeviceView], capabilities: Sequence[str]
) -> tuple[bool, str | None]:
    """(some view of ``target`` can serve, the precise skip reason when none can and one is
    online). A target that is up and cannot do the job is as unavailable as one that is
    down, and the ledger says which it was."""
    reasons = [_why_not(v, capabilities) for v in views]
    if None in reasons:
        return True, None
    precise = [r for r in reasons if r in _PRECISE_REASONS]
    return False, f"{target.value}_{precise[0]}" if precise else None


def _availability(
    views: list[DeviceView], spoken_target: str | None, capabilities: Sequence[str] | None = None
) -> tuple[Availability, str | None, dict[Target, str]]:
    live = [v for v in views if v.status != "revoked"]
    cloud = [v for v in live if v.platform == CLOUD_PLATFORM]
    machines = [v for v in live if v.platform != CLOUD_PLATFORM]
    chrome = [v for v in machines if OWNER_CHROME_LABEL in v.labels]

    precise: dict[Target, str] = {}
    cloud_up, precise_cloud = _target_state(Target.CLOUD, cloud, capabilities or ())
    chrome_up, precise_chrome = _target_state(Target.OWNER_CHROME, chrome, capabilities or ())
    if precise_cloud:
        precise[Target.CLOUD] = precise_cloud
    if precise_chrome:
        precise[Target.OWNER_CHROME] = precise_chrome

    resolved: str | None = None
    device_online = any(v.presence == PRESENCE_ONLINE for v in machines)
    named = bool(spoken_target and spoken_target.strip()) and (
        rule.forced_target_of(spoken_target) is Target.DEVICE
    )
    if named or (capabilities is not None and rule.forced_target_of(spoken_target) is None):
        # The same question the caller's own ``select_device`` will ask over the machines:
        # the ``device`` target is available exactly when that call would succeed.
        device_online = False
        try:
            match = selection.select_device(
                machines, capability=_DEVICE_CAPABILITY, target=spoken_target if named else None
            )
            device_online = True
            if named:
                resolved = str(match.device.id)
        except selection.NoCapableDeviceError as exc:
            if named and exc.reason in ("offline", *_PRECISE_REASONS):
                resolved = "named-but-unavailable"
            if exc.reason in _PRECISE_REASONS:
                precise[Target.DEVICE] = f"{Target.DEVICE.value}_{exc.reason}"
    return (
        Availability(
            cloud_online=cloud_up,
            owner_chrome_enrolled=bool(chrome),
            owner_chrome_device_online=chrome_up,
            device_online=device_online,
        ),
        resolved,
        precise,
    )


def _say_why(decision: Decision, precise: dict[Target, str]) -> Decision:
    """The rule knows 'up' and 'down'. Where a target was skipped as down and the registry
    shows it online but unable to serve, the skip carries that reason instead."""
    skipped = tuple(
        Skip(s.target, precise[s.target])
        if s.target in precise and s.reason in _DOWN_REASONS
        else s
        for s in decision.skipped
    )
    return decision if skipped == decision.skipped else replace(decision, skipped=skipped)


def device_for(
    decision: Decision, views: list[DeviceView], *, capabilities: Sequence[str] | None = None
) -> DeviceView | None:
    """The registry view a decision's target IS, by the conventions above.

    ``cloud`` -> the online device whose platform is ``cloud``; ``owner_chrome`` -> the online
    machine labelled ``owner_chrome``; with ``capabilities``, the first of them that can serve
    every one (advertised and policy-allowed) - the same test ``choose`` applied when it
    called the target available. ``None`` for the ``device`` target (which machine is
    ``app.devices.selection``'s question, asked over the non-cloud views) and for a decision
    that selected nothing. Pure: it reads the views it is handed."""
    if decision.outcome != "selected" or decision.target in (None, Target.DEVICE):
        return None
    if decision.target is Target.CLOUD:
        wanted = [v for v in views if v.platform == CLOUD_PLATFORM]
    else:
        wanted = [
            v for v in views if v.platform != CLOUD_PLATFORM and OWNER_CHROME_LABEL in v.labels
        ]
    for view in wanted:
        if _why_not(view, capabilities or ()) is None:
            return view
    return None


def _acting_allowed(url: str | None) -> bool:
    """The owner's allow-list for acting in the cloud. A missing module means no list has
    been written yet, which is 'not allowed': the cloud never acts by default."""
    try:
        from app.execution.allowlist import acting_allowed
    except ImportError:
        return False
    return bool(acting_allowed(url))


def _write(
    db: Session, decision: Decision, *, run_id: str, research_job_id: uuid.UUID | None = None
) -> None:
    subsystem = _SUBSYSTEM.get(decision.job_kind, ledger_vocab.SUBSYSTEM_BROWSER)
    for n, body in enumerate(rule.events(decision)):
        event_type = str(body["event_type"])
        ledger.record(
            db,
            ledger.ActivityEvent(
                event_type=event_type,
                subsystem=subsystem,
                action=event_type.replace(".", "_"),
                factual_summary=_summary(event_type, body),
                status=_EVENT_STATUS[event_type],
                severity="warning" if event_type == vocab.EXECUTION_REFUSED else "info",
                source="execution",
                source_ref=f"execution:{run_id}:{n}",
                research_job_id=research_job_id,
                detail_json={k: v for k, v in body.items() if k != "event_type"},
            ),
        )


def _summary(event_type: str, body: dict[str, Any]) -> str:
    kind = body["job_kind"]
    if event_type == vocab.EXECUTION_FALLBACK:
        return f"{kind}: {body['skipped_target']} atlandı ({body['reason']})."
    if event_type == vocab.EXECUTION_SELECTED:
        return f"{kind}: {body['target']} seçildi ({body['reason']})."
    return f"{kind}: hedef seçilmedi ({body['reason']})."


def choose(
    job_kind: JobKind | str,
    *,
    spoken_target: str | None,
    url: str | None,
    needs_signed_in_session: bool,
    acting: bool,
    scheduled: bool,
    db: Session,
    runtime: Any,
    involves_payment: bool = False,
    cloud_blocker: str | None = None,
    research_job_id: uuid.UUID | None = None,
    ledger_required: bool = True,
    capabilities: Sequence[str] | None = None,
    views: list[DeviceView] | None = None,
) -> Decision:
    """Decide where a job runs, record why, and return the decision.

    ``capabilities`` are the operations the job will send. With them, a target counts as
    available only when a device of it is online, advertises every one and is allowed them
    by the owner's policy - so a target that is up and cannot do the job is skipped (with
    its own reason) for the next in the chain, never selected and failed afterwards.
    Without them the rule decides by presence, as it did. ``views`` is the registry snapshot
    to decide over, for a caller that goes on to pick the device from the same one.

    ``research_job_id`` is the research task the decision is for: its ledger rows carry it,
    so "why did this run go where it went" is one read by the run's own id.
    ``ledger_required=False`` is for a caller that keeps its own record of the decision and
    must not be stopped by the ledger: a write that fails is rolled back and logged, and the
    decision is returned all the same.

    A scheduled job runs without the owner watching, so it is read-only whatever the caller
    says (ADR-0213 addendum): ``acting`` is forced False and the job is the cloud-only
    ``scheduled`` kind. An acting step that lands in the cloud must also be on the owner's
    allow-list; if not, the decision is replaced by a refusal."""
    kind = JobKind.SCHEDULED if scheduled else JobKind(job_kind)
    acting = False if scheduled else acting
    if views is None:
        views = list_device_views(db, runtime)
    availability, resolved, precise = _availability(views, spoken_target, capabilities)
    decision = _say_why(
        rule.decide(
            ExecutionRequest(
                job_kind=kind,
                availability=availability,
                spoken_target=spoken_target,
                resolved_device=resolved,
                needs_signed_in_session=needs_signed_in_session,
                url=url,
                acting=acting,
                involves_payment=involves_payment,
                cloud_blocker=cloud_blocker,
            )
        ),
        precise,
    )
    if (
        acting
        and decision.outcome == "selected"
        and decision.target is Target.CLOUD
        and not _acting_allowed(url)
    ):
        decision = Decision(
            kind, "refused", None, decision.chain, decision.skipped, NOT_ON_OWNER_ALLOW_LIST
        )
    run_id = str(research_job_id or uuid.uuid4())
    try:
        _write(db, decision, run_id=run_id, research_job_id=research_job_id)
    except SQLAlchemyError as exc:
        if ledger_required:
            raise
        db.rollback()
        logger.warning("execution_ledger_write_failed", run_id=run_id, error=repr(exc)[:300])
    return decision


__all__ = [
    "NOT_ON_OWNER_ALLOW_LIST",
    "NO_CAPABLE_DEVICE",
    "choose",
    "device_for",
    "error_class_of",
]
