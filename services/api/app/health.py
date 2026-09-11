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

#: Checks whose failure is REPORTED but does not make the process degraded. Redis: nothing in
#: Cloud Core reads or writes it (CLAUDE.md: ephemeral/cache concerns only, never a source of
#: truth - and today it is not even a cache). Until 2026-09-11 a Redis restart still turned
#: top-level health `degraded`, which fails every release's exact `ok` gate and raises an
#: alarm about a dependency nothing depends on. The day code starts using Redis it leaves
#: this set - test_health_endpoint holds that, by reading app/ for any Redis client.
ADVISORY_CHECKS: frozenset[str] = frozenset({"redis"})

#: A check status that is not a failure.
HEALTHY_STATUSES: tuple[str, ...] = ("ok", "skipped")


def is_degraded(checks: dict[str, Any]) -> bool:
    """Whether any REQUIRED check failed - THE rule, for every reader of a health map.

    /v1/system/health and the voice `state.now` answer both call this: two readers with two
    rules is how the owner could hear "Cloud Core kismen saglikli" about a process its own
    endpoint called ok. A value may be a check dict or a bare status string; a check is
    advisory by name (ADVISORY_CHECKS) or by carrying `required: false`.
    """
    for name, check in checks.items():
        if check is None:
            continue
        entry = check if isinstance(check, dict) else {"status": check}
        if entry.get("status") in HEALTHY_STATUSES:
            continue
        if name in ADVISORY_CHECKS or entry.get("required", True) is False:
            continue
        return True
    return False


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
    checks = dict(zip(names, results, strict=True))
    for name in ADVISORY_CHECKS & checks.keys():
        checks[name]["required"] = False
    return checks
