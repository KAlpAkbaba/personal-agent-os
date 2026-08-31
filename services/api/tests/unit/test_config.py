"""Config loading unit tests (no external services)."""

from app.config import Settings


def test_defaults_match_dev_compose_ports() -> None:
    s = Settings(_env_file=None)
    assert s.database_url.endswith("@127.0.0.1:15432/pagentos")
    assert s.redis_url == "redis://127.0.0.1:16379/0"
    assert s.s3_endpoint_url == "http://127.0.0.1:19000"
    assert s.temporal_address == "127.0.0.1:17233"
    assert s.temporal_task_queue == "pagentos-core"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_REDIS_URL", "redis://127.0.0.1:26379/2")
    monkeypatch.setenv("PAGENTOS_S3_BUCKET", "other-bucket")
    s = Settings(_env_file=None)
    assert s.redis_url == "redis://127.0.0.1:26379/2"
    assert s.s3_bucket == "other-bucket"


def test_health_timeout_is_bounded_default() -> None:
    s = Settings(_env_file=None)
    assert 0 < s.health_check_timeout_s <= 10
