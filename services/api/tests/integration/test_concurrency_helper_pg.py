"""``fire_together`` really overlaps its requests on the real database - counted, not timed.

A route added to the real application object inside the test (no product code) opens a
session, reads ``pg_backend_pid()`` and records how many such requests are open at once, then
waits for the others on a ``threading.Barrier``. If the requests did not overlap the barrier
breaks and the route says so. The same route called one request after another is the
control: the measurement must tell the two apart, or it proves nothing.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi import Request
from sqlalchemy import text

from app.config import Settings
from tests.integration.concurrency import fire_together
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration

N = 4
PROBE = "/v1/_test/concurrency-probe"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


class Probe:
    def __init__(self, n: int, wait_s: float) -> None:
        self.barrier = threading.Barrier(n)
        self.wait_s = wait_s
        self.lock = threading.Lock()
        self.open = 0
        self.peak = 0


def _client_with_probe(settings: Settings, probe: Probe):  # noqa: ANN202
    client = owner_client(settings)

    async def concurrency_probe(request: Request) -> dict[str, Any]:
        artifacts = request.app.state.artifacts

        def run() -> dict[str, Any]:
            with artifacts.session() as session:
                pid = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
                with probe.lock:
                    probe.open += 1
                    probe.peak = max(probe.peak, probe.open)
                try:
                    probe.barrier.wait(timeout=probe.wait_s)
                    overlapped = True
                except threading.BrokenBarrierError:
                    overlapped = False
                finally:
                    with probe.lock:
                        probe.open -= 1
                return {"pid": pid, "overlapped": overlapped}

        return await asyncio.to_thread(run)

    client.app.add_api_route(PROBE, concurrency_probe, methods=["POST"])
    return client


def test_fire_together_holds_n_database_connections_open_at_once(settings) -> None:
    probe = Probe(N, wait_s=20.0)
    client = _client_with_probe(settings, probe)

    responses = fire_together(client, "POST", PROBE, json={}, n=N)

    assert [r.status_code for r in responses] == [200] * N, [r.text for r in responses]
    bodies = [r.json() for r in responses]
    assert all(b["overlapped"] for b in bodies), bodies
    assert len({b["pid"] for b in bodies}) == N, bodies
    assert probe.peak == N


def test_the_same_requests_one_after_another_do_not_overlap(settings) -> None:
    """The control: sequential calls through the same probe are seen as sequential."""
    probe = Probe(2, wait_s=0.5)
    client = _client_with_probe(settings, probe)

    first = client.post(PROBE, json={})
    probe.barrier.reset()
    second = client.post(PROBE, json={})

    assert [first.json()["overlapped"], second.json()["overlapped"]] == [False, False]
    assert probe.peak == 1


def test_meet_after_holds_every_request_inside_the_window_after_its_read(settings) -> None:
    """No barrier in the route this time: only ``meet_after`` makes the N reads meet."""
    probe = Probe(N, wait_s=0.0)
    client = owner_client(settings)

    async def window(request: Request) -> dict[str, Any]:
        artifacts = request.app.state.artifacts

        def run() -> dict[str, Any]:
            with artifacts.session() as session:
                with probe.lock:
                    probe.open += 1
                session.execute(text("SELECT 'two-devices-window'")).scalar_one()
                with probe.lock:
                    probe.peak = max(probe.peak, probe.open)
                    probe.open -= 1
                return {}

        return await asyncio.to_thread(run)

    client.app.add_api_route(PROBE, window, methods=["POST"])

    responses = fire_together(client, "POST", PROBE, json={}, n=N, meet_after="two-devices-window")

    assert [r.status_code for r in responses] == [200] * N
    assert probe.peak == N


def test_a_meet_after_that_matches_nothing_is_an_error_not_a_quiet_timing_race(settings) -> None:
    client = owner_client(settings)
    client.app.add_api_route(PROBE, lambda: {}, methods=["POST"])

    with pytest.raises(AssertionError, match="matched no statement"):
        fire_together(client, "POST", PROBE, json={}, n=2, meet_after="no-such-statement")


def test_fire_together_refuses_a_race_of_one(settings) -> None:
    with pytest.raises(ValueError):
        fire_together(owner_client(settings), "POST", PROBE, n=1)
