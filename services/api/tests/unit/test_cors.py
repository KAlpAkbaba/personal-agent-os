"""CORS is scoped to configured web origins, never wildcard (M0 review #3)."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


def test_allowed_web_origin_gets_cors_header() -> None:
    client = _client()
    resp = client.get(
        "/v1/system/health", headers={"Origin": "http://127.0.0.1:3100"}
    )
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3100"


def test_unlisted_origin_is_not_allowed() -> None:
    client = _client()
    resp = client.get(
        "/v1/system/health", headers={"Origin": "http://evil.example"}
    )
    # Starlette omits the allow-origin header for a disallowed origin, and it is
    # never the wildcard.
    assert resp.headers.get("access-control-allow-origin") not in (
        "http://evil.example",
        "*",
    )


def test_preflight_from_allowed_origin_succeeds() -> None:
    client = _client()
    resp = client.options(
        "/v1/tasks",
        headers={
            "Origin": "http://127.0.0.1:3100",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3100"
