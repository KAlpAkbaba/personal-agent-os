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

All DB/object-store work runs in a thread (sync SQLAlchemy + boto3). Typed
VoiceError is mapped to a stable HTTP status + error_class body.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.logging import get_logger
from app.voice import registry, service
from app.voice.benchmark import run_stt_benchmark, run_tts_benchmark
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.runtime import VoiceRuntime

logger = get_logger("app.voice.routes")

router = APIRouter(prefix="/v1/voice")

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
    updates = {k: v for k, v in body.model_dump().items()
               if k != "source" and v is not None}
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
    model_config = ConfigDict(extra="forbid")

    probe_embedding: list[float] = Field(min_length=1, max_length=_MAX_EMBED_DIMS)
    # SECURITY (M4 review #1): this is an UNTRUSTED, client-asserted hint today.
    # Speaker verification MUST NOT gate any privileged action until device
    # trust is derived server-side from an authenticated enrolled-device/broker
    # session (tracked owner-action gate). The pure classifier already caps an
    # untrusted device at UNCERTAIN; per-call threshold overrides were removed
    # so the accept/reject band cannot be widened by the caller.
    device_trusted: bool = False


@router.post("/speaker/enroll", status_code=201)
async def enroll_speaker(request: Request, body: EnrollRequest) -> dict[str, Any]:
    runtime = _runtime(request)

    def do() -> dict[str, Any]:
        with runtime.session() as session:
            row = service.enroll_and_store_owner(
                session, runtime.store, runtime.cipher,
                sample_embeddings=body.sample_embeddings, model_id=body.model_id,
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
    logger.info("voice_speaker_enrolled", model_id=body.model_id,
                samples=len(body.sample_embeddings))
    return result


@router.post("/speaker/verify")
async def verify_speaker_route(request: Request, body: VerifyRequest) -> dict[str, Any]:
    runtime = _runtime(request)

    def do() -> dict[str, Any] | None:
        with runtime.session() as session:
            verdict = service.verify_owner(
                session, runtime.store, runtime.cipher,
                probe_embedding=body.probe_embedding, device_trusted=body.device_trusted,
                thresholds=None,  # server-configured band only; not caller-overridable
            )
            return verdict.to_dict() if verdict else None

    try:
        result = await asyncio.to_thread(do)
    except VoiceError as exc:
        _raise_http(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="no enrolled owner profile")
    logger.info("voice_speaker_verified", decision=result["decision"],
                device_trusted=body.device_trusted)
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
            "tts": {"providers": tts_report.providers, "keys": tts_keys,
                    "compares_provider_count": len(tts_report.providers)},
            "stt": {"providers": stt_report.providers, "keys": stt_keys,
                    "compares_provider_count": len(stt_report.providers)},
        }

    try:
        result = await asyncio.to_thread(do)
    except VoiceError as exc:
        _raise_http(exc)
    logger.info("voice_benchmark_generated",
                tts_providers=result["tts"]["compares_provider_count"],
                stt_providers=result["stt"]["compares_provider_count"])
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


__all__ = ["router"]
