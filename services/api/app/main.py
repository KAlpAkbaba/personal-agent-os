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

from app import __version__
from app.artifacts.routes import router as artifacts_router
from app.artifacts.runtime import ArtifactRuntime
from app.broker.routes import router as broker_router
from app.broker.runtime import BrokerRuntime
from app.broker.ws import router as broker_ws_router
from app.config import Settings, get_settings
from app.health import run_health_checks
from app.logging import configure_logging, get_logger
from app.middleware import TraceIdMiddleware
from app.narration.routes import router as narration_router
from app.voice.routes import router as voice_router
from app.voice.runtime import VoiceRuntime

configure_logging()
logger = get_logger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    broker = BrokerRuntime(settings)
    artifacts = ArtifactRuntime(settings)
    voice = VoiceRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await broker.start()
        await artifacts.start()
        logger.info("broker_started")
        try:
            yield
        finally:
            await broker.stop()
            logger.info("broker_stopped")

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.broker = broker
    app.state.artifacts = artifacts
    app.state.voice = voice
    # Scoped CORS: the web shell is a separate origin from the API. Allow only
    # the configured loopback/private web origins (never "*"); M0 review #3.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Trace-Id"],
        max_age=600,
    )
    app.add_middleware(TraceIdMiddleware)
    app.include_router(broker_router)
    app.include_router(broker_ws_router)
    app.include_router(artifacts_router)
    app.include_router(voice_router)
    app.include_router(narration_router)

    @app.get("/v1/system/health")
    async def system_health() -> dict[str, Any]:
        checks = await run_health_checks(settings)
        checks["broker"] = broker.health_check()
        # M3: object-store reachability for the artifact subsystem.
        checks["artifacts"] = await asyncio.to_thread(artifacts.health_check)
        # M4: voice-provider activation status (offline fakes vs key-gated reals).
        checks["voice"] = await asyncio.to_thread(voice.health_check)
        degraded = any(check["status"] != "ok" for check in checks.values())
        status = "degraded" if degraded else "ok"
        logger.info("health_checked", status=status, checks=checks)
        return {"status": status, "version": __version__, "checks": checks}

    return app


app = create_app()
