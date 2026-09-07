"""Activity Ledger closed vocabulary (M16_ACTIVITY_LEDGER_SPEC.md §1.3).

Every ``event_type``, ``subsystem``, ``status``, ``severity`` and
``production_state`` the ledger will ever accept is enumerated HERE, not
discovered from whatever a caller happens to send — a writer with a typo in
an event type must fail loudly at write time, not become a new, unintended
category the Self Explanation engine (M16 §2) later has to explain around.

Deployment event types are the one open-ended slot: ``releases.component`` is
a free string (``app/selfhealing/models.py``) so a future component's
``deployment.<component>.released``/``deployment.<component>.rolled_back``
cannot be enumerated in advance. ``validate_event_type`` accepts the two
components known today (``cloud_core``, ``agent``) as literal constants and
any other component through the same shape via ``_DEPLOYMENT_EVENT_TYPE_RE`` —
a reversible choice (CLAUDE.md "Asking the owner"): tightening this to a
closed component list is a one-line change if the owner later wants it.

``EVENT_TYPE_INCIDENT_OPENED`` is likewise not in the spec's §1.3 list
verbatim, but §1.4's backfill table requires an ``incident.opened`` event for
every ``incidents`` row — an evident spec omission rather than a deliberate
exclusion, so it is added here as ordinary (non-reserved) vocabulary and
noted in DECISIONS.md.
"""

from __future__ import annotations

import re
from typing import Final

# --------------------------------------------------------------- subsystems

SUBSYSTEM_RESEARCH = "research"
SUBSYSTEM_BROWSER = "browser"
SUBSYSTEM_CLOUD_CORE = "cloud_core"
SUBSYSTEM_DEVICE_SERVICE = "device_service"
SUBSYSTEM_SESSION_COMPANION = "session_companion"
SUBSYSTEM_DEPLOYMENT = "deployment"
SUBSYSTEM_VOICE = "voice"
SUBSYSTEM_MEMORY = "memory"
SUBSYSTEM_GOAL = "goal"
SUBSYSTEM_SELF_MODEL = "self_model"
SUBSYSTEM_EVOLUTION = "evolution"
SUBSYSTEM_LEDGER = "ledger"
#: M18 Presence Engine + Active Eye (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2).
SUBSYSTEM_PRESENCE = "presence"
#: M18 Routine Engine (app.routines).
SUBSYSTEM_ROUTINE = "routine"
#: M18.3: the ambient display policy (app.ambient) — display power and the holdoffs that
#: keep it on. Its own subsystem rather than "presence" because presence is EVIDENCE and
#: this is POLICY acting on it: a reader asking "why did my screens go dark?" must not have
#: to separate the two by reading each row's detail.
SUBSYSTEM_AMBIENT = "ambient"

SUBSYSTEMS: Final[tuple[str, ...]] = (
    SUBSYSTEM_RESEARCH,
    SUBSYSTEM_BROWSER,
    SUBSYSTEM_CLOUD_CORE,
    SUBSYSTEM_DEVICE_SERVICE,
    SUBSYSTEM_SESSION_COMPANION,
    SUBSYSTEM_DEPLOYMENT,
    SUBSYSTEM_VOICE,
    SUBSYSTEM_MEMORY,
    SUBSYSTEM_GOAL,
    SUBSYSTEM_SELF_MODEL,
    SUBSYSTEM_EVOLUTION,
    SUBSYSTEM_LEDGER,
    SUBSYSTEM_PRESENCE,
    SUBSYSTEM_ROUTINE,
    SUBSYSTEM_AMBIENT,
)

# ------------------------------------------------------------------ statuses

STATUS_STARTED = "started"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PENDING = "pending"
STATUS_INFO = "info"
#: Action receipts (docs/M18_ACTION_CONTRACT.md §5.5, ADR-0063): an ``action.receipt``
#: row's ``status`` IS the receipt's terminal status, so "did the camera really close?"
#: is answerable from the status column without opening detail_json. ``verified`` = the
#: read-back matched the request and the state changed in this command; ``already`` =
#: nothing changed because it was already so; ``unverified`` = server and client disagree
#: or the read-back did not match. A refused or failed action uses STATUS_FAILED.
STATUS_VERIFIED = "verified"
STATUS_ALREADY = "already"
STATUS_UNVERIFIED = "unverified"

STATUSES: Final[tuple[str, ...]] = (
    STATUS_STARTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_PENDING,
    STATUS_INFO,
    STATUS_VERIFIED,
    STATUS_ALREADY,
    STATUS_UNVERIFIED,
)

# ----------------------------------------------------------------- severities

SEVERITY_INFO = "info"
SEVERITY_NOTICE = "notice"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

SEVERITIES: Final[tuple[str, ...]] = (
    SEVERITY_INFO,
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    SEVERITY_CRITICAL,
)

# ------------------------------------------------------------ production states

PRODUCTION_STATE_NA = "n/a"
PRODUCTION_STATE_IDEA = "idea"
PRODUCTION_STATE_DESIGNED = "designed"
PRODUCTION_STATE_BUILT = "built"
PRODUCTION_STATE_TESTED = "tested"
PRODUCTION_STATE_REVIEWED = "reviewed"
PRODUCTION_STATE_SHADOW_READY = "shadow_ready"
PRODUCTION_STATE_APPROVAL_REQUIRED = "approval_required"
PRODUCTION_STATE_DEPLOYED = "deployed"
PRODUCTION_STATE_ROLLED_BACK = "rolled_back"

PRODUCTION_STATES: Final[tuple[str, ...]] = (
    PRODUCTION_STATE_NA,
    PRODUCTION_STATE_IDEA,
    PRODUCTION_STATE_DESIGNED,
    PRODUCTION_STATE_BUILT,
    PRODUCTION_STATE_TESTED,
    PRODUCTION_STATE_REVIEWED,
    PRODUCTION_STATE_SHADOW_READY,
    PRODUCTION_STATE_APPROVAL_REQUIRED,
    PRODUCTION_STATE_DEPLOYED,
    PRODUCTION_STATE_ROLLED_BACK,
)

# -------------------------------------------------------------- event types

EVENT_TYPE_RESEARCH_PLANNED = "research.planned"
EVENT_TYPE_RESEARCH_COMPLETED = "research.completed"
EVENT_TYPE_RESEARCH_FAILED = "research.failed"
EVENT_TYPE_RESEARCH_QUALITY_GATE = "research.quality_gate"
#: The owner's own qualification verdict for a research run, recorded by the owner
#: command from its evidence file (digest in evidence_refs); never derived by the system.
EVENT_TYPE_RESEARCH_QUALIFIED = "research.qualified"
EVENT_TYPE_BROWSER_SESSION_OPENED = "browser.session.opened"
EVENT_TYPE_BROWSER_SESSION_CLOSED = "browser.session.closed"
EVENT_TYPE_BROWSER_SEARCH = "browser.search"
EVENT_TYPE_VOICE_SESSION_CREATED = "voice.session.created"
EVENT_TYPE_VOICE_SESSION_ATTACHED = "voice.session.attached"
EVENT_TYPE_VOICE_SESSION_CLOSED = "voice.session.closed"
EVENT_TYPE_VOICE_EXPLAINED = "voice.explained"
EVENT_TYPE_VOICE_NARRATION_PAUSED = "voice.narration.paused"
EVENT_TYPE_VOICE_NARRATION_RESUMED = "voice.narration.resumed"
EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED = "deployment.cloud_core.released"
EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK = "deployment.cloud_core.rolled_back"
EVENT_TYPE_DEPLOYMENT_AGENT_INSTALLED = "deployment.agent.installed"
EVENT_TYPE_DEPLOYMENT_AGENT_SKIPPED = "deployment.agent.skipped"
EVENT_TYPE_MEMORY_REMEMBERED = "memory.remembered"
EVENT_TYPE_LEDGER_BACKFILL = "ledger.backfill"
EVENT_TYPE_BRIEFING_QUEUED = "briefing.queued"
EVENT_TYPE_BRIEFING_DELIVERED = "briefing.delivered"
#: not in spec §1.3's list verbatim; see module docstring.
EVENT_TYPE_INCIDENT_OPENED = "incident.opened"

#: Goal Engine / Cognitive Core foundation (overnight plan Phase 4, added here
#: per this module's own rule: every event_type the ledger accepts is
#: enumerated, never discovered from a caller's payload). ``app.goals.service``
#: writes the first three on every goal lifecycle change; ``app.goals.cognitive``
#: writes the loop-specific ``step_executed``/``escalated``/``error`` events.
EVENT_TYPE_GOAL_CREATED = "goal.created"
EVENT_TYPE_GOAL_STATUS_CHANGED = "goal.status_changed"
EVENT_TYPE_GOAL_ACHIEVED = "goal.achieved"
EVENT_TYPE_GOAL_STEP_EXECUTED = "goal.step_executed"
EVENT_TYPE_GOAL_ESCALATED = "goal.escalated"
EVENT_TYPE_GOAL_ERROR = "goal.error"

#: M18 Presence Engine + Active Eye (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2, §5).
#: Only MEANINGFUL transitions get a ledger row — every raw observation does
#: NOT (spec §5: "not every observation - do not overcollect"); the fusion
#: engine (app.presence.engine.PresenceFusionEngine.add_observation) is the
#: only writer, and only when its own state actually changed.
EVENT_TYPE_PRESENCE_STATE_CHANGED = "presence.state_changed"
#: Recorded when app.presence.greeting.evaluate_greeting returns True and the
#: decision is acted on — the greeting's OWN cooldown accounting, not a
#: record of the voice narration itself (that is app.routines/app.voice's
#: concern, M18 item 3, not built by this change).
EVENT_TYPE_PRESENCE_GREETING_DELIVERED = "presence.greeting_delivered"
#: The Active Eye disable path (spec §2, §6) must be durable and observable;
#: an owner action, always explicit, never inferred from observations.
EVENT_TYPE_EYE_ENABLED = "eye.enabled"
EVENT_TYPE_EYE_DISABLED = "eye.disabled"
#: M18 Routine Engine (app.routines.service). Every backlog-style state change a routine
#: goes through — created, armed, triggering, executing, skipped, cancelled — writes exactly
#: one of these (task brief: "no transition may be invisible").
EVENT_TYPE_ROUTINE_CREATED = "routine.created"
EVENT_TYPE_ROUTINE_ARMED = "routine.armed"
EVENT_TYPE_ROUTINE_TRIGGERED = "routine.triggered"
EVENT_TYPE_ROUTINE_EXECUTED = "routine.executed"
EVENT_TYPE_ROUTINE_SKIPPED = "routine.skipped"
EVENT_TYPE_ROUTINE_CANCELLED = "routine.cancelled"
#: M18 dispatch visibility (ADR-0060, app.routines.dispatch). A failed or refused action
#: gets its OWN ledger row - never only a field buried inside routine.executed's
#: detail_json - because "which action, and why" must be answerable without reading
#: JSON blobs the vocabulary itself does not enumerate (module docstring's own rule,
#: applied to this package the same way it was already applied to every other one).
EVENT_TYPE_ROUTINE_ACTION_FAILED = "routine.action_failed"
EVENT_TYPE_ROUTINE_ACTION_REFUSED = "routine.action_refused"
#: M18 action grounding (docs/M18_ACTION_CONTRACT.md §4, §5.5; ADR-0063). Every mutating
#: capability the owner commands by voice ends in ONE ``action.receipt`` row (subsystem =
#: the capability's, action = the capability, status = the receipt's terminal status) - a
#: refusal included, because the refusal is the evidence. ``voice.state_answered`` records
#: that a live-state question was answered from the composer: fact keys and uncertainty
#: subjects only, never the sentence.
EVENT_TYPE_ACTION_RECEIPT = "action.receipt"
EVENT_TYPE_VOICE_STATE_ANSWERED = "voice.state_answered"
#: M18.3 wake alarms (app.alarms.service, spec §3.2). EVERY lifecycle transition writes
#: exactly one ``alarm.<state_lowercase>`` row, idempotent per (alarm_id, state,
#: occurrence): "did that alarm actually ring, and what did it do?" is answerable from the
#: ledger alone, which is the whole reason the owner qualification can be a record read
#: rather than a person watching a screen.
EVENT_TYPE_ALARM_SCHEDULED = "alarm.scheduled"
EVENT_TYPE_ALARM_ARMED = "alarm.armed"
EVENT_TYPE_ALARM_FIRING = "alarm.firing"
EVENT_TYPE_ALARM_DISPLAY_WAKING = "alarm.display_waking"
EVENT_TYPE_ALARM_MEDIA_STARTING = "alarm.media_starting"
EVENT_TYPE_ALARM_PLAYING = "alarm.playing"
EVENT_TYPE_ALARM_GREETING = "alarm.greeting"
EVENT_TYPE_ALARM_SNOOZED = "alarm.snoozed"
EVENT_TYPE_ALARM_STOPPED = "alarm.stopped"
EVENT_TYPE_ALARM_COMPLETED = "alarm.completed"
EVENT_TYPE_ALARM_CANCELLED = "alarm.cancelled"
EVENT_TYPE_ALARM_FAILED = "alarm.failed"
#: The device rang its own armed fallback because the cloud never reached it (spec §3.6d).
#: Not a failure of the alarm — the opposite: the fallback did exactly its job, and the
#: cloud reconciles rather than ringing a second time.
EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG = "alarm.local_fallback_rang"
#: A test alarm released everything it held (spec §8.1): media session closed, device
#: disarmed, one-shot routine resolved. Written on EVERY terminal state, never only the
#: happy one.
EVENT_TYPE_ALARM_CLEANED_UP = "alarm.cleaned_up"
#: M18.3 §3.6b: real keyboard/mouse activity, seen through the heartbeat's input-idle
#: reset. Physical owner input outranks passive inference (spec §1.3), so this row is both
#: the evidence and the start of the input holdoff.
EVENT_TYPE_OWNER_INPUT_ACTIVE = "owner.input_active"
#: M18.3 §3.9: the owner changed the ambient display policy, by voice or by REST.
EVENT_TYPE_AMBIENT_POLICY_CHANGED = "ambient.policy_changed"
#: ADR-0080: one row per Owner Utterance Suite run (app.voice.qualification) - the counts
#: and the bounded confusion rows, never a transcript. What the Living Core's voice routing
#: qualification state is derived from.
EVENT_TYPE_VOICE_QUALIFICATION = "voice.qualification"
#: M18.4 (spec §3.4, §16): the owner's pause switch for self-evolution is the latest of
#: these two rows; and one row per supervisor scan that opened at least one opportunity
#: (a quiet scan writes nothing - the ledger records facts, not heartbeats).
EVENT_TYPE_EVOLUTION_PAUSED = "evolution.paused"
EVENT_TYPE_EVOLUTION_RESUMED = "evolution.resumed"
EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED = "evolution.supervisor_scanned"

EVENT_TYPES: Final[tuple[str, ...]] = (
    EVENT_TYPE_RESEARCH_PLANNED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    EVENT_TYPE_RESEARCH_QUALITY_GATE,
    EVENT_TYPE_RESEARCH_QUALIFIED,
    EVENT_TYPE_BROWSER_SESSION_OPENED,
    EVENT_TYPE_BROWSER_SESSION_CLOSED,
    EVENT_TYPE_BROWSER_SEARCH,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    EVENT_TYPE_VOICE_SESSION_ATTACHED,
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_EXPLAINED,
    EVENT_TYPE_VOICE_NARRATION_PAUSED,
    EVENT_TYPE_VOICE_NARRATION_RESUMED,
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_RELEASED,
    EVENT_TYPE_DEPLOYMENT_CLOUD_CORE_ROLLED_BACK,
    EVENT_TYPE_DEPLOYMENT_AGENT_INSTALLED,
    EVENT_TYPE_DEPLOYMENT_AGENT_SKIPPED,
    EVENT_TYPE_MEMORY_REMEMBERED,
    EVENT_TYPE_LEDGER_BACKFILL,
    EVENT_TYPE_BRIEFING_QUEUED,
    EVENT_TYPE_BRIEFING_DELIVERED,
    EVENT_TYPE_INCIDENT_OPENED,
    EVENT_TYPE_GOAL_CREATED,
    EVENT_TYPE_GOAL_STATUS_CHANGED,
    EVENT_TYPE_GOAL_ACHIEVED,
    EVENT_TYPE_GOAL_STEP_EXECUTED,
    EVENT_TYPE_GOAL_ESCALATED,
    EVENT_TYPE_GOAL_ERROR,
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    EVENT_TYPE_PRESENCE_GREETING_DELIVERED,
    EVENT_TYPE_EYE_ENABLED,
    EVENT_TYPE_EYE_DISABLED,
    EVENT_TYPE_ROUTINE_CREATED,
    EVENT_TYPE_ROUTINE_ARMED,
    EVENT_TYPE_ROUTINE_TRIGGERED,
    EVENT_TYPE_ROUTINE_EXECUTED,
    EVENT_TYPE_ROUTINE_SKIPPED,
    EVENT_TYPE_ROUTINE_CANCELLED,
    EVENT_TYPE_ROUTINE_ACTION_FAILED,
    EVENT_TYPE_ROUTINE_ACTION_REFUSED,
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_VOICE_STATE_ANSWERED,
    EVENT_TYPE_ALARM_SCHEDULED,
    EVENT_TYPE_ALARM_ARMED,
    EVENT_TYPE_ALARM_FIRING,
    EVENT_TYPE_ALARM_DISPLAY_WAKING,
    EVENT_TYPE_ALARM_MEDIA_STARTING,
    EVENT_TYPE_ALARM_PLAYING,
    EVENT_TYPE_ALARM_GREETING,
    EVENT_TYPE_ALARM_SNOOZED,
    EVENT_TYPE_ALARM_STOPPED,
    EVENT_TYPE_ALARM_COMPLETED,
    EVENT_TYPE_ALARM_CANCELLED,
    EVENT_TYPE_ALARM_FAILED,
    EVENT_TYPE_ALARM_LOCAL_FALLBACK_RANG,
    EVENT_TYPE_ALARM_CLEANED_UP,
    EVENT_TYPE_OWNER_INPUT_ACTIVE,
    EVENT_TYPE_AMBIENT_POLICY_CHANGED,
    EVENT_TYPE_VOICE_QUALIFICATION,
    EVENT_TYPE_EVOLUTION_PAUSED,
    EVENT_TYPE_EVOLUTION_RESUMED,
    EVENT_TYPE_EVOLUTION_SUPERVISOR_SCANNED,
)

#: ``alarm.<state_lowercase>`` for every state in ``app.alarms.models.ALARM_STATES``
#: (spec §3.2). Named here so the alarm service derives the event type from the state it
#: is entering instead of carrying a second, hand-maintained mapping that could disagree
#: with the state vocabulary — the failure mode this module's docstring exists to prevent.
ALARM_EVENT_TYPE_BY_STATE: Final[dict[str, str]] = {
    "SCHEDULED": EVENT_TYPE_ALARM_SCHEDULED,
    "ARMED": EVENT_TYPE_ALARM_ARMED,
    "FIRING": EVENT_TYPE_ALARM_FIRING,
    "DISPLAY_WAKING": EVENT_TYPE_ALARM_DISPLAY_WAKING,
    "MEDIA_STARTING": EVENT_TYPE_ALARM_MEDIA_STARTING,
    "PLAYING": EVENT_TYPE_ALARM_PLAYING,
    "GREETING": EVENT_TYPE_ALARM_GREETING,
    "SNOOZED": EVENT_TYPE_ALARM_SNOOZED,
    "STOPPED": EVENT_TYPE_ALARM_STOPPED,
    "COMPLETED": EVENT_TYPE_ALARM_COMPLETED,
    "CANCELLED": EVENT_TYPE_ALARM_CANCELLED,
    "FAILED": EVENT_TYPE_ALARM_FAILED,
}

#: Reserved for the Evolution Engine (M18): constants exist now, writers come
#: later. Each carries related_module_id, version, production_state and
#: evidence_refs (spec §1.3).
EVENT_TYPE_EVOLUTION_IDEA_CREATED = "evolution.idea_created"
EVENT_TYPE_EVOLUTION_MODULE_DESIGNED = "evolution.module_designed"
EVENT_TYPE_EVOLUTION_BUILD_STARTED = "evolution.build_started"
EVENT_TYPE_EVOLUTION_BUILD_COMPLETED = "evolution.build_completed"
EVENT_TYPE_EVOLUTION_TESTS_PASSED = "evolution.tests_passed"
EVENT_TYPE_EVOLUTION_TESTS_FAILED = "evolution.tests_failed"
EVENT_TYPE_EVOLUTION_SECURITY_REVIEW_PASSED = "evolution.security_review_passed"
EVENT_TYPE_EVOLUTION_BENCHMARK_COMPLETED = "evolution.benchmark_completed"
EVENT_TYPE_EVOLUTION_SHADOW_READY = "evolution.shadow_ready"
EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED = "evolution.owner_approval_required"
EVENT_TYPE_EVOLUTION_DEPLOYED = "evolution.deployed"
EVENT_TYPE_EVOLUTION_ROLLED_BACK = "evolution.rolled_back"
#: The five lifecycle closures the first twelve constants could not name. Without
#: them ``app.evolution.service`` had to record NOTHING for ``researching``,
#: ``qualifying``, and for ``rejected``/``quarantined``/``superseded`` that did not
#: come out of a failed test run — the transitions survived only in the
#: opportunity's own detail blob, so "what happened to that candidate?" was not
#: answerable from the ledger, and the owner's since-you-left briefing silently
#: dropped a candidate being parked. Mislabelling them as tests_failed would have
#: been worse than the gap; naming them honestly is the fix (audit-completeness
#: review, 2026-09-05).
EVENT_TYPE_EVOLUTION_RESEARCHING = "evolution.researching"
EVENT_TYPE_EVOLUTION_QUALIFYING = "evolution.qualifying"
EVENT_TYPE_EVOLUTION_REJECTED = "evolution.rejected"
EVENT_TYPE_EVOLUTION_QUARANTINED = "evolution.quarantined"
EVENT_TYPE_EVOLUTION_SUPERSEDED = "evolution.superseded"
#: The owner-authorised release path (ADR-0055 §5, M18). A deployment the owner authorised
#: must be reconstructable from the ledger alone - including the failures, because a release
#: that failed and rolled back is exactly the kind of thing a later reader needs to find.
EVENT_TYPE_EVOLUTION_OWNER_AUTHORIZED = "evolution.owner_authorized"
EVENT_TYPE_EVOLUTION_DEPLOYING = "evolution.deploying"
EVENT_TYPE_EVOLUTION_VERIFYING = "evolution.verifying"
EVENT_TYPE_EVOLUTION_FAILED = "evolution.failed"
EVENT_TYPE_EVOLUTION_ROLLING_BACK = "evolution.rolling_back"

EVOLUTION_EVENT_TYPES: Final[tuple[str, ...]] = (
    EVENT_TYPE_EVOLUTION_IDEA_CREATED,
    EVENT_TYPE_EVOLUTION_MODULE_DESIGNED,
    EVENT_TYPE_EVOLUTION_BUILD_STARTED,
    EVENT_TYPE_EVOLUTION_BUILD_COMPLETED,
    EVENT_TYPE_EVOLUTION_TESTS_PASSED,
    EVENT_TYPE_EVOLUTION_TESTS_FAILED,
    EVENT_TYPE_EVOLUTION_SECURITY_REVIEW_PASSED,
    EVENT_TYPE_EVOLUTION_BENCHMARK_COMPLETED,
    EVENT_TYPE_EVOLUTION_SHADOW_READY,
    EVENT_TYPE_EVOLUTION_OWNER_APPROVAL_REQUIRED,
    EVENT_TYPE_EVOLUTION_DEPLOYED,
    EVENT_TYPE_EVOLUTION_ROLLED_BACK,
    EVENT_TYPE_EVOLUTION_RESEARCHING,
    EVENT_TYPE_EVOLUTION_QUALIFYING,
    EVENT_TYPE_EVOLUTION_REJECTED,
    EVENT_TYPE_EVOLUTION_QUARANTINED,
    EVENT_TYPE_EVOLUTION_SUPERSEDED,
    EVENT_TYPE_EVOLUTION_OWNER_AUTHORIZED,
    EVENT_TYPE_EVOLUTION_DEPLOYING,
    EVENT_TYPE_EVOLUTION_VERIFYING,
    EVENT_TYPE_EVOLUTION_FAILED,
    EVENT_TYPE_EVOLUTION_ROLLING_BACK,
)

#: deployment.<component>.(released|rolled_back) for a component not among the
#: two known literal constants above (module docstring).
_DEPLOYMENT_EVENT_TYPE_RE = re.compile(r"^deployment\.[a-z0-9_]+\.(released|rolled_back)$")


# ------------------------------------------------------------------ validation


class InvalidVocabulary(ValueError):
    """A caller supplied a value outside the ledger's closed vocabulary."""


def validate_event_type(value: str) -> str:
    if value in EVENT_TYPES or value in EVOLUTION_EVENT_TYPES:
        return value
    if _DEPLOYMENT_EVENT_TYPE_RE.match(value):
        return value
    raise InvalidVocabulary(f"unknown ledger event_type: {value!r}")


def validate_subsystem(value: str) -> str:
    if value not in SUBSYSTEMS:
        raise InvalidVocabulary(f"unknown ledger subsystem: {value!r}")
    return value


def validate_status(value: str) -> str:
    if value not in STATUSES:
        raise InvalidVocabulary(f"unknown ledger status: {value!r}")
    return value


def validate_severity(value: str) -> str:
    if value not in SEVERITIES:
        raise InvalidVocabulary(f"unknown ledger severity: {value!r}")
    return value


def validate_production_state(value: str) -> str:
    if value not in PRODUCTION_STATES:
        raise InvalidVocabulary(f"unknown ledger production_state: {value!r}")
    return value


__all__ = [
    "ALARM_EVENT_TYPE_BY_STATE",
    "EVENT_TYPES",
    "EVOLUTION_EVENT_TYPES",
    "InvalidVocabulary",
    "PRODUCTION_STATES",
    "SEVERITIES",
    "STATUSES",
    "SUBSYSTEMS",
    "validate_event_type",
    "validate_production_state",
    "validate_severity",
    "validate_status",
    "validate_subsystem",
]
