"""CodingBackend seam (EVOLUTION_ENGINE_SPEC §5) + independent reviewer (§6).

Two backends implement the same interface:

- ``DeterministicCodingBackend`` — for the CONTROLLED injected-bug class
  (``wrong_error_mapping``): it derives the fix mechanically from the incident
  evidence (the marker whose mapping produced the observed wrong class is
  rewritten to the expected class) and generates a standalone regression-test
  script. Fully offline/deterministic; this is what the acceptance gate runs.
- ``ClaudeCodingBackend`` — the Claude Agent SDK/CLI seam. INERT without
  configuration: every method raises a typed ``backend_not_configured`` error
  before any I/O. Never called in tests. Domain code never sees
  Anthropic-specific response formats — only the interface dataclasses.

Builder/reviewer separation: ``DeterministicReviewer`` is a SEPARATE
deterministic validator (regression test in both directions + static checks in
an isolated subprocess). The implement step alone can never promote — the
pipeline hard-gates on the reviewer's verdict.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.logging import get_logger
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass

logger = get_logger("app.selfhealing.backends")

REGRESSION_ENV_VAR = "PAGENTOS_HANDLER_DIR"
_SUBPROCESS_TIMEOUT_S = 60.0


# ------------------------------------------------------------------ interface


@dataclass(slots=True)
class IssueAnalysis:
    component: str
    fault_kind: str
    failing_check: str
    fingerprint: str
    # For wrong_error_mapping faults: the observed counterexample.
    check_name: str | None = None
    input_value: str | None = None
    expected: str | None = None
    actual: str | None = None
    summary: str = ""


@dataclass(slots=True)
class PatchResult:
    candidate_dir: Path
    regression_test_path: Path
    changed_files: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class ReviewCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ReviewResult:
    approved: bool
    checks: list[ReviewCheck] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "summary": self.summary,
        }


@runtime_checkable
class CodingBackend(Protocol):
    """Provider seam: analyze -> implement -> (independent review) -> summarize."""

    name: str

    def analyze_issue(self, incident: dict[str, Any]) -> IssueAnalysis: ...

    def implement_change(
        self, analysis: IssueAnalysis, broken_release_dir: Path, output_dir: Path
    ) -> PatchResult: ...

    def review_change(
        self, analysis: IssueAnalysis, patch: PatchResult, broken_release_dir: Path
    ) -> ReviewResult: ...

    def summarize_patch(self, analysis: IssueAnalysis, patch: PatchResult) -> str: ...


# ----------------------------------------------------- regression test runner


def run_regression_test(
    test_path: Path, handler_dir: Path, *, python: str = sys.executable
) -> tuple[bool, str]:
    """Run a generated regression-test script against ``handler_dir`` in an
    isolated subprocess. Returns (passed, output)."""
    import os

    env = dict(os.environ)
    env[REGRESSION_ENV_VAR] = str(handler_dir)
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(  # noqa: S603 - our own generated script
            [python, str(test_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, "regression test timed out"
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


# ------------------------------------------------------- deterministic backend

_MAPPING_ENTRY_RE = re.compile(
    r"\(\s*(?P<q1>['\"])(?P<marker>[^'\"]+)(?P=q1)\s*,\s*"
    r"(?P<q2>['\"])(?P<klass>[^'\"]+)(?P=q2)\s*\)"
)

# Error-class identifiers are a fixed lexical shape. Anything the incident
# evidence supplies as expected/actual that will be emitted into generated
# code (backends.py builds a candidate handler.py) MUST match this — a hard
# gate against injecting arbitrary text into generated source from the
# unauthenticated incident-ingest surface (M6 security review, Critical).
_ERROR_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _require_error_class_token(value: str, *, field: str) -> str:
    if not _ERROR_CLASS_RE.match(value):
        raise SelfHealingError(
            SelfHealingErrorClass.PATCH_DERIVATION_FAILED,
            f"{field} is not a valid error-class token; refusing to derive code",
            details={"field": field},
        )
    return value

# Error-class identifiers (expected/actual) are taxonomy tokens. Because the
# evidence arrives over the UNAUTHENTICATED ingest surface and `expected` is
# spliced into generated Python source, it MUST be shape-validated at the


class DeterministicCodingBackend:
    """Derives the fix for the controlled ``wrong_error_mapping`` fault class
    directly from the incident evidence. No model, no network."""

    name = "deterministic"

    SUPPORTED_FAULTS = ("wrong_error_mapping",)

    def analyze_issue(self, incident: dict[str, Any]) -> IssueAnalysis:
        evidence = incident.get("evidence") or {}
        material = evidence.get("fingerprint_material") or {}
        fault_kind = material.get("error_class", "")
        if fault_kind not in self.SUPPORTED_FAULTS:
            raise SelfHealingError(
                SelfHealingErrorClass.PATCH_DERIVATION_FAILED,
                f"deterministic backend cannot handle fault class {fault_kind!r}",
                details={"supported": list(self.SUPPORTED_FAULTS)},
            )
        failing = evidence.get(material.get("failing_check", ""), {})
        if not isinstance(failing, dict):
            failing = {}
        input_value = failing.get("input")
        expected = failing.get("expected")
        actual = failing.get("actual")
        if not all(isinstance(v, str) and v for v in (input_value, expected, actual)):
            raise SelfHealingError(
                SelfHealingErrorClass.PATCH_DERIVATION_FAILED,
                "evidence lacks the input/expected/actual counterexample",
            )
        # expected/actual are emitted into generated code — validate their
        # lexical shape at this single choke point (input_value only ever
        # reaches generated code via repr(), never raw). Security review Crit.
        _require_error_class_token(expected, field="expected")
        _require_error_class_token(actual, field="actual")
        return IssueAnalysis(
            component=incident.get("component", "unknown"),
            fault_kind=fault_kind,
            failing_check=material.get("failing_check", "selftest"),
            fingerprint=incident.get("fingerprint", ""),
            check_name=failing.get("check"),
            input_value=input_value,
            expected=expected,
            actual=actual,
            summary=(
                f"{failing.get('check', 'check')}({input_value!r}) returned "
                f"{actual!r}, expected {expected!r}"
            ),
        )

    def implement_change(
        self, analysis: IssueAnalysis, broken_release_dir: Path, output_dir: Path
    ) -> PatchResult:
        handler_path = Path(broken_release_dir) / "handler.py"
        if not handler_path.is_file():
            raise SelfHealingError(
                SelfHealingErrorClass.PATCH_DERIVATION_FAILED,
                f"handler.py not found in broken release {broken_release_dir}",
            )
        source = handler_path.read_text(encoding="utf-8")
        patched, replacements = self._rewrite_mapping(source, analysis)
        if replacements == 0:
            raise SelfHealingError(
                SelfHealingErrorClass.PATCH_DERIVATION_FAILED,
                "could not locate the faulty mapping entry in handler.py",
                details={"actual": analysis.actual, "input": analysis.input_value},
            )
        candidate_dir = Path(output_dir) / "candidate"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        # Copy the whole release, then overwrite the patched file: the
        # candidate is a complete release directory, not a diff.
        for path in Path(broken_release_dir).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(broken_release_dir)
            target = candidate_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        (candidate_dir / "handler.py").write_text(patched, encoding="utf-8")
        regression_path = Path(output_dir) / f"test_regression_{analysis.fingerprint[:12]}.py"
        regression_path.write_text(self._regression_test_source(analysis), encoding="utf-8")
        logger.info(
            "patch_implemented",
            backend=self.name,
            fingerprint=analysis.fingerprint[:12],
            replacements=replacements,
        )
        return PatchResult(
            candidate_dir=candidate_dir,
            regression_test_path=regression_path,
            changed_files=["handler.py"],
            notes=f"rewrote {replacements} mapping entr{'y' if replacements == 1 else 'ies'}",
        )

    def _rewrite_mapping(self, source: str, analysis: IssueAnalysis) -> tuple[str, int]:
        """Replace the mapping entry whose marker matches the failing input and
        whose class equals the observed wrong class with the expected class."""
        replacements = 0

        def _substitute(match: re.Match[str]) -> str:
            nonlocal replacements
            marker = match.group("marker")
            klass = match.group("klass")
            if (
                analysis.input_value is not None
                and marker in analysis.input_value
                and klass == analysis.actual
            ):
                replacements += 1
                q1, q2 = match.group("q1"), match.group("q2")
                # `expected` is re-validated here (not just at analyze time)
                # against the error-class token shape, so nothing that could
                # terminate the string literal or inject code can ever be
                # spliced into generated source — the value is [a-z0-9_]+ by
                # construction (M6 security review, Critical; defense in depth
                # at the exact splice point).
                expected = _require_error_class_token(analysis.expected or "", field="expected")
                return f"({q1}{marker}{q1}, {q2}{expected}{q2})"
            return match.group(0)

        patched = _MAPPING_ENTRY_RE.sub(_substitute, source)
        if replacements:
            # The fault marker comment no longer describes the code; drop it.
            patched = "\n".join(
                line for line in patched.splitlines() if "INJECTED-FAULT" not in line
            ) + "\n"
        return patched, replacements

    def _regression_test_source(self, analysis: IssueAnalysis) -> str:
        return f'''"""Auto-generated regression test (incident {analysis.fingerprint[:12]}).

Runs against the release directory named by ${REGRESSION_ENV_VAR}. Exits 0 on
pass, 1 on regression. Stdlib only, deterministic, offline.
"""

import importlib.util
import os
import sys

handler_dir = os.environ["{REGRESSION_ENV_VAR}"]
spec = importlib.util.spec_from_file_location(
    "handler_under_test", os.path.join(handler_dir, "handler.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

INPUT = {analysis.input_value!r}
EXPECTED = {analysis.expected!r}

actual = module.map_error(INPUT)
if actual != EXPECTED:
    print(f"REGRESSION-FAIL map_error({{INPUT!r}}) = {{actual!r}}, expected {{EXPECTED!r}}")
    sys.exit(1)
print("REGRESSION-PASS")
sys.exit(0)
'''

    def review_change(
        self, analysis: IssueAnalysis, patch: PatchResult, broken_release_dir: Path
    ) -> ReviewResult:
        # Builder must NOT self-approve (EVOLUTION_ENGINE_SPEC §6): delegate to
        # the independent deterministic reviewer.
        return DeterministicReviewer().review_change(analysis, patch, broken_release_dir)

    def summarize_patch(self, analysis: IssueAnalysis, patch: PatchResult) -> str:
        return (
            f"[{self.name}] {analysis.component}: fixed {analysis.fault_kind} — "
            f"{analysis.summary}; changed {', '.join(patch.changed_files)} ({patch.notes})"
        )


# --------------------------------------------------------- independent review


class DeterministicReviewer:
    """Separate acceptance authority: deterministic checks only.

    A patch is approved only when the regression test FAILS on the broken
    release (the test is meaningful), PASSES on the candidate (the bug is
    fixed), the candidate compiles, exposes the required API surface, and
    carries no leftover fault markers.
    """

    name = "deterministic-reviewer"

    REQUIRED_FUNCTIONS = ("map_error", "run_task", "self_test")

    def review_change(
        self, analysis: IssueAnalysis, patch: PatchResult, broken_release_dir: Path
    ) -> ReviewResult:
        checks: list[ReviewCheck] = []

        passed_on_broken, broken_out = run_regression_test(
            patch.regression_test_path, Path(broken_release_dir)
        )
        checks.append(
            ReviewCheck(
                name="regression_fails_on_broken",
                passed=not passed_on_broken,
                detail=broken_out[-500:],
            )
        )
        passed_on_candidate, candidate_out = run_regression_test(
            patch.regression_test_path, patch.candidate_dir
        )
        checks.append(
            ReviewCheck(
                name="regression_passes_on_candidate",
                passed=passed_on_candidate,
                detail=candidate_out[-500:],
            )
        )

        handler_path = patch.candidate_dir / "handler.py"
        source = ""
        try:
            source = handler_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            checks.append(ReviewCheck(name="candidate_compiles", passed=True))
        except (OSError, SyntaxError) as exc:
            tree = None
            checks.append(
                ReviewCheck(name="candidate_compiles", passed=False, detail=str(exc))
            )
        if tree is not None:
            defined = {
                node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
            }
            missing = [f for f in self.REQUIRED_FUNCTIONS if f not in defined]
            checks.append(
                ReviewCheck(
                    name="required_api_surface",
                    passed=not missing,
                    detail=f"missing: {missing}" if missing else "",
                )
            )
        checks.append(
            ReviewCheck(
                name="no_fault_markers",
                passed="INJECTED-FAULT" not in source,
                detail="candidate still carries an INJECTED-FAULT marker"
                if "INJECTED-FAULT" in source
                else "",
            )
        )

        approved = all(check.passed for check in checks)
        summary = (
            "approved" if approved else
            "rejected: " + ", ".join(c.name for c in checks if not c.passed)
        )
        logger.info(
            "review_completed",
            reviewer=self.name,
            approved=approved,
            failed_checks=[c.name for c in checks if not c.passed],
        )
        return ReviewResult(approved=approved, checks=checks, summary=summary)


# ------------------------------------------------------------- Claude backend


class ClaudeCodingBackend:
    """Claude Agent SDK/CLI coding backend — a configured-later seam.

    Without ``cli_path`` every method raises a typed ``backend_not_configured``
    error BEFORE any I/O, so the vendor path exists but is inert in tests
    (mirrors the voice providers' PROVIDER_AUTH_MISSING discipline). When the
    owner configures it (PAGENTOS_SELFHEALING_CLAUDE_CLI), each method shells
    out to the Claude CLI in an isolated working directory per
    EVOLUTION_ENGINE_SPEC §13 — never with bypassPermissions outside a
    disposable sandbox. Domain code only ever sees the interface dataclasses,
    never Anthropic response formats.
    """

    name = "claude"

    def __init__(self, cli_path: str = "", model: str = "") -> None:
        self.cli_path = cli_path
        self.model = model

    def _require_configured(self) -> None:
        if not self.cli_path:
            raise SelfHealingError(
                SelfHealingErrorClass.BACKEND_NOT_CONFIGURED,
                "Claude coding backend is not configured "
                "(set PAGENTOS_SELFHEALING_CLAUDE_CLI to the Claude CLI path)",
            )

    def build_command(self, prompt: str) -> list[str]:
        """Pure command construction (unit-testable without invocation)."""
        command = [self.cli_path, "-p", prompt, "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]
        return command

    def _invoke(self, prompt: str, cwd: Path) -> str:
        self._require_configured()
        proc = subprocess.run(  # noqa: S603 - owner-configured CLI, isolated cwd
            self.build_command(prompt),
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=600.0,
        )
        if proc.returncode != 0:
            raise SelfHealingError(
                SelfHealingErrorClass.INTERNAL_BUG,
                "Claude CLI invocation failed",
                details={"returncode": proc.returncode, "stderr": proc.stderr[-2000:]},
            )
        return proc.stdout

    def analyze_issue(self, incident: dict[str, Any]) -> IssueAnalysis:
        self._require_configured()
        raise SelfHealingError(
            SelfHealingErrorClass.BACKEND_NOT_CONFIGURED,
            "Claude analyze_issue flow is not enabled in this build",
        )

    def implement_change(
        self, analysis: IssueAnalysis, broken_release_dir: Path, output_dir: Path
    ) -> PatchResult:
        self._require_configured()
        raise SelfHealingError(
            SelfHealingErrorClass.BACKEND_NOT_CONFIGURED,
            "Claude implement_change flow is not enabled in this build",
        )

    def review_change(
        self, analysis: IssueAnalysis, patch: PatchResult, broken_release_dir: Path
    ) -> ReviewResult:
        self._require_configured()
        raise SelfHealingError(
            SelfHealingErrorClass.BACKEND_NOT_CONFIGURED,
            "Claude review_change flow is not enabled in this build",
        )

    def summarize_patch(self, analysis: IssueAnalysis, patch: PatchResult) -> str:
        self._require_configured()
        raise SelfHealingError(
            SelfHealingErrorClass.BACKEND_NOT_CONFIGURED,
            "Claude summarize_patch flow is not enabled in this build",
        )


__all__ = [
    "REGRESSION_ENV_VAR",
    "ClaudeCodingBackend",
    "CodingBackend",
    "DeterministicCodingBackend",
    "DeterministicReviewer",
    "IssueAnalysis",
    "PatchResult",
    "ReviewCheck",
    "ReviewResult",
    "run_regression_test",
]
