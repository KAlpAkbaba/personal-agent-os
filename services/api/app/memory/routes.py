"""Memory REST surface (M5): /v1/memory.

The REST surface is the OWNER surface of the single-owner product: mutating
endpoints (PATCH/pin/supersede/DELETE, /remember) act with Actor.OWNER, while
POST /observe runs the write policy with policy authority (Actor.POLICY unless
`explicit` is asserted).

All inputs are bounded; logs carry ids/actions only — never memory text or
values (no secrets in logs). DB work runs in asyncio.to_thread like the other
modules.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.logging import get_logger
from app.memory import retrieval
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.policy import Observation
from app.memory.retrieval import RetrievalFilters
from app.memory.runtime import MemoryRuntime
from app.memory.service import MemoryLinks
from app.memory.types import Actor, MemoryClass, WriteStage

logger = get_logger("app.memory.routes")

router = APIRouter(prefix="/v1/memory")

_HTTP_STATUS = {
    MemoryErrorClass.SECRET_REJECTED: 422,
    MemoryErrorClass.VALIDATION_ERROR: 422,
    MemoryErrorClass.NOT_FOUND: 404,
    MemoryErrorClass.EXPLICIT_PROTECTED: 403,
    MemoryErrorClass.BACKEND_NOT_IMPLEMENTED: 501,
    MemoryErrorClass.INTERNAL_BUG: 500,
}


def _runtime(request: Request) -> MemoryRuntime:
    return request.app.state.memory


def _http_error(exc: MemorySubsystemError) -> HTTPException:
    return HTTPException(
        status_code=_HTTP_STATUS.get(exc.error_class, 500),
        detail={"error_class": str(exc.error_class), "message": exc.message},
    )


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except MemorySubsystemError as exc:
        raise _http_error(exc) from exc


# -------------------------------------------------------------------- writes


class LinksBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def to_links(self) -> MemoryLinks:
        return MemoryLinks(
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            artifact_id=self.artifact_id,
            device_id=self.device_id,
            occurred_at=self.occurred_at,
            valid_from=self.valid_from,
            valid_until=self.valid_until,
        )


class ObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    memory_class: MemoryClass = MemoryClass.SEMANTIC
    key: str | None = Field(default=None, max_length=256)
    value: dict[str, Any] = Field(default_factory=dict)
    explicit: bool = False
    confidence_hint: float | None = Field(default=None, ge=0.0, le=1.0)
    source: dict[str, Any] = Field(default_factory=dict)
    links: LinksBody = Field(default_factory=LinksBody)


def _observe_payload(result) -> dict[str, Any]:
    return {
        "action": result.action,
        "memory_id": str(result.memory_id) if result.memory_id else None,
        "stage": result.stage,
        "promoted": result.promoted,
        "reason": result.reason,
        "details": result.details,
    }


@router.post("/observe")
async def observe(request: Request, body: ObserveRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    obs = Observation(
        text=body.text,
        memory_class=body.memory_class,
        key=body.key,
        value=body.value,
        explicit=body.explicit,
        confidence_hint=body.confidence_hint,
        source=body.source,
    )
    result = await _call(runtime.backend.observe, obs, body.links.to_links())
    logger.info(
        "memory_observed",
        action=result.action,
        memory_id=str(result.memory_id) if result.memory_id else None,
        memory_class=body.memory_class.value,
    )
    return _observe_payload(result)


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    memory_class: MemoryClass = MemoryClass.PREFERENCE
    key: str | None = Field(default=None, max_length=256)
    value: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    links: LinksBody = Field(default_factory=LinksBody)


@router.post("/remember", status_code=201)
async def remember(request: Request, body: RememberRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    result = await _call(
        runtime.backend.remember,
        text=body.text,
        memory_class=body.memory_class,
        key=body.key,
        value=body.value,
        links=body.links.to_links(),
        source=body.source,
    )
    logger.info(
        "memory_remembered",
        action=result.action,
        memory_id=str(result.memory_id) if result.memory_id else None,
    )
    return _observe_payload(result)


# ------------------------------------------------------------------ retrieval


@router.get("/search")
async def search(
    request: Request,
    q: Annotated[str | None, Query(max_length=1000)] = None,
    memory_class: Annotated[MemoryClass | None, Query()] = None,
    key: Annotated[str | None, Query(max_length=256)] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    stage_min: Annotated[WriteStage | None, Query()] = None,
    explicit: Annotated[bool | None, Query()] = None,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
    valid_at: Annotated[datetime | None, Query()] = None,
    task_id: Annotated[uuid.UUID | None, Query()] = None,
    artifact_id: Annotated[uuid.UUID | None, Query()] = None,
    conversation_id: Annotated[uuid.UUID | None, Query()] = None,
    device_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = retrieval.DEFAULT_K,
) -> dict[str, Any]:
    runtime = _runtime(request)
    filters = RetrievalFilters(
        memory_class=memory_class.value if memory_class else None,
        key=key,
        project_id=project_id,
        stage_min=stage_min.value if stage_min else None,
        explicit=explicit,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        valid_at=valid_at,
        task_id=task_id,
        artifact_id=artifact_id,
        conversation_id=conversation_id,
        device_id=device_id,
    )
    results = await _call(runtime.backend.search, query=q, filters=filters, k=limit)
    return {"results": results, "count": len(results)}


@router.get("/audit")
async def audit(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    runtime = _runtime(request)
    events = await _call(runtime.backend.audit_events, limit=limit)
    return {"events": events, "count": len(events)}


# --------------------------------------------------------------- entity graph


class CreateEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=512)
    attrs: dict[str, Any] = Field(default_factory=dict)


def _entity_payload(entity) -> dict[str, Any]:
    return {
        "entity_id": str(entity.id),
        "kind": entity.kind,
        "name": entity.name,
        "attrs": entity.attrs_json,
    }


@router.post("/entities", status_code=201)
async def create_entity(request: Request, body: CreateEntityRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    from app.memory import service

    def create():
        with runtime.session() as session:
            return _entity_payload(
                service.create_entity(session, kind=body.kind, name=body.name, attrs=body.attrs)
            )

    return await _call(create)


@router.get("/entities")
async def list_entities(
    request: Request,
    kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _runtime(request)
    from app.memory import service

    def load():
        with runtime.session() as session:
            return [
                _entity_payload(e)
                for e in service.list_entities(session, kind=kind, limit=limit)
            ]

    items = await _call(load)
    return {"entities": items, "count": len(items)}


@router.get("/entities/{entity_id}")
async def get_entity(request: Request, entity_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    from app.memory import service

    def load():
        with runtime.session() as session:
            entity = service.get_entity(session, entity_id)
            edges = service.entity_edges(session, entity_id)
            payload = _entity_payload(entity)
            payload["edges"] = [
                {
                    "edge_id": str(edge.id),
                    "src_id": str(edge.src_id),
                    "dst_id": str(edge.dst_id),
                    "relation": edge.relation,
                    "attrs": edge.attrs_json,
                }
                for edge in edges
            ]
            return payload

    return await _call(load)


class CreateEdgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src_id: uuid.UUID
    dst_id: uuid.UUID
    relation: str = Field(min_length=1, max_length=64)
    attrs: dict[str, Any] = Field(default_factory=dict)


@router.post("/edges", status_code=201)
async def create_edge(request: Request, body: CreateEdgeRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    from app.memory import service

    def create():
        with runtime.session() as session:
            edge = service.create_edge(
                session,
                src_id=body.src_id,
                dst_id=body.dst_id,
                relation=body.relation,
                attrs=body.attrs,
            )
            return {
                "edge_id": str(edge.id),
                "src_id": str(edge.src_id),
                "dst_id": str(edge.dst_id),
                "relation": edge.relation,
            }

    return await _call(create)


# ------------------------------------------------------- per-memory endpoints


@router.get("/{memory_id}")
async def inspect(request: Request, memory_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    return await _call(runtime.backend.inspect, memory_id)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=4000)
    value: dict[str, Any] | None = None
    change_reason: str = Field(default="", max_length=512)


@router.patch("/{memory_id}")
async def edit(request: Request, memory_id: uuid.UUID, body: EditRequest) -> dict[str, Any]:
    runtime = _runtime(request)
    payload = await _call(
        runtime.backend.edit,
        memory_id,
        actor=Actor.OWNER,
        text=body.text,
        value=body.value,
        change_reason=body.change_reason,
    )
    logger.info("memory_edited", memory_id=str(memory_id), version=payload["version"])
    return payload


@router.post("/{memory_id}/pin")
async def pin(request: Request, memory_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    payload = await _call(runtime.backend.pin, memory_id, actor=Actor.OWNER)
    logger.info("memory_pinned", memory_id=str(memory_id))
    return payload


class SupersedeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    value: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=512)


@router.post("/{memory_id}/supersede", status_code=201)
async def supersede(
    request: Request, memory_id: uuid.UUID, body: SupersedeRequest
) -> dict[str, Any]:
    runtime = _runtime(request)
    payload = await _call(
        runtime.backend.supersede,
        memory_id,
        actor=Actor.OWNER,
        text=body.text,
        value=body.value,
        reason=body.reason,
    )
    logger.info(
        "memory_superseded", memory_id=str(memory_id), new_memory_id=payload["memory_id"]
    )
    return payload


@router.delete("/{memory_id}")
async def forget(request: Request, memory_id: uuid.UUID) -> dict[str, Any]:
    runtime = _runtime(request)
    payload = await _call(runtime.backend.forget, memory_id, actor=Actor.OWNER)
    logger.info("memory_forgotten", memory_id=str(memory_id))
    return payload


__all__ = ["router"]
