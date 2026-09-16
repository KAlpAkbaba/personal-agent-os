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
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "worker":
        return worker_main(argv[1:])
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


def build_engine(
    repo: Path,
    *,
    worktrees: Path | None,
    runs: Path | None,
    model: str,
    budget: Budget,
    ci_push: bool = False,
    shadow: bool = True,
    full_gate: bool = True,
) -> SelfDevEngine:
    """The engine as the CLI and the worker build it: the real workspace, runner, model,
    CI reader - and B35's gate, shadow and (disabled by default) CI push trigger."""
    from app.selfdev.gate import GitPushTrigger, NoGate, PackageGate
    from app.selfdev.shadow import ProcessShadowRunner, ScriptedShadowRunner

    worktrees = worktrees or repo.parent / f"{repo.name}.selfdev-worktrees"
    runs = runs or repo.parent / f"{repo.name}.selfdev-runs"
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff.exe"))
    ruff_path = ruff if Path(ruff).exists() else None
    return SelfDevEngine(
        workspace=GitWorkspace(repo, worktrees),
        runner=CandidateRunner(python=sys.executable, ruff=ruff_path),
        model=AnthropicEngineeringModel(model=model),
        ci=GitHubCIReader(repo),
        runs_dir=runs,
        budget=budget,
        gate=PackageGate(python=sys.executable, ruff=ruff_path) if full_gate else NoGate(),
        shadow=(
            ProcessShadowRunner(
                command=(sys.executable, "-m", "uvicorn", "app.main:app", "--port", "{port}")
            )
            if shadow
            else ScriptedShadowRunner()
        ),
        ci_trigger=GitPushTrigger(repo, enabled=ci_push),
    )


def worker_main(argv: list[str]) -> int:
    """``python -m app.selfdev worker --api URL --repo PATH``: the dev-machine worker
    (B35 req 615). The owner's bearer token comes from PAGENTOS_OWNER_TOKEN and is never
    printed; CI push stays off unless ``--ci-push`` is given (req 601)."""
    import os
    import subprocess

    from app.selfdev.worker import HttpQueueClient, run_forever

    parser = argparse.ArgumentParser(prog="python -m app.selfdev worker")
    parser.add_argument("--api", required=True, help="the Cloud Core base URL")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--worktrees", type=Path, default=None)
    parser.add_argument("--runs", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--worker-id", default=f"worker-{os.environ.get('COMPUTERNAME', 'dev')}")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--ci-push", action="store_true")
    parser.add_argument("--no-shadow", action="store_true")
    parser.add_argument("--no-full-gate", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=1800.0)
    parser.add_argument("--max-tokens", type=int, default=300_000)
    args = parser.parse_args(argv)
    token = os.environ.get("PAGENTOS_OWNER_TOKEN", "")
    if not token:
        print("PAGENTOS_OWNER_TOKEN is not set; the worker cannot claim work", file=sys.stderr)
        return 2
    engine = build_engine(
        args.repo,
        worktrees=args.worktrees,
        runs=args.runs,
        model=args.model,
        budget=Budget(args.max_attempts, args.max_seconds, args.max_tokens),
        ci_push=args.ci_push,
        shadow=not args.no_shadow,
        full_gate=not args.no_full_gate,
    )

    def base_sha() -> str:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            cwd=args.repo,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        return done.stdout.strip()

    client = HttpQueueClient(args.api, token)
    runs = run_forever(
        client,
        engine,
        worker_id=args.worker_id,
        base_sha=base_sha,
        poll_s=args.poll_seconds,
        max_runs=args.max_runs,
    )
    return 0 if runs or args.max_runs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
