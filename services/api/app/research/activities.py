"""Temporal activities for the research pipeline (side-effectful steps).

Each activity is deterministic-input / idempotent so the durable workflow can be
replayed or resumed after a worker restart without duplicating artifacts,
versions, renders or sources. DB + object-store access happens only here (never
in the workflow), built from settings so the code runs identically in-process
and in the worker.
"""

import uuid
from typing import Any

from temporalio import activity

from app.artifacts import render_store, service
from app.artifacts.models import (
    ARTIFACT_KIND_RESEARCH_REPORT,
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    TASK_STATUS_PLANNED,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
)
from app.artifacts.renderers import DEFAULT_RENDER_FORMATS
from app.artifacts.runtime import build_artifact_context
from app.config import get_settings
from app.logging import get_logger, task_id_var
from app.research import compose
from app.research.compose import ScoredSource
from app.research.provider import DeterministicResearchProvider, ResearchProvider, SourceRecord

logger = get_logger("app.research.activities")


def get_provider(name: str) -> ResearchProvider:
    """Provider factory (seam). M3 ships only the deterministic provider; a real
    web adapter is wired here in a later milestone."""
    if name == DeterministicResearchProvider.name:
        return DeterministicResearchProvider()
    raise ValueError(f"unknown/unwired research provider: {name!r}")


def _scored_to_dict(s: ScoredSource) -> dict[str, Any]:
    return {
        "rank": s.rank,
        "url": s.url,
        "title": s.title,
        "snippet": s.snippet,
        "score": s.score,
        "provider": s.provider,
    }


def _dict_to_scored(d: dict[str, Any]) -> ScoredSource:
    return ScoredSource(
        rank=d["rank"],
        url=d["url"],
        title=d["title"],
        snippet=d["snippet"],
        score=d["score"],
        provider=d["provider"],
    )


def _artifact_title(topic: str) -> str:
    return f"Araştırma Raporu: {topic.strip()}"


@activity.defn(name="research_plan")
def plan_activity(task_id: str, topic: str, provider_name: str) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    factory, _ = build_artifact_context(get_settings())
    with factory() as session:
        service.transition_task(session, tid, TASK_STATUS_PLANNED)
        plan = {
            "topic": topic.strip(),
            "provider": provider_name,
            "steps": ["gather", "score_dedup", "compose", "render"],
            "render_formats": list(DEFAULT_RENDER_FORMATS),
        }
        service.start_task_run(
            session, task_id=tid, attempt=1, status=TASK_STATUS_PLANNED, plan=plan
        )
    logger.info("research_planned", provider=provider_name)
    return plan


@activity.defn(name="research_gather_sources")
def gather_sources_activity(
    task_id: str, topic: str, provider_name: str, limit: int
) -> list[dict[str, Any]]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    provider = get_provider(provider_name)
    raw: list[SourceRecord] = provider.gather(topic, limit=limit)
    scored = compose.score_and_dedup(raw)
    factory, _ = build_artifact_context(get_settings())
    with factory() as session:
        service.transition_task(session, tid, TASK_STATUS_RUNNING)
        service.replace_research_sources(session, task_id=tid, sources=scored)
    logger.info(
        "research_sources_gathered",
        provider=provider_name,
        raw_count=len(raw),
        deduped_count=len(scored),
    )
    return [_scored_to_dict(s) for s in scored]


@activity.defn(name="research_compose")
def compose_activity(
    task_id: str, topic: str, scored_dicts: list[dict[str, Any]]
) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    scored = [_dict_to_scored(d) for d in scored_dicts]
    executive_summary = compose.compose_executive_summary(topic, scored)
    canonical_markdown = compose.compose_canonical_markdown(topic, scored, executive_summary)
    manifest = compose.build_source_manifest(topic, scored)
    from app.artifacts.renderers import content_hash

    body_hash = content_hash(canonical_markdown.encode("utf-8"))

    factory, _ = build_artifact_context(get_settings())
    with factory() as session:
        artifact = service.get_or_create_artifact_for_task(
            session,
            task_id=tid,
            title=_artifact_title(topic),
            kind=ARTIFACT_KIND_RESEARCH_REPORT,
        )
        version = service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=canonical_markdown,
            content_hash=body_hash,
            source_manifest=manifest,
        )
        service.set_executive_summary(session, artifact.id, executive_summary)
        if artifact.state != ARTIFACT_STATE_CANONICAL_READY:
            service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
        service.transition_task(session, tid, TASK_STATUS_RENDERING)
        artifact_id = str(artifact.id)
        version_no = version.version
    logger.info("research_composed", artifact_id=artifact_id, version=version_no)
    return {
        "artifact_id": artifact_id,
        "version": version_no,
        "executive_summary": executive_summary,
        "content_hash": body_hash,
    }


@activity.defn(name="research_render")
def render_activity(
    task_id: str, artifact_id: str, version_no: int, formats: list[str]
) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    aid = uuid.UUID(artifact_id)
    factory, store = build_artifact_context(get_settings())
    renders_meta: list[dict[str, Any]] = []
    with factory() as session:
        artifact = service.get_artifact(session, aid)
        if artifact is None:
            raise ValueError(f"artifact vanished: {artifact_id}")
        version = service.get_version(session, aid, version_no)
        if version is None:
            raise ValueError(f"version missing: {artifact_id} v{version_no}")
        if artifact.state == ARTIFACT_STATE_CANONICAL_READY:
            service.set_artifact_state(session, aid, ARTIFACT_STATE_RENDERS_PENDING)
        rows = render_store.ensure_renders(
            session, store, version=version, title=artifact.title, formats=tuple(formats)
        )
        for r in rows:
            renders_meta.append(
                {
                    "format": r.format,
                    "object_key": r.object_key,
                    "mime_type": r.mime_type,
                    "content_hash": r.content_hash,
                    "size_bytes": r.size_bytes,
                }
            )
        if artifact.state != ARTIFACT_STATE_READY:
            service.set_artifact_state(session, aid, ARTIFACT_STATE_READY)
        # Task reaches READY here; the workflow returns only metadata + the
        # executive summary, never the full body (READY-without-auto-read).
        service.transition_task(session, tid, TASK_STATUS_READY)
        service.finish_task_run(
            session,
            task_id=tid,
            attempt=1,
            status=TASK_STATUS_READY,
            telemetry={"render_count": len(rows)},
        )
    logger.info("research_rendered", artifact_id=artifact_id, render_count=len(renders_meta))
    return {"artifact_id": artifact_id, "version": version_no, "renders": renders_meta}
