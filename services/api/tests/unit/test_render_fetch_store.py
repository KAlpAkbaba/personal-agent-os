"""Unit tests: ``app.artifacts.render_fetch_store`` in isolation (ADR-0085 addendum 5).

The HTTP round trip (``GET /v1/artifacts/renders/fetch/{token}``) is proven in
``tests/unit/test_artifact_routes.py``; this file proves the store's own contract
directly: single-use, TTL-bound, the token is never the dict key it hashes to, and the
handle's path shape matches what DEVICE_PROTOCOL.md §6k step 4 requires
(``/v1/artifacts/...``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.artifacts.render_fetch_store import (
    DEFAULT_TTL_S,
    TOKEN_BYTES,
    RenderFetchStore,
)
from app.identity.tokens import hash_token

ARTIFACT_ID = uuid.uuid4()


def _put(store: RenderFetchStore, **overrides):
    kwargs = {
        "artifact_id": ARTIFACT_ID,
        "fmt": "xlsx",
        "content_hash": "a" * 64,
    }
    kwargs.update(overrides)
    return store.put(**kwargs)


def test_ten_minute_default_ttl_matches_the_adr() -> None:
    # ADR-0085 addendum 5: "at most ten minutes".
    assert DEFAULT_TTL_S == 600


def test_token_is_at_least_32_random_bytes() -> None:
    # token_urlsafe(32) is 43 base64url characters carrying 32 bytes (256 bits) of
    # entropy — comfortably over the ">= 32 bytes" the task requires.
    assert TOKEN_BYTES >= 32
    store = RenderFetchStore()
    handle = _put(store)
    # base64url with no padding: 4 chars per 3 bytes, so 32 bytes -> 43 chars.
    assert len(handle.token) >= 43


def test_handle_path_is_under_v1_artifacts() -> None:
    store = RenderFetchStore()
    handle = _put(store)
    assert handle.path() == f"/v1/artifacts/renders/fetch/{handle.token}"
    assert handle.path().startswith("/v1/artifacts/")


def test_token_is_stored_hashed_never_in_the_clear() -> None:
    """The raw token must never be a key (or a value) inside the store's own internal
    state — a memory dump or a stray ``repr()`` must not hand out a redeemable token."""
    store = RenderFetchStore()
    handle = _put(store)
    assert handle.token not in store._entries  # noqa: SLF001 - test-only introspection
    assert hash_token(handle.token) in store._entries  # noqa: SLF001


def test_take_redeems_exactly_once() -> None:
    store = RenderFetchStore()
    handle = _put(store, fmt="pdf", content_hash="b" * 64)

    target = store.take(handle.token)
    assert target is not None
    assert target.artifact_id == ARTIFACT_ID
    assert target.fmt == "pdf"
    assert target.content_hash == "b" * 64

    # Spent. A token captured from a log after the fact is already useless.
    assert store.take(handle.token) is None


def test_an_unknown_token_and_a_spent_one_are_indistinguishable() -> None:
    store = RenderFetchStore()
    handle = _put(store)
    store.take(handle.token)
    assert store.take(handle.token) is None
    assert store.take("definitely-not-a-real-token") is None


def test_a_tampered_token_never_redeems() -> None:
    store = RenderFetchStore()
    handle = _put(store)
    tampered = handle.token[:-1] + ("a" if handle.token[-1] != "a" else "b")
    assert store.take(tampered) is None
    # The original is untouched by the tamper attempt on a copy.
    assert store.take(handle.token) is not None


def test_an_expired_token_is_refused() -> None:
    store = RenderFetchStore(ttl_s=60)
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    handle = _put(store, now=long_ago)
    assert store.take(handle.token) is None


def test_a_token_that_has_not_expired_yet_still_redeems() -> None:
    store = RenderFetchStore(ttl_s=600)
    moment = datetime.now(UTC)
    handle = _put(store, now=moment)
    assert store.take(handle.token, now=moment + timedelta(minutes=9)) is not None


def test_max_entries_evicts_the_oldest_rather_than_refuses() -> None:
    store = RenderFetchStore(max_entries=2)
    first = _put(store, now=datetime(2026, 1, 1, tzinfo=UTC))
    _put(store, now=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
    _put(store, now=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
    assert store.size() == 2
    # The oldest (first) entry was evicted to make room.
    assert store.take(first.token) is None


def test_size_and_clear() -> None:
    store = RenderFetchStore()
    assert store.size() == 0
    _put(store)
    _put(store, fmt="csv")
    assert store.size() == 2
    store.clear()
    assert store.size() == 0
