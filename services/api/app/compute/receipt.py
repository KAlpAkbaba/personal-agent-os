"""The compute.run receipt: what one sandbox run did, and its ledger-ready plain dict."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ComputeOutcome(StrEnum):
    #: The program ran to its own end (exit_code may still be non-zero).
    OK = "ok"
    #: The runner killed it at the deadline.
    TIMEOUT = "timeout"
    #: SIGKILL (137) the runner did not send: the memory ceiling.
    OOM = "oom"
    #: The policy did not allow the request; no process was started.
    REFUSED = "refused"
    #: No docker CLI, no daemon or no image (docker's own exit 125).
    DOCKER_UNAVAILABLE = "docker_unavailable"


@dataclass(frozen=True, slots=True)
class ComputeReceipt:
    outcome: ComputeOutcome
    image: str
    limits: dict[str, Any]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = 0
    killed: bool = False
    container: str | None = None
    #: Why it was refused / unavailable; None for a run.
    detail: str | None = None

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated

    def to_detail_json(self) -> dict[str, Any]:
        """A plain JSON-able dict, the shape of the ledger's detail_json for compute.run."""
        return {
            "outcome": self.outcome.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "killed": self.killed,
            "image": self.image,
            "limits": dict(self.limits),
            "container": self.container,
            "detail": self.detail,
        }
