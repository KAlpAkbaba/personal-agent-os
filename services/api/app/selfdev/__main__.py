"""``python -m app.selfdev --defect defect.json --base <sha> [--test <path>]...``

Runs one self-development attempt on the repository this package lives in and prints the
run record's path and verdict. The defect file is JSON: ``{"defect_id", "title", "evidence",
"scope": [paths], "failing_test"?}``. The Anthropic key comes from ``ANTHROPIC_API_KEY``
(``scripts/selfdev/run-selfdev.ps1`` sets it from the DPAPI store); ``--model scripted`` is
refused here - scripted runs exist for tests, not for claims.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from app.selfdev.anthropic_model import DEFAULT_MODEL, AnthropicEngineeringModel
from app.selfdev.budget import Budget
from app.selfdev.ci import GitHubCIReader
from app.selfdev.engine import SelfDevEngine
from app.selfdev.model import DefectSpec
from app.selfdev.runner import CandidateRunner
from app.selfdev.workspace import GitWorkspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.selfdev")
    parser.add_argument("--defect", required=True, type=Path)
    parser.add_argument("--base", required=True, help="the exact base commit SHA")
    parser.add_argument(
        "--test", action="append", default=[], help="targeted test path(s): repeated or a,b"
    )
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--worktrees", type=Path, default=None)
    parser.add_argument("--runs", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=1800.0)
    parser.add_argument("--max-tokens", type=int, default=300_000)
    args = parser.parse_args(argv)

    spec = json.loads(args.defect.read_text(encoding="utf-8"))
    defect = DefectSpec(
        defect_id=str(spec["defect_id"]),
        title=str(spec["title"]),
        evidence=str(spec["evidence"]),
        scope=tuple(str(s) for s in spec["scope"]),
        failing_test=spec.get("failing_test"),
    )
    repo: Path = args.repo
    worktrees = args.worktrees or repo.parent / f"{repo.name}.selfdev-worktrees"
    runs = args.runs or repo.parent / f"{repo.name}.selfdev-runs"
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff.exe"))
    engine = SelfDevEngine(
        workspace=GitWorkspace(repo, worktrees),
        runner=CandidateRunner(python=sys.executable, ruff=ruff if Path(ruff).exists() else None),
        model=AnthropicEngineeringModel(model=args.model),
        ci=GitHubCIReader(repo),
        runs_dir=runs,
        budget=Budget(args.max_attempts, args.max_seconds, args.max_tokens),
    )
    # run-selfdev.ps1 run with -File passes "-Test a,b" as ONE string; take both shapes.
    targeted = [path.strip() for value in args.test for path in value.split(",") if path.strip()]
    record = engine.run(defect, base_sha=args.base, targeted_tests=targeted)
    print(
        json.dumps(
            {
                "run_id": record.run_id,
                "status": record.status,
                "reason": record.reason,
                "record": str(runs / record.run_id / "record.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if record.status == "STOPPED_AT_POLICY_BOUNDARY" else 1


if __name__ == "__main__":
    sys.exit(main())
