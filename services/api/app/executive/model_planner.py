"""The model-backed executive planner (B38 req 550, 551, 552) behind the SAME gate.

M26 left ``ClaudeExecutivePlanner`` inert. This module wires the seam for real, on M26's
own terms (ADR-0089 decision 2): the model PROPOSES a graph as data, from a vocabulary it
is handed - the closed step kinds with their fixed profiles, the precondition checks, the
evidence kinds, the bounds - and the proposal is turned into a ``TaskGraph`` whose fixed
fields (risk class, compensation, evidence kind) are LOOKED UP from the kind, never taken
from the proposal, and then validated by ``app.executive.graph.validate_graph`` exactly as
the rule-based planner's own output is. A proposal that names a kind outside the
vocabulary, claims an authority, or breaks the DAG is a clarification to the owner, never
a run.

``CompositeExecutivePlanner`` is what the product uses: the deterministic shapes first
(the same directive always plans the same way), and only when no shape matches AND the
owner's flag is on does the model get asked. Off (the default), an unshaped directive is
the honest clarification M26 already gave.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx
from pydantic import ValidationError

from app.executive.graph import GraphValidationError, validate_graph
from app.executive.planner import PlanningClarificationNeeded, RuleBasedExecutivePlanner
from app.executive.spec import (
    DEFAULT_TIMEOUT_S_BY_KIND,
    EVIDENCE_KINDS,
    MAX_GOAL_CHARS,
    MAX_REPEAT_ROUNDS,
    MAX_RETRY_ATTEMPTS,
    MAX_STEPS,
    MAX_TIMEOUT_S,
    PLANNER_MODEL,
    PLANNER_RULE,
    PRECONDITION_CHECKS,
    RETRYABLE_ERROR_CLASSES,
    STEP_KIND_PROFILES,
    TaskGraph,
)
from app.logging import get_logger

logger = get_logger("app.executive.model_planner")

API_URL: Final = "https://api.anthropic.com/v1/messages"
API_VERSION: Final = "2023-06-01"
DEFAULT_MODEL: Final = "claude-sonnet-5"
MAX_TOKENS: Final = 4000

SYSTEM: Final = (
    "You plan ONE task graph for a personal assistant. Answer with the tool call only. Use "
    "only the step kinds in the vocabulary; every step's inputs reference earlier steps as "
    "'<step id>.<output>' or are literals; the last step is 'synthesis'. Never plan sending, "
    "paying, deleting or publishing - a mail.draft or calendar.propose step is where such a "
    "plan ends. Give each step a one-sentence rationale in Turkish."
)


def vocabulary() -> dict[str, Any]:
    """What the model may build from: the closed kinds with their fixed profiles, the
    checks, the evidence kinds and the bounds. Handed to the model verbatim, so the
    vocabulary it sees is the vocabulary the validator enforces."""
    return {
        "kinds": {
            kind: {
                "risk_class": profile.risk_class,
                "evidence": profile.evidence_kind,
                "compensation": profile.compensation,
                "description": profile.description,
                "default_timeout_s": DEFAULT_TIMEOUT_S_BY_KIND.get(kind, 120),
            }
            for kind, profile in STEP_KIND_PROFILES.items()
        },
        "precondition_checks": list(PRECONDITION_CHECKS),
        "evidence_kinds": list(EVIDENCE_KINDS),
        "retryable_errors": list(RETRYABLE_ERROR_CLASSES),
        "bounds": {
            "max_steps": MAX_STEPS,
            "max_timeout_s": MAX_TIMEOUT_S,
            "max_retry_attempts": MAX_RETRY_ATTEMPTS,
            "max_repeat_rounds": MAX_REPEAT_ROUNDS,
        },
    }


PROPOSAL_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string"},
                    "inputs": {"type": "object", "additionalProperties": {"type": "string"}},
                    "precondition": {
                        "type": "object",
                        "properties": {"check": {"type": "string"}, "arg": {"type": "string"}},
                    },
                    "min": {"type": "integer"},
                    "timeout_s": {"type": "integer"},
                    "retry": {
                        "type": "object",
                        "properties": {
                            "max_attempts": {"type": "integer"},
                            "backoff_s": {"type": "number"},
                            "only_on": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "repeat": {
                        "type": "object",
                        "properties": {"max_rounds": {"type": "integer"}},
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["id", "kind", "rationale"],
            },
        }
    },
    "required": ["steps"],
}


class PlannerModel(Protocol):
    name: str

    def propose(self, directive: str, folder: str | None, vocab: dict[str, Any]) -> dict[str, Any]:
        """A proposal: ``{"steps": [{id, kind, inputs?, precondition?, min?, timeout_s?,
        retry?, repeat?, rationale}]}``. Nothing else about a step is the model's to say."""
        ...


@dataclass(slots=True)
class ScriptedPlannerModel:
    proposal: dict[str, Any] | Callable[[str], dict[str, Any]]
    name: str = "scripted"
    asked: list[str] = field(default_factory=list)

    def propose(self, directive: str, folder: str | None, vocab: dict[str, Any]) -> dict[str, Any]:
        self.asked.append(directive)
        return self.proposal(directive) if callable(self.proposal) else dict(self.proposal)


@dataclass(slots=True)
class AnthropicPlannerModel:
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_s: float = 90.0
    transport: httpx.BaseTransport | None = None
    name: str = "anthropic"

    def _key(self) -> str:
        key = (
            self.api_key
            or os.environ.get("PAGENTOS_ANTHROPIC_API_KEY", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if not key:
            raise PlanningClarificationNeeded(
                "Model destekli planlama açık ama bir model anahtarı yapılandırılmamış efendim."
            )
        return key

    def propose(self, directive: str, folder: str | None, vocab: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "tools": [
                {
                    "name": "propose_graph",
                    "description": "The task graph for the owner's directive.",
                    "input_schema": PROPOSAL_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "propose_graph"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Directive (the owner's words): {directive}\n"
                        f"Folder in focus: {folder or 'none'}\n\n"
                        "Vocabulary:\n" + json.dumps(vocab, ensure_ascii=False)
                    ),
                }
            ],
        }
        headers = {
            "x-api-key": self._key(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PlanningClarificationNeeded(
                f"Planlama modeline ulaşamadım efendim ({type(exc).__name__})."
            ) from None
        if response.status_code != 200:
            raise PlanningClarificationNeeded(
                f"Planlama modeli cevap vermedi efendim ({response.status_code})."
            )
        try:
            data = response.json()
        except ValueError:
            raise PlanningClarificationNeeded(
                "Planlama modelinin cevabını okuyamadım efendim."
            ) from None
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return dict(block["input"])
        raise PlanningClarificationNeeded("Planlama modeli bir plan önermedi efendim.")


def graph_from_proposal(
    raw: dict[str, Any], directive: str, *, planner: str = PLANNER_MODEL
) -> TaskGraph:
    """The proposal -> a validated ``TaskGraph``. The fixed fields come from the kind's
    profile, so the proposal cannot smuggle an authority; a timeout the proposal omits is
    the kind's default (req 556); everything else is what the proposal said, bounded by
    the spec's own models."""
    steps_raw = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanningClarificationNeeded("Model bir adım önermedi efendim; planı kuramadım.")
    steps: list[dict[str, Any]] = []
    for item in steps_raw[: MAX_STEPS + 1]:
        if not isinstance(item, dict):
            raise PlanningClarificationNeeded("Modelin önerdiği adımlardan biri okunamadı efendim.")
        kind = str(item.get("kind") or "")
        profile = STEP_KIND_PROFILES.get(kind)
        if profile is None:
            raise PlanningClarificationNeeded(
                f"Model bilmediğim bir adım türü önerdi ({kind or 'boş'}); planı kuramadım efendim."
            )
        step: dict[str, Any] = {
            "id": str(item.get("id") or ""),
            "kind": kind,
            "inputs": {str(k): str(v) for k, v in (item.get("inputs") or {}).items()},
            "precondition": dict(item.get("precondition") or {"check": "none"}),
            "postcondition": {"evidence": profile.evidence_kind, "min": item.get("min")},
            "timeout_s": int(item.get("timeout_s") or DEFAULT_TIMEOUT_S_BY_KIND.get(kind, 120)),
            "retry": dict(item.get("retry") or {}),
            "repeat": dict(item.get("repeat") or {}),
            "risk_class": profile.risk_class,
            "compensation": profile.compensation,
            "rationale": str(item.get("rationale") or "")[:300] or None,
        }
        steps.append(step)
    try:
        graph = TaskGraph.model_validate(
            {"goal": directive[:MAX_GOAL_CHARS], "steps": steps, "planner": planner}
        )
        validate_graph(graph)
    except (ValidationError, GraphValidationError) as exc:
        logger.info("executive_model_proposal_refused", reason=str(exc)[:400])
        raise PlanningClarificationNeeded(
            "Modelin önerdiği plan kurallara uymadı efendim; planı kuramadım."
        ) from exc
    return graph


class ModelExecutivePlanner:
    """``ExecutivePlanner`` over a ``PlannerModel``: ask, then judge."""

    def __init__(self, model: PlannerModel) -> None:
        self.model = model

    def plan(self, directive: str, *, folder: str | None = None) -> TaskGraph:
        text = (directive or "").strip()
        if not text:
            raise PlanningClarificationNeeded("Ne yapmamı istediğinizi tam anlayamadım efendim.")
        proposal = self.model.propose(text, folder, vocabulary())
        return graph_from_proposal(proposal, text)


class CompositeExecutivePlanner:
    """Rules first, the model only for what the rules cannot shape and only under the
    owner's flag (req 550/551). The graph says which planner built it."""

    def __init__(
        self,
        rule: RuleBasedExecutivePlanner | None = None,
        model: ModelExecutivePlanner | None = None,
        *,
        enabled: bool | Callable[[], bool] = False,
    ) -> None:
        self.rule = rule or RuleBasedExecutivePlanner()
        self.model = model
        self._enabled = enabled

    @property
    def model_enabled(self) -> bool:
        flag = self._enabled() if callable(self._enabled) else bool(self._enabled)
        return flag and self.model is not None

    def plan(self, directive: str, *, folder: str | None = None) -> TaskGraph:
        try:
            graph = self.rule.plan(directive, folder=folder)
        except PlanningClarificationNeeded:
            if not self.model_enabled or self.model is None:
                raise
            return self.model.plan(directive, folder=folder)
        if graph.planner is None:
            graph.planner = PLANNER_RULE
        return graph


_planner: CompositeExecutivePlanner | None = None


def get_executive_planner() -> CompositeExecutivePlanner:
    """The process-wide planner ``create_app`` installs; a bare composite (rules only)
    when nothing did - a test that builds no application still plans deterministically."""
    global _planner
    if _planner is None:
        _planner = CompositeExecutivePlanner()
    return _planner


def set_executive_planner(planner: CompositeExecutivePlanner | None) -> None:
    global _planner
    _planner = planner


__all__ = [
    "AnthropicPlannerModel",
    "CompositeExecutivePlanner",
    "ModelExecutivePlanner",
    "PlannerModel",
    "ScriptedPlannerModel",
    "get_executive_planner",
    "graph_from_proposal",
    "set_executive_planner",
    "vocabulary",
]
