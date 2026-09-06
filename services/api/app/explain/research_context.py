"""Which COMPLETED research a follow-up question is about (docs/DECISIONS.md ADR-0075).

The owner's real run, 2026-09-06/07: a research finished ("Son üç gündeki OpenAI ile
ilgili gelişmeler", task ``deabbd44``), the owner opened a NEW voice session and said
only "Teknik anlat." The technical explanation was correct — and a SECOND crawl was
started alongside it, because nothing in the server bound that question to the run it
was obviously about; the only thing connecting them was the owner's own memory.

This module is that binding, and it is a READ. It never starts, re-runs or mutates
anything. It answers one question — "which finished research is the owner talking
about?" — from durable rows only:

1. the research this voice session itself started and saw complete
   (``context_json['last_research']``, written by
   ``app.voice.realtime_sessions.service.complete_tool_call_system``);
2. otherwise the research named by the session's open plan
   (``plan['research_job_id']`` / ``plan['task_id']``), when that run really is READY
   with a report;
3. otherwise the most recently completed research of the owner (a ``ResearchRunRow``
   at stage ``ready`` whose ``ResearchReportRow`` carries a report).

If step 3 finds two completed runs close enough together in time to be genuinely
ambiguous, the binding says so and the caller asks ONE short Turkish question rather
than guessing — and, either way, never crawls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: How far back a "the last research" may reach. Older than this and a bare "teknik
#: anlat" is no longer plausibly about it; the answer is then the ordinary last-activity
#: explanation, with nothing research-bound about it.
CONTEXT_WINDOW = timedelta(days=14)

#: Two completed researches this close together, with nothing linking either one to the
#: conversation, are ambiguous: the owner could mean either, and picking the newer one
#: by a few minutes would be a guess presented as a fact.
AMBIGUITY_WINDOW = timedelta(hours=6)

#: How many completed runs to look at when resolving "the last research".
LOOKBACK_RUNS = 5

_MAX_TOPIC_CHARS = 60


@dataclass(frozen=True, slots=True)
class CompletedResearch:
    """One finished research run, as a follow-up needs to name it."""

    research_job_id: str
    artifact_id: str | None
    topic: str
    completed_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_job_id": self.research_job_id,
            "artifact_id": self.artifact_id,
            "topic": self.topic,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @property
    def short_topic(self) -> str:
        topic = (self.topic or "").strip()
        if len(topic) <= _MAX_TOPIC_CHARS:
            return topic
        return topic[: _MAX_TOPIC_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ResearchBinding:
    """The bound run, how it was found, and whether the context was ambiguous."""

    context: CompletedResearch | None = None
    #: "session" | "plan" | "latest" | "" - how the binding was reached, for the record.
    basis: str = ""
    ambiguous: bool = False
    candidates: tuple[CompletedResearch, ...] = ()

    @property
    def research_job_id(self) -> str | None:
        return self.context.research_job_id if self.context else None

    @property
    def artifact_id(self) -> str | None:
        return self.context.artifact_id if self.context else None

    @property
    def bound(self) -> bool:
        return self.context is not None and not self.ambiguous

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_job_id": self.research_job_id,
            "research_artifact_id": self.artifact_id,
            "basis": self.basis,
            "ambiguous": self.ambiguous,
            "candidates": [c.as_dict() for c in self.candidates],
        }

    def clarifying_question(self) -> str:
        """ONE short Turkish question, asked instead of guessing (ADR-0075).

        Never an apology, never a list of identifiers: two topics and a question.
        """
        topics = [c.short_topic or "başlıksız araştırma" for c in self.candidates[:2]]
        if len(topics) < 2:
            return "Hangi araştırmayı anlatmamı istersiniz efendim?"
        return (
            f"Efendim, iki tamamlanmış araştırmam var: «{topics[0]}» ve «{topics[1]}». "
            "Hangisini anlatayım?"
        )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _completed(db: Session, task_id: uuid.UUID) -> CompletedResearch | None:
    """The run at ``task_id`` as a completed research, or None when it is not one.

    "Completed" means what the pipeline means by it: the run row is at stage ``ready``
    AND a report row exists with a report body. A run still crawling, a failed run and
    a ready run whose report never landed are all "no completed research" here - the
    caller then has nothing to bind and asks, or explains without a binding.
    """
    from app.research.models import STAGE_READY, ResearchReportRow, ResearchRunRow

    run = db.get(ResearchRunRow, task_id)
    if run is None or run.stage != STAGE_READY:
        return None
    report = db.get(ResearchReportRow, task_id)
    if report is None or not (report.report_json or {}):
        return None
    return _view(run, report)


def _view(run: Any, report: Any) -> CompletedResearch:
    report_json = dict(report.report_json or {})
    plan_json = dict(getattr(run, "plan_json", None) or {})
    topic = str(
        report_json.get("topic") or plan_json.get("topic") or plan_json.get("input") or ""
    )
    return CompletedResearch(
        research_job_id=str(run.task_id),
        artifact_id=str(report.artifact_id) if report.artifact_id else None,
        topic=topic,
        completed_at=_aware(getattr(report, "updated_at", None))
        or _aware(getattr(run, "updated_at", None)),
    )


def list_completed_research(
    db: Session, *, now: datetime | None = None, limit: int = LOOKBACK_RUNS
) -> list[CompletedResearch]:
    """The owner's completed researches, most recent first, inside the context window."""
    from app.research.models import STAGE_READY, ResearchReportRow, ResearchRunRow

    now = now or datetime.now(UTC)
    rows = (
        db.execute(
            select(ResearchRunRow)
            .where(ResearchRunRow.stage == STAGE_READY)
            .order_by(ResearchRunRow.updated_at.desc())
            .limit(max(limit, 1) * 2)
        )
        .scalars()
        .all()
    )
    out: list[CompletedResearch] = []
    for run in rows:
        report = db.get(ResearchReportRow, run.task_id)
        if report is None or not (report.report_json or {}):
            continue
        view = _view(run, report)
        if view.completed_at is not None and (now - view.completed_at) > CONTEXT_WINDOW:
            continue
        out.append(view)
        if len(out) >= limit:
            break
    out.sort(key=lambda c: c.completed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


def bind_completed_research(
    db: Session,
    *,
    last_research: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ResearchBinding:
    """Bind a follow-up to the completed research it is about (see module docstring).

    Never re-runs the query, never starts anything, never infers the job by matching
    topic text: identity comes from a durable row's own primary key or from nothing.
    """
    now = now or datetime.now(UTC)

    for source, raw in (
        ("session", (last_research or {}).get("research_job_id")),
        ("plan", (plan or {}).get("research_job_id") or (plan or {}).get("task_id")),
    ):
        if not raw:
            continue
        try:
            task_id = uuid.UUID(str(raw))
        except ValueError:
            continue
        found = _completed(db, task_id)
        if found is not None:
            return ResearchBinding(context=found, basis=source, candidates=(found,))

    recent = list_completed_research(db, now=now)
    if not recent:
        return ResearchBinding()
    if len(recent) >= 2:
        newest, runner_up = recent[0], recent[1]
        if (
            newest.completed_at is not None
            and runner_up.completed_at is not None
            and (newest.completed_at - runner_up.completed_at) <= AMBIGUITY_WINDOW
        ):
            return ResearchBinding(
                context=None, basis="latest", ambiguous=True, candidates=tuple(recent[:2])
            )
    return ResearchBinding(context=recent[0], basis="latest", candidates=(recent[0],))


def has_completed_research(db: Session, *, now: datetime | None = None) -> bool:
    """Whether ANY completed research exists to answer a follow-up from.

    This is the one bit of durable context ``app.voice.intents.resolve_intent`` takes:
    without it, "teknik anlat" is an ordinary technical explanation and nothing is bound.
    """
    return bool(list_completed_research(db, now=now, limit=1))


__all__ = [
    "AMBIGUITY_WINDOW",
    "CONTEXT_WINDOW",
    "CompletedResearch",
    "ResearchBinding",
    "bind_completed_research",
    "has_completed_research",
    "list_completed_research",
]
