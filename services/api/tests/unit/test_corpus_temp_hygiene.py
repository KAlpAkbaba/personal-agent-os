"""The corpus harness leaves nothing in the temp folder (card corpus-temp-dirs-leak, 2026-10-03).

Measured on the home PC 2026-10-02: 2 671 896 entries at the top of %TEMP%, 2 663 542 of them
the four ``mkdtemp`` prefixes of ``tests/voice_corpus/harness.py`` - four folders per corpus
case, never removed; listing the folder took minutes and the gate's corpus stretch ran ~40 min
instead of 11. Every test here points the temp folder at its OWN empty directory: it never
lists the real temp folder and never deletes anything it did not make itself.
"""

from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

import pytest

from tests.unit.test_owner_utterance_corpus import CASES
from tests.voice_corpus import harness as harness_module
from tests.voice_corpus.harness import run_case

#: The four prefixes as the leak was measured (and as the owner's one-time cleanup keys on).
PREFIXES = ("creative3d-render-", "genesis-skills-", "genesis-work-", "native-corpus-")

#: Twenty cases spread across the corpus (every category's head, not just the first family).
TWENTY = CASES[:: max(1, len(CASES) // 20)][:20]


@pytest.fixture
def empty_temp(tmp_path, monkeypatch) -> Path:
    """The temp folder for this test: an empty directory of its own. pytest makes nothing
    inside it (its own files live in ``tmp_path``'s siblings), so 'empty' means empty."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    for name in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(name, str(temp))
    return temp


def _left_by_prefix(temp: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in temp.iterdir():
        prefix = next((p for p in PREFIXES if entry.name.startswith(p)), entry.name)
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _assert_empty(temp: Path) -> None:
    left = _left_by_prefix(temp)
    assert left == {}, f"left behind in the temp folder ({sum(left.values())}): {left}"


def _run_twenty() -> list:
    return [run_case(case) for case in TWENTY]


def test_twenty_corpus_cases_leave_nothing_in_the_temp_folder(empty_temp):
    """Red before: eighty folders (four per case)."""
    assert len(TWENTY) == 20
    results = _run_twenty()
    assert [r.verdict for r in results] == ["correct"] * 20
    _assert_empty(empty_temp)


def test_a_caller_owned_harness_is_cleaned_by_close_and_by_with(empty_temp):
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    assert run_case(TWENTY[0], harness=h).verdict == "correct"
    # The harness is the caller's: run_case leaves its folders alone ...
    assert sorted(_left_by_prefix(empty_temp)) == sorted(PREFIXES)
    # ... and the caller's close() removes them.
    assert h.close() == []
    _assert_empty(empty_temp)

    with build_harness() as h2:
        assert run_case(TWENTY[1], harness=h2).verdict == "correct"
    _assert_empty(empty_temp)


def test_a_harness_nobody_closes_cleans_up_when_it_is_collected(empty_temp):
    """Most test files build one per test and drop it (test_appfactory_*, test_artifact_*,
    test_genesis_*, ... - every caller of build_harness): they are not edited."""
    from tests.voice_corpus.harness import build_harness

    h = build_harness()
    assert run_case(TWENTY[2], harness=h).verdict == "correct"
    del h
    gc.collect()
    _assert_empty(empty_temp)


def test_a_caller_supplied_root_takes_the_folders(empty_temp, tmp_path):
    from tests.voice_corpus.harness import build_harness

    root = tmp_path / "root"
    root.mkdir()
    h = build_harness(temp_root=root)
    assert sorted(_left_by_prefix(root)) == sorted(PREFIXES)
    _assert_empty(empty_temp)
    h.close()
    _assert_empty(root)


def test_the_stt_corpus_canonical_world_leaves_nothing(empty_temp):
    """tests/voice_corpus/stt_harness.py builds a harness, runs one case on it and drops it."""
    from tests.voice_corpus.stt_corpus import all_stt_cases
    from tests.voice_corpus.stt_harness import _uses_device_world, run_stt_case

    case = next(c for c in all_stt_cases() if not _uses_device_world(c))
    assert run_stt_case(case).world == "canonical"
    gc.collect()
    _assert_empty(empty_temp)


def test_a_case_that_raises_still_leaves_nothing(empty_temp, monkeypatch):
    # The harness is kept alive here on purpose: run_case must clean up itself, not leave
    # it to the collector (which would hide a missing cleanup on the error path).
    seen: list = []

    def boom(self, context):
        seen.append(self)
        raise RuntimeError("a case that breaks mid-way")

    monkeypatch.setattr(harness_module.Harness, "seed", boom)
    result = run_case(TWENTY[0])
    assert result.verdict == "error"
    assert "a case that breaks mid-way" in result.problems[0]
    assert len(seen) == 1
    _assert_empty(empty_temp)


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete an open file")
def test_a_folder_with_an_open_file_is_left_counted_and_removed_by_a_second_pass(
    empty_temp, monkeypatch
):
    seen: list = []
    held: list = []
    real_seed = harness_module.Harness.seed

    def seed_and_hold(self, context):
        seen.append(self)
        held.append(open(Path(self.native_root) / "held.txt", "w"))  # noqa: SIM115
        return real_seed(self, context)

    monkeypatch.setattr(harness_module.Harness, "seed", seed_and_hold)
    before = harness_module.TEMP_DIRS_LEFT_BEHIND["native-corpus-"]
    try:
        # The case does not fail because its folder could not go.
        assert run_case(TWENTY[0]).verdict == "correct"
        assert harness_module.TEMP_DIRS_LEFT_BEHIND["native-corpus-"] == before + 1
        assert _left_by_prefix(empty_temp) == {"native-corpus-": 1}
    finally:
        for handle in held:
            handle.close()
    # Released: the second pass removes it.
    assert seen[0].close() == []
    _assert_empty(empty_temp)


def test_two_harnesses_alive_at_once_never_remove_each_others_folders(empty_temp):
    from tests.voice_corpus.harness import build_harness

    first = build_harness()
    second = build_harness()
    second_dirs = [p for _prefix, p in second.temp_dirs.pending]
    assert len(second_dirs) == 4
    first.close()
    assert all(p.is_dir() for p in second_dirs), "closing one harness removed the other's"
    assert _left_by_prefix(empty_temp) == dict.fromkeys(PREFIXES, 1)
    (Path(second.native_root) / "still-usable.txt").write_text("ok", encoding="utf-8")
    assert run_case(TWENTY[3], harness=second).verdict == "correct"
    second.close()
    _assert_empty(empty_temp)


def test_running_the_twenty_twice_in_one_process_leaves_nothing_both_times(empty_temp):
    _run_twenty()
    _assert_empty(empty_temp)
    _run_twenty()
    _assert_empty(empty_temp)
