"""Deterministic evaluation of a generated skill (EVOLUTION_ENGINE_SPEC §9).

The generated tests and the generated eval set are actually EXECUTED, each in
its own isolated subprocess with a minimal environment, and the release score is
computed from what really happened. Nothing here asks a model whether the code
looks good — §9: "No automatic promotion solely from 'LLM reviewer likes it'".

Release score dimensions produced here:

- ``functional_success_rate`` — eval cases passed / total;
- ``regression_count``       — failing generated unit checks;
- ``p95_latency_ms``         — from the eval runner's per-case timings;
- ``test_pass_rate``         — generated unit checks passed / total;
- ``security_findings``      — forbidden constructs found in the generated src;
- ``metrics``                — every metric the skill manifest DECLARES in
                               ``health_metrics``; a declared metric the eval
                               set does not emit is itself a gate failure.

The gate verdict (``passed``) is the AND of: tests exit 0, zero regressions,
functional success rate >= threshold, p95 latency <= budget, zero security
findings, and all declared health metrics present.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.resources import (
    ResourceBudget,
    assert_no_secrets,
    build_isolated_env,
    enforce_disk_budget,
    enforce_output_budget,
    permission_findings,
)
from app.evolution.skills import SKILL_DIR_ENV, SkillLayout, read_manifest
from app.evolution.supply_chain import scan_dependencies
from app.logging import get_logger

logger = get_logger("app.evolution.evaluation")

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_SUCCESS_THRESHOLD = 1.0
DEFAULT_MAX_P95_LATENCY_MS = 250.0

EVAL_RESULT_PREFIX = "EVAL-RESULT "

# Constructs a generated, sandboxed skill has no business containing. Matched
# on the AST (imports) and on the source (dynamic-execution builtins) so a
# string mention in a docstring is not a false positive for the import checks.
#
# NETWORK_GATED_IMPORTS (M24, ADR-0087): the HttpAdapterGenerator's rendered
# `src/<skill>.py` legitimately needs exactly one `urllib.request` call to its
# declared loopback base_url. These imports stay forbidden by DEFAULT (the
# `static_findings(source)` single-arg call every existing caller/test used
# before M24 is unchanged) and are allowed ONLY when the manifest explicitly
# grants `network_permissions` — the same deny-by-default gate
# `app.evolution.resources.permission_findings` already applies to the same
# constructs, so there is exactly one place the "network" grant is decided.
ALWAYS_FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess",
        "shutil",
        "ctypes",
        "importlib",
        "pickle",
        "multiprocessing",
        "threading",
        "pathlib",
    }
)
NETWORK_GATED_IMPORTS = frozenset({"socket", "urllib", "http", "ftplib", "smtplib", "requests"})
FORBIDDEN_IMPORTS = ALWAYS_FORBIDDEN_IMPORTS | NETWORK_GATED_IMPORTS
FORBIDDEN_CALLS = ("eval(", "exec(", "__import__(", "compile(", "open(", "os.system")


@dataclass(slots=True)
class ProcessRun:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 3),
            "timed_out": self.timed_out,
            "stdout_tail": self.stdout[-1000:],
            "stderr_tail": self.stderr[-1000:],
        }


@dataclass(slots=True)
class ReleaseScore:
    """EVOLUTION_ENGINE_SPEC §9 scorecard."""

    functional_success_rate: float = 0.0
    regression_count: int = 0
    p95_latency_ms: float = 0.0
    test_pass_rate: float = 0.0
    security_findings: int = 0
    eval_cases: int = 0
    test_checks: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "functional_success_rate": round(self.functional_success_rate, 6),
            "regression_count": self.regression_count,
            "p95_latency_ms": round(self.p95_latency_ms, 6),
            "test_pass_rate": round(self.test_pass_rate, 6),
            "security_findings": self.security_findings,
            "eval_cases": self.eval_cases,
            "test_checks": self.test_checks,
            "metrics": dict(self.metrics),
        }


@dataclass(slots=True)
class EvaluationResult:
    passed: bool
    score: ReleaseScore
    failed_gates: list[str] = field(default_factory=list)
    tests: dict[str, Any] = field(default_factory=dict)
    evals: dict[str, Any] = field(default_factory=dict)
    static: dict[str, Any] = field(default_factory=dict)
    evaluator: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "evaluator": self.evaluator,
            "score": self.score.to_dict(),
            "failed_gates": list(self.failed_gates),
            "tests": dict(self.tests),
            "evals": dict(self.evals),
            "static": dict(self.static),
        }


def run_skill_script(
    script: Path,
    skill_dir: Path,
    *,
    python: str = sys.executable,
    timeout_s: float | None = None,
    budget: ResourceBudget | None = None,
    extra_env: dict[str, str] | None = None,
) -> ProcessRun:
    """Execute one generated script in an ISOLATED subprocess.

    The environment is built from an ALLOWLIST (``app.evolution.resources``),
    never by filtering the parent environment, and is then asserted against the
    secret deny-list; the working directory is the skill directory; and
    ``-I -S`` isolates the interpreter from ``PYTHON*`` inherited state, the user
    site directory AND every site-packages entry — so generated code cannot
    import the ``app`` package, its settings or its credentials, and sees only
    the standard library it is contractually limited to.

    The resource budget supplies the hard wall-clock timeout (every platform)
    and POSIX CPU/address-space rlimits; output size is capped afterwards.
    """
    script = Path(script)
    skill_dir = Path(skill_dir)
    if not script.is_file():
        raise EvolutionError(
            EvolutionErrorClass.NOT_FOUND, f"generated script not found: {script.name}"
        )
    budget = budget or ResourceBudget()
    timeout_s = budget.timeout_s if timeout_s is None else timeout_s
    env = build_isolated_env(skill_dir, skill_dir_var=SKILL_DIR_ENV)
    if extra_env:
        env.update(extra_env)
        assert_no_secrets(env, skill_dir_var=SKILL_DIR_ENV)
    started = time.perf_counter()
    kwargs: dict[str, Any] = {}
    preexec = budget.rlimit_preexec()
    if preexec is not None:  # pragma: no cover - POSIX only
        kwargs["preexec_fn"] = preexec
    try:
        proc = subprocess.run(  # noqa: S603 - our own generated script, isolated env
            [python, "-I", "-S", "-X", "utf8", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(skill_dir),
            timeout=timeout_s,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return ProcessRun(
            exit_code=124,
            stdout="",
            stderr=f"timed out after {timeout_s}s",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            timed_out=True,
        )
    stdout = enforce_output_budget(proc.stdout or "", budget)
    stderr = enforce_output_budget(proc.stderr or "", budget)
    return ProcessRun(
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )


def parse_eval_output(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(EVAL_RESULT_PREFIX):
            try:
                parsed = json.loads(line[len(EVAL_RESULT_PREFIX) :])
            except ValueError as exc:
                raise EvolutionError(
                    EvolutionErrorClass.EVALUATION_FAILED,
                    "eval runner emitted an unparseable EVAL-RESULT line",
                ) from exc
            if not isinstance(parsed, dict):
                raise EvolutionError(
                    EvolutionErrorClass.EVALUATION_FAILED,
                    "eval runner emitted a non-object EVAL-RESULT payload",
                )
            return parsed
    raise EvolutionError(
        EvolutionErrorClass.EVALUATION_FAILED,
        "eval runner produced no EVAL-RESULT line",
    )


def parse_test_output(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def static_findings(source: str, manifest: dict[str, Any] | None = None) -> list[str]:
    """Forbidden imports/dynamic-execution constructs in generated source.

    ``manifest`` is optional and additive (M24, ADR-0087): with no manifest
    (every call site before M24, and every existing test) the full forbidden
    set applies unchanged. When a manifest is given AND it grants
    ``network_permissions``, the NETWORK_GATED_IMPORTS are removed from the
    forbidden set — mirrors ``app.evolution.resources.permission_findings``'s
    existing deny-by-default rule for the same constructs, so there is exactly
    one place "does this manifest allow network code" is decided.
    """
    findings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax_error:{exc.msg}"]
    forbidden = ALWAYS_FORBIDDEN_IMPORTS | (
        frozenset() if (manifest or {}).get("network_permissions") else NETWORK_GATED_IMPORTS
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden:
                    findings.append(f"forbidden_import:{root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden:
                findings.append(f"forbidden_import:{root}")
    stripped = re.sub(r'(""".*?"""|\'\'\'.*?\'\'\'|#[^\n]*)', "", source, flags=re.DOTALL)
    for needle in FORBIDDEN_CALLS:
        if needle in stripped:
            findings.append(f"forbidden_call:{needle.rstrip('(')}")
    return sorted(set(findings))


class SkillEvaluator:
    """Runs the generated tests + evals and scores the candidate per §9."""

    name = "deterministic"

    def __init__(
        self,
        *,
        python: str = sys.executable,
        timeout_s: float | None = None,
        success_threshold: float = DEFAULT_SUCCESS_THRESHOLD,
        max_p95_latency_ms: float = DEFAULT_MAX_P95_LATENCY_MS,
        budget: ResourceBudget | None = None,
    ) -> None:
        self.budget = budget or ResourceBudget()
        self.python = python
        self.timeout_s = self.budget.timeout_s if timeout_s is None else timeout_s
        self.success_threshold = success_threshold
        self.max_p95_latency_ms = max_p95_latency_ms

    def run_tests(self, layout: SkillLayout) -> tuple[ProcessRun, dict[str, Any]]:
        run = run_skill_script(
            layout.test_path,
            layout.root,
            python=self.python,
            timeout_s=self.timeout_s,
            budget=self.budget,
        )
        return run, parse_test_output(run.stdout)

    def run_evals(self, layout: SkillLayout) -> tuple[ProcessRun, dict[str, Any]]:
        run = run_skill_script(
            layout.eval_path,
            layout.root,
            python=self.python,
            timeout_s=self.timeout_s,
            budget=self.budget,
        )
        if run.timed_out:
            return run, {}
        try:
            return run, parse_eval_output(run.stdout)
        except EvolutionError:
            return run, {}

    def evaluate(self, layout: SkillLayout) -> EvaluationResult:
        failed_gates: list[str] = []

        missing = layout.missing_paths()
        if missing:
            raise EvolutionError(
                EvolutionErrorClass.EVALUATION_FAILED,
                f"generated skill is incomplete; missing {missing}",
                details={"missing": missing},
            )

        # 1. Generated unit tests.
        test_run, test_summary = self.run_tests(layout)
        test_total = int(test_summary.get("total") or 0)
        test_failed = int(test_summary.get("failed") or 0)
        if test_run.exit_code != 0:
            failed_gates.append("generated_tests_failed")
        if test_run.timed_out:
            failed_gates.append("generated_tests_timed_out")
        if test_total == 0:
            failed_gates.append("generated_tests_produced_no_checks")

        # 2. Generated eval set.
        eval_run, eval_summary = self.run_evals(layout)
        eval_total = int(eval_summary.get("total") or 0)
        eval_passed = int(eval_summary.get("passed") or 0)
        if not eval_summary:
            failed_gates.append("eval_set_produced_no_result")
        if eval_total == 0:
            failed_gates.append("eval_set_is_empty")

        raw_metrics = eval_summary.get("metrics")
        metrics: dict[str, Any] = dict(raw_metrics) if isinstance(raw_metrics, dict) else {}

        # 3. Skill-declared health metrics must actually be emitted.
        manifest = read_manifest(layout)
        declared = manifest.get("health_metrics") or []
        if isinstance(declared, str):
            declared = [declared]
        missing_metrics = [name for name in declared if name not in metrics]
        if missing_metrics:
            failed_gates.append("declared_health_metrics_missing")

        # 4. Static security scan of the generated source.
        source = layout.module_path.read_text(encoding="utf-8")
        findings = static_findings(source, manifest)
        if findings:
            failed_gates.append("security_findings")

        # 4b. Capability-scoped access, DENY-BY-DEFAULT: any construct needing a
        # permission the manifest does not grant is a gate failure.
        scope_findings = permission_findings(source, manifest)
        if scope_findings:
            failed_gates.append("permission_scope_violation")

        # 4c. Supply chain: pinned name+version+source+digest, no install
        # scripts, and the component must actually exist locally.
        supply = scan_dependencies(
            list(manifest.get("dependencies") or []) + list(manifest.get("components") or [])
        )
        if not supply.ok:
            failed_gates.append(
                "dependency_unavailable"
                if any(f.rule == "dependency_unavailable" for f in supply.findings)
                else "supply_chain_rejected"
            )

        # 4d. Disk budget for the generated artifact.
        try:
            artifact_bytes = enforce_disk_budget(layout.root, self.budget)
            disk_detail: dict[str, Any] = {"bytes": artifact_bytes, "ok": True}
        except EvolutionError as exc:
            disk_detail = {"ok": False, "error": exc.to_dict()}
            failed_gates.append("disk_budget_exceeded")

        functional_success_rate = (eval_passed / eval_total) if eval_total else 0.0
        test_pass_rate = ((test_total - test_failed) / test_total) if test_total else 0.0
        p95 = float(metrics.get("p95_latency_ms") or 0.0)

        if functional_success_rate < self.success_threshold:
            failed_gates.append("functional_success_rate_below_threshold")
        if test_failed > 0:
            failed_gates.append("regressions_present")
        if p95 > self.max_p95_latency_ms:
            failed_gates.append("p95_latency_over_budget")

        score = ReleaseScore(
            functional_success_rate=functional_success_rate,
            regression_count=test_failed,
            p95_latency_ms=p95,
            test_pass_rate=test_pass_rate,
            security_findings=len(findings) + len(scope_findings) + len(supply.findings),
            eval_cases=eval_total,
            test_checks=test_total,
            metrics=metrics,
        )
        result = EvaluationResult(
            passed=not failed_gates,
            score=score,
            failed_gates=sorted(set(failed_gates)),
            tests={
                "process": test_run.to_dict(),
                "total": test_total,
                "failed": test_failed,
                "failures": test_summary.get("failures") or [],
            },
            evals={
                "process": eval_run.to_dict(),
                "total": eval_total,
                "passed": eval_passed,
                "failed_cases": eval_summary.get("failed_cases") or [],
            },
            static={
                "findings": findings,
                "declared_health_metrics": list(declared),
                "missing_health_metrics": missing_metrics,
                "permission_scope_findings": scope_findings,
                "granted_permissions": {
                    key: list(manifest.get(key) or [])
                    for key in (
                        "network_permissions",
                        "filesystem_permissions",
                        "device_permissions",
                        "secret_requirements",
                    )
                    if manifest.get(key)
                },
                "supply_chain": supply.to_dict(),
                "disk": disk_detail,
                "resource_budget": self.budget.to_dict(),
            },
            evaluator=self.name,
        )
        logger.info(
            "skill_evaluated",
            capability_id=layout.capability_id,
            version=layout.version,
            passed=result.passed,
            failed_gates=result.failed_gates,
        )
        return result


__all__ = [
    "ALWAYS_FORBIDDEN_IMPORTS",
    "DEFAULT_MAX_P95_LATENCY_MS",
    "DEFAULT_SUCCESS_THRESHOLD",
    "DEFAULT_TIMEOUT_S",
    "EVAL_RESULT_PREFIX",
    "FORBIDDEN_CALLS",
    "FORBIDDEN_IMPORTS",
    "NETWORK_GATED_IMPORTS",
    "EvaluationResult",
    "ProcessRun",
    "ReleaseScore",
    "SkillEvaluator",
    "parse_eval_output",
    "parse_test_output",
    "run_skill_script",
    "static_findings",
]
