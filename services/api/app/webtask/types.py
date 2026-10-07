"""The vocabulary of the browser task loop (ADR-0207).

Plain, frozen data. Everything here round-trips through ``as_dict`` / ``from_dict``
because the task's state lives in a database row between rounds: a workflow that dies
resumes from what was WRITTEN, never from what a process remembered.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Final

# ------------------------------------------------------------------ risk

RISK_READ: Final = "READ"
RISK_NAVIGATE: Final = "NAVIGATE"
RISK_REVERSIBLE_WRITE: Final = "REVERSIBLE_WRITE"
RISK_EXTERNAL_COMMUNICATION: Final = "EXTERNAL_COMMUNICATION"
RISK_HIGH_IMPACT: Final = "HIGH_IMPACT"
#: Contract section 4, in ascending order. The order is the contract's, not a guess.
RISK_ORDER: Final[tuple[str, ...]] = (
    RISK_READ,
    RISK_NAVIGATE,
    RISK_REVERSIBLE_WRITE,
    RISK_EXTERNAL_COMMUNICATION,
    RISK_HIGH_IMPACT,
)
#: What runs on the owner's first word (his decision of 2026-09-19, unchanged here).
FREE_RISKS: Final = frozenset({RISK_READ, RISK_NAVIGATE, RISK_REVERSIBLE_WRITE})
#: What reads and moves: no write, so no allow-list is asked.
NO_WRITE_RISKS: Final = frozenset({RISK_READ, RISK_NAVIGATE})
#: The classes a task may reach in the CLOUD (ADR-0213 addendum; card cloud-task-loop-core):
#: a reversible write on a site the owner listed, and nothing that sends or cannot be
#: undone - the most restrictive safe option, pending the owner's review.
CLOUD_RISKS: Final[tuple[str, ...]] = (RISK_READ, RISK_NAVIGATE, RISK_REVERSIBLE_WRITE)

# ------------------------------------------------------------------ where a task runs

#: ``app.execution.rule.Target``'s values, as a task's state carries them. "" is a task
#: started before targets existed: the owner's Chrome, as it always was.
TARGET_CLOUD: Final = "cloud"
TARGET_OWNER_CHROME: Final = "owner_chrome"
TARGET_DEVICE: Final = "device"


def risk_rank(risk: str) -> int:
    return RISK_ORDER.index(risk)


def one_class_higher(risk: str) -> str:
    return RISK_ORDER[min(risk_rank(risk) + 1, len(RISK_ORDER) - 1)]


# ------------------------------------------------------------------ actions

ACTION_NAVIGATE: Final = "navigate"
ACTION_CLICK: Final = "click"
ACTION_FILL: Final = "fill"
ACTION_SELECT: Final = "select_option"
ACTION_CHECK: Final = "set_checked"
ACTION_SCROLL: Final = "scroll"
ACTION_BACK: Final = "back"
ACTION_DONE: Final = "done"
ACTION_ASK_OWNER: Final = "ask_owner"

#: The closed vocabulary. Each acting entry maps onto exactly one ``browser.*`` operation
#: of contract v1.6; there is no "evaluate", no "press", no coordinate.
ACTIONS: Final[tuple[str, ...]] = (
    ACTION_NAVIGATE,
    ACTION_CLICK,
    ACTION_FILL,
    ACTION_SELECT,
    ACTION_CHECK,
    ACTION_SCROLL,
    ACTION_BACK,
    ACTION_DONE,
    ACTION_ASK_OWNER,
)
ACTING: Final = frozenset(
    {
        ACTION_NAVIGATE,
        ACTION_CLICK,
        ACTION_FILL,
        ACTION_SELECT,
        ACTION_CHECK,
        ACTION_SCROLL,
        ACTION_BACK,
    }
)
#: Actions that name an element of the observation.
NEEDS_REF: Final = frozenset({ACTION_CLICK, ACTION_FILL, ACTION_SELECT, ACTION_CHECK})
CAPABILITY_OF: Final[dict[str, str]] = {
    ACTION_NAVIGATE: "browser.navigate",
    ACTION_CLICK: "browser.click",
    ACTION_FILL: "browser.fill",
    ACTION_SELECT: "browser.select_option",
    ACTION_CHECK: "browser.set_checked",
    ACTION_SCROLL: "browser.scroll",
    ACTION_BACK: "browser.back",
}

# ------------------------------------------------------------------ expectations

EXPECT_URL_CONTAINS: Final = "url_contains"
EXPECT_TEXT_PRESENT: Final = "text_present"
EXPECT_TEXT_ABSENT: Final = "text_absent"
EXPECT_ELEMENT_PRESENT: Final = "element_present"
EXPECT_ELEMENT_ABSENT: Final = "element_absent"
EXPECT_FIELD_HAS_VALUE: Final = "field_has_value"
EXPECT_CHECKED: Final = "checked"
EXPECT_PAGE_CHANGED: Final = "page_changed"
EXPECTATIONS: Final[tuple[str, ...]] = (
    EXPECT_URL_CONTAINS,
    EXPECT_TEXT_PRESENT,
    EXPECT_TEXT_ABSENT,
    EXPECT_ELEMENT_PRESENT,
    EXPECT_ELEMENT_ABSENT,
    EXPECT_FIELD_HAS_VALUE,
    EXPECT_CHECKED,
    EXPECT_PAGE_CHANGED,
)

# ------------------------------------------------------------------ task and ask vocabulary

STATUS_RUNNING: Final = "running"
STATUS_WAITING_OWNER: Final = "waiting_owner"
STATUS_DONE: Final = "done"
STATUS_FAILED: Final = "failed"
STATUS_CANCELLED: Final = "cancelled"
TERMINAL: Final = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED})

#: Why the loop stopped for the owner. ``confirm`` is the only one a word can turn into an
#: action; the others are answered by the owner DOING something and saying "devam".
ASK_CONFIRM: Final = "confirm"
ASK_LOGIN: Final = "login"
ASK_CANNOT_SEE: Final = "cannot_see"
ASK_SENSITIVE_FIELD: Final = "sensitive_field"
ASK_PAYMENT: Final = "payment"
ASK_DENIED_SITE: Final = "denied_site"
ASK_CHALLENGE: Final = "challenge"
ASK_QUESTION: Final = "question"
ASK_KINDS: Final[tuple[str, ...]] = (
    ASK_CONFIRM,
    ASK_LOGIN,
    ASK_CANNOT_SEE,
    ASK_SENSITIVE_FIELD,
    ASK_PAYMENT,
    ASK_DENIED_SITE,
    ASK_CHALLENGE,
    ASK_QUESTION,
)

FAIL_ROUND_BUDGET: Final = "round_budget_exhausted"
FAIL_TIME_BUDGET: Final = "time_budget_exhausted"
FAIL_STREAK: Final = "too_many_failed_rounds"
FAIL_LOOP: Final = "loop_detected"
FAIL_PLANNER: Final = "planner_unavailable"
FAIL_BROWSER: Final = "browser_unavailable"
FAIL_REFUSED: Final = "step_refused"
FAIL_REASONS: Final[tuple[str, ...]] = (
    FAIL_ROUND_BUDGET,
    FAIL_TIME_BUDGET,
    FAIL_STREAK,
    FAIL_LOOP,
    FAIL_PLANNER,
    FAIL_BROWSER,
    FAIL_REFUSED,
)


# ------------------------------------------------------------------ the observation

#: A link's address as the worker hands it out (``browser_agent.observe.clean_href``).
MAX_HREF_CHARS: Final = 512


def _href(raw: Any) -> str | None:
    """The worker's address, held to its own promise: http(s), one token, capped."""
    if not isinstance(raw, str) or len(raw) > MAX_HREF_CHARS:
        return None
    if not raw.lower().startswith(("http://", "https://")) or not raw.isprintable():
        return None
    return None if any(ch.isspace() for ch in raw) else raw


@dataclass(frozen=True, slots=True)
class Element:
    ref: str
    role: str
    name: str
    tag: str = ""
    state: tuple[str, ...] = ()
    in_form: bool = False
    submits: bool = False
    href_host: str | None = None
    in_viewport: bool = True
    sensitive: bool = False
    risk_hint: str = RISK_REVERSIBLE_WRITE
    #: A link's address: no query, no fragment. ``None`` for anything else.
    href: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Element:
        return cls(
            ref=str(raw.get("ref") or ""),
            role=str(raw.get("role") or ""),
            name=str(raw.get("name") or ""),
            tag=str(raw.get("tag") or ""),
            state=tuple(str(s) for s in (raw.get("state") or [])),
            in_form=bool(raw.get("in_form")),
            submits=bool(raw.get("submits")),
            href_host=(str(raw["href_host"]) if raw.get("href_host") else None),
            in_viewport=bool(raw.get("in_viewport", True)),
            sensitive=bool(raw.get("sensitive")),
            risk_hint=str(raw.get("risk_hint") or RISK_REVERSIBLE_WRITE),
            href=_href(raw.get("href")),
        )

    def as_dict(self) -> dict[str, Any]:
        out = {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "tag": self.tag,
            "state": list(self.state),
            "in_form": self.in_form,
            "submits": self.submits,
            "href_host": self.href_host,
            "in_viewport": self.in_viewport,
            "sensitive": self.sensitive,
            "risk_hint": self.risk_hint,
        }
        if self.href is not None:
            out["href"] = self.href
        return out


@dataclass(frozen=True, slots=True)
class Observation:
    """A ``browser.observe`` result, as the loop reads it (contract section 3c)."""

    observation_id: str
    url: str
    title: str
    page_kind: str
    elements: tuple[Element, ...]
    text: str
    injection_markers: int = 0
    truncated: bool = False

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> Observation:
        return cls(
            observation_id=str(result.get("observation_id") or ""),
            url=str(result.get("url") or ""),
            title=str(result.get("title") or ""),
            page_kind=str(result.get("page_kind") or "ok"),
            elements=tuple(
                Element.from_dict(e) for e in (result.get("elements") or []) if isinstance(e, dict)
            ),
            text=str(result.get("text") or ""),
            injection_markers=int(result.get("injection_markers") or 0),
            truncated=bool(result.get("truncated")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "url": self.url,
            "title": self.title,
            "page_kind": self.page_kind,
            "elements": [e.as_dict() for e in self.elements],
            "text": self.text,
            "injection_markers": self.injection_markers,
            "truncated": self.truncated,
        }

    def by_ref(self, ref: str | None) -> Element | None:
        if not ref:
            return None
        return next((e for e in self.elements if e.ref == ref), None)

    @property
    def flagged(self) -> bool:
        """The page carries instruction-like text: steps planned from it are gated
        one class higher (ADR-0207 c)."""
        return self.injection_markers > 0

    def digest(self) -> str:
        """What the page IS for the purpose of "have I been here": its controls and
        their state - not the observation's id, not the numbering of one visit."""
        # What is on the screen is part of where the loop IS: after a scroll the same
        # document is a different place, and scrolling a long page twice is not a loop.
        basis = [(e.role, e.name, e.tag, e.state, e.submits, e.in_viewport) for e in self.elements]
        blob = json.dumps(basis, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ the step


@dataclass(frozen=True, slots=True)
class Expectation:
    kind: str
    value: str = ""
    role: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "role": self.role}

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Expectation | None:
        if not raw or not raw.get("kind"):
            return None
        return cls(
            kind=str(raw["kind"]),
            value=str(raw.get("value") or ""),
            role=str(raw.get("role") or ""),
        )


@dataclass(frozen=True, slots=True)
class Step:
    """ONE step. Flat on purpose: it is what a model is asked for, and arrays of objects
    came back garbled from forced tool calls."""

    action: str
    ref: str | None = None
    value: str | None = None
    url: str | None = None
    checked: bool | None = None
    direction: str | None = None
    expect: Expectation | None = None
    why: str = ""
    #: ``done``: what the task found or did, for the owner. ``ask_owner``: the question.
    message: str = ""
    ask_kind: str = ""
    #: The planner's own word that it was unsure - the seam for the capable model.
    unsure: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ref": self.ref,
            "value": self.value,
            "url": self.url,
            "checked": self.checked,
            "direction": self.direction,
            "expect": self.expect.as_dict() if self.expect else None,
            "why": self.why,
            "message": self.message,
            "ask_kind": self.ask_kind,
            "unsure": self.unsure,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Step:
        return cls(
            action=str(raw.get("action") or ""),
            ref=(str(raw["ref"]) if raw.get("ref") else None),
            value=(str(raw["value"]) if raw.get("value") is not None else None),
            url=(str(raw["url"]) if raw.get("url") else None),
            checked=(bool(raw["checked"]) if raw.get("checked") is not None else None),
            direction=(str(raw["direction"]) if raw.get("direction") else None),
            expect=Expectation.from_dict(raw.get("expect")),
            why=str(raw.get("why") or ""),
            message=str(raw.get("message") or ""),
            ask_kind=str(raw.get("ask_kind") or ""),
            unsure=bool(raw.get("unsure")),
        )

    def digest(self, element: Element | None = None) -> str:
        """The identity of this step for idempotency and for binding a confirmation: the
        action and what it acts ON, by what the element IS - a reference is only a number
        in one observation, and the confirmed step has to be recognisable in the next."""
        basis = {
            "action": self.action,
            "value": self.value,
            "url": self.url,
            "checked": self.checked,
            "element": [element.role, element.name, element.tag] if element else None,
        }
        blob = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ the round


@dataclass(frozen=True, slots=True)
class Round:
    """One line of "what did you do on this screen". No typed value, ever: a value is
    recorded by its length and kind."""

    index: int
    site: str
    action: str
    element: str = ""
    role: str = ""
    risk: str = ""
    outcome: str = ""
    verified: bool | None = None
    detail: str = ""
    value_chars: int | None = None
    confirmed_by: str = ""
    planner: str = ""
    flagged: bool = False
    state_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "site": self.site,
            "action": self.action,
            "element": self.element,
            "role": self.role,
            "risk": self.risk,
            "outcome": self.outcome,
            "verified": self.verified,
            "detail": self.detail,
            "value_chars": self.value_chars,
            "confirmed_by": self.confirmed_by,
            "planner": self.planner,
            "flagged": self.flagged,
            "state_key": self.state_key,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Round:
        return cls(
            index=int(raw.get("index") or 0),
            site=str(raw.get("site") or ""),
            action=str(raw.get("action") or ""),
            element=str(raw.get("element") or ""),
            role=str(raw.get("role") or ""),
            risk=str(raw.get("risk") or ""),
            outcome=str(raw.get("outcome") or ""),
            verified=raw.get("verified"),
            detail=str(raw.get("detail") or ""),
            value_chars=raw.get("value_chars"),
            confirmed_by=str(raw.get("confirmed_by") or ""),
            planner=str(raw.get("planner") or ""),
            flagged=bool(raw.get("flagged")),
            state_key=str(raw.get("state_key") or ""),
        )


ROUND_ACTED: Final = "acted"
ROUND_VERIFY_FAILED: Final = "verify_failed"
ROUND_ACT_FAILED: Final = "act_failed"
ROUND_ASKED: Final = "asked_owner"
ROUND_DONE: Final = "done"
ROUND_REFUSED: Final = "refused"


@dataclass(slots=True)
class Pending:
    """What the loop is waiting for the owner about."""

    kind: str
    message: str
    #: ``confirm`` only: the step to run once the owner's word arrives, and what it was
    #: read back as - the facts the act re-checks before it clicks.
    step: Step | None = None
    step_digest: str = ""
    risk: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    asked_round: int = 0
    read_back_session_id: str = ""
    read_back_turn: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "step": self.step.as_dict() if self.step else None,
            "step_digest": self.step_digest,
            "risk": self.risk,
            "facts": dict(self.facts),
            "asked_round": self.asked_round,
            "read_back_session_id": self.read_back_session_id,
            "read_back_turn": self.read_back_turn,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Pending | None:
        if not raw or not raw.get("kind"):
            return None
        return cls(
            kind=str(raw["kind"]),
            message=str(raw.get("message") or ""),
            step=Step.from_dict(raw["step"]) if raw.get("step") else None,
            step_digest=str(raw.get("step_digest") or ""),
            risk=str(raw.get("risk") or ""),
            facts=dict(raw.get("facts") or {}),
            asked_round=int(raw.get("asked_round") or 0),
            read_back_session_id=str(raw.get("read_back_session_id") or ""),
            read_back_turn=raw.get("read_back_turn"),
        )
