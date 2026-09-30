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
from typing import Any, Final

from sqlalchemy.orm import Session

from app.devices import selection
from app.devices.presence import PRESENCE_ONLINE
from app.devices.service import list_device_views
from app.devices.types import DeviceView
from app.execution import rule
from app.execution import vocabulary as vocab
from app.execution.rule import Availability, Decision, ExecutionRequest, JobKind, Target
from app.ledger import service as ledger
from app.ledger import vocabulary as ledger_vocab

CLOUD_PLATFORM: Final = "cloud"
OWNER_CHROME_LABEL: Final = "owner_chrome"
#: Capability the device target is asked for when an alias is resolved.
_DEVICE_CAPABILITY: Final = "browser.chrome"

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


def _availability(
    views: list[DeviceView], spoken_target: str | None
) -> tuple[Availability, str | None]:
    live = [v for v in views if v.status != "revoked"]
    cloud = [v for v in live if v.platform == CLOUD_PLATFORM]
    machines = [v for v in live if v.platform != CLOUD_PLATFORM]
    chrome = [v for v in machines if OWNER_CHROME_LABEL in v.labels]

    resolved: str | None = None
    device_online = any(v.presence == PRESENCE_ONLINE for v in machines)
    if spoken_target and spoken_target.strip() and rule.forced_target_of(spoken_target) is (
        Target.DEVICE
    ):
        device_online = False
        try:
            match = selection.select_device(
                machines, capability=_DEVICE_CAPABILITY, target=spoken_target
            )
            resolved, device_online = str(match.device.id), True
        except selection.NoCapableDeviceError as exc:
            if exc.reason in ("offline", "capability_missing", "policy_denied"):
                resolved = "named-but-unavailable"
    return (
        Availability(
            cloud_online=any(v.presence == PRESENCE_ONLINE for v in cloud),
            owner_chrome_enrolled=bool(chrome),
            owner_chrome_device_online=any(v.presence == PRESENCE_ONLINE for v in chrome),
            device_online=device_online,
        ),
        resolved,
    )


def _acting_allowed(url: str | None) -> bool:
    """The owner's allow-list for acting in the cloud. A missing module means no list has
    been written yet, which is 'not allowed': the cloud never acts by default."""
    try:
        from app.execution.allowlist import acting_allowed
    except ImportError:
        return False
    return bool(acting_allowed(url))


def _write(db: Session, decision: Decision, *, run_id: str) -> None:
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
) -> Decision:
    """Decide where a job runs, record why, and return the decision.

    A scheduled job runs without the owner watching, so it is read-only whatever the caller
    says (ADR-0213 addendum): ``acting`` is forced False and the job is the cloud-only
    ``scheduled`` kind. An acting step that lands in the cloud must also be on the owner's
    allow-list; if not, the decision is replaced by a refusal."""
    kind = JobKind.SCHEDULED if scheduled else JobKind(job_kind)
    acting = False if scheduled else acting
    availability, resolved = _availability(list_device_views(db, runtime), spoken_target)
    decision = rule.decide(
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
    _write(db, decision, run_id=str(uuid.uuid4()))
    return decision


__all__ = ["NOT_ON_OWNER_ALLOW_LIST", "NO_CAPABLE_DEVICE", "choose", "error_class_of"]
