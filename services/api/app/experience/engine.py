"""Experience Engine (Phase 2): durable experience -> memory.

Reads DURABLE evidence only — ``activity_events`` via
``app.ledger.service.query`` — and turns it into memory rows through the
EXISTING memory service (``app.memory.service.record_observation``). This
module never constructs an ``app.memory.models.Memory`` row itself and never
bypasses the write policy's secrets guard: every write goes through
``policy.decide()`` exactly like every other observation in this product.

Two kinds of memory come out of one ingest pass:

1. **Episodic** — one memory per qualifying ledger event, text taken verbatim
   from the event's own ``factual_summary`` (already invariant-checked by the
   ledger to state only what its evidence supports — this module invents
   nothing). Idempotent per event (see "Idempotency" below).
2. **Semantic** — a stable fact is proposed only once the SAME derived value
   has independent support from >= ``SEMANTIC_MIN_CORROBORATION`` distinct
   ledger events (never from one event: one observation is a fact about that
   run, not a fact about the subsystem). Provenance is always tagged
   ``kind: "inference"`` and confidence never reaches 1.0 for these rows —
   both are structural properties of ``app.memory`` (``policy.decide()``
   caps a single observation at ``SINGLE_OBSERVATION_MAX_CONFIDENCE``, and
   ``lifecycle.add_evidence`` caps a non-explicit memory at
   ``MAX_INFERRED_CONFIDENCE`` < 1.0 no matter how much it is corroborated),
   not something this module has to re-implement.

Actor deviation (documented here, not in docs/DECISIONS.md — this package's
rules keep it out of docs/; recorded in the commit body instead):
``app.memory.policy.decide()`` only ever assigns ``Actor.OWNER`` (for a
caller-asserted ``explicit=True`` observation) or ``Actor.POLICY`` (every
other path) — including ``app.memory.lifecycle.detect_procedures``'s own
"proposal" pattern, which this module mirrors. There is no reachable
``Actor.SYSTEM`` path for a *submitted observation* without editing the
frozen write-policy decision table, which app/memory/* is off-limits to this
package. Memories this engine writes therefore land as ``Actor.POLICY``,
identically to every other inferred write in this product; "durable" happens
the same way it happens for anyone else's inferred memory — evidence
accumulation crossing ``PROMOTE_MIN_EVIDENCE``/``PROMOTE_MIN_CONFIDENCE`` in
``app.memory.lifecycle.maybe_promote`` — never asserted directly by this
module.

Idempotency: the memory subsystem has no ledger-event index of its own, so
this module keeps its own marker — a deterministic ``Memory.key`` derived
from the ledger ``event_id`` for episodic rows, and the evidence rows
(``MemoryEvidence.source_ref_json["event_id"]``) already recorded against a
semantic fact's key for semantic rows. A second ``ingest()`` call over an
unchanged window therefore performs zero memory writes for events it has
already processed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.experience.signals import publish_progress
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_BRIEFING_DELIVERED,
    EVENT_TYPE_BRIEFING_QUEUED,
    EVENT_TYPE_EXPERIENCE_INGESTED,
    EVENT_TYPE_LEDGER_BACKFILL,
    EVENT_TYPE_OWNER_INPUT_ACTIVE,
    EVENT_TYPE_PRESENCE_STATE_CHANGED,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_VOICE_SESSION_ATTACHED,
    EVENT_TYPE_VOICE_SESSION_CLOSED,
    EVENT_TYPE_VOICE_SESSION_CREATED,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from app.logging import get_logger
from app.memory import service as memory_service
from app.memory.embedding import DeterministicEmbedder, Embedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.models import Memory, MemoryEvidence
from app.memory.policy import Observation
from app.memory.service import MemoryLinks
from app.memory.types import SINGLE_OBSERVATION_MAX_CONFIDENCE, MemoryClass, MemoryStatus

logger = get_logger("app.experience.engine")

#: episodic Memory.key namespace — see module docstring "Idempotency".
EPISODIC_KEY_PREFIX = "experience.episodic"
#: semantic Memory.key namespace for corroborated stable facts.
SEMANTIC_KEY_PREFIX = "experience.semantic"
#: ADR-0191: a PREFERENCE is "the same subject, again". Its key IS the subject, so the
#: third research about it is evidence on the same row rather than a third row — which is
#: the only way the promotion ladder (``PROMOTE_MIN_EVIDENCE``) can ever fire for a
#: behaviour. Thirteen one-off "Araştırma tamamlandı" rows never promoted anything.
PREFERENCE_KEY_PREFIX = "experience.preference"
#: Two is a coincidence. Deliberately the ladder's own threshold: a preference that became
#: durable the moment it was noticed would be a guess in a confident voice.
PREFERENCE_MIN_OCCURRENCES = 3

#: ledger bookkeeping about itself — not "experience" worth remembering.
#: ADR-0190: the machine's own heartbeat. Every one of these is a true and useful LEDGER
#: event, and not one of them is a memory: that a voice session opened at 14:02 tells
#: nobody anything about the owner, and there were 216 of them in sixteen days. Measured in
#: production on 2026-09-20, these five were 765 of the 2316 episodic memories in the store
#: — and they are what retrieval returned when the assistant reached for what it knows
#: about its owner. The ledger keeps every one of them either way.
TELEMETRY_EVENT_TYPES = frozenset(
    {
        EVENT_TYPE_PRESENCE_STATE_CHANGED,
        EVENT_TYPE_VOICE_SESSION_CREATED,
        EVENT_TYPE_VOICE_SESSION_ATTACHED,
        EVENT_TYPE_VOICE_SESSION_CLOSED,
        EVENT_TYPE_OWNER_INPUT_ACTIVE,
    }
)

EXCLUDED_EVENT_TYPES = frozenset(
    {
        EVENT_TYPE_LEDGER_BACKFILL,
        EVENT_TYPE_BRIEFING_QUEUED,
        EVENT_TYPE_BRIEFING_DELIVERED,
        *TELEMETRY_EVENT_TYPES,
        # B18 req 71: this engine's OWN pass receipt. Without it every pass would write a
        # row saying it ran and the next pass would remember that it ran - a system
        # learning from the record of its own learning, corroborating itself for ever.
        EVENT_TYPE_EXPERIENCE_INGESTED,
    }
)

#: a derived (SEMANTIC) fact is proposed only once this many DISTINCT ledger
#: events independently support the same value (task brief: "only with
#: corroboration >= 2 distinct events"). One event is a fact about that run;
#: two agreeing events is the first evidence of a stable pattern.
SEMANTIC_MIN_CORROBORATION = 2

#: default ledger.query() page size for one ingest pass.
DEFAULT_INGEST_LIMIT = 200


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class IngestReport:
    """Per-ingest counters — every count is a real write-or-not decision, not
    an estimate (mirrors ``app.ledger.service.BackfillReport``)."""

    events_scanned: int = 0
    episodic_created: int = 0
    episodic_corroborated: int = 0
    episodic_ignored: int = 0
    episodic_skipped_existing: int = 0
    episodic_refused_secret: int = 0
    semantic_created: int = 0
    semantic_corroborated: int = 0
    semantic_skipped_no_new_evidence: int = 0
    semantic_refused_secret: int = 0
    #: B18 req 71: the oldest event this pass READ, whether or not it produced a row. The
    #: scheduler's backlog cursor moves on what was scanned and never on what was written -
    #: an event whose summary the write policy ignores produces nothing, and a whole page of
    #: those would leave a written-based cursor exactly where it started.
    oldest_scanned: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "events_scanned": self.events_scanned,
            "episodic_created": self.episodic_created,
            "episodic_corroborated": self.episodic_corroborated,
            "episodic_ignored": self.episodic_ignored,
            "episodic_skipped_existing": self.episodic_skipped_existing,
            "episodic_refused_secret": self.episodic_refused_secret,
            "semantic_created": self.semantic_created,
            "semantic_corroborated": self.semantic_corroborated,
            "semantic_skipped_no_new_evidence": self.semantic_skipped_no_new_evidence,
            "semantic_refused_secret": self.semantic_refused_secret,
            "errors": list(self.errors),
        }


def _existing_by_key(session: Session, memory_class: str, key: str) -> Memory | None:
    return session.execute(
        select(Memory).where(
            Memory.memory_class == memory_class,
            Memory.key == key,
            Memory.status == MemoryStatus.ACTIVE.value,
        )
    ).scalar_one_or_none()


def _event_confidence(row: ActivityEventRow) -> float:
    """Confidence for a single ledger-evidenced episodic observation.

    Pure function of how much the ledger event itself substantiates the
    statement: a terminal status carries more weight than "started"/"info",
    a non-empty evidence trail more than none, structured detail more than
    a bare summary. Each signal is a fixed, documented increment; the write
    policy's ``SINGLE_OBSERVATION_MAX_CONFIDENCE`` still has the final say —
    this never claims more than a single observation is worth.
    """
    confidence = 0.15
    if row.status in (STATUS_COMPLETED, STATUS_FAILED):
        confidence += 0.10
    if row.evidence_refs:
        confidence += 0.10
    if row.detail_json:
        confidence += 0.05
    return min(confidence, SINGLE_OBSERVATION_MAX_CONFIDENCE)


def _episodic_observation(row: ActivityEventRow) -> tuple[Observation, MemoryLinks, str]:
    key = f"{EPISODIC_KEY_PREFIX}:{row.event_id}"
    tags = {
        "subsystem": row.subsystem,
        "event_type": row.event_type,
        "status": row.status,
        "outcome": row.result or row.status,
    }
    value = {
        "ledger_event_id": str(row.event_id),
        "tags": tags,
        "detail": dict(row.detail_json or {}),
    }
    obs = Observation(
        text=row.factual_summary,
        memory_class=MemoryClass.EPISODIC,
        key=key,
        value=value,
        explicit=False,
        confidence_hint=_event_confidence(row),
        source={
            "kind": "ledger_event",
            "origin": "experience_engine",
            "event_id": str(row.event_id),
            "event_type": row.event_type,
            "subsystem": row.subsystem,
            "source": row.source,
            "source_ref": row.source_ref,
            "evidence_refs": list(row.evidence_refs or []),
        },
    )
    links = MemoryLinks(occurred_at=row.occurred_at)
    return obs, links, key


def _ingest_episodic(
    session: Session, embedder: Embedder, row: ActivityEventRow, report: IngestReport
) -> None:
    obs, links, key = _episodic_observation(row)
    if _existing_by_key(session, MemoryClass.EPISODIC.value, key) is not None:
        report.episodic_skipped_existing += 1
        return
    try:
        result = memory_service.record_observation(session, embedder, obs, links)
    except MemorySubsystemError as exc:
        if exc.error_class is MemoryErrorClass.SECRET_REJECTED:
            report.episodic_refused_secret += 1
            logger.warning(
                "experience_engine_secret_refused",
                event_id=str(row.event_id),
                event_type=row.event_type,
            )
            return
        raise
    if result.action == "ignored":
        report.episodic_ignored += 1
    elif result.action == "corroborated":
        report.episodic_corroborated += 1
    else:
        report.episodic_created += 1


#: reasons app/research/eligibility.py refuses a page (docs/DECISIONS.md #22)
#: that mean the page was never real, on-topic content — a fact about the
#: RESEARCH PIPELINE's own behavior, not about one run, once it recurs.
_NON_CONTENT_REJECTION_REASONS = frozenset({"interstitial", "consent", "captcha"})


def _dominant_reason(rejected_by_reason: dict[str, Any]) -> str | None:
    if not rejected_by_reason:
        return None
    try:
        return max(sorted(rejected_by_reason), key=lambda name: int(rejected_by_reason[name] or 0))
    except (TypeError, ValueError):
        return None


def _new_corroborating_rows(
    session: Session, memory_class: str, key: str, members: list[ActivityEventRow]
) -> list[ActivityEventRow]:
    """Members whose event id is NOT already recorded as evidence for `key`.

    Keeps semantic derivation idempotent across ingest runs the same way
    episodic idempotency works: check what already happened before writing
    anything new, rather than relying on the memory service to no-op.
    """
    existing = _existing_by_key(session, memory_class, key)
    if existing is None:
        return list(members)
    seen = {
        str(ev.source_ref_json.get("event_id"))
        for ev in session.execute(
            select(MemoryEvidence).where(MemoryEvidence.memory_id == existing.id)
        )
        .scalars()
        .all()
        if isinstance(ev.source_ref_json, dict)
    }
    return [row for row in members if str(row.event_id) not in seen]


def _derive_semantic_facts(
    session: Session, embedder: Embedder, rows: list[ActivityEventRow], report: IngestReport
) -> None:
    """Stable-fact derivation: research runs whose DOMINANT rejection reason
    is a non-content page kind (interstitial/consent/captcha), once that
    reason recurs across >= SEMANTIC_MIN_CORROBORATION distinct completed
    runs, becomes one corroborated SEMANTIC memory per reason (never per
    event) — an inference about the pipeline, explicitly tagged as such.
    """
    groups: dict[str, list[ActivityEventRow]] = defaultdict(list)
    for row in rows:
        if row.event_type != EVENT_TYPE_RESEARCH_COMPLETED:
            continue
        detail = row.detail_json or {}
        rejected = detail.get("rejected_by_reason") or detail.get("rejected") or {}
        reason = _dominant_reason(rejected if isinstance(rejected, dict) else {})
        if reason is None:
            continue
        groups[reason].append(row)

    for reason, members in sorted(groups.items()):
        if len(members) < SEMANTIC_MIN_CORROBORATION:
            continue
        key = f"{SEMANTIC_KEY_PREFIX}:research.dominant_rejection_reason:{reason}"
        new_members = _new_corroborating_rows(session, MemoryClass.SEMANTIC.value, key, members)
        if not new_members:
            report.semantic_skipped_no_new_evidence += 1
            continue
        non_content = (
            " (a non-content page kind)" if reason in _NON_CONTENT_REJECTION_REASONS else ""
        )
        text = (
            f"Research runs recurrently reject candidate pages primarily for reason "
            f"'{reason}'{non_content}, observed across {len(members)} completed runs."
        )
        for row in new_members:
            obs = Observation(
                text=text,
                memory_class=MemoryClass.SEMANTIC,
                key=key,
                value={"reason": reason, "kind": "inference", "occurrences": len(members)},
                explicit=False,
                confidence_hint=SINGLE_OBSERVATION_MAX_CONFIDENCE,
                source={
                    "kind": "inference",
                    "origin": "experience_engine",
                    "derived_from": "research.completed:rejected_by_reason",
                    "event_id": str(row.event_id),
                },
            )
            try:
                result = memory_service.record_observation(
                    session, embedder, obs, MemoryLinks(occurred_at=row.occurred_at)
                )
            except MemorySubsystemError as exc:
                if exc.error_class is MemoryErrorClass.SECRET_REJECTED:
                    report.semantic_refused_secret += 1
                    continue
                raise
            if result.action == "corroborated":
                report.semantic_corroborated += 1
            elif result.action not in ("ignored",):
                report.semantic_created += 1


#: How far back the preference pass reads to count a subject. Research runs are a handful a
#: day; this is weeks of them, and a bound on a query that runs every scheduler pass.
PREFERENCE_HISTORY_LIMIT = 500


def _research_topic(session: Session, row: ActivityEventRow) -> str:
    """The owner's topic for one research event.

    The event carries it since ADR-0191. Before that it did not, so every earlier research
    would be invisible to the preference pass - but the event names the research it
    describes (``research_job_id``), and that research's report still holds the topic. The
    report is durable state exactly as the ledger is; this reads it, never writes it.
    """
    topic = str((row.detail_json or {}).get("topic") or "").strip()
    if topic or row.research_job_id is None:
        return topic
    try:
        from app.research.models import ResearchReportRow

        report = session.get(ResearchReportRow, row.research_job_id)
    except Exception:  # noqa: BLE001 - an older schema without the table: nothing to read
        return ""
    if report is None or not isinstance(report.report_json, dict):
        return ""
    return str(report.report_json.get("topic") or "").strip()


def _derive_preferences(
    session: Session, embedder: Embedder, rows: list[ActivityEventRow], report: IngestReport
) -> None:
    """The subjects the owner keeps coming back to (ADR-0191).

    Measured on 2026-09-20: thirteen durable memories, every one of them a one-off
    "Araştırma tamamlandı: <konu>", and an empty preference class — after sixteen days in
    which the owner asked about the same thing again and again. Nothing could ever be
    promoted, because promotion needs evidence on ONE key and every event had its own.

    The subject is the key. ``app.research.plan.bare_subject`` decides what a subject is —
    imported, never re-implemented: a second copy of a Turkish suffix table drifting from
    the first is a defect this repository has already paid for twice.
    """
    from app.research.plan import bare_subject

    # The subjects THIS pass has news about...
    touched: set[str] = set()
    for row in rows:
        if row.event_type != EVENT_TYPE_RESEARCH_COMPLETED:
            continue
        subject = bare_subject(_research_topic(session, row)).strip().casefold()
        if len(subject) >= 3:
            touched.add(subject)
    if not touched:
        return

    # ...counted over the WHOLE history, not over this pass's window. The scheduler ingests
    # "everything since the last pass", so a research a day is one event per window: a
    # count limited to the window never reaches three and the preference never forms.
    # (The first version did exactly that, and its test passed only because it put all
    # three events in one window.)
    history = ledger_service.query(
        session, event_types=[EVENT_TYPE_RESEARCH_COMPLETED], limit=PREFERENCE_HISTORY_LIMIT
    )
    groups: dict[str, list[ActivityEventRow]] = defaultdict(list)
    for row in history:
        subject = bare_subject(_research_topic(session, row)).strip().casefold()
        if subject in touched:
            groups[subject].append(row)

    for subject, members in sorted(groups.items()):
        if len(members) < PREFERENCE_MIN_OCCURRENCES:
            continue
        key = f"{PREFERENCE_KEY_PREFIX}:research.subject:{subject}"
        new_members = _new_corroborating_rows(session, MemoryClass.PREFERENCE.value, key, members)
        if not new_members:
            report.semantic_skipped_no_new_evidence += 1
            continue
        display = bare_subject(_research_topic(session, members[0]) or subject).strip()
        text = (
            f"Sahip '{display}' konusunu düzenli olarak araştırıyor "
            f"({len(members)} kez sordu)."
        )
        for row in new_members:
            obs = Observation(
                text=text,
                memory_class=MemoryClass.PREFERENCE,
                key=key,
                value={"subject": display, "kind": "inference", "occurrences": len(members)},
                explicit=False,
                confidence_hint=SINGLE_OBSERVATION_MAX_CONFIDENCE,
                source={
                    "kind": "inference",
                    "origin": "experience_engine",
                    "derived_from": "research.completed:topic",
                    "event_id": str(row.event_id),
                },
            )
            try:
                result = memory_service.record_observation(
                    session, embedder, obs, MemoryLinks(occurred_at=row.occurred_at)
                )
            except MemorySubsystemError as exc:
                if exc.error_class is MemoryErrorClass.SECRET_REJECTED:
                    report.semantic_refused_secret += 1
                    continue
                raise
            if result.action == "corroborated":
                report.semantic_corroborated += 1
            elif result.action not in ("ignored",):
                report.semantic_created += 1


def ingest(
    session: Session,
    *,
    embedder: Embedder | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
    limit: int = DEFAULT_INGEST_LIMIT,
) -> IngestReport:
    """Turn durable ledger evidence in [`since`, `until`] into memory.

    ``until`` (B18 req 71) is what lets the scheduler walk BACKWARD through the history a
    page at a time: `ledger.query` is newest-first and capped, so "everything since my last
    pass" reaches today and never reaches the 1441 events that were already there.

    Reuses ``app.ledger.service.query`` (never touches ``ActivityEventRow``
    directly beyond reading it) and ``app.memory.service.record_observation``
    for every write. Safe to call repeatedly and on overlapping windows: see
    the module docstring's "Idempotency" section.
    """
    now = now or utcnow()
    embedder = embedder or DeterministicEmbedder()
    report = IngestReport()
    publish_progress(phase="ingest_started", since=since.isoformat() if since else None)

    rows = ledger_service.query(session, since=since, until=until, limit=limit)
    # ledger.query is newest-first; process oldest-first so occurred_at order
    # matches the order the facts actually happened in.
    rows = list(reversed(rows))
    report.events_scanned = len(rows)
    # B18 req 71: what the scheduler's backlog cursor moves on. The oldest row READ, which
    # is `rows[0]` now that they are oldest-first.
    if rows:
        report.oldest_scanned = rows[0].occurred_at

    for row in rows:
        if row.event_type in EXCLUDED_EVENT_TYPES:
            continue
        try:
            _ingest_episodic(session, embedder, row, report)
        except Exception as exc:  # noqa: BLE001 - one bad event must not sink the pass
            report.errors.append(f"{row.event_id}: {type(exc).__name__}")
            logger.warning(
                "experience_engine_episodic_failed",
                event_id=str(row.event_id),
                reason=type(exc).__name__,
            )

    try:
        _derive_semantic_facts(session, embedder, rows, report)
    except Exception as exc:  # noqa: BLE001 - semantic derivation is best-effort
        report.errors.append(f"semantic: {type(exc).__name__}")
        logger.warning("experience_engine_semantic_failed", reason=type(exc).__name__)

    try:
        _derive_preferences(session, embedder, rows, report)
    except Exception as exc:  # noqa: BLE001 - same discipline as the pass above
        report.errors.append(f"preference: {type(exc).__name__}")
        logger.warning("experience_engine_preference_failed", reason=type(exc).__name__)

    publish_progress(phase="ingest_completed", **report.as_dict())
    return report


__all__ = [
    "DEFAULT_INGEST_LIMIT",
    "EPISODIC_KEY_PREFIX",
    "SEMANTIC_KEY_PREFIX",
    "SEMANTIC_MIN_CORROBORATION",
    "EXCLUDED_EVENT_TYPES",
    "IngestReport",
    "ingest",
    "utcnow",
]
