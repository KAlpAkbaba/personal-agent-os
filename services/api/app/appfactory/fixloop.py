"""Failed-test analysis and the bounded fix loop (B40 req 435-437).

``analyze_failures`` reads the device's ``project.test`` report - the runner's own
``not ok - name :: message`` lines - into named failures with the file each names, so the
owner hears WHICH test failed and why before any model is asked. ``FixLoop.run`` is the
self-development engine's shape applied to a generated application: diagnose, propose a
fix confined to the model slots, lint + scan + validate it, scaffold it as a NEW version
of the project on the device (the device never rewrites a project in place), run the
tests again; stop on green, on the attempt bound, on the same failure twice, or on a
refusal - and say which. Without a model the loop stops after the analysis and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

MAX_FIX_ATTEMPTS: Final = 3
#: The loop's own outcomes that are refusals the owner hears (app/errors/catalog.py).
ERROR_NO_MODEL = "no_model"
ERROR_SAME_FAILURE = "same_failure"
ERROR_EXHAUSTED = "exhausted"
_NOT_OK: Final = re.compile(r"^not ok - (?P<name>.+?)(?: :: (?P<message>.*))?$", re.MULTILINE)
_FILE_HINT: Final = re.compile(r"\b([\w./-]+\.js)\b")


@dataclass(slots=True)
class TestFailure:
    name: str
    message: str
    file: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "message": self.message, "file": self.file}


@dataclass(slots=True)
class FailureAnalysis:
    failed: int
    passed: int
    failures: list[TestFailure] = field(default_factory=list)
    crashed: bool = False
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "failed": self.failed,
            "passed": self.passed,
            "crashed": self.crashed,
            "summary": self.summary,
            "failures": [f.as_dict() for f in self.failures[:20]],
        }

    def fingerprint(self) -> str:
        return "|".join(sorted(f.name for f in self.failures)) or f"exit:{self.failed}"


def analyze_failures(report: dict[str, Any]) -> FailureAnalysis:
    """Req 435: the report -> what failed, by name, with the file a message names."""
    tail = str(report.get("report_tail") or report.get("output") or "")
    failed = int(report.get("failed") or 0)
    passed = int(report.get("passed") or 0)
    failures: list[TestFailure] = []
    for match in _NOT_OK.finditer(tail):
        name = match.group("name").strip()
        message = (match.group("message") or "").strip()
        hint = _FILE_HINT.search(message) or _FILE_HINT.search(name)
        failures.append(
            TestFailure(
                name=name[:200], message=message[:400], file=hint.group(1) if hint else None
            )
        )
    crashed = "suite crashed" in tail or (failed == 0 and int(report.get("exit_code") or 0) != 0)
    if failures:
        summary = f"{len(failures)} test başarısız: " + "; ".join(f.name for f in failures[:3])
    elif crashed:
        summary = "test koşusu çöktü (bir test bile raporlanmadan)"
    elif failed:
        summary = f"{failed} test başarısız (adları raporda yok)"
    else:
        summary = "başarısız test yok"
    return FailureAnalysis(
        failed=failed or len(failures),
        passed=passed,
        failures=failures,
        crashed=crashed,
        summary=summary,
    )


@dataclass(slots=True)
class FixAttempt:
    n: int
    diagnosis: dict[str, Any] | None = None
    edits: list[str] = field(default_factory=list)
    outcome: str = ""
    detail: str = ""
    passed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "diagnosis": self.diagnosis,
            "edits": list(self.edits),
            "outcome": self.outcome,
            "detail": self.detail,
            "passed": self.passed,
            "failed": self.failed,
        }


@dataclass(slots=True)
class FixRecord:
    attempts: list[FixAttempt] = field(default_factory=list)
    status: str = (
        "not_started"  # fixed | exhausted | same_failure | refused | no_model | not_needed
    )
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "attempts": [a.as_dict() for a in self.attempts],
        }


__all__ = [
    "MAX_FIX_ATTEMPTS",
    "FailureAnalysis",
    "FixAttempt",
    "FixRecord",
    "TestFailure",
    "analyze_failures",
]
