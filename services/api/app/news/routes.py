"""Latest News Mode REST surface (docs/M26_LATEST_NEWS_MODE_SPEC.md §1, §2, §5, §6).
Owner-gated like every other surface (research, artifacts, devices).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.logging import get_logger
from app.news import sources_service
from app.news.models import CONTENT_TYPES, PROVIDER_YOUTUBE
from app.news.playback_service import PlaybackOutcome, close_playback, open_latest_news
from app.news.resolve_service import NewsResolveError, resolve_for_source
from app.news.sources_service import NewsSourceError
from app.news.summary import summary_topic
from app.research import service as research_service

logger = get_logger("app.news.routes")

router = APIRouter(prefix="/v1/news", dependencies=[Depends(require_owner_session)])


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _device_action(request: Request) -> Any:
    return getattr(request.app.state, "device_action", None)


def _news_provider(request: Request) -> Any:
    """A provider OVERRIDE (tests only — a deterministic fixture registered on
    ``app.state.news_provider``, the same seam ``ctx.live["news_provider"]`` gives the
    voice tools). Production never sets this: ``None`` falls through to each source's
    own configured provider (``YouTubeFeedProvider`` for ``provider="youtube"``)."""
    return getattr(request.app.state, "news_provider", None)


def _broker(request: Request) -> Any:
    return request.app.state.broker


_ERROR_STATUS: dict[str, int] = {
    sources_service.ERROR_INVALID_SLUG: 400,
    sources_service.ERROR_ALREADY_EXISTS: 409,
    sources_service.ERROR_NOT_FOUND: 404,
    sources_service.ERROR_INVALID_CONTENT_TYPE: 400,
}


# --------------------------------------------------------------------- sources CRUD


class CreateSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_source_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    channel_input: str | None = Field(default=None, max_length=500)
    provider: str = PROVIDER_YOUTUBE
    content_type: str = "latest_any_news"
    priority: int = 100
    enabled: bool = True


class UpdateSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)
    channel_input: str | None = Field(default=None, max_length=500)
    content_type: str | None = None
    priority: int | None = None
    enabled: bool | None = None


@router.post("/sources", status_code=201)
async def create_source(request: Request, body: CreateSourceRequest) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def _do() -> dict[str, Any]:
        with artifacts.session() as session:
            try:
                view = sources_service.create_source(
                    session,
                    news_source_id=body.news_source_id,
                    display_name=body.display_name,
                    channel_input=body.channel_input,
                    provider=body.provider,
                    content_type=body.content_type,
                    priority=body.priority,
                    enabled=body.enabled,
                )
            except NewsSourceError as exc:
                raise HTTPException(
                    status_code=_ERROR_STATUS.get(exc.error_class, 400),
                    detail={"error_class": exc.error_class, "detail": exc.message},
                ) from exc
            return view.as_dict()

    return await asyncio.to_thread(_do)


@router.get("/sources")
async def list_sources(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def _do() -> list[dict[str, Any]]:
        with artifacts.session() as session:
            return [v.as_dict() for v in sources_service.list_sources(session)]

    return {"sources": await asyncio.to_thread(_do)}


@router.get("/sources/{news_source_id}")
async def get_source(request: Request, news_source_id: str) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def _do() -> dict[str, Any] | None:
        with artifacts.session() as session:
            view = sources_service.get_source(session, news_source_id)
            return view.as_dict() if view else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail={"error_class": "not_found"})
    return result


@router.patch("/sources/{news_source_id}")
async def update_source(
    request: Request, news_source_id: str, body: UpdateSourceRequest
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def _do() -> dict[str, Any]:
        with artifacts.session() as session:
            try:
                view = sources_service.update_source(
                    session,
                    news_source_id,
                    display_name=body.display_name,
                    channel_input=body.channel_input,
                    content_type=body.content_type,
                    priority=body.priority,
                    enabled=body.enabled,
                )
            except NewsSourceError as exc:
                raise HTTPException(
                    status_code=_ERROR_STATUS.get(exc.error_class, 400),
                    detail={"error_class": exc.error_class, "detail": exc.message},
                ) from exc
            return view.as_dict()

    return await asyncio.to_thread(_do)


@router.delete("/sources/{news_source_id}", status_code=204)
async def delete_source(request: Request, news_source_id: str) -> None:
    artifacts = _artifacts(request)

    def _do() -> bool:
        with artifacts.session() as session:
            return sources_service.delete_source(session, news_source_id)

    found = await asyncio.to_thread(_do)
    if not found:
        raise HTTPException(status_code=404, detail={"error_class": "not_found"})


# --------------------------------------------------------------------- resolve (query-only)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_source_id: str
    content_type: str | None = None


@router.post("/resolve")
async def resolve(request: Request, body: ResolveRequest) -> dict[str, Any]:
    """ "Son haber ne zaman yüklenmiş?" / "hangi haberi açacaksın?" — the resolver's
    own decision, without opening anything (task brief §6)."""
    if body.content_type is not None and body.content_type not in CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error_class": "invalid_content_type", "detail": body.content_type},
        )
    artifacts = _artifacts(request)
    news_provider = _news_provider(request)

    def _do() -> dict[str, Any]:
        with artifacts.session() as session:
            source = sources_service.get_source(session, body.news_source_id)
            if source is None:
                raise HTTPException(status_code=404, detail={"error_class": "not_found"})
            try:
                outcome = resolve_for_source(
                    session, source, content_type=body.content_type, provider=news_provider
                )
            except NewsResolveError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error_class": exc.error_class, "detail": exc.message},
                ) from exc
            result = outcome.result
            return {
                "resolution_id": outcome.row_id,
                "news_source_id": source.news_source_id,
                "channel_id": source.channel_id,
                "content_type": result.content_type,
                "answered_by": result.answered_by,
                "reason": result.reason,
                "ambiguous": result.ambiguous,
                "candidates_considered": result.candidates_considered,
                "selected": (
                    {
                        "video_id": result.selected.video_id,
                        "title": result.selected.title,
                        "published_at": result.selected.published_at.isoformat(),
                        "url": result.selected.url,
                    }
                    if result.selected is not None
                    else None
                ),
            }

    return await asyncio.to_thread(_do)


# --------------------------------------------------------------------- open / close (playback)


class OpenNewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_source_id: str | None = None
    content_type: str | None = None


def _playback_status_code(outcome: PlaybackOutcome) -> int:
    if outcome.ok:
        return 202
    if outcome.error_class in ("not_found",):
        return 404
    if outcome.error_class in ("no_eligible_video", "identity_unresolved"):
        return 409
    return 502


@router.post("/open")
async def open_news(request: Request, body: OpenNewsRequest) -> JSONResponse:
    artifacts = _artifacts(request)
    device_action = _device_action(request)
    news_provider = _news_provider(request)

    def _do() -> PlaybackOutcome:
        with artifacts.session() as session:
            if body.news_source_id is not None:
                source = sources_service.get_source(session, body.news_source_id)
                if source is None:
                    raise HTTPException(status_code=404, detail={"error_class": "not_found"})
            else:
                source = sources_service.default_source(session)
            if source is None or source.channel_id is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_class": "identity_unresolved",
                        "detail": "no news source with a resolved channel identity",
                    },
                )
            return open_latest_news(
                session,
                device_action,
                source=source,
                content_type=body.content_type,
                provider=news_provider,
            )

    outcome = await asyncio.to_thread(_do)
    return JSONResponse(status_code=_playback_status_code(outcome), content=outcome.as_dict())


@router.get("/open/{context_id}")
async def get_playback(request: Request, context_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def _do() -> dict[str, Any] | None:
        with artifacts.session() as session:
            from app.news.models import NewsPlaybackContextRow

            row = session.get(NewsPlaybackContextRow, context_id)
            if row is None:
                return None
            return {
                "context_id": str(row.id),
                "news_source_id": row.news_source_id,
                "video_id": row.video_id,
                "channel_id": row.channel_id,
                "video_title": row.video_title,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "status": row.status,
                "receipt": row.receipt_json,
                "error_class": row.error_class,
            }

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail={"error_class": "not_found"})
    return result


@router.post("/open/{context_id}/close")
async def close_news(request: Request, context_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    device_action = _device_action(request)

    def _do() -> PlaybackOutcome:
        with artifacts.session() as session:
            return close_playback(session, device_action, context_id=context_id)

    outcome = await asyncio.to_thread(_do)
    if outcome.error_class == "not_found":
        raise HTTPException(status_code=404, detail={"error_class": "not_found"})
    return outcome.as_dict()


# --------------------------------------------------------------------- summarize (-> research)


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_source_id: str | None = None


@router.post("/summarize", status_code=202)
async def summarize_news(request: Request, body: SummarizeRequest) -> JSONResponse:
    """ "Haberleri özetle." — a research SUMMARY, never playback (task brief §6)."""
    artifacts = _artifacts(request)
    broker = _broker(request)

    def _prepare() -> tuple[Any, str]:
        with artifacts.session() as session:
            source = (
                sources_service.get_source(session, body.news_source_id)
                if body.news_source_id
                else None
            )
            topic = summary_topic(source)
            started = research_service.start_browser_research(
                session,
                broker,
                input=topic,
                recency_days=1,
                source=research_service.SOURCE_REST,
            )
            return started, topic

    started, topic = await asyncio.to_thread(_prepare)
    if started.error is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "no_capable_device",
                "detail": started.error,
                "task_id": str(started.task_id),
            },
        )
    from temporalio.client import Client

    settings = artifacts.settings
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await research_service.start_browser_research_workflow(
        client,
        artifacts,
        task_id=started.task_id,
        workflow_id=started.workflow_id,
        input=topic,
        recency_days=1,
        synthesis=artifacts.settings.research_default_synthesis,
        search_provider=artifacts.settings.research_search_provider,
    )
    return JSONResponse(
        status_code=202,
        content={
            "task_id": str(started.task_id),
            "workflow_id": started.workflow_id,
            "status": "planned",
            "device": started.device,
            "topic": topic,
        },
    )


__all__ = ["router"]
