"""ObjectStore interface contract tests using the in-memory fake."""

import pytest

from app.object_store import InMemoryObjectStore, ObjectStore


def exercise_object_store_contract(store: ObjectStore) -> None:
    """Shared contract: also used by the MinIO integration test."""
    key = "test/contract/hello.txt"
    payload = "merhaba dünya".encode()

    assert store.exists(key) is False
    store.put(key, payload, content_type="text/plain; charset=utf-8")
    assert store.exists(key) is True
    assert store.get(key) == payload

    # Overwrite is allowed.
    store.put(key, b"v2")
    assert store.get(key) == b"v2"

    store.delete(key)
    assert store.exists(key) is False
    with pytest.raises(KeyError):
        store.get(key)

    # Deleting a missing key is a no-op.
    store.delete(key)


def test_in_memory_store_satisfies_contract() -> None:
    exercise_object_store_contract(InMemoryObjectStore())


def test_in_memory_store_is_objectstore_protocol() -> None:
    assert isinstance(InMemoryObjectStore(), ObjectStore)
