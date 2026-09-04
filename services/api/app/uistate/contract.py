"""The UI-state vocabulary and event shape (ADR-0052).

Design rules, in order of importance:

1. **Truthful.** A state is published because a subsystem entered it, never to make
   something move on screen. `agent.thinking` means work is running; `agent.researching`
   means a research job is actually fetching; `evolution.shadow_ready` means a candidate
   passed its gates and is NOT live.
2. **Decoupled.** The renderer learns states, not internals. Subsystems may change freely
   as long as they keep publishing the same vocabulary.
3. **Content-free.** Metadata is identity, progress and severity — ids, bounded numbers,
   short machine tokens. No transcripts, no owner audio, no page text, no secrets: the
   same forbidden-key rule the voice and ledger surfaces use applies here.
4. **Bounded.** Every event fits in a small JSON object; the bus keeps a short tail so a
   client that connects late can draw the current state without replaying history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

CONTRACT_VERSION = 1

#: Metadata value bounds. Numbers are floats in [0, 1] except where noted; strings are
#: short machine tokens, never prose.
MAX_LABEL_CHARS = 64
MAX_METADATA_KEYS = 16


class UiState(StrEnum):
    """What the owner's interface may show. Extending this is a contract change."""

    # --- the agent itself -------------------------------------------------
    IDLE = "agent.idle"  # calm: nothing running
    LISTENING = "agent.listening"  # the owner is speaking to it
    THINKING = "agent.thinking"  # reasoning/planning between input and answer
    SPEAKING = "agent.speaking"  # narrating
    RESEARCHING = "agent.researching"  # a research job is discovering/fetching
    MEMORY_RETRIEVAL = "agent.memory_retrieval"  # recalling / consolidating memory
    TOOL_RUNNING = "agent.tool_running"  # a capability is executing
    WAITING_OWNER = "agent.waiting_owner"  # blocked on the owner (approval, verification)
    GOAL_COMPLETED = "agent.goal_completed"  # a goal reached its success criteria
    ERROR = "agent.error"  # a failure the owner may care about
    # --- the evolution lab ------------------------------------------------
    EVOLUTION_RESEARCHING = "evolution.researching"
    EVOLUTION_DESIGNING = "evolution.designing"
    EVOLUTION_BUILDING = "evolution.building"
    EVOLUTION_TESTING = "evolution.testing"
    EVOLUTION_SHADOW_READY = "evolution.shadow_ready"


UI_STATES: tuple[str, ...] = tuple(s.value for s in UiState)

#: Which subsystem published the state (the ledger's vocabulary, plus the UI itself).
SUBSYSTEMS: tuple[str, ...] = (
    "voice",
    "research",
    "browser",
    "memory",
    "experience",
    "goal",
    "cognitive",
    "self_model",
    "evolution",
    "deployment",
    "ledger",
    "system",
)

SEVERITIES: tuple[str, ...] = ("info", "notice", "warning", "critical")


def _clamp01(value: float | int | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class UiStateEvent:
    """One truthful state change, ready to be drawn.

    ``intensity`` is how much is going on (0..1) — a renderer may map it to how far the
    core expands or how fast it breathes. ``progress`` is real progress when the
    publisher knows it (0..1), ``None`` when it does not: a renderer must not invent a
    bar for work whose length is unknown.
    """

    state: UiState
    subsystem: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    intensity: float | None = None
    progress: float | None = None
    severity: str = "info"
    status: str | None = None
    #: What the state is about: a task, goal, module or session id, plus a short label.
    task_id: str | None = None
    goal_id: str | None = None
    module_id: str | None = None
    session_id: str | None = None
    label: str | None = None
    #: Bounded extra numbers/tokens (never content). Keys are checked by the publisher.
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "state": self.state.value,
            "subsystem": self.subsystem,
            "at": self.at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "intensity": _clamp01(self.intensity),
            "progress": _clamp01(self.progress),
            "severity": self.severity,
            "status": self.status,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "module_id": self.module_id,
            "session_id": self.session_id,
            "label": (self.label or "")[:MAX_LABEL_CHARS] or None,
            "metadata": dict(self.metadata),
        }


def ui_state_contract() -> dict[str, Any]:
    """What a renderer needs to know before it draws anything."""
    return {
        "contract_version": CONTRACT_VERSION,
        "states": list(UI_STATES),
        "subsystems": list(SUBSYSTEMS),
        "severities": list(SEVERITIES),
        "metadata_rules": {
            "max_keys": MAX_METADATA_KEYS,
            "value_kinds": ["number", "bool", "short_token"],
            "forbidden": "audio, transcripts, page text, secrets, owner content",
        },
        "intensity": "0..1, how much is going on; null when unknown",
        "progress": "0..1 real progress; null when the publisher does not know it",
    }


__all__ = [
    "CONTRACT_VERSION",
    "MAX_LABEL_CHARS",
    "MAX_METADATA_KEYS",
    "SEVERITIES",
    "SUBSYSTEMS",
    "UI_STATES",
    "UiState",
    "UiStateEvent",
    "ui_state_contract",
]
