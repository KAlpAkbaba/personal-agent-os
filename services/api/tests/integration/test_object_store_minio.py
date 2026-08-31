"""MinIO-backed ObjectStore write/read/delete via the provider interface (M0 gate)."""

import pytest

from app.config import Settings
from app.object_store import S3ObjectStore
from tests.unit.test_object_store import exercise_object_store_contract

pytestmark = pytest.mark.integration


def test_minio_store_satisfies_contract(settings: Settings) -> None:
    store = S3ObjectStore.from_settings(settings)
    store.ensure_bucket()
    exercise_object_store_contract(store)


def test_minio_health_check(settings: Settings) -> None:
    store = S3ObjectStore.from_settings(settings)
    store.ensure_bucket()
    store.health_check()
