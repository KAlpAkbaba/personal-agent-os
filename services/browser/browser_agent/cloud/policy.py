"""The cloud session policy: READ + NAVIGATE, never wider (ADR-0213 addendum).

Acting beyond that arrives, if ever, as a per-command decision from Cloud Core's allow-list;
it is never a wider default here, and a ``session_open`` cannot widen it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .. import policy
from ..errors import BrowserError, ErrorClass

CLOUD_SESSION_CLASSES: frozenset[policy.RiskClass] = policy.RESEARCH_SESSION_CLASSES

#: The cloud worker has one dedicated profile; the owner's own browser is not reachable here.
ALLOWED_PROFILES = frozenset({"research"})


def _refuse(message: str, **evidence: Any) -> BrowserError:
    return BrowserError(
        ErrorClass.SECURITY_SCOPE_ERROR, message, retryable=False, evidence=evidence or None
    )


def assert_policy_within(classes: Iterable[policy.RiskClass | str]) -> None:
    """Refuse any class set that allows more than READ + NAVIGATE."""
    wanted = {policy.RiskClass(str(c)) for c in classes}
    extra = wanted - CLOUD_SESSION_CLASSES
    if extra:
        raise _refuse(
            "the cloud worker's policy is READ+NAVIGATE; refused wider: "
            f"{sorted(c.value for c in extra)}",
            refused=sorted(c.value for c in extra),
        )


def clamp_command(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the payload the worker may see. Only ``session_open`` carries a policy."""
    if capability != "browser.session_open":
        return payload
    profile = payload.get("profile", "research")
    if profile not in ALLOWED_PROFILES:
        raise _refuse(f"the cloud worker has no profile {profile!r}", profile=str(profile))
    out = dict(payload)
    session_policy = dict(out.get("policy") or {})
    if "allowed_risk_classes" in session_policy:
        requested = policy.parse_risk_classes(session_policy["allowed_risk_classes"])
        assert_policy_within(requested)
        session_policy["allowed_risk_classes"] = sorted(c.value for c in requested)
    else:
        session_policy["allowed_risk_classes"] = sorted(c.value for c in CLOUD_SESSION_CLASSES)
    out["policy"] = session_policy
    return out
