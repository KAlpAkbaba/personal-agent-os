"""Voice REST surface (M4).

Endpoints:
- GET  /v1/voice/preferences                 -> current owner preferences
- PATCH /v1/voice/preferences                -> partial update (owner override)
- POST /v1/voice/speaker/enroll              -> enroll from fixture embeddings
- POST /v1/voice/speaker/verify              -> OWNER/NOT_OWNER/UNCERTAIN
- GET  /v1/voice/speaker                     -> enrollment status (no audio/vector)
- POST /v1/voice/benchmark/run               -> generate + persist reports
- GET  /v1/voice/benchmark/reports           -> last generated report(s)
- GET  /v1/voice/providers                   -> provider list + capabilities
- GET  /v1/voice/capabilities                -> what the owner may say (B25 req 701)

All DB/object-store work runs in a thread (sync SQLAlchemy + boto3). Typed
VoiceError is mapped to a stable HTTP status + error_class body.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.dependencies import require_owner_session
from app.identity.service import SessionContext
from app.logging import get_logger
from app.security import step_up
from app.voice import capabilities as voice_capabilities
from app.voice import registry, service
from app.voice.benchmark import run_stt_benchmark, run_tts_benchmark
from app.voice.device_trust import device_is_trusted
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.runtime import VoiceRuntime

logger = get_logger("app.voice.routes")

# M9/ADR-0027: owner authentication is applied at the router, so a new
# endpoint in this module is protected by default rather than by memory.
# speaker enrollment/verification and voice preferences are owner identity,
# and this module has no surface that must stay reachable unauthenticated.
router = APIRouter(
    prefix="/v1/voice",
    dependencies=[Depends(require_owner_session)],
)

_STATUS_BY_CLASS = {
    VoiceErrorClass.VALIDATION_ERROR: 422,
    VoiceErrorClass.CAPABILITY_MISSING: 422,
    VoiceErrorClass.PROVIDER_AUTH_MISSING: 503,
    VoiceErrorClass.DEPENDENCY_UNAVAILABLE: 503,
    VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING: 503,
    VoiceErrorClass.ALL_PROVIDERS_FAILED: 502,
    VoiceErrorClass.TIMEOUT: 504,
    VoiceErrorClass.INTERNAL_BUG: 500,
}


def _runtime(request: Request) -> VoiceRuntime:
    return request.app.state.voice


def _raise_http(exc: VoiceError) -> None:
    status = _STATUS_BY_CLASS.get(exc.error_class, 400)
    raise HTTPException(status_code=status, detail=exc.to_dict()) from exc


# --------------------------------------------------------------- preferences


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = None
    executive_summary_first: bool | None = None
    narration_speed: float | None = Field(default=None, ge=0.5, le=3.0)
    read_headings: bool | None = None
    read_urls: bool | None = None
    read_footnotes: bool | None = None
    barge_in: bool | None = None
    source: str = Field(default="owner", pattern="^(owner|inferred)$")


@router.get("/preferences")
async def get_preferences(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)

    def load() -> dict[str, Any]:
        with runtime.session() as session:
            prefs = service.load_preferences(session)
        data = prefs.to_narration_settings()
        data["locale"] = prefs.locale
        return data

    return await asyncio.to_thread(load)


@router.patch("/preferences")
async def patch_preferences(request: Request, body: PreferencesUpdate) -> dict[str, Any]:
    runtime = _runtime(request)
    updates = {k: v for k, v in body.model_dump().items() if k != "source" and v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="no preference fields supplied")

    def save() -> dict[str, Any]:
        with runtime.session() as session:
            prefs = service.update_preferences(session, updates, source=body.source)
        data = prefs.to_narration_settings()
        data["locale"] = prefs.locale
        return data

    try:
        result = await asyncio.to_thread(save)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info("voice_preferences_updated", fields=sorted(updates), source=body.source)
    return result


# ------------------------------------------------------------------ speaker


# Embedding-shape bounds: keep requests small and DoS-resistant. A speaker
# embedding is a few hundred dims; a handful of samples enrolls an owner.
_MAX_EMBED_DIMS = 4096
_MAX_ENROLL_SAMPLES = 64


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Fixture embedding vectors (tests) or a real extractor's output (owner path).
    # Raw audio is NEVER accepted here.
    sample_embeddings: list[list[float]] = Field(min_length=1, max_length=_MAX_ENROLL_SAMPLES)
    model_id: str = Field(default="fixture-embed-v1", max_length=128)

    @field_validator("sample_embeddings")
    @classmethod
    def _bound_dims(cls, value: list[list[float]]) -> list[list[float]]:
        for vec in value:
            if not 1 <= len(vec) <= _MAX_EMBED_DIMS:
                raise ValueError(f"embedding dim out of range: {len(vec)}")
        return value


class VerifyRequest(BaseModel):
    # B05 req 246/663: `device_trusted` USED to be a field here, and the caller set it.
    # `extra="forbid"` means a client still sending it now gets a 422 rather than being
    # quietly ignored - a request that believes it is choosing its own trust level should
    # be told it is not, not allowed to think it succeeded. Trust is derived from the
    # authenticated session's device binding (app.voice.device_trust).
    model_config = ConfigDict(extra="forbid")

    probe_embedding: list[float] = Field(min_length=1, max_length=_MAX_EMBED_DIMS)


@router.post("/speaker/enroll", status_code=201)
async def enroll_speaker(request: Request, body: EnrollRequest) -> dict[str, Any]:
    runtime = _runtime(request)

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            row = service.enroll_and_store_owner(
                session,
                runtime.store,
                runtime.cipher,
                sample_embeddings=body.sample_embeddings,
                model_id=body.model_id,
                prefix=runtime.settings.voice_speaker_object_prefix,
            )
            return {
                "speaker_profile_id": str(row.id),
                "label": row.label,
                "model_id": row.model_id,
                "embedding_ref": row.embedding_ref,
                "enrollment_metadata": row.enrollment_metadata_json,
            }

    try:
        result = await asyncio.to_thread(do)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info(
        "voice_speaker_enrolled", model_id=body.model_id, samples=len(body.sample_embeddings)
    )
    return result


@router.post("/speaker/verify")
async def verify_speaker_route(
    request: Request,
    body: VerifyRequest,
    owner: Annotated[SessionContext, Depends(require_owner_session)],
) -> dict[str, Any]:
    runtime = _runtime(request)

    def do() -> tuple[dict[str, Any] | None, bool]:
        with runtime.session() as session:
            # Derived here, inside the same session that reads the profile: the answer is
            # about THIS request's authenticated session, and nothing the body carries.
            trusted = device_is_trusted(session, owner)
            verdict = service.verify_owner(
                session,
                runtime.store,
                runtime.cipher,
                probe_embedding=body.probe_embedding,
                device_trusted=trusted,
                thresholds=None,  # server-configured band only; not caller-overridable
            )
            if verdict is not None:
                # B05 req 245/665: the verdict outlives the response now. Before this it
                # was computed, returned and forgotten, so nothing downstream could ask
                # who was speaking - "advisory" in the most literal sense.
                step_up.record_verdict(
                    session,
                    owner_session_id=owner.session_id,
                    decision=str(verdict.decision),
                    score=verdict.score,
                    device_trusted=trusted,
                    effective_accept=verdict.effective_accept,
                )
            return (verdict.to_dict() if verdict else None), trusted

    try:
        result, trusted = await asyncio.to_thread(do)
    except VoiceError as exc:
        _raise_http(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="no enrolled owner profile")
    logger.info(
        "voice_speaker_verified",
        decision=result["decision"],
        device_trusted=trusted,
        derived_from="owner_session",
    )
    return result


@router.get("/speaker")
async def speaker_status(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)

    def load() -> dict[str, Any]:
        with runtime.session() as session:
            row = service.get_speaker_profile_row(session)
        if row is None:
            return {"enrolled": False}
        return {
            "enrolled": True,
            "speaker_profile_id": str(row.id),
            "model_id": row.model_id,
            "enrollment_metadata": row.enrollment_metadata_json,
        }

    return await asyncio.to_thread(load)


# ----------------------------------------------------------------- benchmark


@router.post("/benchmark/run")
async def run_benchmark(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)

    def do() -> dict[str, Any]:
        tts_report = run_tts_benchmark(registry.benchmark_tts_candidates())
        stt_report = run_stt_benchmark(registry.benchmark_stt_candidates())
        prefix = runtime.settings.voice_benchmark_object_prefix
        tts_keys = service.store_benchmark_report(runtime.store, tts_report, prefix=prefix)
        stt_keys = service.store_benchmark_report(runtime.store, stt_report, prefix=prefix)
        return {
            "tts": {
                "providers": tts_report.providers,
                "keys": tts_keys,
                "compares_provider_count": len(tts_report.providers),
            },
            "stt": {
                "providers": stt_report.providers,
                "keys": stt_keys,
                "compares_provider_count": len(stt_report.providers),
            },
        }

    try:
        result = await asyncio.to_thread(do)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info(
        "voice_benchmark_generated",
        tts_providers=result["tts"]["compares_provider_count"],
        stt_providers=result["stt"]["compares_provider_count"],
    )
    return result


@router.get("/benchmark/reports")
async def get_benchmark_reports(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    prefix = runtime.settings.voice_benchmark_object_prefix

    def load() -> dict[str, Any]:
        return {
            "tts": service.load_benchmark_report(runtime.store, kind="tts", prefix=prefix),
            "stt": service.load_benchmark_report(runtime.store, kind="stt", prefix=prefix),
        }

    reports = await asyncio.to_thread(load)
    if reports["tts"] is None and reports["stt"] is None:
        raise HTTPException(
            status_code=404,
            detail="no benchmark report generated yet (POST /v1/voice/benchmark/run)",
        )
    return reports


# ----------------------------------------------------------------- providers


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    caps = registry.all_provider_capabilities(runtime.settings)
    return {"providers": caps, "count": len(caps)}


@router.get("/capabilities")
async def list_capabilities(
    family: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    """B25 req 701: what the owner may SAY to this system, derived from the tool registry.

    Not a hand-written list, which the requirement forbids in four words (*elle liste
    yasak*) for the reason a hand-written list always fails: it is a second source of truth
    about the system's own abilities, and it starts drifting the day after it is written.

    The example phrases are lifted out of the tool descriptions rather than reworded, so
    what the page tells the owner to say is literally what the model was told to listen for.
    """
    items = voice_capabilities.capabilities()
    if family:
        wanted = family.strip().lower()
        items = [item for item in items if item.family == wanted]
    return {
        "capabilities": [item.as_dict() for item in items],
        "families": voice_capabilities.families(voice_capabilities.capabilities()),
        "count": len(items),
        "speech": voice_capabilities.speech(items),
    }


__all__ = ["router"]
