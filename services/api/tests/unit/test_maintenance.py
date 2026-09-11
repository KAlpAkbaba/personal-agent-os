"""The retention sweeps nothing ran (Phase 8, 2026-09-11) - now run, and wired for real.

Each sweep's own semantics are held by its module's tests (memory lifecycle, identity,
security registry). What is new, and pinned here, is that they RUN: that the real
application object carries a sweeper that calls exactly those three, that one failing
never stops the others, and that it can be switched off.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.identity.service import IdentityService
from app.main import create_app
from app.maintenance import RetentionSweeper
from app.memory.store import NativeMemoryBackend
from app.security.registry import AuthorizedAssetRegistry


def test_the_application_sweeps_memory_sessions_and_assets(monkeypatch) -> None:
    # The three methods that existed with no caller. Replaced here only to count calls: the
    # point is that the APPLICATION's sweeper reaches each of them.
    monkeypatch.setattr(NativeMemoryBackend, "sweep_expired", lambda self: 2)
    monkeypatch.setattr(IdentityService, "sweep_expired", lambda self, **_: 1)
    monkeypatch.setattr(AuthorizedAssetRegistry, "sweep_expired", lambda self, **_: ["a", "b", "c"])
    app = create_app(Settings(_env_file=None))

    results = app.state.retention_sweeper.sweep_once()

    assert results == {"memory": 2, "identity_sessions": 1, "security_assets": 3}
    assert app.state.retention_sweeper.last_results == results
    assert app.state.retention_sweeper.last_run_at is not None


def test_one_failing_sweep_never_stops_the_others() -> None:
    calls: list[str] = []

    def boom() -> int:
        calls.append("memory")
        raise RuntimeError("database gone")

    def fine() -> int:
        calls.append("identity_sessions")
        return 4

    sweeper = RetentionSweeper(
        {"memory": boom, "identity_sessions": fine}, interval_s=60, initial_delay_s=0
    )
    results = sweeper.sweep_once()

    assert calls == ["memory", "identity_sessions"]
    assert results == {"memory": "error: RuntimeError", "identity_sessions": 4}


def test_an_interval_of_zero_turns_it_off() -> None:
    sweeper = RetentionSweeper({"memory": lambda: 1}, interval_s=0, initial_delay_s=0)

    async def scenario() -> bool:
        await sweeper.start()
        running = sweeper.running
        await sweeper.stop()
        return running

    assert asyncio.run(scenario()) is False
    assert sweeper.health_check()["status"] == "ok"
    assert sweeper.health_check()["required"] is False


def test_the_first_pass_waits_for_the_process_to_finish_booting() -> None:
    ran: list[int] = []
    sweeper = RetentionSweeper(
        {"memory": lambda: ran.append(1) or 1}, interval_s=0.01, initial_delay_s=30
    )

    async def scenario() -> None:
        await sweeper.start()
        await asyncio.sleep(0.05)
        assert sweeper.running
        await sweeper.stop()

    asyncio.run(scenario())
    assert ran == []


def test_the_loop_really_sweeps_on_its_clock() -> None:
    ran: list[int] = []
    sweeper = RetentionSweeper(
        {"memory": lambda: ran.append(1) or 1}, interval_s=0.01, initial_delay_s=0
    )

    async def scenario() -> None:
        await sweeper.start()
        for _ in range(200):
            if len(ran) >= 2:
                break
            await asyncio.sleep(0.01)
        await sweeper.stop()

    asyncio.run(scenario())
    assert len(ran) >= 2
