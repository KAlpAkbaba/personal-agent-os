"""Dependency health checks for /v1/system/health.

Each check returns {"status": "ok"|"fail", "latency_ms": float, ["error": str]}
and never raises: a down dependency yields a degraded overall status, not a
crashed endpoint.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import create_engine, text
from temporalio.client import Client as TemporalClient

from app.config import Settings
from app.object_store import S3ObjectStore

CheckResult = dict[str, Any]


async def _run_check(
    name: str, fn: Callable[[], Awaitable[None]], timeout_s: float
) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(fn(), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - health checks must not raise
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "fail",
            "latency_ms": latency_ms,
            "error": f"{type(exc).__name__}: {exc}",
        }
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {"status": "ok", "latency_ms": latency_ms}


async def check_db(settings: Settings) -> None:
    # Sync engine in a thread: psycopg async requires a selector event loop,
    # which uvicorn on Windows (Proactor) does not provide.
    def probe() -> None:
        engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()

    await asyncio.to_thread(probe)


async def check_redis(settings: Settings) -> None:
    client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_object_store(settings: Settings) -> None:
    store = S3ObjectStore.from_settings(settings)
    await asyncio.to_thread(store.health_check)


async def check_temporal(settings: Settings) -> None:
    """Connect to the Temporal frontend; connect itself performs a server call."""
    await TemporalClient.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )


async def run_health_checks(settings: Settings) -> dict[str, CheckResult]:
    timeout = settings.health_check_timeout_s
    names = ["db", "redis", "object_store", "temporal"]
    results = await asyncio.gather(
        _run_check("db", lambda: check_db(settings), timeout),
        _run_check("redis", lambda: check_redis(settings), timeout),
        _run_check("object_store", lambda: check_object_store(settings), timeout),
        _run_check("temporal", lambda: check_temporal(settings), timeout),
    )
    return dict(zip(names, results, strict=True))
