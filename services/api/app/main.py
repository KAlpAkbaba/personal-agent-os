"""FastAPI application entrypoint.

Run locally:
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
"""

from typing import Any

from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings
from app.health import run_health_checks
from app.logging import configure_logging, get_logger
from app.middleware import TraceIdMiddleware

configure_logging()
logger = get_logger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, version=__version__)
    app.add_middleware(TraceIdMiddleware)

    @app.get("/v1/system/health")
    async def system_health() -> dict[str, Any]:
        checks = await run_health_checks(settings)
        degraded = any(check["status"] != "ok" for check in checks.values())
        status = "degraded" if degraded else "ok"
        logger.info("health_checked", status=status, checks=checks)
        return {"status": status, "version": __version__, "checks": checks}

    return app


app = create_app()
