"""Running a candidate's tests and lint inside its own worktree, bounded in time.

Python tests run with the worktree's package on ``PYTHONPATH`` ahead of anything installed:
a virtualenv's editable install points at the main checkout, and a test run that imported
the main checkout's code would "pass" a candidate it never loaded.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    output: str
    seconds: float
    timed_out: bool = False


@dataclass(slots=True)
class CandidateRunner:
    python: str
    package_root: str = "services/api"
    timeout_s: float = 600.0
    ruff: str | None = None

    def _run(self, args: list[str], cwd: Path, env: dict[str, str]) -> CommandResult:
        started = time.monotonic()
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                args,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
            return CommandResult(
                False,
                f"timed out after {self.timeout_s:.0f}s\n{output}"[-4000:],
                time.monotonic() - started,
                timed_out=True,
            )
        output = (done.stdout + done.stderr)[-6000:]
        return CommandResult(done.returncode == 0, output, time.monotonic() - started)

    def _package(self, worktree: Path) -> Path:
        return worktree / self.package_root

    def pytest(self, worktree: Path, tests: list[str]) -> CommandResult:
        """``tests`` are repository-relative paths (or node ids) under the package root."""
        package = self._package(worktree)
        prefix = self.package_root.rstrip("/") + "/"
        local = [t[len(prefix) :] if t.startswith(prefix) else t for t in tests]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(package) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUTF8"] = "1"
        return self._run(
            [
                self.python,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:warnings",
                *local,
            ],
            package,
            env,
        )

    def _ruff(self, worktree: Path, paths: list[str], *flags: str) -> CommandResult | None:
        python_paths = [p for p in paths if p.endswith(".py")]
        if not python_paths or self.ruff is None:
            return None
        prefix = self.package_root.rstrip("/") + "/"
        local = [p[len(prefix) :] if p.startswith(prefix) else p for p in python_paths]
        return self._run(
            [self.ruff, "check", *flags, *local], self._package(worktree), dict(os.environ)
        )

    def fix(self, worktree: Path, paths: list[str]) -> CommandResult:
        """Ruff's SAFE fixes only - never ``--unsafe-fixes`` - on the given Python files. An
        unsorted import block is not worth an attempt (the fourth real run spent one on it,
        and the retry lost the fix it was carrying)."""
        done = self._ruff(worktree, paths, "--fix", "--exit-zero")
        return done or CommandResult(True, "nothing to fix", 0.0)

    def lint(self, worktree: Path, paths: list[str]) -> CommandResult:
        return self._ruff(worktree, paths) or CommandResult(True, "nothing to lint", 0.0)
