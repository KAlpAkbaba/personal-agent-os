"""app.research.embedded_worker: worker_mode off/embedded posture (no real Temporal)."""

import asyncio

from app.config import Settings
from app.research.embedded_worker import EmbeddedWorkerRuntime


def test_off_mode_is_a_noop_and_reports_skipped() -> None:
    runtime = EmbeddedWorkerRuntime(Settings(_env_file=None, worker_mode="off"))
    assert runtime.enabled is False
    asyncio.run(runtime.start())
    health = runtime.health_check()
    assert health["status"] == "skipped"
    assert health["running"] is False
    asyncio.run(runtime.stop())  # must not raise even though nothing started


def test_external_mode_is_also_a_noop() -> None:
    runtime = EmbeddedWorkerRuntime(Settings(_env_file=None, worker_mode="external"))
    assert runtime.enabled is False
    health = runtime.health_check()
    assert health["status"] == "skipped"
    assert health["mode"] == "external"


def test_embedded_mode_reports_fail_when_temporal_unreachable() -> None:
    # No real Temporal server in unit tests; connect() must fail fast and the
    # health check must report a typed failure rather than hang or crash.
    runtime = EmbeddedWorkerRuntime(
        Settings(_env_file=None, worker_mode="embedded", temporal_address="127.0.0.1:1")
    )
    assert runtime.enabled is True
    asyncio.run(runtime.start())
    health = runtime.health_check()
    assert health["status"] == "fail"
    assert health["running"] is False
    assert health["error"]
    asyncio.run(runtime.stop())
