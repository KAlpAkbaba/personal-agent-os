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

#: v2 added the room (``eye.*``, ``owner.*``), routines and the owner-authorised
#: release path, plus the ``presence`` and ``routine`` subsystems. A v1 renderer
#: keeps working - it simply never sees the new states - and
#: ``GET /v1/ui/state/contract`` reports the true version, so a caller that
#: enumerates the vocabulary should re-read it rather than cache it.
#: v3 (M18.3 spec §7) adds the wake-alarm channel (``alarm.armed`` .. ``alarm.failed``) and
#: the ambient display channel (``display.on`` / ``display.off``), plus the ``ambient``
#: subsystem. Same additive rule as v2: a v2 renderer keeps working and simply never sees
#: the new states, and ``GET /v1/ui/state/contract`` reports the true version.
#: v4 (M19 Digital Operator spec §4, §7) adds the operator's own channel
#: (``operator.running`` / ``operator.verifying`` / ``operator.failed``), published from
#: ``OperatorService`` transitions, plus the ``operator`` subsystem. Same additive rule.
#: v5 (M20 File & Document Intelligence spec §3) adds ``document.analysis``, published
#: around every device call and answer with metadata ``{file, part, refs?}`` (``refs``
#: only on an answer/summary that cited any — the ONE structured metadata value the
#: publisher allows through, as ``[{ref, path}]``, never ``excerpt``: see
#: ``app.uistate.publisher._clean_metadata``), plus the ``documents`` subsystem. Same
#: additive rule: a v4 renderer keeps working and simply never sees it.
#: v6 (M21 Mail & Calendar spec §3, ADR-0084) adds ``mail.activity`` (metadata
#: ``{folder?, subject?, draft_state?}``) and ``calendar.activity`` (metadata
#: ``{range?, event?, proposal_state?}``), plus the ``mail``/``calendar`` subsystems. Same
#: additive rule: a v5 renderer keeps working and simply never sees them.
#: v7 (M22 Artifact Factory spec §6, ADR-0085) adds ``artifact.factory``, published while a
#: render is being made and while an INDEPENDENT parser reopens it and compares it to what
#: was asked, with metadata ``{title?, format?, verdict?, failing_ref?}`` (verdict one of
#: ``rendering | valid | invalid``), plus the ``artifacts`` subsystem. Same additive rule: a
#: v6 renderer keeps working and simply never sees it.
CONTRACT_VERSION = 7

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
    # --- M18. The Core stops being a picture of the assistant's own activity and starts
    # being a picture of the ROOM as well: whether the camera is perceiving, whether the
    # owner is there, and what the system is about to do on their behalf. Every one of
    # these is published because something entered the state, never to make the renderer
    # look busy (ADR-0052 §2 still holds).
    EYE_ACTIVE = "eye.active"  # local perception is running
    EYE_DISABLED = "eye.disabled"  # the owner turned it off, or it never started

    #: Presence is PROBABILISTIC and every one of these carries a confidence. The renderer
    #: must be able to show "likely" without the model claiming certainty it does not have.
    #: Published on a sustained TRANSITION, never per observation: the fusion engine in
    #: app/presence/engine.py has already collapsed raw observations into a held state,
    #: and a renderer redrawing on every camera frame would be showing noise, not truth.
    OWNER_PRESENT = "owner.present"
    OWNER_AWAY = "owner.away"
    OWNER_RETURNED = "owner.returned"
    OWNER_RESTING = "owner.resting"
    OWNER_LIKELY_ASLEEP = "owner.likely_asleep"
    OWNER_AWAKE = "owner.awake"

    #: A routine is armed when it exists and is being watched; triggered when its
    #: trigger fired AND its conditions passed. An alarm is called out separately
    #: because it is the one action the owner experiences as an interruption.
    ROUTINE_ARMED = "routine.armed"
    ROUTINE_TRIGGERED = "routine.triggered"
    ALARM_TRIGGERED = "alarm.triggered"

    #: M18.3 (spec §7): the wake alarm's own channel. ``alarm.*`` never displaces a Core
    #: that is genuinely thinking or speaking — it is its own channel, like release
    #: (ADR-0056/0065) — and every one of these is published because the alarm ENTERED
    #: that state, never to animate a surge. ``alarm.triggered`` above stays what it was:
    #: the routine engine's "this routine's alarm action fired"; these are the physical
    #: wake sequence's own states.
    ALARM_ARMED = "alarm.armed"
    ALARM_FIRING = "alarm.firing"
    ALARM_PLAYING = "alarm.playing"
    ALARM_GREETING = "alarm.greeting"
    ALARM_SNOOZED = "alarm.snoozed"
    ALARM_STOPPED = "alarm.stopped"
    ALARM_COMPLETED = "alarm.completed"
    ALARM_FAILED = "alarm.failed"

    #: M18.3 (spec §7): the ambient display channel. Ambient-band only — the display is
    #: never the Core. Published from an OBSERVED change of the device's own power state
    #: (the companion's power-setting notification, relayed on the heartbeat), never from
    #: having asked for one: a display-off command that the device refused must not leave
    #: the strip claiming the screens are dark.
    DISPLAY_ON = "display.on"
    DISPLAY_OFF = "display.off"

    #: The owner-authorised release path (ADR-0055). These exist so a deployment is
    #: WATCHABLE - and so a failure visibly becomes a rollback instead of a false success.
    RELEASE_OWNER_APPROVAL_REQUIRED = "release.owner_approval_required"
    RELEASE_OWNER_AUTHORIZED = "release.owner_authorized"
    RELEASE_QUALIFYING = "release.qualifying"
    RELEASE_DEPLOYING = "release.deploying"
    RELEASE_VERIFYING = "release.verifying"
    RELEASE_LIVE = "release.live"
    RELEASE_ROLLBACK = "release.rollback"

    #: M19 (spec §4, §7): the Digital Operator's own channel, like ``alarm.*`` never
    #: displacing a Core that is genuinely thinking or speaking. Published from
    #: ``OperatorService`` transitions with metadata ``{step, capability, window_title}``,
    #: never to animate a surge.
    OPERATOR_RUNNING = "operator.running"
    OPERATOR_VERIFYING = "operator.verifying"
    OPERATOR_FAILED = "operator.failed"

    #: M20 (spec §3, §7): the File & Document Intelligence channel. Published around
    #: every device call and answer, metadata ``{file, part}`` (``part`` one of
    #: "summary"/"answer" when the state is about a specific kind of result) — never to
    #: animate a surge, the same rule every other channel here follows.
    DOCUMENT_ANALYSIS = "document.analysis"

    #: M21 (spec §3, §7): the Mail & Calendar channel. Published around a read, a
    #: draft/proposal read-back, and a confirmed send/commit — never to animate a surge,
    #: the same rule every other channel here follows. Metadata is identity only: a
    #: folder/subject/draft_state for mail, a range/event/proposal_state for calendar —
    #: never a body, an address book, or the spoken text (app.uistate.publisher's
    #: forbidden-key rule already refuses "body"/"content"/anything text-shaped).
    MAIL_ACTIVITY = "mail.activity"
    CALENDAR_ACTIVITY = "calendar.activity"

    #: M22 (spec §6, §7): the Artifact Factory's channel. Published while a render is
    #: being made and while it is reopened by an independent parser and compared to what
    #: was asked (ADR-0085 decision 3) — never to animate a surge, the same rule every
    #: other channel here follows. One token: the whole render -> reopen -> compare loop,
    #: with ``metadata.verdict`` naming where it is (``rendering | valid | invalid``).
    ARTIFACT_FACTORY = "artifact.factory"


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
    "presence",
    "routine",
    # M18.3: the ambient display policy publishes display.on/display.off (spec §7).
    "ambient",
    # M19: the Digital Operator publishes operator.running/verifying/failed (spec §7).
    "operator",
    # M20: File & Document Intelligence publishes document.analysis (spec §7).
    "documents",
    # M21: Mail & Calendar publishes mail.activity / calendar.activity (spec §7).
    "mail",
    "calendar",
    # M22: the Artifact Factory publishes artifact.factory (spec §7).
    "artifacts",
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
