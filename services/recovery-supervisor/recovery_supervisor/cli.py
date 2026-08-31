"""Recovery Supervisor CLI.

Verbs:

    activate  --version V [--source DIR]   stage (optional) + repoint current
    promote   [--version V]                mark active release last-known-good
    rollback                               repoint current to last-known-good
    status                                 print workspace status JSON
    run       ...                          health loop (bounded via --max-cycles)

Exit codes for ``run``: 0 healthy, 3 rolled back to last-known-good,
4 unhealthy but already at last-known-good (no rollback target). Workspace or
usage errors exit 2.
"""

from __future__ import annotations

import argparse
import json
import sys

from recovery_supervisor.procs import ManagedProcess
from recovery_supervisor.runner import EXIT_CODES, RunnerConfig, SupervisorRunner
from recovery_supervisor.workspace import ReleaseWorkspace, WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supervisor", description=__doc__)
    parser.add_argument("--workspace", required=True, help="release workspace root directory")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    verbs = parser.add_subparsers(dest="verb", required=True)

    activate = verbs.add_parser("activate", help="stage (optionally) and activate a release")
    activate.add_argument("--version", required=True)
    activate.add_argument("--source", help="directory to stage as the release before activating")

    promote = verbs.add_parser("promote", help="mark the active release as last-known-good")
    promote.add_argument("--version", help="must equal the active release when given")

    verbs.add_parser("rollback", help="repoint current to last-known-good")
    verbs.add_parser("status", help="print workspace status")

    run = verbs.add_parser("run", help="run the health loop for the supervised service")
    run.add_argument("--component", required=True)
    run.add_argument("--health-url", required=True)
    run.add_argument("--selftest-url", required=True)
    run.add_argument(
        "--command-arg",
        action="append",
        dest="command_args",
        default=[],
        help="supervised service command argv, one flag per token (repeatable)",
    )
    run.add_argument("--cwd", default=None, help="working directory for the supervised command")
    run.add_argument("--log", default=None, help="append supervised process output to this file")
    run.add_argument("--interval", type=float, default=1.0)
    run.add_argument("--failure-threshold", type=int, default=3)
    run.add_argument("--window", type=float, default=30.0)
    run.add_argument("--startup-timeout", type=float, default=20.0)
    run.add_argument("--check-timeout", type=float, default=5.0)
    run.add_argument("--max-cycles", type=int, default=None)
    run.add_argument("--promote-on-healthy", action="store_true")
    run.add_argument("--api-ingest-url", default=None)
    return parser


def _emit(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = ReleaseWorkspace(args.workspace)
    try:
        if args.verb == "activate":
            previous = workspace.activate(args.version, args.source)
            _emit(args, {"activated": args.version, "previous": previous})
            return 0
        if args.verb == "promote":
            previous = workspace.promote(args.version)
            _emit(
                args,
                {
                    "last_known_good": workspace.last_known_good,
                    "previous_last_known_good": previous,
                },
            )
            return 0
        if args.verb == "rollback":
            previous, lkg = workspace.rollback()
            _emit(args, {"rolled_back_from": previous, "rolled_back_to": lkg})
            return 0
        if args.verb == "status":
            _emit(args, workspace.status())
            return 0
        if args.verb == "run":
            process = None
            if args.command_args:
                process = ManagedProcess(args.command_args, cwd=args.cwd, log_path=args.log)
            config = RunnerConfig(
                component=args.component,
                health_url=args.health_url,
                selftest_url=args.selftest_url,
                interval_s=args.interval,
                failure_threshold=args.failure_threshold,
                window_s=args.window,
                startup_timeout_s=args.startup_timeout,
                check_timeout_s=args.check_timeout,
                max_cycles=args.max_cycles,
                promote_on_healthy=args.promote_on_healthy,
                api_ingest_url=args.api_ingest_url,
            )
            verdict = SupervisorRunner(workspace, config, process).run()
            _emit(args, verdict.to_dict())
            return EXIT_CODES.get(verdict.result, 4)
    except WorkspaceError as exc:
        _emit(args, {"error": exc.code, "message": exc.message})
        return 2
    parser.error(f"unknown verb {args.verb!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
