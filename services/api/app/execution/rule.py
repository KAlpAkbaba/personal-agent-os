"""The execution_target rule: cloud / owner_chrome / device, as pure functions.

Nothing here talks to a device, a database or a model. The caller says what the job is,
what the owner said, what the site needs and which targets are up; ``decide`` answers
with the chosen target, the ordered chain it considered and one reason per skipped
target. ``events`` turns that answer into the ledger records to write.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.execution import vocabulary as vocab
from app.webtask.sites import denied


class JobKind(StrEnum):
    RESEARCH = "research"
    BROWSER_TASK = "browser_task"
    DESKTOP = "desktop"
    SCHEDULED = "scheduled"
    COMPUTE = "compute"


class Target(StrEnum):
    CLOUD = "cloud"
    OWNER_CHROME = "owner_chrome"
    DEVICE = "device"


#: The order each kind may run in. A target absent from a kind's chain is never
#: considered for it - not as a fallback, not when forced.
_CHAINS: Final[dict[JobKind, tuple[Target, ...]]] = {
    JobKind.RESEARCH: (Target.CLOUD, Target.OWNER_CHROME, Target.DEVICE),
    JobKind.BROWSER_TASK: (Target.CLOUD, Target.OWNER_CHROME, Target.DEVICE),
    JobKind.DESKTOP: (Target.DEVICE,),
    JobKind.SCHEDULED: (Target.CLOUD,),
    JobKind.COMPUTE: (Target.CLOUD,),
}
#: Kinds that can use a signed-in session: the cloud has none of the owner's sessions.
_SIGNED_IN_KINDS: Final = frozenset({JobKind.RESEARCH, JobKind.BROWSER_TASK})

_CLOUD_WORDS: Final = frozenset({"bulutta", "bulut"})
_CLOUD_BLOCKERS: Final = frozenset({"auth_wall", "captcha", "challenge"})

# Refusal / skip reason codes.
FORCED_UNAVAILABLE: Final = "forced_target_unavailable"
FORCED_NOT_ALLOWED: Final = "forced_target_not_allowed"
NO_ELIGIBLE_TARGET: Final = "no_eligible_target"
NO_TARGET_AVAILABLE: Final = "no_target_available"
PAYMENT_OUT_OF_SCOPE: Final = "payment_out_of_scope"
DENY_LISTED_SITE: Final = "deny_listed_site"
ASK_OWNER: Final = "ask_owner"
CLOUD_OFFLINE: Final = "cloud_offline"
CHROME_NOT_ENROLLED: Final = "owner_chrome_not_enrolled"
CHROME_DEVICE_OFFLINE: Final = "owner_chrome_device_offline"
DEVICE_OFFLINE: Final = "device_offline"
DEVICE_UNRESOLVED: Final = "device_unresolved"


@dataclass(frozen=True, slots=True)
class Availability:
    cloud_online: bool = True
    owner_chrome_enrolled: bool = False
    owner_chrome_device_online: bool = False
    #: The device the job would use: the named one when the owner named one.
    device_online: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    job_kind: JobKind
    availability: Availability
    #: The resolved spoken target word ('bulutta' / 'bulut' / a device alias), or None.
    spoken_target: str | None = None
    #: The device the alias resolved to (ADR-0212's layer); None = it resolved to none.
    resolved_device: str | None = None
    needs_signed_in_session: bool = False
    url: str | None = None
    #: False for reading/observing; only acting is refused on a deny-listed site.
    acting: bool = True
    involves_payment: bool = False
    #: What a cloud run that is already under way met: auth_wall / captcha / challenge.
    cloud_blocker: str | None = None


@dataclass(frozen=True, slots=True)
class Skip:
    target: Target
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    job_kind: JobKind
    #: "selected" | "refused" | "ask_owner"
    outcome: str
    target: Target | None
    chain: tuple[Target, ...]
    skipped: tuple[Skip, ...]
    reason: str
    forced: bool = False


def forced_target_of(spoken: str | None) -> Target | None:
    """The target the owner's word forces: a cloud word -> cloud, any other word -> a
    device alias, nothing -> None. The whole word only: 'bulutlu' is not 'bulut'."""
    word = (spoken or "").strip().strip(".,!?;:").replace("İ", "i").replace("I", "ı").casefold()
    if not word:
        return None
    return Target.CLOUD if word in _CLOUD_WORDS else Target.DEVICE


def _chain_for(request: ExecutionRequest) -> tuple[Target, ...]:
    chain = _CHAINS[request.job_kind]
    if request.needs_signed_in_session:
        if request.job_kind not in _SIGNED_IN_KINDS:
            return ()
        chain = tuple(t for t in chain if t is not Target.CLOUD)
    return chain


def _unavailable(target: Target, request: ExecutionRequest) -> str | None:
    a = request.availability
    if target is Target.CLOUD:
        return None if a.cloud_online else CLOUD_OFFLINE
    if target is Target.OWNER_CHROME:
        if not a.owner_chrome_enrolled:
            return CHROME_NOT_ENROLLED
        return None if a.owner_chrome_device_online else CHROME_DEVICE_OFFLINE
    if request.spoken_target and request.resolved_device is None:
        return DEVICE_UNRESOLVED
    return None if a.device_online else DEVICE_OFFLINE


def _refuse(
    request: ExecutionRequest,
    reason: str,
    chain: tuple[Target, ...] = (),
    skipped: tuple[Skip, ...] = (),
    forced: bool = False,
) -> Decision:
    return Decision(request.job_kind, "refused", None, chain, skipped, reason, forced)


def decide(request: ExecutionRequest) -> Decision:
    kind = request.job_kind
    if request.involves_payment:
        return _refuse(request, PAYMENT_OUT_OF_SCOPE)
    if request.acting and request.url and denied(request.url):
        return _refuse(request, DENY_LISTED_SITE)
    chain = _chain_for(request)
    if request.cloud_blocker in _CLOUD_BLOCKERS and Target.CLOUD in chain:
        # The cloud run stays where it is; only the owner may move it (ADR-0207 d.6).
        # A job that can never be in the cloud has no cloud run to be blocked.
        return Decision(kind, "ask_owner", Target.CLOUD, (Target.CLOUD,), (), ASK_OWNER)

    forced = forced_target_of(request.spoken_target)
    if forced is not None:
        if forced not in chain:
            return _refuse(request, FORCED_NOT_ALLOWED, (forced,), forced=True)
        reason = _unavailable(forced, request)
        if reason:
            return _refuse(
                request, FORCED_UNAVAILABLE, (forced,), (Skip(forced, reason),), forced=True
            )
        return Decision(kind, "selected", forced, (forced,), (), "forced", True)

    if not chain:
        return _refuse(request, NO_ELIGIBLE_TARGET)
    skipped: list[Skip] = []
    for target in chain:
        reason = _unavailable(target, request)
        if reason is None:
            return Decision(kind, "selected", target, chain, tuple(skipped), "default")
        skipped.append(Skip(target, reason))
    return _refuse(request, NO_TARGET_AVAILABLE, chain, tuple(skipped))


def events(decision: Decision) -> list[dict[str, object]]:
    """The ledger records for a decision: one ``execution.fallback`` per skipped target,
    then exactly one ``execution.selected`` or ``execution.refused``."""
    base = {"job_kind": decision.job_kind.value, "chain": [t.value for t in decision.chain]}
    out: list[dict[str, object]] = [
        {
            **base,
            "event_type": vocab.EXECUTION_FALLBACK,
            "skipped_target": skip.target.value,
            "reason": skip.reason,
        }
        for skip in decision.skipped
    ]
    if decision.outcome == "selected":
        out.append(
            {
                **base,
                "event_type": vocab.EXECUTION_SELECTED,
                "target": decision.target.value if decision.target else None,
                "reason": decision.reason,
                "forced": decision.forced,
            }
        )
    else:
        out.append(
            {
                **base,
                "event_type": vocab.EXECUTION_REFUSED,
                "reason": decision.reason,
                "ask_owner": decision.outcome == "ask_owner",
                "forced": decision.forced,
            }
        )
    return out
