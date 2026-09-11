"""What CI says about an exact commit.

Read-only: the engine never pushes to make CI run. It reads the verdict on the BASE it
starts from (a base CI calls red is a base whose failures no candidate can be judged
against) and on the candidate when the owner's pipeline has built it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CI_SUCCESS = "success"
CI_FAILURE = "failure"
CI_PENDING = "pending"
CI_NONE = "none"


@dataclass(frozen=True, slots=True)
class CIStatus:
    state: str
    detail: str = ""


class CIReader(Protocol):
    def status(self, sha: str) -> CIStatus: ...


class NoCIReader:
    def status(self, sha: str) -> CIStatus:
        return CIStatus(CI_NONE, "no CI reader configured")


@dataclass(slots=True)
class GitHubCIReader:
    """``gh run list --commit <sha>``: the newest run for exactly that commit."""

    repo: Path
    gh: str = "gh"
    timeout_s: float = 60.0

    def status(self, sha: str) -> CIStatus:
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [
                    self.gh,
                    "run",
                    "list",
                    "--commit",
                    sha,
                    "--limit",
                    "1",
                    "--json",
                    "status,conclusion,url,databaseId",
                ],
                cwd=self.repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CIStatus(CI_NONE, f"gh unavailable: {type(exc).__name__}")
        if done.returncode != 0:
            return CIStatus(CI_NONE, done.stderr.strip()[:200])
        runs = json.loads(done.stdout or "[]")
        if not runs:
            return CIStatus(CI_NONE, f"no CI run for {sha[:12]}")
        run = runs[0]
        if run.get("status") != "completed":
            return CIStatus(CI_PENDING, str(run.get("url", "")))
        state = CI_SUCCESS if run.get("conclusion") == "success" else CI_FAILURE
        return CIStatus(state, f"{run.get('conclusion')} {run.get('url', '')}".strip())
