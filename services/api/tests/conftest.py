"""Shared pytest fixtures."""

import os
import re
import zlib
from collections.abc import Callable

import pytest

from app.config import Settings
from tests.identity_support import authenticate

#: CI splits the unit suite across parallel jobs (2026-09-17: one job grew past its 25-minute
#: bound). ``PAGENTOS_TEST_SHARD=i/n`` keeps the items whose node id hashes to shard ``i`` of
#: ``n`` (1-based); unset, everything runs. Per ITEM, not per file: the owner corpus is one
#: file holding most of the suite's time.
SHARD_ENV = "PAGENTOS_TEST_SHARD"


def parse_shard(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    index_text, _, count_text = value.partition("/")
    index, count = int(index_text), int(count_text)
    if count < 1 or not 1 <= index <= count:
        raise ValueError(f"{SHARD_ENV}={value!r} is not i/n with 1 <= i <= n")
    return index, count


def shard_of(nodeid: str, count: int) -> int:
    """The 1-based shard a test belongs to: stable across machines and runs (crc32, never
    Python's salted ``hash``)."""
    return zlib.crc32(nodeid.encode("utf-8")) % count + 1


def shard_keys(nodeids: list[str]) -> list[str]:
    """A process-independent key per test: the function's node id without its parameter
    part, plus the item's position among that function's parameter sets. Some parameter
    ids carry a fresh uuid4 per collection (route-guard tables), so the raw node id differs
    between the shard processes and a test could run in two shards or in none."""
    seen: dict[str, int] = {}
    keys = []
    for nodeid in nodeids:
        base = nodeid.split("[", 1)[0]
        position = seen.get(base, 0)
        seen[base] = position + 1
        keys.append(f"{base}#{position}")
    return keys


#: The gate runs the unit suite under pytest-xdist (team/plans/gate-unit-parallel-adr.md), and
#: xdist refuses a run whose workers collected different test ids. Two things made the ids
#: differ between processes (measured 2026-10-06 by collecting twice): route-guard tables put a
#: fresh uuid4 into their paths, and parameter tables built from sets come out in the process's
#: string-hash order. A uuid in a parameter's id is written as ``<uuid>``, and the workers xdist
#: starts share one hash seed.
_UUID_IN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
XDIST_HASH_SEED = "20261006"


def pytest_make_parametrize_id(config, val, argname):
    if isinstance(val, str) and _UUID_IN_ID.search(val):
        return _UUID_IN_ID.sub("<uuid>", val)
    return None


def pytest_configure(config):
    # In the xdist controller, before it starts the workers (they inherit its environment); a
    # seed the caller set already is kept. A serial run is left as it was.
    if os.environ.get("PYTEST_XDIST_WORKER") or not getattr(config.option, "numprocesses", None):
        return
    os.environ.setdefault("PYTHONHASHSEED", XDIST_HASH_SEED)


#: FastAPI keeps three module-level lru_caches (4096 entries each) keyed by endpoint callables.
#: A create_app() endpoint is a closure over its app, so every test's app stayed alive: one
#: serial unit run grew to 21 GB (measured 2026-10-06; 25 of 25 apps alive after one file).
#: Cleared after each test; a cache missing in another FastAPI version is skipped.
_FASTAPI_CALLABLE_CACHES = (
    "_is_gen_callable_cached",
    "_is_async_gen_callable_cached",
    "_is_coroutine_callable_cached",
)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    from fastapi.dependencies import models

    for name in _FASTAPI_CALLABLE_CACHES:
        cached = getattr(models, name, None)
        if cached is not None and hasattr(cached, "cache_clear"):
            cached.cache_clear()


#: Tests that cannot run beside another, marked ``serial_tail``: the gate runs the unit suite
#: under xdist with ``-m "not serial_tail"`` and then these serially. A serial run runs them in
#: place, as before. Each id carries why (found in the gate's parallel run of 2026-10-06).
SERIAL_TAIL = {
    "tests/unit/test_contract_falsification.py::test_hiding_a_contract_actually_fails_its_guard": (
        "its mutation proof deletes packages/protocol/realtime-session-contract.json from the "
        "shared tree; test_compose_web_shell and test_every_shared_artifact_is_registered read "
        "that file and failed beside it"
    ),
    "tests/unit/test_team_guards_runner.py::"
    "test_red_hung_and_missing_are_three_wordings_and_the_exit_codes_are_three": (
        "a PowerShell guard must finish inside -HangSeconds 2; beside eight busy workers its "
        "start alone took longer and 'red' came back 'hung'"
    ),
}


def pytest_itemcollected(item):
    if item.nodeid in SERIAL_TAIL:
        item.add_marker(pytest.mark.serial_tail)


def pytest_collection_modifyitems(config, items):
    shard = parse_shard(os.environ.get(SHARD_ENV))
    if shard is None:
        return
    index, count = shard
    kept, dropped = [], []
    for item, key in zip(items, shard_keys([item.nodeid for item in items]), strict=True):
        (kept if shard_of(key, count) == index else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.fixture()
def settings() -> Settings:
    """Settings built from environment/dev-defaults (compose loopback ports)."""
    return Settings()


@pytest.fixture(autouse=True)
def _ambient_holdoffs_already_restored(monkeypatch):
    """``app.ambient.service`` rebuilds holdoffs from the ledger once per PROCESS, on the
    first tick. Left process-wide, the first test to tick in a run inherits that restore:
    measured 2026-09-17 when CI split the suite, where
    test_a_recent_input_refusal_starts_the_input_holdoff ran first in its shard, restored
    the owner-command holdoff its own policy change had just written, and failed - it had
    only ever passed because an earlier test used the restore up. Every test starts as a
    process that has already restored; a test about the restore says so with
    ``reset_holdoff_restore()``."""
    from app.ambient import service as ambient_service

    monkeypatch.setattr(ambient_service, "_holdoffs_restored", True)


@pytest.fixture(autouse=True)
def _reset_browser_gateway_session_registry():
    """``DeviceBrowserGateway``'s known-open session registry (spec §5a) is
    process-wide by design (M13_RESEARCH_SPEC.md §5a) so it survives across
    the many gateway instances one research job constructs. Tests reuse the
    same device/task ids across cases, so without a reset the SECOND test to
    touch a given (device_id, session_id) would see it as "already open" and
    skip dispatching ``browser.session_open`` — reset it before (and after)
    every test so each test starts from a clean registry."""
    from app.research.browser_gateway import reset_known_open_sessions

    reset_known_open_sessions()
    yield
    reset_known_open_sessions()


@pytest.fixture(autouse=True)
def _reset_operator_service_registry():
    """``app.operator.service``'s module-wide registry (mirrors
    ``app.devices.commands.register_broker_runtime``) must not leak a "task running"
    state across tests that never touch it (docs/M19_DIGITAL_OPERATOR_SPEC.md §4): a test
    file with its own hand-rolled realtime runtime and no operator of its own must see
    ``operator_running=False`` at the router, never whatever a PRECEDING test's operator
    happened to be doing. A fixture that wants the real thing (the corpus harness,
    ``test_operator_wiring.py``) registers its own after this reset runs."""
    from app.operator.service import register_operator_service

    register_operator_service(None)
    yield
    register_operator_service(None)


@pytest.fixture(autouse=True)
def _reset_route_telemetry():
    """B26 req 749: the router's telemetry ring is module-wide for the same reason the
    operator registry is — `record_client_events` is a function, not a service object. A
    ring that survived a test would let one test's resolutions pair with the next test's
    "dur" and report a misroute nobody caused."""
    from app.voice.route_telemetry import reset_telemetry

    reset_telemetry()
    yield
    reset_telemetry()


@pytest.fixture(autouse=True)
def _correlation_ids_do_not_leak_between_tests():
    """``app.logging``'s ``task_id_var`` / ``trace_id_var`` are context variables that product
    code SETS and never resets (an activity runs in its own task, so nothing leaks there). A
    test that calls such a function directly, in the test's own context, leaves the id behind
    for every later test of the process: measured 2026-10-02, when the full gate was red on
    ``test_task_id_defaults_to_none_in_logs`` - green alone, red after
    ``test_execution_call_site_research.py``, a file merged that day. Nobody's own run of
    their own area could have seen it. Every test starts and ends with both unset."""
    from app.logging import task_id_var, trace_id_var

    tokens = (task_id_var.set(None), trace_id_var.set(None))
    yield
    task_id_var.reset(tokens[0])
    trace_id_var.reset(tokens[1])


@pytest.fixture()
def owner_auth() -> Callable[..., object]:
    """M9: `owner_auth(app, client)` bootstraps identity and authenticates.

    Every endpoint except health now requires an owner bearer session, so this
    is the one shared way a test becomes the owner. It issues a *real* session
    through the *real* service — nothing about the authentication path is
    stubbed or overridden — so a test passing is evidence that the endpoint is
    protected and that a valid session gets through, not that auth was skipped.
    """
    return authenticate
