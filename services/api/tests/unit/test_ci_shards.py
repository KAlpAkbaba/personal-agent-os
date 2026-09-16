"""The CI shard split (tests/conftest.py): every test runs in exactly one shard, and the
split the workflow names is the split the hook understands."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import SHARD_ENV, parse_shard, shard_keys, shard_of

REPO = Path(__file__).resolve().parents[4]
CI = REPO / ".github" / "workflows" / "ci.yml"


def test_every_node_id_lands_in_exactly_one_shard() -> None:
    ids = [f"tests/unit/test_x.py::test_{n}[case-{n % 7}]" for n in range(3000)]
    for count in (1, 2, 3, 4):
        buckets = {i: [] for i in range(1, count + 1)}
        for nodeid in ids:
            buckets[shard_of(nodeid, count)].append(nodeid)
        assert sorted(sum(buckets.values(), [])) == sorted(ids)
        # Balanced enough that no shard carries the suite alone.
        assert max(len(b) for b in buckets.values()) < 1.2 * len(ids) / count


def test_the_split_is_stable_across_processes() -> None:
    # crc32, not the salted builtin hash: a runner must not pick a different split.
    assert shard_of("tests/unit/test_a.py::test_b", 3) == shard_of(
        "tests/unit/test_a.py::test_b", 3
    )
    assert [shard_of(f"n{i}", 3) for i in range(5)] == [
        (__import__("zlib").crc32(f"n{i}".encode()) % 3) + 1 for i in range(5)
    ]


def test_a_parameter_id_that_changes_per_process_keeps_its_shard() -> None:
    """2026-09-17: route-guard tables put a fresh uuid4 in their parameter ids, so the three
    shard processes saw different node ids - measured, one test ran in no shard and several
    ran twice. The key must not depend on the parameter text."""
    import uuid

    def collection() -> list[str]:
        return [
            f"tests/unit/test_routes.py::test_guarded[post-/v1/x/{uuid.uuid4()}/{verb}]"
            for verb in ("run", "stop", "test", "delete", "authorize")
        ] + ["tests/unit/test_routes.py::test_plain"]

    first, second = shard_keys(collection()), shard_keys(collection())
    assert first == second
    assert len(set(first)) == len(first)
    for count in (2, 3):
        assert [shard_of(k, count) for k in first] == [shard_of(k, count) for k in second]


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "x/3", "3"])
def test_a_malformed_shard_is_refused_not_ignored(value: str) -> None:
    with pytest.raises(ValueError):
        parse_shard(value)


def test_unset_means_everything() -> None:
    assert parse_shard(None) is None
    assert parse_shard("") is None


def test_the_workflow_runs_every_shard_it_declares() -> None:
    text = CI.read_text(encoding="utf-8")
    job = text.split("  api-lint-unit:", 1)[1].split("\n  api-integration:", 1)[0]
    shards = re.findall(r"shard:\s*\[([^\]]*)\]", job)
    assert shards, "the unit job declares no shard matrix"
    values = [int(v) for v in shards[0].split(",")]
    count = re.search(r"shard_count:\s*\[(\d+)\]", job) or re.search(r"/(\d+)\"?\s*$", job, re.M)
    assert count, "the unit job does not name the shard count"
    total = int(count.group(1))
    assert sorted(values) == list(range(1, total + 1))
    assert f"{SHARD_ENV}: ${{{{ matrix.shard }}}}/${{{{ matrix.shard_count }}}}" in job
