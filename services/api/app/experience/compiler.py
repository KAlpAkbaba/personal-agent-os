"""Experience Compiler (Phase 3): incident -> root cause -> resolution ->
generalized lesson.

``compile_lessons`` reads durable, already-canonical rows — incidents
(``app.selfhealing.models.Incident``) and the activity ledger
(``app.ledger.service.query``) — and, only where BOTH a root cause and a
resolution are evidenced, proposes a generalized lesson. A failure with no
resolution yet produces no lesson: "incident -> root_cause -> resolution ->
lesson" is a chain, and a chain with a missing link does not compile.

Two evidence shapes feed the same scoring/persistence pipeline:

1. **Incident-based** (``_build_incident_lesson``): a row in
   ``app.selfhealing.models.Incident``. Read directly rather than through the
   ledger because the ledger vocabulary has ``incident.opened`` but no
   "incident resolved/fixed" event type — the ledger snapshots the incident
   at open time and is append-only, so whether/how it was later resolved
   lives only on the canonical ``Incident`` row (``status``,
   ``fixed_release_id``, ``occurrence_count``). This mirrors how the ledger's
   OWN backfill reads ``Incident``/``Release``/``ResearchReportRow`` directly
   — using a canonical table this package does not own, not rebuilding one.
2. **Ledger-event-based** (``_build_ledger_lesson``): a standalone
   ``status=failed`` activity event (e.g. ``research.failed``) or a
   ``deployment.<component>.rolled_back`` event that never became an
   ``Incident`` row, paired with the next later ``completed`` event in the
   same subsystem as its resolution.

Two named, evidence-grounded patterns are recognized (both drawn from this
repo's own incident history — docs/DECISIONS.md #16 and #22); anything else
compiles as a low-confidence GENERIC lesson so the compiler degrades
gracefully instead of only ever recognizing what it was told about in
advance.

Scoring (``score_lesson``) is a pure function, documented weight-by-weight.
Promotion to a memory happens automatically ONLY above a named threshold, and
NEVER from a single event unless confidence is very high and risk is low
(see ``AUTO_PROMOTE_SCORE_THRESHOLD`` / ``_meets_auto_promote_rule`` below) —
everything else is persisted as a ``candidate`` row in ``experience_lessons``
for the owner to review via ``POST /v1/experience/lessons/{id}/promote``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experience.models import STATUS_CANDIDATE, STATUS_PROMOTED, ExperienceLessonRow
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import STATUS_COMPLETED, STATUS_FAILED
from app.logging import get_logger
from app.memory import service as memory_service
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.policy import Observation
from app.memory.service import MemoryLinks
from app.memory.types import SINGLE_OBSERVATION_MAX_CONFIDENCE, MemoryClass
from app.selfhealing.models import Incident

logger = get_logger("app.experience.compiler")

SOURCE = "experience_compiler"

#: Incident.status values that mean "the chain has a resolution" (see
#: app/selfhealing/models.py INCIDENT_STATUSES).
_RESOLVED_STATUSES = frozenset({"fixed", "recovered", "closed"})

# --------------------------------------------------------------- patterns

PATTERN_RESEARCH_EVIDENCE_LEAK = "research_evidence_leak"
PATTERN_DEPLOYMENT_PROVENANCE = "deployment_runtime_provenance"
PATTERN_GENERIC = "generic"

#: subsystems/components this compiler treats as "research pipeline" evidence
#: (docs/DECISIONS.md #22: the 2026-09-04 interstitial-as-evidence incident).
_RESEARCH_COMPONENTS = frozenset({"research", "browser", "research_eligibility"})

#: reasons app/research/eligibility.py refuses a page that mean it was never
#: real content — the same set app.experience.engine treats as a
#: "non-content" rejection when deriving semantic facts.
_NON_CONTENT_REJECTION_REASONS = frozenset({"interstitial", "consent", "captcha"})

#: per-subsystem/component owner-relevance prior — how much a lesson about
#: this area of the product matters to a single owner running it daily.
#: Reversible, documented heuristic (CLAUDE.md "Asking the owner"): research,
#: browser and deployment are the subsystems the owner directly depends on
#: for the product's core loop; self_model/evolution are important but
#: further from daily visible impact.
_OWNER_RELEVANCE_PRIOR: dict[str, float] = {
    "research": 0.9,
    "browser": 0.9,
    "deployment": 0.9,
    "voice": 0.7,
    "memory": 0.7,
    "self_model": 0.6,
    "evolution": 0.6,
    "ledger": 0.4,
}
_DEFAULT_OWNER_RELEVANCE = 0.5

#: generalizability prior per pattern: a NAMED pattern is a known class of bug
#: this product has hit before (docs/DECISIONS.md #16 and #22) — recognizing
#: it again says something general about the system. The GENERIC fallback has
#: no such backing, so it starts low.
_GENERALIZABILITY_PRIOR = {
    PATTERN_RESEARCH_EVIDENCE_LEAK: 0.85,
    PATTERN_DEPLOYMENT_PROVENANCE: 0.85,
    PATTERN_GENERIC: 0.35,
}

#: recurrence beyond this many confirming events stops adding score (see
#: score_lesson docstring).
RECURRENCE_SATURATION = 5

#: how far back compile_lessons looks for failures/incidents by default.
DEFAULT_LOOKBACK = timedelta(days=365)

_RESEARCH_EVIDENCE_LEAK_STATEMENT = (
    "Interstitial/consent/captcha pages must not become research evidence: "
    "page validity has to be judged BEFORE ranking, and that verdict must "
    "persist onto the evidence row so a later synthesis pass cannot read the "
    "rejected page back in."
)
_DEPLOYMENT_PROVENANCE_STATEMENT = (
    "Runtime identity/provenance must be independently verified after every "
    "install: a clean repo/staged-tree check does not prove which build the "
    "LIVE process is actually executing — the running worker/service must "
    "assert its own module path and digest, and the verifier must compare "
    "that against the staged release, not the source tree."
)

# ------------------------------------------------------------- scoring weights
#
# Each weight is a documented judgment call (reversible, CLAUDE.md "Asking the
# owner"), not a tuned constant:
#
# - generalizability (0.30, largest positive weight): a lesson that does not
#   generalize is not a lesson — it is a fact the Experience Engine's episodic
#   memory already captured. This is what "lesson-ness" actually measures.
# - confidence (0.25): how well the stated root cause is actually shown by the
#   evidence (named pattern vs. guessed), independent of how often it recurred.
# - recurrence (0.20, saturating): repeated failures of the same shape are
#   stronger evidence than one, but saturate at RECURRENCE_SATURATION — past
#   that, additional repeats of the SAME shape stop teaching anything new;
#   confidence/generalizability are what should keep rising instead.
# - owner_relevance (0.15, smallest positive weight): relevance should nudge
#   ranking among otherwise-similar lessons, not override evidence quality.
# - risk_overgeneralization (0.30 penalty, tied with generalizability): a
#   high-risk lesson can cancel out even a perfect positive score — the
#   compiler must be able to refuse to teach a wrong rule with confidence,
#   which requires the penalty to be as strong as the strongest positive term.
W_GENERALIZABILITY = 0.30
W_CONFIDENCE = 0.25
W_RECURRENCE = 0.20
W_OWNER_RELEVANCE = 0.15
W_RISK_PENALTY = 0.30

#: a lesson scoring at or above this becomes a candidate-stage PROCEDURAL
#: memory automatically (subject to _meets_auto_promote_rule below); below it,
#: the lesson stays a `candidate` row for the owner to review.
AUTO_PROMOTE_SCORE_THRESHOLD = 0.75


def utcnow() -> datetime:
    return datetime.now(UTC)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_lesson(
    *,
    generalizability: float,
    confidence: float,
    recurrence: int,
    owner_relevance: float,
    risk_overgeneralization: float,
) -> float:
    """Pure, deterministic lesson score in [0, 1]. See module docstring for
    why each weight has the value it does. Monotonic in every input: higher
    generalizability/confidence/recurrence/owner_relevance raises the score;
    higher risk_overgeneralization lowers it."""
    recurrence_factor = min(1.0, max(0, recurrence) / RECURRENCE_SATURATION)
    raw = (
        W_GENERALIZABILITY * _clamp01(generalizability)
        + W_CONFIDENCE * _clamp01(confidence)
        + W_RECURRENCE * recurrence_factor
        + W_OWNER_RELEVANCE * _clamp01(owner_relevance)
        - W_RISK_PENALTY * _clamp01(risk_overgeneralization)
    )
    return _clamp01(raw)


def _meets_auto_promote_rule(
    *, score: float, recurrence: int, confidence: float, risk: float
) -> bool:
    """Never auto-promote from a single event unless confidence is very high
    AND risk is low (task brief, verbatim rule). A single confirming incident
    is real signal — but on its own it is one data point, and this compiler
    would rather under-promote (leaving a `candidate` row the owner can
    promote by hand) than teach a wrong generalized rule from one occurrence.
    """
    if score < AUTO_PROMOTE_SCORE_THRESHOLD:
        return False
    if recurrence >= 2:
        return True
    return confidence >= 0.9 and risk <= 0.1


def _confidence_and_risk(
    pattern: str, recurrence: int, *, strong_resolution: bool
) -> tuple[float, float]:
    """Shared confidence/risk derivation for both incident- and ledger-event-
    based lessons (documented once, used twice — see module scoring notes).

    confidence: base 0.45 (some resolved chain exists at all) + 0.20 for a
    NAMED pattern (root cause is a recognized mechanism, not a guess) + 0.15
    for a resolution backed by concrete evidence (a release id, not just "a
    later event happened to succeed") + 0.15 once recurrence >= 2 (repetition
    is itself corroborating evidence) — capped at 0.95 (never 1.0: always an
    inference).

    risk_overgeneralization: starts at 0.55 for an unrecognized mechanism
    (GENERIC), 0.25 for a named one, then falls by 0.05 per confirming
    occurrence up to 4 (a mechanism seen 4+ times is well past "maybe
    coincidence"), floored at 0.05.
    """
    confidence = 0.45
    if pattern != PATTERN_GENERIC:
        confidence += 0.20
    if strong_resolution:
        confidence += 0.15
    if recurrence >= 2:
        confidence += 0.15
    confidence = min(confidence, 0.95)

    risk = 0.55 if pattern == PATTERN_GENERIC else 0.25
    risk -= 0.05 * min(recurrence, 4)
    risk = max(0.05, risk)
    return confidence, risk


def _dominant_reason(rejected: dict[str, Any]) -> str | None:
    if not rejected:
        return None
    try:
        return max(sorted(rejected), key=lambda name: int(rejected[name] or 0))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class LessonCandidate:
    """One incident -> root_cause -> resolution -> lesson compilation."""

    pattern: str
    title: str
    statement: str
    root_cause: str
    resolution: str
    scope: str
    incident_refs: list[str]
    evidence_refs: list[dict[str, Any]]
    recurrence: int
    confidence: float
    generalizability: float
    owner_relevance: float
    risk_overgeneralization: float
    source_ref: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return score_lesson(
            generalizability=self.generalizability,
            confidence=self.confidence,
            recurrence=self.recurrence,
            owner_relevance=self.owner_relevance,
            risk_overgeneralization=self.risk_overgeneralization,
        )


# ------------------------------------------------------- incident-based path


def _classify_incident(incident: Incident) -> tuple[str, dict[str, Any]]:
    evidence = incident.evidence_json or {}

    if incident.component in _RESEARCH_COMPONENTS:
        rejected = evidence.get("rejected_by_reason") or evidence.get("rejected") or {}
        reason = evidence.get("dominant_rejection_reason") or _dominant_reason(
            rejected if isinstance(rejected, dict) else {}
        )
        if reason in _NON_CONTENT_REJECTION_REASONS:
            return PATTERN_RESEARCH_EVIDENCE_LEAK, {"reason": reason, "rejected": rejected}

    if evidence.get("repo_state") and evidence.get("runtime_state"):
        if evidence["repo_state"] != evidence["runtime_state"]:
            return PATTERN_DEPLOYMENT_PROVENANCE, {
                "repo_state": evidence["repo_state"],
                "runtime_state": evidence["runtime_state"],
            }

    return PATTERN_GENERIC, {}


def _incident_resolution_text(incident: Incident) -> str | None:
    """None when the chain has no resolution yet (no lesson compiles)."""
    if incident.status not in _RESOLVED_STATUSES:
        return None
    text = f"Incident on {incident.component} was later marked '{incident.status}'"
    if incident.fixed_release_id is not None:
        text += f" by release {incident.fixed_release_id}"
    text += "."
    return text


def _build_incident_lesson(incident: Incident) -> LessonCandidate | None:
    resolution = _incident_resolution_text(incident)
    if resolution is None:
        return None  # no resolution yet: the chain is incomplete

    pattern, extra = _classify_incident(incident)
    recurrence = max(1, int(incident.occurrence_count or 1))
    generalizability = _GENERALIZABILITY_PRIOR[pattern]
    owner_relevance = _OWNER_RELEVANCE_PRIOR.get(incident.component, _DEFAULT_OWNER_RELEVANCE)
    confidence, risk = _confidence_and_risk(
        pattern, recurrence, strong_resolution=incident.fixed_release_id is not None
    )

    evidence_refs: list[dict[str, Any]] = [{"kind": "incident", "ref": str(incident.id)}]
    if incident.fixed_release_id is not None:
        evidence_refs.append({"kind": "release", "ref": str(incident.fixed_release_id)})

    if pattern == PATTERN_RESEARCH_EVIDENCE_LEAK:
        reason = extra.get("reason", "interstitial")
        title = "Non-content pages must not become research evidence"
        statement = _RESEARCH_EVIDENCE_LEAK_STATEMENT
        root_cause = (
            f"Pages classified as '{reason}' were ranked and cited as evidence before "
            "an eligibility/page-validity check ran against them."
        )
        scope = "research"
    elif pattern == PATTERN_DEPLOYMENT_PROVENANCE:
        title = "Runtime provenance must be independently verified"
        statement = _DEPLOYMENT_PROVENANCE_STATEMENT
        root_cause = (
            f"{incident.component} reported repo/staged state '{extra.get('repo_state')}' "
            f"while the running process still executed a '{extra.get('runtime_state')}' build."
        )
        scope = "deployment"
    else:
        title = f"Recurring {incident.component} incident"
        statement = (
            f"{incident.component} failures shaped like this incident were followed by "
            "a resolution; the specific mechanism is not yet well enough understood to "
            "generalize beyond 'investigate before assuming this recurs the same way'."
        )
        root_cause = f"Incident evidence: {incident.evidence_json!r}"[:2000]
        scope = incident.component or "global"

    return LessonCandidate(
        pattern=pattern,
        title=title,
        statement=statement,
        root_cause=root_cause,
        resolution=resolution,
        scope=scope,
        incident_refs=[f"incident:{incident.id}"],
        evidence_refs=evidence_refs,
        recurrence=recurrence,
        confidence=confidence,
        generalizability=generalizability,
        owner_relevance=owner_relevance,
        risk_overgeneralization=risk,
        source_ref=f"incident:{incident.id}:{pattern}",
        detail={"component": incident.component, "incident_status": incident.status, **extra},
    )


# --------------------------------------------------------- ledger-event path


def _is_failure_event(row: ActivityEventRow) -> bool:
    return row.status == STATUS_FAILED or row.event_type.endswith(".rolled_back")


def _find_resolution_event(session: Session, row: ActivityEventRow) -> ActivityEventRow | None:
    """Earliest later completed-status event in the same subsystem."""
    later = ledger_service.query(
        session,
        since=row.occurred_at,
        subsystems=[row.subsystem],
        statuses=[STATUS_COMPLETED],
        limit=200,
    )
    candidates = [
        r for r in later if r.occurred_at > row.occurred_at and r.event_id != row.event_id
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.occurred_at)


def _classify_ledger_failure(session: Session, row: ActivityEventRow) -> tuple[str, dict[str, Any]]:
    if row.subsystem in _RESEARCH_COMPONENTS and row.research_job_id is not None:
        companions = ledger_service.query(session, research_job_id=row.research_job_id, limit=50)
        for companion in companions:
            detail = companion.detail_json or {}
            rejected = detail.get("rejected_by_reason") or detail.get("rejected") or {}
            reason = _dominant_reason(rejected if isinstance(rejected, dict) else {})
            if reason in _NON_CONTENT_REJECTION_REASONS:
                return PATTERN_RESEARCH_EVIDENCE_LEAK, {"reason": reason, "rejected": rejected}
    return PATTERN_GENERIC, {}


def _count_recurrence(session: Session, row: ActivityEventRow, since: datetime) -> int:
    similar = ledger_service.query(
        session,
        since=since,
        subsystems=[row.subsystem],
        event_types=[row.event_type],
        statuses=[row.status],
        limit=200,
    )
    return len({r.event_id for r in similar})


def _build_ledger_lesson(
    session: Session, row: ActivityEventRow, since: datetime
) -> LessonCandidate | None:
    resolution_row = _find_resolution_event(session, row)
    if resolution_row is None:
        return None  # no resolution yet: the chain is incomplete

    pattern, extra = _classify_ledger_failure(session, row)
    recurrence = _count_recurrence(session, row, since)
    generalizability = _GENERALIZABILITY_PRIOR[pattern]
    owner_relevance = _OWNER_RELEVANCE_PRIOR.get(row.subsystem, _DEFAULT_OWNER_RELEVANCE)
    # A ledger-only resolution is "some later event happened to complete" —
    # weaker evidence than an Incident's fixed_release_id, so never "strong".
    confidence, risk = _confidence_and_risk(pattern, recurrence, strong_resolution=False)

    resolution_text = (
        f"A later {row.subsystem} event completed successfully "
        f"({resolution_row.event_type} at {resolution_row.occurred_at.isoformat()})."
    )
    evidence_refs = [
        {"kind": "activity_event", "ref": str(row.event_id)},
        {"kind": "activity_event", "ref": str(resolution_row.event_id)},
    ]

    if pattern == PATTERN_RESEARCH_EVIDENCE_LEAK:
        reason = extra.get("reason", "interstitial")
        title = "Non-content pages must not become research evidence"
        statement = _RESEARCH_EVIDENCE_LEAK_STATEMENT
        root_cause = (
            f"Pages classified as '{reason}' were ranked/cited as evidence before an "
            f"eligibility check ran; the run then failed with '{row.result or row.status}'."
        )
        scope = "research"
    else:
        title = f"Recurring {row.subsystem} failure ({row.event_type})"
        statement = (
            f"{row.subsystem} runs failing with '{row.result or row.status}' were later "
            "followed by a successful run; the specific mechanism is not yet well enough "
            "understood to generalize beyond 'investigate before assuming this recurs the "
            "same way'."
        )
        root_cause = f"Event detail: {row.detail_json!r}"[:2000]
        scope = row.subsystem

    return LessonCandidate(
        pattern=pattern,
        title=title,
        statement=statement,
        root_cause=root_cause,
        resolution=resolution_text,
        scope=scope,
        incident_refs=[f"event:{row.event_id}"],
        evidence_refs=evidence_refs,
        recurrence=recurrence,
        confidence=confidence,
        generalizability=generalizability,
        owner_relevance=owner_relevance,
        risk_overgeneralization=risk,
        source_ref=f"event:{row.event_id}:{pattern}",
        detail={
            "subsystem": row.subsystem,
            "event_type": row.event_type,
            "result": row.result,
            **extra,
        },
    )


# --------------------------------------------------------------- persistence


def _upsert_lesson_row(session: Session, candidate: LessonCandidate) -> ExperienceLessonRow:
    """Insert or refresh a `candidate` row for this exact (source, source_ref).

    A row already `promoted`/`rejected` by the owner is left untouched — a
    re-compile must never quietly overwrite an owner decision.
    """
    existing = session.execute(
        select(ExperienceLessonRow).where(
            ExperienceLessonRow.source == SOURCE,
            ExperienceLessonRow.source_ref == candidate.source_ref,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.status != STATUS_CANDIDATE:
        return existing

    score = candidate.score
    if existing is None:
        row = ExperienceLessonRow(
            title=candidate.title,
            statement=candidate.statement,
            incident_refs=list(candidate.incident_refs),
            evidence_refs=list(candidate.evidence_refs),
            root_cause=candidate.root_cause,
            resolution=candidate.resolution,
            scope=candidate.scope,
            recurrence=candidate.recurrence,
            confidence=candidate.confidence,
            generalizability=candidate.generalizability,
            owner_relevance=candidate.owner_relevance,
            risk_overgeneralization=candidate.risk_overgeneralization,
            score=score,
            status=STATUS_CANDIDATE,
            source=SOURCE,
            source_ref=candidate.source_ref,
            detail_json=dict(candidate.detail),
        )
        session.add(row)
        session.flush()
        return row

    existing.title = candidate.title
    existing.statement = candidate.statement
    existing.incident_refs = list(candidate.incident_refs)
    existing.evidence_refs = list(candidate.evidence_refs)
    existing.root_cause = candidate.root_cause
    existing.resolution = candidate.resolution
    existing.recurrence = candidate.recurrence
    existing.confidence = candidate.confidence
    existing.generalizability = candidate.generalizability
    existing.owner_relevance = candidate.owner_relevance
    existing.risk_overgeneralization = candidate.risk_overgeneralization
    existing.score = score
    existing.detail_json = dict(candidate.detail)
    existing.updated_at = utcnow()
    return existing


def _auto_write_memory(
    session: Session, embedder: Embedder, row: ExperienceLessonRow
) -> str | None:
    """Record the lesson as a candidate-stage PROCEDURAL memory when the
    auto-promote rule fires. Returns the memory id (str) or None.

    Goes through app.memory.service.record_observation exactly like any other
    inferred write in this product (see app.experience.engine's module
    docstring "Actor deviation" note — the same reasoning applies here:
    Actor.POLICY, never Actor.OWNER, for a system-derived generalization).
    """
    key = f"experience.lesson.{row.source_ref}"
    obs = Observation(
        text=row.statement,
        memory_class=MemoryClass.PROCEDURAL,
        key=key,
        value={
            "kind": "inference",
            "lesson_id": str(row.lesson_id),
            "root_cause": row.root_cause,
            "resolution": row.resolution,
            "scope": row.scope,
        },
        explicit=False,
        confidence_hint=SINGLE_OBSERVATION_MAX_CONFIDENCE,
        source={
            "kind": "inference",
            "origin": "experience_compiler",
            "lesson_id": str(row.lesson_id),
            "incident_refs": list(row.incident_refs or []),
            "evidence_refs": list(row.evidence_refs or []),
        },
    )
    try:
        result = memory_service.record_observation(session, embedder, obs, MemoryLinks())
    except MemorySubsystemError as exc:
        if exc.error_class is MemoryErrorClass.SECRET_REJECTED:
            logger.warning("experience_compiler_secret_refused", lesson_id=str(row.lesson_id))
            return None
        raise
    if result.memory_id is None:
        return None
    row.status = STATUS_PROMOTED
    row.promoted_memory_id = result.memory_id
    row.detail_json = {
        **(row.detail_json or {}),
        "promotion": {"kind": "auto", "actor": "policy", "stage": result.stage},
    }
    row.updated_at = utcnow()
    return str(result.memory_id)


def _publish_uistate(*, phase: str, **metadata: Any) -> None:
    """See app.experience.engine._publish_uistate — identical soft-import
    rationale (app.uistate does not exist in this worktree yet)."""
    try:
        from app.uistate import UiState, publish  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        publish(UiState.MEMORY_RETRIEVAL, subsystem="experience", phase=phase, **metadata)
    except Exception:  # noqa: BLE001 - progress signalling must never break compile
        logger.debug("experience_compiler_uistate_publish_failed", phase=phase)


def _process(
    session: Session,
    embedder: Embedder,
    candidate: LessonCandidate,
    candidates: list[LessonCandidate],
) -> None:
    candidates.append(candidate)
    row = _upsert_lesson_row(session, candidate)
    if row.status == STATUS_CANDIDATE and _meets_auto_promote_rule(
        score=candidate.score,
        recurrence=candidate.recurrence,
        confidence=candidate.confidence,
        risk=candidate.risk_overgeneralization,
    ):
        _auto_write_memory(session, embedder, row)


def compile_lessons(
    session: Session,
    *,
    embedder: Embedder | None = None,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_LOOKBACK,
) -> list[LessonCandidate]:
    """Compile incident -> root_cause -> resolution -> lesson candidates.

    Persists every compiled candidate as an ``experience_lessons`` row
    (inserted or refreshed — never duplicated, never overwriting an owner
    decision, see ``_upsert_lesson_row``) and auto-writes a candidate-stage
    PROCEDURAL memory for any lesson meeting ``_meets_auto_promote_rule``.
    Returns the in-memory ``LessonCandidate`` list for callers (e.g. the
    route) that want the scoring detail without a second query.
    """
    now = now or utcnow()
    embedder = embedder or DeterministicEmbedder()
    since = now - lookback
    _publish_uistate(phase="compile_started")

    candidates: list[LessonCandidate] = []

    incidents = (
        session.execute(select(Incident).where(Incident.first_seen_at >= since)).scalars().all()
    )
    for incident in incidents:
        try:
            candidate = _build_incident_lesson(incident)
        except Exception as exc:  # noqa: BLE001 - one bad incident must not sink the pass
            logger.warning(
                "experience_compiler_incident_failed",
                incident_id=str(incident.id),
                reason=type(exc).__name__,
            )
            continue
        if candidate is not None:
            _process(session, embedder, candidate, candidates)

    failures = [
        row
        for row in ledger_service.query(session, since=since, limit=200)
        if _is_failure_event(row)
    ]
    for row in failures:
        try:
            candidate = _build_ledger_lesson(session, row, since)
        except Exception as exc:  # noqa: BLE001 - one bad event must not sink the pass
            logger.warning(
                "experience_compiler_event_failed",
                event_id=str(row.event_id),
                reason=type(exc).__name__,
            )
            continue
        if candidate is not None:
            _process(session, embedder, candidate, candidates)

    session.commit()
    _publish_uistate(phase="compile_completed", lessons=len(candidates))
    return candidates


__all__ = [
    "AUTO_PROMOTE_SCORE_THRESHOLD",
    "DEFAULT_LOOKBACK",
    "PATTERN_DEPLOYMENT_PROVENANCE",
    "PATTERN_GENERIC",
    "PATTERN_RESEARCH_EVIDENCE_LEAK",
    "RECURRENCE_SATURATION",
    "W_CONFIDENCE",
    "W_GENERALIZABILITY",
    "W_OWNER_RELEVANCE",
    "W_RECURRENCE",
    "W_RISK_PENALTY",
    "LessonCandidate",
    "compile_lessons",
    "score_lesson",
]
