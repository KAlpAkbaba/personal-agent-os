"""M13 risk classes, capability->class mapping, click classification, enforcement.

Reference: packages/protocol/BROWSER_CAPABILITIES.md §1 and §4 (binding).

Five risk classes gate every ``browser.*`` operation before it runs:

- ``READ`` — inspection only, never changes page/browser state.
- ``NAVIGATE`` — changes what page/tab is loaded, but not page content.
- ``REVERSIBLE_WRITE`` — mutates form state (fill/select/check) or clicks a
  plain (non-submitting, non-navigating) control.
- ``EXTERNAL_COMMUNICATION`` — a click that submits a form / triggers a
  submit control: this can send data somewhere.
- ``HIGH_IMPACT`` — downloads, and any click whose accessible name matches a
  purchase/delete/send marker (English + Turkish).

Every session declares ``allowed_risk_classes``; :func:`enforce` refuses an
operation whose class is not in that set with ``security_scope_error``
(``retryable=False``, message names the class) BEFORE the operation runs —
this module never touches the page itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import BrowserError, ErrorClass

CAPABILITY_NAME_RE_SOURCE = r"^[a-z][a-z0-9_.]{1,63}$"


class RiskClass(StrEnum):
    READ = "READ"
    NAVIGATE = "NAVIGATE"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    HIGH_IMPACT = "HIGH_IMPACT"


#: All ``browser.*`` capability names this worker advertises/serves (contract §1).
#: Order matches the contract table; the worker's hello.capabilities uses this list.
CAPABILITIES: tuple[str, ...] = (
    "browser.session_open",
    "browser.session_close",
    "browser.worker_status",
    "browser.navigate",
    "browser.back",
    "browser.forward",
    "browser.tab_list",
    "browser.tab_new",
    "browser.tab_close",
    "browser.tab_select",
    "browser.inspect",
    "browser.find",
    "browser.click",
    "browser.fill",
    "browser.select_option",
    "browser.set_checked",
    "browser.scroll",
    "browser.wait",
    "browser.extract",
    "browser.snapshot",
    "browser.screenshot",
    "browser.download",
    "browser.search",
    "browser.fetch_evidence",
    # M18.3 (contract v1.2): the alarm media surface. NAVIGATE for the three
    # that change what the page is doing, READ for the pure status read.
    "browser.media_play",
    "browser.media_volume",
    "browser.media_status",
    "browser.media_stop",
)

#: Static risk class per capability. ``browser.click`` is intentionally absent
#: here — its class is resolved dynamically from the clicked element by
#: :func:`classify_click`.
CAPABILITY_RISK_CLASS: dict[str, RiskClass] = {
    "browser.session_open": RiskClass.NAVIGATE,
    "browser.session_close": RiskClass.NAVIGATE,
    "browser.worker_status": RiskClass.READ,
    "browser.navigate": RiskClass.NAVIGATE,
    "browser.back": RiskClass.NAVIGATE,
    "browser.forward": RiskClass.NAVIGATE,
    "browser.tab_list": RiskClass.NAVIGATE,
    "browser.tab_new": RiskClass.NAVIGATE,
    "browser.tab_close": RiskClass.NAVIGATE,
    "browser.tab_select": RiskClass.NAVIGATE,
    "browser.inspect": RiskClass.READ,
    "browser.find": RiskClass.READ,
    "browser.fill": RiskClass.REVERSIBLE_WRITE,
    "browser.select_option": RiskClass.REVERSIBLE_WRITE,
    "browser.set_checked": RiskClass.REVERSIBLE_WRITE,
    "browser.scroll": RiskClass.NAVIGATE,
    "browser.wait": RiskClass.READ,
    "browser.extract": RiskClass.READ,
    "browser.snapshot": RiskClass.READ,
    "browser.screenshot": RiskClass.READ,
    "browser.download": RiskClass.HIGH_IMPACT,
    "browser.search": RiskClass.NAVIGATE,
    "browser.fetch_evidence": RiskClass.NAVIGATE,
    "browser.media_play": RiskClass.NAVIGATE,
    "browser.media_volume": RiskClass.NAVIGATE,
    "browser.media_status": RiskClass.READ,
    "browser.media_stop": RiskClass.NAVIGATE,
}

#: Research sessions per contract §2: READ + NAVIGATE only.
RESEARCH_SESSION_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.READ, RiskClass.NAVIGATE})

#: Alarm media sessions (contract §2, M18.3): the SAME two classes. The media
#: surface never fills a field, never submits, never downloads and never clicks
#: anything — a wall is reported, not opened — so it needs nothing a research
#: session does not already have.
MEDIA_SESSION_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.READ, RiskClass.NAVIGATE})

#: All classes — used to validate a session_open payload's requested set.
ALL_RISK_CLASSES: frozenset[RiskClass] = frozenset(RiskClass)

# Accessible-name markers that make a click HIGH_IMPACT regardless of its
# element shape (contract §4; English + Turkish).
_HIGH_IMPACT_NAME_MARKERS: tuple[str, ...] = (
    "buy",
    "purchase",
    "pay",
    "delete",
    "remove",
    "send",
    "submit order",
    "satın al",
    "satin al",
    "öde",
    "ode",
    "sil",
    "gönder",
    "gonder",
)


@dataclass(frozen=True, slots=True)
class ResolvedElement:
    """Provider-neutral description of a click target, resolved BEFORE acting.

    ``tag``: lowercase HTML tag name. ``role``: computed/implicit ARIA role
    when known. ``name``: accessible name (visible text / aria-label / value).
    ``has_href``: true for ``a[href]``. ``is_submit``: true for a submit
    button/input, or any control whose click path is inside a ``<form>`` and
    would trigger its submission. ``has_onclick``: true when the element (or
    an ancestor up to the nearest link) carries a JS click handler — used to
    keep a JS-driven "link" out of the plain-NAVIGATE bucket per the
    contract's ``a[href] without onclick -> NAVIGATE`` rule.
    """

    tag: str
    role: str | None = None
    name: str = ""
    has_href: bool = False
    is_submit: bool = False
    has_onclick: bool = False


def classify_click(element: ResolvedElement) -> RiskClass:
    """Classify a resolved click target per contract §4, in priority order:

    1. Accessible-name HIGH_IMPACT markers (buy/purchase/pay/delete/remove/
       send/submit order/satın al/öde/sil/gönder) — checked first because a
       "Delete" button that also happens to be a submit control is still
       HIGH_IMPACT, not merely EXTERNAL_COMMUNICATION.
    2. A submit control, or a click on a path that submits an enclosing
       ``<form>`` -> EXTERNAL_COMMUNICATION.
    3. ``a[href]`` without an onclick handler -> NAVIGATE.
    4. Otherwise -> REVERSIBLE_WRITE.
    """
    lowered_name = (element.name or "").strip().lower()
    if any(marker in lowered_name for marker in _HIGH_IMPACT_NAME_MARKERS):
        return RiskClass.HIGH_IMPACT
    if element.is_submit:
        return RiskClass.EXTERNAL_COMMUNICATION
    if element.has_href and not element.has_onclick:
        return RiskClass.NAVIGATE
    return RiskClass.REVERSIBLE_WRITE


def enforce(
    allowed_risk_classes: frozenset[RiskClass] | set[RiskClass],
    risk_class: RiskClass,
    *,
    capability: str,
) -> None:
    """Raise ``security_scope_error`` unless ``risk_class`` is permitted.

    Enforced BEFORE the operation acts on the page (defence in depth — Cloud
    Core is expected to enforce the same rule before ever sending the
    command). ``retryable=False``: a policy refusal never resolves itself by
    retrying the same command.
    """
    if risk_class not in allowed_risk_classes:
        raise BrowserError(
            ErrorClass.SECURITY_SCOPE_ERROR,
            f"{capability}: risk class {risk_class} is not permitted by this "
            f"session's policy (allowed: {sorted(c.value for c in allowed_risk_classes)})",
            retryable=False,
            evidence={
                "capability": capability,
                "risk_class": str(risk_class),
                "allowed_risk_classes": sorted(c.value for c in allowed_risk_classes),
            },
        )


def parse_risk_classes(
    values: Any, *, field_name: str = "allowed_risk_classes"
) -> frozenset[RiskClass]:
    """Parse a JSON payload's risk-class list into a validated frozenset."""
    if not isinstance(values, list) or not values:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{field_name} must be a non-empty list of risk class names",
            retryable=False,
        )
    result: set[RiskClass] = set()
    for value in values:
        try:
            result.add(RiskClass(str(value)))
        except ValueError as exc:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"{field_name}: unknown risk class {value!r}; known: "
                f"{sorted(c.value for c in RiskClass)}",
                retryable=False,
            ) from exc
    return frozenset(result)


def narrow_reopen(
    existing: frozenset[RiskClass], requested: frozenset[RiskClass] | None
) -> frozenset[RiskClass]:
    """A second ``session_open`` never widens a session's policy (contract §2).

    ``requested is None`` (no policy in the reopen payload) keeps the
    existing set unchanged. Otherwise the effective set is the intersection —
    a reopen can only narrow, never grow, what a session may do.
    """
    if requested is None:
        return existing
    return existing & requested


__all__ = [
    "ALL_RISK_CLASSES",
    "CAPABILITIES",
    "CAPABILITY_NAME_RE_SOURCE",
    "CAPABILITY_RISK_CLASS",
    "MEDIA_SESSION_CLASSES",
    "RESEARCH_SESSION_CLASSES",
    "ResolvedElement",
    "RiskClass",
    "classify_click",
    "enforce",
    "narrow_reopen",
    "parse_risk_classes",
]
