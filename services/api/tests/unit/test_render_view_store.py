"""Unit tests: ``app.artifacts.render_view_store`` in isolation (ADR-0210).

The HTTP round trip and the open that mints these tokens are proven through the real
application object in ``test_research_open_in_owner_chrome.py``; this file proves the store's
own contract directly, and where it differs from ``render_fetch_store`` (the single-use token a
device's ``file.fetch`` redeems) it says so in its names.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.artifacts.render_view_store import (
    ALLOWED_FORMAT,
    DEFAULT_TTL_S,
    MAX_READS,
    TOKEN_BYTES,
    TOKEN_PARAM,
    VIEW_PATH,
    FormatNotViewableError,
    RenderViewStore,
)
from app.identity.tokens import hash_token

ARTIFACT_ID = uuid.uuid4()


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _put(store: RenderViewStore, **overrides):
    kwargs = {"artifact_id": ARTIFACT_ID, "fmt": "html", "content_hash": "a" * 64}
    kwargs.update(overrides)
    return store.put(**kwargs)


def test_the_bounds_are_the_ones_the_adr_states() -> None:
    assert DEFAULT_TTL_S == 900
    assert MAX_READS == 6
    assert ALLOWED_FORMAT == "html"


def test_token_is_at_least_32_random_bytes() -> None:
    assert TOKEN_BYTES >= 32
    assert len(_put(RenderViewStore()).token) >= 43


def test_two_tokens_for_the_same_render_are_different() -> None:
    store = RenderViewStore()
    assert _put(store).token != _put(store).token


def test_the_handle_path_carries_the_secret_in_the_query_and_not_the_path() -> None:
    handle = _put(RenderViewStore())
    assert handle.path() == f"{VIEW_PATH}?{TOKEN_PARAM}={handle.token}"
    assert handle.token not in VIEW_PATH
    assert handle.path().startswith("/v1/artifacts/")


def test_token_is_stored_hashed_never_in_the_clear() -> None:
    store = RenderViewStore()
    handle = _put(store)
    assert handle.token not in store._entries  # noqa: SLF001 - test-only introspection
    assert hash_token(handle.token) in store._entries  # noqa: SLF001
    assert handle.token not in repr(store._entries)  # noqa: SLF001


def test_read_resolves_the_one_render_the_token_names() -> None:
    store = RenderViewStore()
    other = uuid.uuid4()
    handle = _put(store, content_hash="b" * 64)
    _put(store, artifact_id=other, content_hash="c" * 64)
    target = store.read(handle.token)
    assert target is not None
    assert (target.artifact_id, target.fmt, target.content_hash) == (
        ARTIFACT_ID,
        "html",
        "b" * 64,
    )


def test_a_token_is_redeemable_exactly_max_reads_times() -> None:
    """The difference from the single-use fetch token: a browser tab re-requests."""
    store = RenderViewStore()
    handle = _put(store)
    reads = [store.read(handle.token) for _ in range(MAX_READS + 2)]
    assert [r is not None for r in reads] == [True] * MAX_READS + [False, False]


def test_an_exhausted_token_leaves_the_store() -> None:
    store = RenderViewStore()
    handle = _put(store)
    for _ in range(MAX_READS):
        store.read(handle.token)
    assert store.size() == 0


def test_a_token_expires_at_its_ttl_even_with_reads_left() -> None:
    clock = _Clock()
    store = RenderViewStore(clock=clock)
    handle = _put(store)
    clock.advance(DEFAULT_TTL_S - 1)
    assert store.read(handle.token) is not None
    clock.advance(2)
    assert store.read(handle.token) is None
    assert store.size() == 0


def test_a_token_is_live_at_the_last_second_and_dead_at_the_ttl() -> None:
    clock = _Clock()
    store = RenderViewStore(clock=clock)
    handle = _put(store)
    clock.advance(DEFAULT_TTL_S)
    assert store.read(handle.token) is None


def test_an_unknown_an_expired_and_a_spent_token_are_indistinguishable() -> None:
    clock = _Clock()
    store = RenderViewStore(clock=clock, max_reads=1)
    spent = _put(store)
    assert store.read(spent.token) is not None
    expired = _put(store)
    clock.advance(DEFAULT_TTL_S + 1)
    assert store.read("A" * 43) is None
    assert store.read(spent.token) is None
    assert store.read(expired.token) is None


@pytest.mark.parametrize("fmt", ["pdf", "docx", "md", "txt", "xlsx", "csv", "json", "pptx", ""])
def test_a_render_a_browser_would_not_display_cannot_be_minted(fmt: str) -> None:
    store = RenderViewStore()
    with pytest.raises(FormatNotViewableError):
        _put(store, fmt=fmt)
    assert store.size() == 0


def test_the_store_is_bounded_and_drops_the_oldest() -> None:
    clock = _Clock()
    store = RenderViewStore(clock=clock, max_entries=2)
    first = _put(store)
    clock.advance(1)
    second = _put(store)
    clock.advance(1)
    third = _put(store)
    assert store.size() == 2
    assert store.read(first.token) is None
    assert store.read(second.token) is not None
    assert store.read(third.token) is not None


def test_a_store_that_allows_no_read_cannot_be_built() -> None:
    with pytest.raises(ValueError):
        RenderViewStore(max_reads=0)


def test_the_store_has_no_way_to_list_what_it_holds() -> None:
    public = {name for name in dir(RenderViewStore) if not name.startswith("_")}
    assert public == {"put", "read", "size", "clear"}


def test_the_route_is_the_one_the_office_pcs_browser_worker_is_allowed_to_open() -> None:
    # The device admits exactly ONE route of the broker origin (owner decision 2026-09-29, the
    # narrow SSRF exception in services/browser/browser_agent/destination.py). services/api
    # cannot import services/browser, so the mirror is read from its source: if either side moves
    # this route the drift fails HERE, instead of on the owner's desk as a spoken refusal.
    destination = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "browser"
        / "browser_agent"
        / "destination.py"
    )
    if not destination.is_file():  # a services/api-only checkout
        pytest.skip("services/browser is not part of this checkout")
    match = re.search(r'^TRUSTED_VIEW_PATH = "([^"]+)"$', destination.read_text("utf-8"), re.M)
    assert match is not None, "TRUSTED_VIEW_PATH is not in the canonical form"
    assert match.group(1) == VIEW_PATH
    # ...and the URL the open builds (origin + handle.path()) has exactly that path.
    minted = urlsplit("http://100.90.158.26:8001" + _put(RenderViewStore()).path())
    assert minted.path == match.group(1)
