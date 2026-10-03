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

from . import risk_markers
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
    # B31 (contract v1.5): the operation behind the ``uploads`` flag.
    "browser.upload",
    "browser.search",
    "browser.fetch_evidence",
    # M18.3 (contract v1.2): the alarm media surface. NAVIGATE for the three
    # that change what the page is doing, READ for the pure status read.
    "browser.media_play",
    "browser.media_volume",
    "browser.media_status",
    "browser.media_stop",
    # Contract v1.6 (ADR-0207): the page as a numbered list of what can be acted on.
    "browser.observe",
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
    "browser.upload": RiskClass.HIGH_IMPACT,
    "browser.search": RiskClass.NAVIGATE,
    "browser.fetch_evidence": RiskClass.NAVIGATE,
    "browser.media_play": RiskClass.NAVIGATE,
    "browser.media_volume": RiskClass.NAVIGATE,
    "browser.media_status": RiskClass.READ,
    "browser.media_stop": RiskClass.NAVIGATE,
    # Reads and changes nothing: no attribute is written into the page to number it.
    "browser.observe": RiskClass.READ,
}

#: Research sessions per contract §2: READ + NAVIGATE only.
RESEARCH_SESSION_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.READ, RiskClass.NAVIGATE})

#: Alarm media sessions (contract §2, M18.3): the SAME two classes. The media
#: surface never fills a field, never submits, never downloads and never clicks
#: anything — a wall is reported, not opened — so it needs nothing a research
#: session does not already have.
MEDIA_SESSION_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.READ, RiskClass.NAVIGATE})

#: The owner's own attached browser (v1.4, ADR-0113): EVERY class, because the owner
#: asked for every class after being told twice, in concrete terms, that it means the
#: agent can click, fill and submit as them on every site they are signed into. The
#: narrower default is still one line away (``RESEARCH_SESSION_CLASSES``) and a
#: ``session_open`` may always request a narrower set for a single session -- widening is
#: what is impossible, not narrowing.
OWNER_SESSION_CLASSES: frozenset[RiskClass] = frozenset(RiskClass)

#: All classes — used to validate a session_open payload's requested set.
ALL_RISK_CLASSES: frozenset[RiskClass] = frozenset(RiskClass)

# The words that make a click HIGH_IMPACT or EXTERNAL_COMMUNICATION regardless of the
# element's shape live in ``risk_markers`` (contract v1.6 item 10): whole words and whole
# phrases, English and Turkish, one JSON source for both sides. Until v1.6 they were
# SUBSTRINGS here - "sil" matched "silver", "pay" matched "paylaş".


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

    1. Accessible-name HIGH_IMPACT markers (``risk_markers.HIGH_IMPACT``, whole
       words and phrases) — checked first because a
       "Delete" button that also happens to be a submit control is still
       HIGH_IMPACT, not merely EXTERNAL_COMMUNICATION.
    2. A submit control, or a click on a path that submits an enclosing
       ``<form>`` -> EXTERNAL_COMMUNICATION.
    3. ``a[href]`` without an onclick handler -> NAVIGATE.
    4. Otherwise -> REVERSIBLE_WRITE.
    """
    if risk_markers.is_high_impact(element.name or ""):
        return RiskClass.HIGH_IMPACT
    # v1.6: a control NAMED as one that sends ("Onayla", "Yayınla", "Post") is one,
    # whether or not the page built it as a form's submit button.
    if element.is_submit or risk_markers.is_external_communication(element.name or ""):
        return RiskClass.EXTERNAL_COMMUNICATION
    if element.has_href and not element.has_onclick:
        return RiskClass.NAVIGATE
    return RiskClass.REVERSIBLE_WRITE


#: The writes contract v1.8 classifies, by their short name.
WRITE_ACTIONS: frozenset[str] = frozenset({"fill", "select_option", "set_checked"})


def classify_write(name: str, submits: bool, in_form: bool, action: str) -> RiskClass:
    """Contract v1.8 §4a: what CHANGING an element is - typing into it, choosing in it,
    ticking it - from its accessible name and its wiring, in the collector's terms.

    The Cloud Core's ``app.webtask.risk.classify_step`` makes the same decision from the
    observation; ``tests/unit/test_write_ceiling.py`` runs both on one table. A field's
    role does not make a write reversible: a ``<select>`` that buys on change is named by
    the page like any button.
    """
    if action not in WRITE_ACTIONS:
        return RiskClass.HIGH_IMPACT  # a write this module does not know is not a safe one
    if risk_markers.is_high_impact(name or ""):
        return RiskClass.HIGH_IMPACT
    if submits or risk_markers.is_external_communication(name or ""):
        return RiskClass.EXTERNAL_COMMUNICATION
    # Unnamed and wired: nothing says what it does, and where it sits says it may send.
    # Not text entry - typing sends nothing; what sends the form is judged when pressed.
    unnamed = not any(ch.isalnum() for ch in risk_markers.fold(name or ""))
    if unnamed and (submits or in_form) and action != "fill":
        return RiskClass.EXTERNAL_COMMUNICATION
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


#: Contract section 4, ascending. The order is the contract's.
RISK_ORDER: tuple[RiskClass, ...] = (
    RiskClass.READ,
    RiskClass.NAVIGATE,
    RiskClass.REVERSIBLE_WRITE,
    RiskClass.EXTERNAL_COMMUNICATION,
    RiskClass.HIGH_IMPACT,
)


def enforce_ceiling(risk_class: RiskClass, ceiling: Any, *, capability: str) -> None:
    """Contract v1.7: a consumer that gated a step tells the worker the class it gated
    at. The worker classifies the element it actually resolved, and a class ABOVE the
    ceiling is refused - the step was more than it looked, and it is never performed.
    ``None`` means the caller named no ceiling (every caller before v1.7)."""
    if ceiling is None:
        return
    try:
        limit = RiskClass(str(ceiling))
    except ValueError:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"{capability}: 'risk_ceiling' must be one of {', '.join(c.value for c in RISK_ORDER)}",
            retryable=False,
        ) from None
    if RISK_ORDER.index(risk_class) > RISK_ORDER.index(limit):
        raise BrowserError(
            ErrorClass.SECURITY_SCOPE_ERROR,
            f"{capability}: the element is {risk_class}, above the ceiling {limit} "
            "this step was gated at",
            retryable=False,
            evidence={
                "capability": capability,
                "risk_class": str(risk_class),
                "risk_ceiling": str(limit),
                "reason": "above_ceiling",
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
    "OWNER_SESSION_CLASSES",
    "RESEARCH_SESSION_CLASSES",
    "ResolvedElement",
    "RiskClass",
    "WRITE_ACTIONS",
    "classify_click",
    "classify_write",
    "enforce",
    "narrow_reopen",
    "parse_risk_classes",
]
