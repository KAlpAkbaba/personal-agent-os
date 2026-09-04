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
)

# ------------------------------------------------------------------ statuses

STATUS_STARTED = "started"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PENDING = "pending"
STATUS_INFO = "info"

STATUSES: Final[tuple[str, ...]] = (
    STATUS_STARTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_PENDING,
    STATUS_INFO,
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
)

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
