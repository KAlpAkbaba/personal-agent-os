"""REST surface for voice routing qualification (ADR-0080).

- GET  /v1/voice/qualification   the owner-facing state, from the record alone
- POST /v1/voice/qualification   record one suite run (the nightly script posts its report)

Both owner-gated. The POST is how a run that happened on the development machine becomes a
fact the Cloud Core can show; it records, it never runs anything.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.voice.qualification import service as qualification_service

router = APIRouter(
    prefix="/v1/voice/qualification",
    tags=["voice"],
    dependencies=[Depends(require_owner_session)],
)


class ConfusionRowIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(max_length=64)
    utterance: str = Field(default="", max_length=200)
    expected: str | None = Field(default=None, max_length=64)
    resolved: str | None = Field(default=None, max_length=64)
    problems: list[str] = Field(default_factory=list, max_length=6)


class ReportIn(BaseModel):
    """The suite's report, bounded. ``summary`` is recomputed from the counts server-side:
    a client cannot post a healthy word over failing numbers."""

    model_config = ConfigDict(extra="ignore")

    suite: str = Field(default="OwnerUtteranceSuite", max_length=64)
    corpus_version: int = Field(ge=0, le=100_000)
    generated_at: str = Field(max_length=40)
    total_cases: int = Field(ge=0, le=1_000_000)
    passed: int = Field(ge=0, le=1_000_000)
    clarification: int = Field(default=0, ge=0, le=1_000_000)
    failed_routing: int = Field(default=0, ge=0, le=1_000_000)
    forbidden_side_effects: int = Field(default=0, ge=0, le=1_000_000)
    summary: str | None = Field(default=None, max_length=32)
    confusion: list[ConfusionRowIn] = Field(default_factory=list, max_length=50)
    #: ``voice_corpus`` (the synthetic suite) or ``owner_audio`` (the owner's harness).
    source: str = Field(default=qualification_service.SOURCE_SYNTHETIC, pattern=r"^[a-z_]{3,32}$")


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _evolution_service(request: Request) -> Any | None:
    runtime = getattr(request.app.state, "evolution", None)
    if runtime is None:
        return None
    try:
        return runtime.evolution_service
    except Exception:  # noqa: BLE001 - a runtime that cannot build its service
        return None


@router.get("")
async def get_state(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            return qualification_service.state(session)

    return await asyncio.to_thread(load)


@router.post("")
async def post_report(request: Request, body: ReportIn) -> dict[str, Any]:
    artifacts = _artifacts(request)
    evolution = _evolution_service(request)
    report = body.model_dump()

    def write() -> dict[str, Any]:
        with artifacts.session() as session:
            row, normalised = qualification_service.record_report(
                session, report, source=body.source
            )
            opened = (
                qualification_service.open_opportunities(
                    normalised, ledger_row_id=str(row.event_id), evolution_service=evolution
                )
                if body.source == qualification_service.SOURCE_SYNTHETIC
                else []
            )
            current = qualification_service.state(session)
        return {
            **current,
            "recorded_at": (
                row.occurred_at.isoformat().replace("+00:00", "Z") if row.occurred_at else None
            ),
            "ledger_event_id": str(row.event_id),
            "opportunities_opened": [o.get("opportunity_id") for o in opened],
        }

    return await asyncio.to_thread(write)
