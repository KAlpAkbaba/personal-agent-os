"""Embedded Temporal worker (ADR-0050 §9).

The production compose runs no separate worker container, and the browser
research path needs the API process's own ``BrokerRuntime`` for immediate
device-command delivery anyway — so ``PAGENTOS_WORKER_MODE=embedded`` runs
the exact same ``app.worker.build_worker`` Worker in-process, inside the
API's own lifespan, instead of standing up a second process. ``off``
(default, tests) starts nothing; ``external`` means the standalone
``python -m app.worker`` is used instead and this runtime is a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import Settings
from app.logging import get_logger

logger = get_logger("app.research.embedded_worker")

WORKER_MODE_OFF = "off"
WORKER_MODE_EMBEDDED = "embedded"
WORKER_MODE_EXTERNAL = "external"
WORKER_MODES = (WORKER_MODE_OFF, WORKER_MODE_EMBEDDED, WORKER_MODE_EXTERNAL)


class EmbeddedWorkerRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = settings.worker_mode == WORKER_MODE_EMBEDDED
        self._worker: Any = None
        self._task: asyncio.Task[None] | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._client: Any = None
        self._started = False
        self._start_error: str | None = None

    async def start(self) -> None:
        if not self.enabled:
            return
        from temporalio.client import Client

        from app.worker import build_worker

        try:
            self._client = await Client.connect(
                self.settings.temporal_address, namespace=self.settings.temporal_namespace
            )
            self._executor = ThreadPoolExecutor(max_workers=8)
            self._worker = build_worker(
                self._client, self.settings.temporal_task_queue, activity_executor=self._executor
            )
            self._task = asyncio.create_task(self._worker.run(), name="embedded-temporal-worker")
            self._started = True
            logger.info("embedded_worker_started", task_queue=self.settings.temporal_task_queue)
        except Exception as exc:  # noqa: BLE001 - startup must not crash the API process
            self._start_error = f"{type(exc).__name__}: {exc}"
            logger.warning("embedded_worker_start_failed", error=self._start_error)

    async def stop(self) -> None:
        if self._worker is not None:
            with contextlib.suppress(Exception):
                await self._worker.shutdown()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._executor is not None:
            self._executor.shutdown(wait=False)
        self._started = False
        self._task = None
        self._worker = None

    def health_check(self) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "skipped", "mode": self.settings.worker_mode, "running": False}
        running = self._started and self._task is not None and not self._task.done()
        if running:
            return {"status": "ok", "mode": WORKER_MODE_EMBEDDED, "running": True}
        return {
            "status": "fail",
            "mode": WORKER_MODE_EMBEDDED,
            "running": False,
            "error": self._start_error or "not started",
        }


__all__ = [
    "WORKER_MODES",
    "WORKER_MODE_EMBEDDED",
    "WORKER_MODE_EXTERNAL",
    "WORKER_MODE_OFF",
    "EmbeddedWorkerRuntime",
]
