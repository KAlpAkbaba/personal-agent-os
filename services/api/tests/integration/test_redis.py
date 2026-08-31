"""Redis connectivity: SET/GET/DEL round-trip (M0 gate)."""

import pytest
import redis

from app.config import Settings

pytestmark = pytest.mark.integration


def test_redis_set_get_del(settings: Settings) -> None:
    client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3)
    key = "pagentos:test:m0"
    try:
        assert client.ping() is True
        client.set(key, "merhaba", ex=60)
        assert client.get(key) == b"merhaba"
        assert client.delete(key) == 1
        assert client.get(key) is None
    finally:
        client.close()
