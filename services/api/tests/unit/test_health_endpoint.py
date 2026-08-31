"""Health endpoint shape tests with mocked dependency checks."""

from fastapi.testclient import TestClient

import app.health as health_module
from app import __version__
from app.config import Settings
from app.main import create_app


def make_client(monkeypatch, checks: dict[str, dict]) -> TestClient:
    async def fake_run_health_checks(settings: Settings) -> dict[str, dict]:
        return checks

    # create_app resolves run_health_checks via app.main's import; patch there.
    monkeypatch.setattr("app.main.run_health_checks", fake_run_health_checks)
    return TestClient(create_app(Settings(_env_file=None)))


ALL_OK = {
    "db": {"status": "ok", "latency_ms": 1.2},
    "redis": {"status": "ok", "latency_ms": 0.4},
    "object_store": {"status": "ok", "latency_ms": 3.1},
    "temporal": {"status": "ok", "latency_ms": 5.0},
}


def test_health_ok_shape(monkeypatch) -> None:
    client = make_client(monkeypatch, ALL_OK)
    response = client.get("/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert set(body["checks"].keys()) == {"db", "redis", "object_store", "temporal"}
    for check in body["checks"].values():
        assert check["status"] == "ok"
        assert isinstance(check["latency_ms"], int | float)


def test_health_degraded_still_200(monkeypatch) -> None:
    checks = dict(ALL_OK)
    checks["redis"] = {"status": "fail", "latency_ms": 2000.0, "error": "TimeoutError: timeout"}
    client = make_client(monkeypatch, checks)
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
    client = make_client(monkeypatch, checks)
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
    assert set(results) == {"db", "redis", "object_store", "temporal"}
    for result in results.values():
        assert result["status"] == "fail"
        assert "ConnectionError" in result["error"]
