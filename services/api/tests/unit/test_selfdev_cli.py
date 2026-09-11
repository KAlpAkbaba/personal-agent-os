"""``python -m app.selfdev``: what reaches the engine from the command line.

``scripts/selfdev/run-selfdev.ps1`` is run with ``-File``, and there ``-Test a,b`` arrives as
ONE string; from the call operator it arrives as two. The first real invocation handed the
engine a single test path containing a comma, which pytest reports as not found - every
attempt would have failed its targeted tests. So the CLI takes both shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.selfdev.__main__ as cli
from app.selfdev.engine import RunRecord


class _Engine:
    seen: dict = {}

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def run(self, defect, *, base_sha: str, targeted_tests: list[str]) -> RunRecord:
        _Engine.seen = {"defect": defect, "base_sha": base_sha, "targeted": targeted_tests}
        return RunRecord("r1", {}, base_sha, "t", status="STOPPED_AT_POLICY_BOUNDARY")


@pytest.mark.parametrize(
    "tests",
    [
        ["--test", "services/api/tests/unit/a.py,services/api/tests/unit/b.py"],
        ["--test", "services/api/tests/unit/a.py", "--test", "services/api/tests/unit/b.py"],
        ["--test", " services/api/tests/unit/a.py , services/api/tests/unit/b.py ,"],
    ],
    ids=["one-comma-string", "repeated", "spaces-and-trailing-comma"],
)
def test_targeted_tests_arrive_as_separate_paths_whichever_way_they_were_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tests: list[str]
) -> None:
    defect = tmp_path / "defect.json"
    defect.write_text(
        json.dumps({"defect_id": "d", "title": "t", "evidence": "e", "scope": ["x.py"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "SelfDevEngine", _Engine)

    code = cli.main(
        ["--defect", str(defect), "--base", "a" * 40, "--repo", str(tmp_path), *tests]
    )

    assert code == 0
    assert _Engine.seen["targeted"] == [
        "services/api/tests/unit/a.py",
        "services/api/tests/unit/b.py",
    ]
