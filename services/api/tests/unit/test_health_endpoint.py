"""Health endpoint shape tests with mocked dependency checks.

M1: the endpoint adds a "broker" check (sweeper alive + active sessions) on
top of the M0 dependency checks. Clients are used as context managers so the
app lifespan runs and the broker sweeper is alive.
"""

from fastapi.testclient import TestClient

import app.health as health_module
from app import __version__
from app.config import Settings
from app.main import create_app

DEPENDENCY_CHECKS = {"db", "redis", "object_store", "temporal"}
# M3 adds an "artifacts" check (object store reachable) alongside "broker".
# M4 adds a "voice" check (real-provider activation status; always ok/offline).
# M5 adds a "memory" check (backend + embedder identity; no I/O, always ok).
# M6 adds a "selfhealing" check (coding backend + supervisor script presence).
# M7 adds an "evolution" check (skill generator identity + sandbox posture).
# M8 adds a "security" check (scope authority + collector posture; no I/O).
# M9 adds an "identity" check (auth posture only - deliberately does NOT say
# whether an owner credential is bootstrapped; health is the one open endpoint)
# and a "mobile" check (which real push transport a credential would activate,
# plus the share/export bound; no I/O, no secrets).
ALL_CHECKS = DEPENDENCY_CHECKS | {
    "broker",
    "artifacts",
    "voice",
    "memory",
    "selfhealing",
    "evolution",
    "security",
    "identity",
    "mobile",
    # M12 adds "voice_realtime" (capability-selected ConversationRealtime
    # provider + tool manifest; the simulator is always selectable offline).
    "voice_realtime",
}


def make_client(monkeypatch, checks: dict[str, dict]) -> TestClient:
    async def fake_run_health_checks(settings: Settings) -> dict[str, dict]:
        return dict(checks)

    # create_app resolves run_health_checks via app.main's import; patch there.
    monkeypatch.setattr("app.main.run_health_checks", fake_run_health_checks)
    # The artifacts check hits the object store; keep this a true unit test.
    monkeypatch.setattr(
        "app.artifacts.runtime.ArtifactRuntime.health_check",
        lambda self: {"status": "ok", "latency_ms": 0.0},
    )
    return TestClient(create_app(Settings(_env_file=None)))


ALL_OK = {
    "db": {"status": "ok", "latency_ms": 1.2},
    "redis": {"status": "ok", "latency_ms": 0.4},
    "object_store": {"status": "ok", "latency_ms": 3.1},
    "temporal": {"status": "ok", "latency_ms": 5.0},
}


def test_health_ok_shape(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert set(body["checks"].keys()) == ALL_CHECKS
    for check in body["checks"].values():
        assert check["status"] == "ok"
        assert isinstance(check["latency_ms"], int | float)


def test_health_broker_check_shape(monkeypatch) -> None:
    with make_client(monkeypatch, ALL_OK) as client:
        response = client.get("/v1/system/health")
    broker = response.json()["checks"]["broker"]
    assert broker["status"] == "ok"
    assert broker["sweeper_alive"] is True
    assert broker["active_sessions"] == 0


def test_health_broker_fails_without_lifespan(monkeypatch) -> None:
    """Without the lifespan (sweeper not started) the broker check reports fail."""
    client = make_client(monkeypatch, ALL_OK)
    response = client.get("/v1/system/health")
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["broker"]["status"] == "fail"
    assert body["checks"]["broker"]["sweeper_alive"] is False


def test_health_degraded_still_200(monkeypatch) -> None:
    checks = dict(ALL_OK)
    checks["redis"] = {"status": "fail", "latency_ms": 2000.0, "error": "TimeoutError: timeout"}
    with make_client(monkeypatch, checks) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["status"] == "fail"
    assert "error" in body["checks"]["redis"]


def test_health_never_crashes_when_all_down(monkeypatch) -> None:
    checks = {
        name: {"status": "fail", "latency_ms": 1.0, "error": "ConnectionError: down"}
        for name in ALL_OK
    }
    with make_client(monkeypatch, checks) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_real_check_runner_reports_fail_not_raise(monkeypatch) -> None:
    """run_health_checks itself must swallow dependency errors."""

    async def boom(settings: Settings) -> None:
        raise ConnectionError("dependency down")

    monkeypatch.setattr(health_module, "check_db", boom)
    monkeypatch.setattr(health_module, "check_redis", boom)
    monkeypatch.setattr(health_module, "check_object_store", boom)
    monkeypatch.setattr(health_module, "check_temporal", boom)

    import asyncio

    results = asyncio.run(health_module.run_health_checks(Settings(_env_file=None)))
    assert set(results) == DEPENDENCY_CHECKS
    for result in results.values():
        assert result["status"] == "fail"
        assert "ConnectionError" in result["error"]


def test_health_serves_the_realtime_contract_version() -> None:
    # ADR-0045: the release qualification reads this without auth and refuses an old one.
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.voice.realtime_sessions.contract_version import CONTRACT_VERSION

    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        doc = client.get("/v1/system/health").json()
    assert doc["checks"]["voice_realtime"]["contract_version"] == CONTRACT_VERSION == 2
