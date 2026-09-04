"""FastAPI application entrypoint.

Run locally:
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.artifacts.routes import router as artifacts_router
from app.artifacts.runtime import ArtifactRuntime
from app.broker.routes import router as broker_router
from app.broker.runtime import BrokerRuntime
from app.broker.ws import router as broker_ws_router
from app.config import Settings, get_settings
from app.devices.commands import register_broker_runtime
from app.evolution.routes import router as evolution_router
from app.evolution.runtime import EvolutionRuntime
from app.health import run_health_checks
from app.identity.routes import router as identity_router
from app.identity.runtime import IdentityRuntime
from app.ledger import service as ledger_service
from app.ledger.routes import router as ledger_router
from app.logging import configure_logging, get_logger
from app.memory.routes import router as memory_router
from app.memory.runtime import MemoryRuntime
from app.middleware import TraceIdMiddleware
from app.mobile.routes import router as mobile_router
from app.mobile.runtime import MobileRuntime
from app.narration.routes import router as narration_router
from app.research.embedded_worker import EmbeddedWorkerRuntime
from app.research.health import research_health
from app.research.routes import router as research_router
from app.security.routes import router as security_router
from app.security.runtime import SecurityRuntime
from app.selfhealing.routes import router as selfhealing_router
from app.selfhealing.runtime import SelfHealingRuntime
from app.voice.realtime_sessions.routes import router as voice_realtime_router
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.routes import router as voice_router
from app.voice.runtime import VoiceRuntime

configure_logging()
logger = get_logger("app.main")


class UTF8JSONResponse(JSONResponse):
    """``application/json; charset=utf-8`` on every JSON response.

    The body was always UTF-8 (the database holds ``ş``/``ğ`` correctly); without
    the charset a Windows PowerShell 5.1 client decoded it as Latin-1 and showed
    the owner ``Ã``/``Å`` mojibake in a real qualification record. Declaring it
    fixes every RFC-conformant client; the repository's own scripts decode bytes as
    UTF-8 regardless.
    """

    media_type = "application/json; charset=utf-8"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    broker = BrokerRuntime(settings)
    artifacts = ArtifactRuntime(settings)
    voice = VoiceRuntime(settings)
    memory = MemoryRuntime(settings)
    selfhealing = SelfHealingRuntime(settings)
    evolution = EvolutionRuntime(settings)
    security = SecurityRuntime(settings)
    identity = IdentityRuntime(settings)
    mobile = MobileRuntime(settings)
    # M12: realtime voice sessions push sideband messages over the broker's
    # device WebSocket, so the runtime is handed the broker (never a socket).
    voice_realtime = RealtimeVoiceRuntime(settings, broker=broker)
    # M13/ADR-0050 §9: the research pipeline's fetch activities dispatch
    # device commands through THIS process's BrokerRuntime for immediate
    # delivery (app.devices.commands); embedded_worker optionally runs the
    # Temporal worker in-process too (PAGENTOS_WORKER_MODE=embedded).
    embedded_worker = EmbeddedWorkerRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await broker.start()
        register_broker_runtime(broker)
        await artifacts.start()
        # M9: the artifact-ready announcer runs HERE, in the process that holds
        # the push registrations. The Temporal worker only makes a task READY;
        # this drains READY-but-unannounced tasks (app/mobile/announcer.py).
        await mobile.announcer.start()
        await embedded_worker.start()
        # M16 track A: re-derive activity_events from canonical tables on every
        # start (spec §1.4, safe to call twice). Never blocks startup — an older
        # DB without the ledger tables yet, or any other backfill failure, is
        # logged and swallowed so a broken ledger can never take Cloud Core down.
        try:
            with artifacts.session() as ledger_session:
                report = await asyncio.to_thread(ledger_service.backfill, ledger_session)
            logger.info("ledger_backfill_at_startup", created=report.total_created)
        except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
            logger.warning(
                "ledger_backfill_at_startup_failed", error=f"{type(exc).__name__}: {exc}"
            )
        logger.info("broker_started")
        try:
            yield
        finally:
            await embedded_worker.stop()
            await mobile.announcer.stop()
            register_broker_runtime(None)
            await broker.stop()
            logger.info("broker_stopped")

    # M9: the interactive docs and the OpenAPI schema enumerate every endpoint
    # and request shape to an unauthenticated caller. Useful locally, gratuitous
    # once this is reachable over a network, so they follow the environment.
    docs_enabled = settings.environment == "dev"
    app = FastAPI(
        default_response_class=UTF8JSONResponse,
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.broker = broker
    app.state.artifacts = artifacts
    app.state.voice = voice
    app.state.memory = memory
    app.state.selfhealing = selfhealing
    app.state.evolution = evolution
    app.state.security = security
    app.state.identity = identity
    app.state.mobile = mobile
    app.state.voice_realtime = voice_realtime
    app.state.embedded_worker = embedded_worker
    # Scoped CORS: the web shell is a separate origin from the API. Allow only
    # the configured loopback/private web origins (never "*"); M0 review #3.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        # PATCH/PUT/DELETE: the owner web UI must be able to correct/forget
        # memory and edit narration/pronunciation (constitution §9).
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        # M9: every non-health endpoint now requires an owner bearer session,
        # so the browser must be allowed to send Authorization.
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
        max_age=600,
    )
    app.add_middleware(TraceIdMiddleware)
    # M9/ADR-0027: the identity router is included first so the authentication
    # surface is registered before everything it protects.
    app.include_router(identity_router)
    app.include_router(broker_router)
    app.include_router(broker_ws_router)
    app.include_router(artifacts_router)
    app.include_router(voice_router)
    app.include_router(voice_realtime_router)
    app.include_router(narration_router)
    app.include_router(memory_router)
    app.include_router(selfhealing_router)
    app.include_router(evolution_router)
    app.include_router(security_router)
    app.include_router(mobile_router)
    app.include_router(research_router)
    app.include_router(ledger_router)

    @app.get("/v1/system/health")
    async def system_health() -> dict[str, Any]:
        checks = await run_health_checks(settings)
        checks["broker"] = broker.health_check()
        # M3: object-store reachability for the artifact subsystem.
        checks["artifacts"] = await asyncio.to_thread(artifacts.health_check)
        # M4: voice-provider activation status (offline fakes vs key-gated reals).
        checks["voice"] = await asyncio.to_thread(voice.health_check)
        # M5: memory backend + embedder identity (native, deterministic by default).
        checks["memory"] = await asyncio.to_thread(memory.health_check)
        # M6: coding-backend identity + recovery-supervisor script availability.
        checks["selfhealing"] = await asyncio.to_thread(selfhealing.health_check)
        # M7: skill-generator identity + evolution sandbox posture.
        checks["evolution"] = await asyncio.to_thread(evolution.health_check)
        # M8: authorized-asset scope authority + defensive-collector posture.
        checks["security"] = await asyncio.to_thread(security.health_check)
        # M9: authentication posture (scheme, credential-root kind, windows).
        # Deliberately does not say whether an owner credential exists — this
        # is the one unauthenticated endpoint.
        checks["identity"] = await asyncio.to_thread(identity.health_check)
        # M9: push-transport posture (which real provider a credential would
        # activate) + the share/export bound. No I/O, no secrets.
        checks["mobile"] = await asyncio.to_thread(mobile.health_check)
        # M12: which ConversationRealtime provider a session would select, by
        # capability, and why (no I/O, no secrets).
        checks["voice_realtime"] = await asyncio.to_thread(voice_realtime.health_check)
        # M13: the embedded Temporal worker's own run state (ok when running,
        # "skipped" — not degraded — when PAGENTOS_WORKER_MODE is not
        # "embedded", e.g. every test and the "off"/"external" defaults).
        checks["temporal_worker"] = await asyncio.to_thread(embedded_worker.health_check)
        # M13: synthesis-provider configuration posture (no I/O, no secrets).
        checks["research"] = await asyncio.to_thread(research_health, settings)
        degraded = any(check["status"] not in ("ok", "skipped") for check in checks.values())
        status = "degraded" if degraded else "ok"
        logger.info("health_checked", status=status, checks=checks)
        return {"status": status, "version": __version__, "checks": checks}

    return app


app = create_app()
