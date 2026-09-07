"""Voice routing qualification: the Owner Utterance Suite's result, as a durable fact.

The synthetic suite (``services/api/tests/voice_corpus``) runs every corpus case through the
real canonical path and writes a report. This module is where that report becomes
something the Living Core can show without lying (docs/DECISIONS.md ADR-0080):

- ``record_report`` writes ONE ``voice.qualification`` ledger row per run (idempotent on the
  run's identity), with the counts and the bounded confusion rows;
- a run that found a regression turns each wrong route into an ``EvolutionOpportunity``
  (source ``voice_corpus``, one per case id, idempotent), so the closed-loop policy's
  "failure -> opportunity -> reproduce -> fix" starts from the record, not from a chat;
- ``state`` derives the owner-facing state from those rows alone:

    NOT_YET_RUN                 no report was ever recorded
    REGRESSION_FOUND            the latest run failed and nothing tracks it yet
    SELF_HEALING                the latest run failed and an opportunity is open for it
    OWNER_AUDIO_TEST_REQUIRED   routing is healthy; the physical audio test is still the
                                owner's (there is no durable owner-audio qualification)
    HEALTHY                     routing healthy AND an owner audio qualification recorded

Nothing here runs the suite, and nothing here talks to a device.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_VOICE_QUALIFICATION,
    SEVERITY_INFO,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_VOICE,
)

QUALIFICATION_VERSION = 1

SOURCE_SYNTHETIC = "voice_corpus"
SOURCE_OWNER_AUDIO = "owner_audio"

STATE_NOT_YET_RUN = "NOT_YET_RUN"
STATE_HEALTHY = "HEALTHY"
STATE_REGRESSION_FOUND = "REGRESSION_FOUND"
STATE_SELF_HEALING = "SELF_HEALING"
STATE_OWNER_AUDIO_TEST_REQUIRED = "OWNER_AUDIO_TEST_REQUIRED"

SUMMARY_HEALTHY = "HEALTHY"
SUMMARY_REGRESSION = "REGRESSION_FOUND"

#: The confusion rows a ledger row keeps: enough to name what broke, never a transcript
#: dump. The full report stays in the file the nightly run wrote.
MAX_CONFUSION_ROWS = 20
#: The opportunity backlog's idempotency namespace for these (``OPPORTUNITY_SOURCES``).
OPPORTUNITY_SOURCE = "voice_corpus"
#: Statuses in which an opportunity still counts as "being worked on".
_OPEN_OPPORTUNITY_STATUSES = frozenset(
    {
        "idea",
        "researching",
        "design_ready",
        "building",
        "testing",
        "evaluating",
        "shadow_ready",
        "owner_approval_required",
        "owner_approved",
        "owner_authorized",
    }
)

_SCORES_FOR_ROUTING_REGRESSION: dict[str, float] = {
    "owner_relevance": 0.9,  # the owner's own words stopped doing what they say
    "expected_utility": 0.8,
    "recurrence": 0.6,  # a corpus case fails on every run until fixed
    "confidence": 0.95,  # deterministic reproduction through the real path
    "engineering_cost": 0.3,
    "operational_risk": 0.2,  # a router change, tested by the whole corpus
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalise_report(report: dict[str, Any]) -> dict[str, Any]:
    """The bounded, typed subset of a suite report this module records."""
    confusion_in = report.get("confusion")
    confusion: list[dict[str, Any]] = []
    if isinstance(confusion_in, list):
        for row in confusion_in[:MAX_CONFUSION_ROWS]:
            if not isinstance(row, dict):
                continue
            confusion.append(
                {
                    "case_id": str(row.get("case_id") or "")[:64],
                    "utterance": str(row.get("utterance") or "")[:200],
                    "expected": (
                        str(row["expected"])[:64] if row.get("expected") is not None else None
                    ),
                    "resolved": (
                        str(row["resolved"])[:64] if row.get("resolved") is not None else None
                    ),
                    "problems": [str(p)[:200] for p in (row.get("problems") or [])][:6],
                }
            )
    failed = _int(report.get("failed_routing"))
    forbidden = _int(report.get("forbidden_side_effects"))
    summary = SUMMARY_HEALTHY if failed == 0 and forbidden == 0 else SUMMARY_REGRESSION
    generated_at = str(report.get("generated_at") or utcnow().isoformat())
    return {
        "suite": str(report.get("suite") or "OwnerUtteranceSuite")[:64],
        "corpus_version": _int(report.get("corpus_version")),
        "generated_at": generated_at,
        "total_cases": _int(report.get("total_cases")),
        "passed": _int(report.get("passed")),
        "clarification": _int(report.get("clarification")),
        "failed_routing": failed,
        "forbidden_side_effects": forbidden,
        "summary": summary,
        "confusion": confusion,
    }


def _summary_tr(normalised: dict[str, Any]) -> str:
    n = normalised
    verdict = "sağlıklı" if n["summary"] == SUMMARY_HEALTHY else "regresyon bulundu"
    return (
        f"Ses yönlendirme sınaması (derlem s{n['corpus_version']}): {n['total_cases']} cümle, "
        f"{n['passed']} doğru, {n['clarification']} netleştirme, {n['failed_routing']} yanlış "
        f"yönlendirme, {n['forbidden_side_effects']} yasak yan etki — {verdict}."
    )


def record_report(
    session: Session,
    report: dict[str, Any],
    *,
    source: str = SOURCE_SYNTHETIC,
    now: datetime | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Write the run as ONE ledger row; return (row, normalised report).

    Idempotent on the run's identity (``source`` + suite + corpus version + generated_at):
    the nightly script posting twice records once.
    """
    normalised = normalise_report(report)
    moment = now or utcnow()
    event = ledger_service.ActivityEvent(
        event_type=EVENT_TYPE_VOICE_QUALIFICATION,
        subsystem=SUBSYSTEM_VOICE,
        action="voice_routing_qualified",
        status=STATUS_COMPLETED if normalised["summary"] == SUMMARY_HEALTHY else STATUS_FAILED,
        severity=SEVERITY_INFO if normalised["summary"] == SUMMARY_HEALTHY else "warning",
        result=normalised["summary"],
        factual_summary=_summary_tr(normalised),
        occurred_at=moment,
        version=str(QUALIFICATION_VERSION),
        evidence_refs=[],
        detail_json={**normalised, "source": source},
        source=source,
        source_ref=(
            f"{source}:{normalised['suite']}:{normalised['corpus_version']}:"
            f"{normalised['generated_at']}"
        ),
    )
    row = ledger_service.record(session, event)
    session.commit()
    return row, normalised


def open_opportunities(
    normalised: dict[str, Any],
    *,
    ledger_row_id: str,
    evolution_service: Any,
) -> list[dict[str, Any]]:
    """One ``EvolutionOpportunity`` per wrong route, citing the ledger row as evidence.

    The backlog is idempotent on (source, source_ref) = (``voice_corpus``, the case id), so
    a regression that persists across nights is one opportunity, not one per night.
    ``evolution_service`` is the runtime's ``EvolutionService``; None records nothing.
    """
    if evolution_service is None or normalised["summary"] == SUMMARY_HEALTHY:
        return []
    created: list[dict[str, Any]] = []
    for row in normalised["confusion"]:
        case_id = row.get("case_id") or ""
        if not case_id:
            continue
        problems = "; ".join(row.get("problems") or []) or "wrong route"
        statement = (
            f"Owner Utterance Suite case {case_id} ({row.get('utterance')!r}) expected "
            f"{row.get('expected')!r} and resolved {row.get('resolved')!r}: {problems}. "
            "Reproduce through tests/unit/test_owner_utterance_corpus.py, fix the ONE router "
            "or the tool it dispatches, add the regression utterance to the corpus, rerun "
            "the neighbouring-intent suite."
        )
        created.append(
            evolution_service.create_from_evidence(
                title=f"Ses yönlendirme regresyonu: {case_id}"[:200],
                statement=statement[:8000],
                evidence_refs=[
                    {"kind": "ledger_event", "ref": ledger_row_id, "note": "voice.qualification"}
                ],
                scores=_SCORES_FOR_ROUTING_REGRESSION,
                source=OPPORTUNITY_SOURCE,
                source_ref=f"{OPPORTUNITY_SOURCE}:{case_id}",
                detail={"case_id": case_id, "utterance": row.get("utterance")},
            )
        )
    return created


def latest_run(session: Session, *, source: str = SOURCE_SYNTHETIC) -> Any | None:
    rows = ledger_service.query(
        session,
        subsystems=[SUBSYSTEM_VOICE],
        event_types=[EVENT_TYPE_VOICE_QUALIFICATION],
        limit=20,
    )
    for row in rows:
        if row.source == source:
            return row
    return None


def _count_open_voice_opportunities(session: Session) -> int:
    from app.evolution.models import EvolutionOpportunity

    try:
        rows = session.execute(
            select(EvolutionOpportunity.status).where(
                EvolutionOpportunity.source == OPPORTUNITY_SOURCE
            )
        ).all()
    except Exception:  # noqa: BLE001 - a deployment without the evolution table
        return 0
    return sum(1 for (status,) in rows if status in _OPEN_OPPORTUNITY_STATUSES)


def state(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """The owner-facing qualification state, from the record alone."""
    moment = now or utcnow()
    synthetic = latest_run(session, source=SOURCE_SYNTHETIC)
    owner_audio = latest_run(session, source=SOURCE_OWNER_AUDIO)
    open_count = _count_open_voice_opportunities(session)

    if synthetic is None:
        routing_state = STATE_NOT_YET_RUN
    elif synthetic.result == SUMMARY_HEALTHY:
        routing_state = STATE_HEALTHY
    elif open_count > 0:
        routing_state = STATE_SELF_HEALING
    else:
        routing_state = STATE_REGRESSION_FOUND

    if routing_state == STATE_HEALTHY and owner_audio is None:
        overall = STATE_OWNER_AUDIO_TEST_REQUIRED
    else:
        overall = routing_state

    def _run(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        detail = dict(row.detail_json or {})
        recorded_at = row.occurred_at
        if recorded_at is not None and recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        return {
            "recorded_at": recorded_at.isoformat().replace("+00:00", "Z") if recorded_at else None,
            "age_s": (max(0.0, (moment - recorded_at).total_seconds()) if recorded_at else None),
            "summary": row.result,
            "corpus_version": detail.get("corpus_version"),
            "total_cases": detail.get("total_cases"),
            "passed": detail.get("passed"),
            "clarification": detail.get("clarification"),
            "failed_routing": detail.get("failed_routing"),
            "forbidden_side_effects": detail.get("forbidden_side_effects"),
            "confusion": list(detail.get("confusion") or []),
            "ledger_event_id": str(row.event_id),
        }

    return {
        "qualification_version": QUALIFICATION_VERSION,
        "state": overall,
        "routing_state": routing_state,
        "owner_audio_qualified": owner_audio is not None,
        "open_opportunities": open_count,
        "latest_synthetic_run": _run(synthetic),
        "latest_owner_audio_run": _run(owner_audio),
        "observed_at": moment.isoformat().replace("+00:00", "Z"),
    }


__all__ = [
    "MAX_CONFUSION_ROWS",
    "OPPORTUNITY_SOURCE",
    "QUALIFICATION_VERSION",
    "SOURCE_OWNER_AUDIO",
    "SOURCE_SYNTHETIC",
    "STATE_HEALTHY",
    "STATE_NOT_YET_RUN",
    "STATE_OWNER_AUDIO_TEST_REQUIRED",
    "STATE_REGRESSION_FOUND",
    "STATE_SELF_HEALING",
    "latest_run",
    "normalise_report",
    "open_opportunities",
    "record_report",
    "state",
]
