"""Explain over the real ledger, and turn the briefing into an artifact a narration can
read (spec §2, §3.1).

Task → Artifact → Presentation holds: the briefing is an artifact
(``kind=activity_briefing``) with a canonical Markdown body whose sections are the
narration levels, and a ``narration_sessions`` row carries the durable cursor. The voice
tool and the REST route both go through here, so what is spoken and what is stored are
the same words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.artifacts import service as artifact_service
from app.artifacts.models import (
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
)
from app.artifacts.renderers import content_hash
from app.explain.classify import LEVEL_EXECUTIVE, ExplainQuery, classify
from app.explain.engine import (
    Briefing,
    EventView,
    EvidenceSource,
    explain,
    render_markdown,
    speech_for_level,
)
from app.logging import get_logger
from app.narration import service as narration_service
from app.narration.engine import Cursor, build_plan
from app.voice.intents import (
    PRESENTATION_FULL,
    level_section_cursor,
    speech_budget,
    speech_from,
)

ARTIFACT_KIND_ACTIVITY_BRIEFING = "activity_briefing"

logger = get_logger("app.explain.service")


# ------------------------------------------------------------------ ledger adapter


def row_as_dict(row: Any) -> dict[str, Any]:
    """A subsystem row as plain data for the engine.

    Rows carry their own ``as_dict`` where a subsystem defines one; otherwise the mapped
    columns are copied. Either way the engine never holds an ORM object, so a query can
    close its session without the briefing losing its facts.
    """
    if hasattr(row, "as_dict"):
        try:
            return dict(row.as_dict())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(row, dict):
        return dict(row)
    out: dict[str, Any] = {}
    for column in getattr(getattr(row, "__table__", None), "columns", []):
        value = getattr(row, column.name, None)
        out[column.name] = str(value) if hasattr(value, "hex") else value
    return out


def _view(row: Any) -> EventView:
    """A ledger row as the engine's neutral view. Tolerant of column names so the
    engine never imports the ORM."""
    occurred = getattr(row, "occurred_at", None) or datetime.now(UTC)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    refs = getattr(row, "evidence_refs", None) or ()
    return EventView(
        event_id=str(getattr(row, "event_id", "")),
        occurred_at=occurred,
        event_type=str(getattr(row, "event_type", "")),
        subsystem=str(getattr(row, "subsystem", "")),
        status=str(getattr(row, "status", "")),
        severity=str(getattr(row, "severity", "info")),
        factual_summary=str(getattr(row, "factual_summary", "")),
        module=getattr(row, "module", None),
        version=getattr(row, "version", None),
        action=str(getattr(row, "action", "") or ""),
        result=getattr(row, "result", None),
        production_state=str(getattr(row, "production_state", "n/a") or "n/a"),
        command_id=str(row.command_id) if getattr(row, "command_id", None) else None,
        trace_id=getattr(row, "trace_id", None),
        research_job_id=(
            str(row.research_job_id) if getattr(row, "research_job_id", None) else None
        ),
        browser_session_id=getattr(row, "browser_session_id", None),
        related_module_id=getattr(row, "related_module_id", None),
        evidence_refs=tuple(dict(r) for r in refs if isinstance(r, dict)),
        detail=dict(getattr(row, "detail_json", None) or {}),
        source=str(getattr(row, "source", "live") or "live"),
    )


class LedgerEvidenceSource:
    """The production :class:`EvidenceSource`: the activity ledger plus the records it
    points at. Read-only; a missing ledger (older deployment) reads as no evidence."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def events(self, *, since, subsystems, statuses, limit) -> list[EventView]:
        try:
            from app.ledger import service as ledger_service
        except ImportError:  # pragma: no cover - the ledger track is merged separately
            return []
        rows = ledger_service.query(
            self._db, since=since, subsystems=subsystems, statuses=statuses, limit=limit
        )
        return [_view(r) for r in rows]

    def research_report(self, task_id: str) -> dict[str, Any] | None:
        from app.research import runs_service

        try:
            tid = uuid.UUID(task_id)
        except ValueError:
            return None
        row = runs_service.get_report(self._db, tid)
        if row is None:
            return None
        report = dict(row.report_json or {})
        if row.artifact_id:
            report.setdefault("artifact_id", str(row.artifact_id))
        return report

    # --- M17 phase 9. These subsystems arrive over several releases; a deployment
    # without one has no such rows, and the engine then says it has no record. The
    # imports are local and defensive for exactly that reason.

    def lessons(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Compiled lessons, highest-scoring first.

        Queried from the model, not through a service helper: an earlier version called
        ``app.experience.service.list_lessons``, which has never existed. The call raised,
        the defensive handler swallowed it, and the owner was told "henüz kayda geçmiş bir
        ders çıkarmadım" while the table held two compiled lessons. A wiring bug that
        impersonates an honest absence is worse than a crash (M17 rehearsal, 2026-09-05).
        """
        from sqlalchemy import select

        try:
            from app.experience.models import ExperienceLessonRow
        except ImportError:
            return []
        return self._rows(
            select(ExperienceLessonRow).order_by(ExperienceLessonRow.score.desc()).limit(limit),
            what="lessons",
        )

    def procedural_memories(self, *, limit: int = 20) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from app.memory.models import Memory

        try:
            rows = self._db.execute(
                select(Memory)
                .where(Memory.memory_class == "procedural", Memory.status == "active")
                .order_by(Memory.updated_at.desc())
                .limit(limit)
            ).scalars()
        except Exception:  # noqa: BLE001
            return []
        return [
            {"memory_id": str(r.id), "key": r.key, "text": r.text, "confidence": r.confidence}
            for r in rows
        ]

    def opportunities(
        self, *, statuses: tuple[str, ...] | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Evolution opportunities, newest first.

        Same correction as :meth:`lessons`: the previous call went to
        ``evolution_service.list_opportunities(session, statuses=...)``, which is a METHOD
        on ``EvolutionService`` taking a singular ``status``. It raised on every call, so
        "gece kendi üzerinde ne geliştirdin?" answered "no record" while a real
        SHADOW_READY candidate sat in the table.
        """
        from sqlalchemy import select

        try:
            from app.evolution.models import EvolutionOpportunity
        except ImportError:
            return []
        stmt = select(EvolutionOpportunity)
        if statuses:
            stmt = stmt.where(EvolutionOpportunity.status.in_(list(statuses)))
        stmt = stmt.order_by(EvolutionOpportunity.created_at.desc()).limit(limit)
        return self._rows(stmt, what="opportunities")

    def _rows(self, stmt: Any, *, what: str) -> list[dict[str, Any]]:
        """Run a read and turn rows into plain dicts, distinguishing the two silences.

        A MISSING TABLE is an absence: this build, or this deployment, does not have that
        subsystem yet, and the engine should say it has no record. Anything else is a bug
        in this file, and it is logged loudly instead of being dressed up as an absence -
        which is exactly how two broken accessors survived until the M17 rehearsal.
        """
        from sqlalchemy.exc import OperationalError, ProgrammingError

        try:
            return [row_as_dict(r) for r in self._db.scalars(stmt).all()]
        except (OperationalError, ProgrammingError):
            return []  # the table is not there yet; a genuine "I have no record"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "explain_evidence_source_failed", what=what, error=f"{type(exc).__name__}: {exc}"
            )
            return []

    def goals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        from sqlalchemy import select

        try:
            from app.goals.models import Goal
        except ImportError:
            return []
        return self._rows(select(Goal).order_by(Goal.created_at.desc()).limit(limit), what="goals")

    # The three M17 surfaces the owner asks about by voice. Each is defensive in the same
    # way as the four above: a subsystem that is absent from this build, or a table that a
    # migration has not created yet, yields "I don't know" rather than an exception — and
    # the engine says "I don't know" out loud rather than inventing an answer.

    def world_state(self) -> dict[str, Any] | None:
        """The four-truths snapshot, or None when the world model is unavailable."""
        try:
            from app.worldmodel.state import assemble_snapshot
        except ImportError:
            return None
        try:
            return assemble_snapshot(self._db).as_dict()
        except Exception:  # noqa: BLE001
            return None

    def code_overview(self, *, limit: int = 8) -> dict[str, Any] | None:
        """What the self model knows about this system's own code, or None."""
        try:
            from app.selfmodel import query as selfmodel_query
        except ImportError:
            return None
        try:
            modules = selfmodel_query.list_modules(self._db, kind=None, limit=limit)
            total = selfmodel_query.count_modules(self._db)
        except Exception:  # noqa: BLE001
            return None
        return {"modules": modules, "module_count": total}

    def authority_policy(self) -> dict[str, Any] | None:
        """The production-authority boundary as the code itself defines it.

        Read from the module, not from prose: the answer to "can you put this live?"
        must be the same object the guard consults, or the spoken answer and the
        enforced rule could drift apart.
        """
        try:
            from app.evolution.authority import (
                LAB_GRANTS,
                PRODUCTION_ACTIONS,
                PRODUCTION_GRANTS,
                root_policies,
            )
        except ImportError:
            return None
        try:
            return {
                "root_policies": [dict(p) for p in root_policies()],
                "production_actions": sorted(PRODUCTION_ACTIONS),
                "lab_grants": sorted(str(g) for g in LAB_GRANTS),
                "production_grants": sorted(str(g) for g in PRODUCTION_GRANTS),
                "lab_holds_any_production_grant": bool(LAB_GRANTS & PRODUCTION_GRANTS),
                # The two halves of the rule, kept apart because they are different claims
                # and the answer must make both (owner authority policy, 2026-09-05):
                #
                #   autonomous promotion  - never, by construction. The lab holds no
                #                           production grant and cannot mint one.
                #   owner-authorised release - permitted. An explicit command from the
                #                           AUTHENTICATED owner mints a production
                #                           authority, and the release then runs the same
                #                           transactional path any release runs.
                #
                # "I can never deploy" would be the wrong answer: it is not the policy, and
                # it would tell the owner they cannot ask for something they can ask for.
                "autonomous_promotion_permitted": False,
                "owner_authorised_release_permitted": True,
                "owner_authorisation_requires": [
                    "authenticated_owner_session",
                    "explicit_production_command",
                    "candidate_shadow_ready",
                ],
                "asking_is_not_authorising": True,
            }
        except Exception:  # noqa: BLE001
            return None

    def open_incidents(self) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from app.selfhealing.models import Incident

        try:
            rows = self._db.execute(
                select(Incident).where(Incident.status.in_(("open", "fix_in_progress")))
            ).scalars()
        except Exception:  # noqa: BLE001 - table absent on an older schema
            return []
        return [
            {
                "id": str(r.id),
                "component": getattr(r, "component", None),
                "severity": getattr(r, "severity", None),
                "occurrence_count": getattr(r, "occurrence_count", 0),
            }
            for r in rows
        ]


#: How the service obtains evidence. Tests replace it with an in-memory source; the
#: production value is the ledger adapter above.
evidence_source_factory = LedgerEvidenceSource


# ------------------------------------------------------------------ briefing artifact


@dataclass(frozen=True, slots=True)
class BriefingRecord:
    briefing: Briefing
    artifact_id: uuid.UUID
    artifact_version: int
    narration_session_id: uuid.UUID | None
    level: str
    speech: str
    cursor: Cursor | None

    def as_dict(self) -> dict[str, Any]:
        counts = self.briefing.counts()
        return {
            "briefing_id": str(self.artifact_id),
            "artifact_id": str(self.artifact_id),
            "artifact_version": self.artifact_version,
            "narration_session_id": (
                str(self.narration_session_id) if self.narration_session_id else None
            ),
            "level": self.level,
            "speech": self.speech,
            # ADR-0075: the completed research this answer was built from, at the top
            # level of the tool result - so the durable tool-call row itself says which
            # run was reused, without a reader having to dig into provenance.
            "research_job_id": self.briefing.research_job_id,
            "research_artifact_id": self.briefing.research_artifact_id,
            "sections": ["Özet", "Ayrıntı", "Teknik"],
            "cursor": self.cursor.as_dict() if self.cursor else None,
            "query": self.briefing.query.as_dict(),
            "provenance": self.briefing.provenance(),
            # The routing record travels with the answer, so the durable tool-call row can
            # say WHICH cognitive path served the question instead of leaving a checker to
            # guess it from the Turkish (owner M17 run, 2026-09-05).
            "cognition": self.briefing.cognition(),
            "evidence_count": counts["evidence"],
            "facts": counts["facts"],
            "inferences": counts["inferences"],
            "uncertainties": counts["uncertainties"],
        }


def persist_briefing(db: Session, briefing: Briefing) -> tuple[uuid.UUID, int, str]:
    """Store the briefing as an artifact; returns (artifact_id, version, body)."""
    body = render_markdown(briefing)
    artifact = artifact_service.get_or_create_artifact_for_task(
        db, task_id=None, title=briefing.title, kind=ARTIFACT_KIND_ACTIVITY_BRIEFING
    )
    version = artifact_service.add_artifact_version(
        db,
        artifact_id=artifact.id,
        canonical_body=body,
        content_hash=content_hash(body.encode("utf-8")),
        source_manifest=briefing.as_dict(),
    )
    artifact_service.set_executive_summary(
        db, artifact.id, speech_for_level(briefing, LEVEL_EXECUTIVE)
    )
    # A briefing has no renders to wait for: its presentation IS the narration.
    for state in (
        ARTIFACT_STATE_CANONICAL_READY,
        ARTIFACT_STATE_RENDERS_PENDING,
        ARTIFACT_STATE_READY,
    ):
        if artifact.state != state:
            artifact_service.set_artifact_state(db, artifact.id, state)
    return artifact.id, version.version, body


def explain_to_briefing(
    db: Session,
    question: str,
    *,
    level: str | None = None,
    now: datetime | None = None,
    source: EvidenceSource | None = None,
    device_id: uuid.UUID | None = None,
    attach_narration: bool = True,
    research_job_id: str | None = None,
) -> BriefingRecord:
    """The whole path: classify → retrieve → compose → artifact → narration session
    positioned at the requested level, with the text to speak for that level.

    ``research_job_id`` binds the answer to one completed research (ADR-0075); it is
    passed straight through to :func:`app.explain.engine.explain`, which uses it to
    choose WHICH recorded run to read. Nothing here starts or re-runs a research."""
    now = now or datetime.now(UTC)
    query = classify(question, now=now)
    if level and level != query.level:
        query = ExplainQuery(
            kind=query.kind,
            level=level,
            since=query.since,
            subsystem=query.subsystem,
            module=query.module,
            normalized=query.normalized,
        )
    briefing = explain(
        source or evidence_source_factory(db),
        question,
        query,
        now=now,
        research_job_id=research_job_id,
    )
    artifact_id, version_no, body = persist_briefing(db, briefing)

    plan = build_plan(
        body,
        artifact_id=str(artifact_id),
        version=version_no,
        pronunciation=narration_service.pronunciation_map(db),
    )
    start = level_section_cursor(plan, _presentation_for(query.level))
    if query.level == "full" and plan.chunks:
        start = plan.chunks[0].cursor
    narration_id: uuid.UUID | None = None
    if attach_narration:
        from app.narration.commands import State
        from app.narration.routes import _cursor_top_level

        cursor_json = _cursor_top_level(start)
        cursor_json["_state"] = {
            "state": State.READING.value,
            "saved_cursor": None,
            "paragraph_anchor": start.as_dict() if start else None,
            "speed": 1.0,
        }
        row = narration_service.create_session(
            db,
            artifact_id=artifact_id,
            artifact_version=version_no,
            device_id=device_id,
            initial_cursor=cursor_json,
        )
        narration_service.update_cursor(db, row.id, state=State.READING.value)
        narration_id = row.id
    # What is spoken is what the narration plan holds, so "devam" continues from exactly
    # the words that were said; the engine's own rendering is the fallback for an empty plan.
    presentation = _presentation_for(query.level)
    speech = (
        speech_from(
            plan,
            start,
            whole_section=presentation != PRESENTATION_FULL,
            max_chars=speech_budget(presentation),
        )
        if start is not None
        else speech_for_level(briefing, query.level)
    )
    logger.info(
        "activity_explained",
        kind=query.kind,
        level=query.level,
        artifact_id=str(artifact_id),
        facts=briefing.counts()["facts"],
        uncertainties=briefing.counts()["uncertainties"],
    )
    return BriefingRecord(
        briefing=briefing,
        artifact_id=artifact_id,
        artifact_version=version_no,
        narration_session_id=narration_id,
        level=query.level,
        speech=speech,
        cursor=start,
    )


def _presentation_for(level: str) -> str:
    from app.voice.intents import (
        PRESENTATION_DETAIL,
        PRESENTATION_SUMMARY,
        PRESENTATION_TECHNICAL,
    )

    return {
        "executive": PRESENTATION_SUMMARY,
        "detailed": PRESENTATION_DETAIL,
        "technical": PRESENTATION_TECHNICAL,
        "full": PRESENTATION_FULL,
    }.get(level, PRESENTATION_SUMMARY)


__all__ = [
    "ARTIFACT_KIND_ACTIVITY_BRIEFING",
    "BriefingRecord",
    "LedgerEvidenceSource",
    "explain_to_briefing",
    "persist_briefing",
]
