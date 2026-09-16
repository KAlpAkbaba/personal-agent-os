"""The full quality gate a candidate must pass in its worktree (B35 req 600) and the CI
trigger that may follow it (req 601).

The reviewer proves the regression and the targeted tests; the gate proves the REST of the
package - the whole API test suite and its lint - in the candidate's own worktree, before
the candidate is committed. A red gate is one more failure the fix loop feeds back to
the model (req 603), bounded by the same attempt budget as every other failure.

CI is a push. The engine never pushes on its own account: :class:`GitPushTrigger` is
constructed disabled and the setting that enables it (``PAGENTOS_SELFDEV_CI_PUSH``) is
the owner's. Disabled, the trigger records that it did not push and why; the candidate
still exists on its local branch for the owner to push by hand.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

GATE_PASSED = "passed"
GATE_FAILED = "failed"
GATE_SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class GateResult:
    state: str
    detail: str = ""
    seconds: float = 0.0
    steps: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == GATE_PASSED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail[-2000:],
            "seconds": round(self.seconds, 1),
            "steps": list(self.steps),
        }


class GateRunner(Protocol):
    def run(self, worktree: Path) -> GateResult: ...


@dataclass(slots=True)
class NoGate:
    """No gate configured: the record says so; nothing is claimed."""

    reason: str = "no gate configured"

    def run(self, worktree: Path) -> GateResult:
        return GateResult(GATE_SKIPPED, self.reason)


@dataclass(slots=True)
class PackageGate:
    """The API package's own gate inside the worktree: the whole unit suite and the lint,
    in that order, each a step named in the result. ``pytest_args`` defaults to the
    deterministic unit tree; a candidate that broke a test far from its scope is red here
    and only here."""

    python: str
    package_root: str = "services/api"
    ruff: str | None = None
    timeout_s: float = 1500.0
    pytest_args: tuple[str, ...] = ("tests/unit", "-q", "-x", "-p", "no:cacheprovider")

    def _run(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUTF8"] = "1"
        # No bytecode in a worktree: a .pyc left beside a source restored within the same
        # second (same size) is loaded in its place, and the base would be judged by the
        # patched module's bytecode.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
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
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{type(exc).__name__}: {exc}"[-2000:]
        output = (done.stdout or "") + (done.stderr or "")
        return done.returncode == 0, output[-4000:]

    def run(self, worktree: Path) -> GateResult:
        import time

        started = time.monotonic()
        package = worktree / Path(self.package_root)
        steps: list[str] = []
        ok, output = self._run([self.python, "-m", "pytest", *self.pytest_args], package)
        steps.append("pytest")
        if not ok:
            return GateResult(
                GATE_FAILED, "pytest: " + output, time.monotonic() - started, tuple(steps)
            )
        if self.ruff:
            ok, output = self._run([self.ruff, "check", "app", "tests"], package)
            steps.append("ruff")
            if not ok:
                return GateResult(
                    GATE_FAILED, "ruff: " + output, time.monotonic() - started, tuple(steps)
                )
        return GateResult(GATE_PASSED, "", time.monotonic() - started, tuple(steps))


# ------------------------------------------------------------------ CI trigger


@dataclass(frozen=True, slots=True)
class TriggerResult:
    pushed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"pushed": self.pushed, "detail": self.detail[-500:]}


class CITrigger(Protocol):
    def push(self, branch: str) -> TriggerResult: ...


@dataclass(slots=True)
class GitPushTrigger:
    """``git push <remote> <branch>`` from the repository, only when ``enabled``. The
    default is the owner's default: off."""

    repo: Path
    remote: str = "origin"
    enabled: bool = False
    timeout_s: float = 120.0
    pushed: list[str] = field(default_factory=list)

    def push(self, branch: str) -> TriggerResult:
        if not self.enabled:
            return TriggerResult(False, "CI push is disabled (PAGENTOS_SELFDEV_CI_PUSH=false)")
        if not branch.startswith("selfdev/"):
            return TriggerResult(False, f"refused: only selfdev/* branches are pushed ({branch})")
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "push", self.remote, f"{branch}:{branch}"],
                cwd=self.repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return TriggerResult(False, f"{type(exc).__name__}: {exc}")
        if done.returncode != 0:
            return TriggerResult(False, (done.stderr or done.stdout or "")[-500:])
        self.pushed.append(branch)
        return TriggerResult(True, f"pushed {branch} to {self.remote}")
