"""Routing that knows how sure it is (B51 req 740, 742-745).

The deterministic router (``app.voice.intents.resolve_intent``, row 741) stays the router.
Around it:

* **743 - confidence.** A transparent score of HOW a route was decided, not a probability
  anyone measured: a rule route is 0.95; a single content word that acts on the world
  (the most misroute-prone shape B26 measured) is 0.7; removed fillers cost 0.05; an
  unrouted utterance is 0.0.
* **744 - a question instead of a guess.** An unrouted utterance that names two capability
  families asks which one; one family asks what to do with it; nothing named asks nothing.
* **740/742 - a model, only for what the rules left unrouted, only under the owner's flag.**
  Its choice must be one of the registry's tools and never an acting intent, a stop or an
  eye-disable - the safety router's verbs are never a model's to decide - and below
  :data:`MIN_MODEL_CONFIDENCE` it is not taken. A model that fails is silence, never an
  exception in the owner's turn.
* **745 - "bunu".** A deictic word points at the most recently focused object of any kind,
  when that focus is fresh and the word is not a time ("bu hafta").
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.voice.intents import (
    _CAPABILITY_FAMILY_STEMS,
    _DEICTIC_WORDS,
    CAPABILITY_BY_INTENT,
    QUERY_TOOL_BY_INTENT,
    Intent,
    ResolvedIntent,
    _has,
)
from app.voice.route_telemetry import ACTING_INTENTS

logger = get_logger("app.voice.intent_router")

ROUTE_SOURCE_RULE: Final = "rule"
ROUTE_SOURCE_MODEL: Final = "model"
ROUTE_SOURCE_NONE: Final = "none"
MODEL_MATCH_PREFIX: Final = "model:"
MIN_MODEL_CONFIDENCE: Final = 0.7
#: The intents a model may never produce, whatever it says: everything that acts on the
#: world, the stop, the eye-disable, and "nothing" itself.
NEVER_FROM_MODEL: Final[frozenset[Intent]] = frozenset(
    {*ACTING_INTENTS, Intent.STOP, Intent.EYE_DISABLE, Intent.NONE}
)
#: How long a focus stays the referent of "bunu".
REFERENCE_FRESH_FOR: Final = timedelta(minutes=30)
_TEMPORAL_AFTER_DEICTIC: Final[frozenset[str]] = frozenset(
    {
        "hafta",
        "haftaki",
        "gün",
        "gun",
        "ay",
        "yıl",
        "yil",
        "sabah",
        "akşam",
        "aksam",
        "gece",
        "sefer",
    }
)
_FAMILY_TR: Final[dict[str, str]] = {
    "mail": "mail",
    "calendar": "takvim",
    "alarm": "alarm",
    "memory": "hafıza",
    "research": "araştırma",
    "news": "haberler",
    "weather": "hava durumu",
    "routine": "rutinler",
    "document": "belge",
    "file": "dosya",
    "display": "ekran",
    "eye": "kamera",
    "media": "medya",
    "app": "uygulama",
    "operator": "bilgisayar",
    "clock": "saat",
    "location": "konum",
    "pronunciation": "telaffuz",
    "scene": "3B sahne",
    "release": "sürüm",
    "briefing": "brifing",
    "narration": "seslendirme",
    "creative": "görsel",
}


# ------------------------------------------------------------------------------ 743


def score_confidence(resolved: ResolvedIntent) -> float:
    """How the route was decided, as a number a caller can compare (module docstring)."""
    if resolved.intent is Intent.NONE:
        return 0.0
    if resolved.matched.startswith(MODEL_MATCH_PREFIX):
        return round(max(0.0, min(1.0, resolved.confidence)), 2)
    score = 0.95
    content = [t for t in resolved.tokens if t not in _DEICTIC_WORDS]
    if resolved.intent in ACTING_INTENTS and len(content) <= 1:
        score = 0.7
    if resolved.fillers_removed:
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 2)


# ------------------------------------------------------------------------------ 744


def families_named(tokens: Sequence[str]) -> tuple[str, ...]:
    """Every capability family the words name, in the router's own family order."""
    found: list[str] = []
    for stems, family in _CAPABILITY_FAMILY_STEMS:
        if family not in found and _has(tuple(tokens), *stems):
            found.append(family)
    return tuple(found)


def clarification_for(resolved: ResolvedIntent) -> str | None:
    """The question to ask instead of guessing, or None when there is nothing to ask about."""
    if resolved.intent is not Intent.NONE:
        return None
    families = families_named(resolved.tokens)
    if len(families) >= 2:
        names = [_FAMILY_TR.get(f, f) for f in families[:3]]
        return "Hangisini kastettiniz efendim: " + " ya da ".join(names) + "?"
    if len(families) == 1:
        family = _FAMILY_TR.get(families[0], families[0]).capitalize()
        return f"{family} ile ne yapmamı istersiniz efendim?"
    return None


# ------------------------------------------------------------------------------ 742


def tool_for(intent: Intent) -> str | None:
    return CAPABILITY_BY_INTENT.get(intent) or QUERY_TOOL_BY_INTENT.get(intent)


def model_candidates(registry_names: set[str]) -> tuple[Intent, ...]:
    """The intents a model may choose among: each has a registered tool, none acts."""
    table = {**CAPABILITY_BY_INTENT, **QUERY_TOOL_BY_INTENT}
    return tuple(
        sorted(
            (
                i
                for i, tool in table.items()
                if tool in registry_names and i not in NEVER_FROM_MODEL
            ),
            key=lambda i: i.value,
        )
    )


def registry_candidates() -> tuple[Intent, ...]:
    from app.voice.realtime_sessions.tools import default_registry

    return model_candidates(set(default_registry().names()))


# ------------------------------------------------------------------------------ 740


class IntentModel(Protocol):
    name: str

    def choose(self, text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """``{"intent": <value or "none">, "confidence": 0..1}``, or None when it could not
        answer. Never raises."""
        ...


@dataclass(slots=True)
class ScriptedIntentModel:
    answer: dict[str, Any] | None
    name: str = "scripted"
    asked: list[str] = field(default_factory=list)

    def choose(self, text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        self.asked.append(text)
        return dict(self.answer) if self.answer is not None else None


@dataclass(slots=True)
class AnthropicIntentModel:
    model: str = "claude-sonnet-5"
    api_key: str | None = None
    timeout_s: float = 15.0
    transport: httpx.BaseTransport | None = None
    name: str = "anthropic"

    def _key(self) -> str:
        return (
            self.api_key
            or os.environ.get("PAGENTOS_ANTHROPIC_API_KEY", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def choose(self, text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        key = self._key()
        if not key or not candidates:
            return None
        values = [str(c["intent"]) for c in candidates] + ["none"]
        body = {
            "model": self.model,
            "max_tokens": 256,
            "system": (
                "You route a Turkish voice command to exactly one intent from the list, or "
                "'none'. Choose 'none' unless one intent clearly matches. Never invent one."
            ),
            "tools": [
                {
                    "name": "choose_intent",
                    "description": "The single intent the owner's sentence asks for.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": values},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["intent", "confidence"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "choose_intent"},
            "messages": [
                {
                    "role": "user",
                    "content": "Candidates:\n"
                    + json.dumps(candidates, ensure_ascii=False)
                    + "\n\nSentence: "
                    + text,
                }
            ],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages", headers=headers, json=body
                )
            if response.status_code != 200:
                logger.warning("intent_model_http", status=response.status_code)
                return None
            for block in response.json().get("content") or []:
                if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    return dict(block["input"])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("intent_model_failed", error_class=type(exc).__name__)
        return None


@dataclass(frozen=True, slots=True)
class RoutedIntent:
    resolved: ResolvedIntent
    source: str
    confidence: float
    clarification: str | None


class CompositeIntentRouter:
    """Rules first and always; the model only for an unrouted utterance, only under the
    owner's flag, only among :func:`registry_candidates`; a question when neither routes."""

    def __init__(
        self,
        model: IntentModel | None = None,
        *,
        enabled: bool | Callable[[], bool] = False,
        candidates: Callable[[], tuple[Intent, ...]] = registry_candidates,
        min_confidence: float = MIN_MODEL_CONFIDENCE,
        clarify_aloud: bool | Callable[[], bool] = False,
    ) -> None:
        self.model = model
        self._enabled = enabled
        self._candidates = candidates
        self.min_confidence = min_confidence
        self._clarify_aloud = clarify_aloud

    @property
    def model_enabled(self) -> bool:
        flag = self._enabled() if callable(self._enabled) else bool(self._enabled)
        return flag and self.model is not None

    @property
    def clarify_aloud(self) -> bool:
        """744: whether the clarification question is SPOKEN (a sideband say) rather than only
        recorded. Off by default: the realtime model has already heard the utterance and may
        be answering it, and a pushed question could overlap that answer - a judgement for a
        live session, not for a unit test."""
        flag = self._clarify_aloud() if callable(self._clarify_aloud) else bool(self._clarify_aloud)
        return flag

    def route(self, text: str, resolved: ResolvedIntent) -> RoutedIntent:
        if resolved.intent is not Intent.NONE:
            confidence = score_confidence(resolved)
            return RoutedIntent(
                replace(resolved, confidence=confidence), ROUTE_SOURCE_RULE, confidence, None
            )
        if self.model_enabled and self.model is not None and resolved.tokens:
            options = self._candidates()
            answer = self.model.choose(
                text, [{"intent": i.value, "tool": tool_for(i)} for i in options]
            )
            chosen = self._accept(answer, options)
            if chosen is not None:
                intent, confidence = chosen
                routed = replace(
                    resolved,
                    intent=intent,
                    matched=f"{MODEL_MATCH_PREFIX}{self.model.name}",
                    confidence=confidence,
                )
                return RoutedIntent(routed, ROUTE_SOURCE_MODEL, confidence, None)
        return RoutedIntent(resolved, ROUTE_SOURCE_NONE, 0.0, clarification_for(resolved))

    def _accept(
        self, answer: dict[str, Any] | None, options: tuple[Intent, ...]
    ) -> tuple[Intent, float] | None:
        if not isinstance(answer, dict):
            return None
        try:
            intent = Intent(str(answer.get("intent")))
            confidence = float(answer.get("confidence"))
        except (ValueError, TypeError):
            return None
        if intent in NEVER_FROM_MODEL or intent not in options:
            return None
        if not 0.0 <= confidence <= 1.0 or confidence < self.min_confidence:
            return None
        return intent, round(confidence, 2)


_router: CompositeIntentRouter | None = None


def get_intent_router() -> CompositeIntentRouter:
    """The router ``create_app`` installs; rules-only when nothing did."""
    global _router
    if _router is None:
        _router = CompositeIntentRouter()
    return _router


def set_intent_router(router: CompositeIntentRouter | None) -> None:
    global _router
    _router = router


# ------------------------------------------------------------------------------ 745


def resolve_deictic_reference(
    db: Session | None, tokens: Sequence[str], *, now: datetime | None = None
) -> dict[str, str] | None:
    """What "bunu / şunu / onu" points at: the most recently focused object of ANY kind, if
    it was focused within :data:`REFERENCE_FRESH_FOR` and the word is not a time."""
    if db is None:
        return None
    words = list(tokens)
    deictic = False
    for index, token in enumerate(words):
        if token in _DEICTIC_WORDS:
            following = words[index + 1] if index + 1 < len(words) else ""
            if following in _TEMPORAL_AFTER_DEICTIC:
                continue
            deictic = True
            break
    if not deictic:
        return None
    from app.operator.models import ObjectFocusRow

    try:
        row = db.execute(
            select(ObjectFocusRow)
            .order_by(ObjectFocusRow.selected_at.desc(), ObjectFocusRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - a deployment without the focus table has no referent
        return None
    if row is None:
        return None
    moment = now or datetime.now(UTC)
    selected = row.selected_at if row.selected_at.tzinfo else row.selected_at.replace(tzinfo=UTC)
    if moment - selected > REFERENCE_FRESH_FOR:
        return None
    return {"kind": row.kind, "object_id": row.object_id, "label": row.label}


__all__ = [
    "MIN_MODEL_CONFIDENCE",
    "NEVER_FROM_MODEL",
    "ROUTE_SOURCE_MODEL",
    "ROUTE_SOURCE_NONE",
    "ROUTE_SOURCE_RULE",
    "AnthropicIntentModel",
    "CompositeIntentRouter",
    "IntentModel",
    "RoutedIntent",
    "ScriptedIntentModel",
    "clarification_for",
    "families_named",
    "get_intent_router",
    "model_candidates",
    "registry_candidates",
    "resolve_deictic_reference",
    "score_confidence",
    "set_intent_router",
]
