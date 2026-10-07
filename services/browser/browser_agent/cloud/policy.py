"""The cloud session policy: READ + NAVIGATE, and for a cloud TASK one class more.

ADR-0213 addendum: research on the cloud reads and navigates, never wider. Contract v1.9
(the owner's option 4): a ``session_open`` that says ``cloud_task: true`` and carries the
owner's ``owner_allow_list`` may also reach ``REVERSIBLE_WRITE``; the worker then makes each
write only on a listed site. ``EXTERNAL_COMMUNICATION`` and ``HIGH_IMPACT`` are never
reachable from the cloud, listed site or not (pending the owner's review).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .. import cloud_allowlist, policy
from ..errors import BrowserError, ErrorClass

CLOUD_SESSION_CLASSES: frozenset[policy.RiskClass] = policy.RESEARCH_SESSION_CLASSES

#: Contract v1.9: the most a cloud task's session may hold.
CLOUD_TASK_CLASSES: frozenset[policy.RiskClass] = CLOUD_SESSION_CLASSES | {
    policy.RiskClass.REVERSIBLE_WRITE
}

#: The cloud worker has one dedicated profile; the owner's own browser is not reachable here.
ALLOWED_PROFILES = frozenset({"research"})

#: What the cloud device launches, whatever a ``session_open`` asks for: the image carries
#: Playwright's Chromium and no Google Chrome, and the container has no display.
CLOUD_CHANNEL = "chromium"
CLOUD_VISIBLE = False


def _refuse(message: str, **evidence: Any) -> BrowserError:
    return BrowserError(
        ErrorClass.SECURITY_SCOPE_ERROR, message, retryable=False, evidence=evidence or None
    )


def assert_policy_within(
    classes: Iterable[policy.RiskClass | str],
    ceiling: frozenset[policy.RiskClass] = CLOUD_SESSION_CLASSES,
) -> None:
    """Refuse any class set that allows more than ``ceiling`` (READ + NAVIGATE unless the
    session is a cloud task)."""
    wanted = {policy.RiskClass(str(c)) for c in classes}
    extra = wanted - ceiling
    if extra:
        raise _refuse(
            f"the cloud worker's policy is {'+'.join(sorted(c.value for c in ceiling))}; "
            f"refused wider: {sorted(c.value for c in extra)}",
            refused=sorted(c.value for c in extra),
        )


def _cloud_task_ceiling(payload: dict[str, Any]) -> frozenset[policy.RiskClass]:
    """Contract v1.9: which ceiling this ``session_open`` gets. A malformed list is refused,
    never trimmed - a site silently dropped is a site the owner believes is listed."""
    cloud_task = payload.get("cloud_task", False)
    if not isinstance(cloud_task, bool):
        raise _refuse("session_open: cloud_task must be a boolean")
    if not cloud_task:
        if "owner_allow_list" in payload:
            raise _refuse("session_open: owner_allow_list belongs to a cloud task only")
        return CLOUD_SESSION_CLASSES
    sites = payload.get("owner_allow_list")
    if not isinstance(sites, list):
        raise _refuse("session_open: a cloud task carries owner_allow_list, a list of sites")
    for site in sites:
        if not cloud_allowlist.valid_site(site):
            raise _refuse(
                f"session_open: owner_allow_list holds {site!r}, not a registrable domain",
                site=str(site)[:80],
            )
    return CLOUD_TASK_CLASSES


def clamp_command(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the payload the worker may see. Only ``session_open`` carries a policy, and
    only it says which browser is launched and whether it has a window."""
    if capability != "browser.session_open":
        return payload
    profile = payload.get("profile", "research")
    if profile not in ALLOWED_PROFILES:
        raise _refuse(f"the cloud worker has no profile {profile!r}", profile=str(profile))
    ceiling = _cloud_task_ceiling(payload)
    out = dict(payload)
    session_policy = dict(out.get("policy") or {})
    if "allowed_risk_classes" in session_policy:
        requested = policy.parse_risk_classes(session_policy["allowed_risk_classes"])
        assert_policy_within(requested, ceiling)
        session_policy["allowed_risk_classes"] = sorted(c.value for c in requested)
    else:
        session_policy["allowed_risk_classes"] = sorted(c.value for c in CLOUD_SESSION_CLASSES)
    # Cloud Core's gateway sends one session_open for every device, and it says "visible
    # Chrome" (right for the owner's machines). The worker takes both from the payload ahead
    # of its own `--channel chromium --headless`; here they are not the caller's to choose.
    session_policy["visible"] = CLOUD_VISIBLE
    out["policy"] = session_policy
    out["channel"] = CLOUD_CHANNEL
    return out
