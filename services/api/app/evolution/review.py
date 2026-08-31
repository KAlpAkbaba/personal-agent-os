"""Independent review of a generated skill (EVOLUTION_ENGINE_SPEC §6).

Builder/reviewer separation, exactly as M6 established it: the thing that WROTE
the code is never the acceptance authority. The reviewer here is a separate
class that does not import, hold or trust any generator, and it does not read
the evaluator's verdict either — it RE-RUNS the generated tests and evals in its
own subprocesses and adds static checks the generator cannot influence.

Its strongest check is a mutation probe: the reviewer copies the candidate,
overrides the entrypoint with a constant-returning stub and asserts that the
generated tests FAIL on the mutant. That is the generated-skill analogue of M6's
"the regression test must fail on the broken release" — it proves the tests are
meaningful rather than vacuously green.

``ReviewCheck``/``ReviewResult`` are reused verbatim from
``app.selfhealing.backends`` so both engineering loops report review evidence in
one shape.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

from app.evolution.errors import EvolutionError
from app.evolution.evaluation import SkillEvaluator, static_findings
from app.evolution.registry import validate_manifest
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import SkillLayout, read_manifest
from app.logging import get_logger
from app.selfhealing.backends import ReviewCheck, ReviewResult

logger = get_logger("app.evolution.review")

REQUIRED_ENTRYPOINT = "run"
MUTANT_SUFFIX = (
    "\n\n# --- reviewer mutation probe (not part of the candidate) ---\n"
    "def run(payload):  # noqa: F811 - deliberate override\n"
    '    return {"__mutant__": True}\n'
)


class IndependentSkillReviewer:
    """The only acceptance authority for a generated skill candidate."""

    name = "independent-skill-reviewer"

    def __init__(
        self,
        *,
        evaluator: SkillEvaluator | None = None,
        sandbox: SandboxPolicy | None = None,
    ) -> None:
        # A *fresh* evaluator by default: the reviewer re-runs everything itself
        # instead of consuming the builder-side evaluation report.
        self.evaluator = evaluator or SkillEvaluator()
        self.sandbox = sandbox

    def review(self, layout: SkillLayout) -> ReviewResult:
        checks: list[ReviewCheck] = []

        missing = layout.missing_paths()
        checks.append(
            ReviewCheck(
                name="layout_complete",
                passed=not missing,
                detail=f"missing: {missing}" if missing else "",
            )
        )

        if self.sandbox is not None:
            try:
                self.sandbox.ensure_within(layout.root, label="candidate skill root")
                checks.append(ReviewCheck(name="sandbox_confined", passed=True))
            except EvolutionError as exc:
                checks.append(
                    ReviewCheck(name="sandbox_confined", passed=False, detail=exc.message)
                )

        # Every generated python file must parse.
        unparseable: list[str] = []
        for source_file in sorted(layout.root.rglob("*.py")):
            try:
                ast.parse(source_file.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as exc:
                unparseable.append(f"{source_file.name}: {exc}")
        checks.append(
            ReviewCheck(
                name="generated_sources_parse",
                passed=not unparseable,
                detail="; ".join(unparseable)[:500],
            )
        )

        # Entrypoint surface.
        entrypoint_ok = False
        if layout.module_path.is_file():
            try:
                tree = ast.parse(layout.module_path.read_text(encoding="utf-8"))
                entrypoint_ok = any(
                    isinstance(node, ast.FunctionDef) and node.name == REQUIRED_ENTRYPOINT
                    for node in tree.body
                )
            except SyntaxError:
                entrypoint_ok = False
        checks.append(
            ReviewCheck(
                name="entrypoint_present",
                passed=entrypoint_ok,
                detail="" if entrypoint_ok else f"no module-level def {REQUIRED_ENTRYPOINT}()",
            )
        )

        # Manifest must satisfy the §2 registry contract.
        try:
            manifest = read_manifest(layout)
            validate_manifest(manifest)
            manifest_ok, manifest_detail = True, ""
        except EvolutionError as exc:
            manifest_ok, manifest_detail = False, exc.message
        checks.append(
            ReviewCheck(name="manifest_valid", passed=manifest_ok, detail=manifest_detail)
        )

        # Static security scan (independent of the evaluator's run).
        findings = (
            static_findings(layout.module_path.read_text(encoding="utf-8"))
            if layout.module_path.is_file()
            else ["missing_source"]
        )
        checks.append(
            ReviewCheck(
                name="no_forbidden_constructs",
                passed=not findings,
                detail=", ".join(findings),
            )
        )

        # Re-run the generated tests and evals ourselves.
        if not missing:
            test_run, test_summary = self.evaluator.run_tests(layout)
            checks.append(
                ReviewCheck(
                    name="tests_rerun_pass",
                    passed=test_run.exit_code == 0,
                    detail=(test_run.stdout + test_run.stderr)[-500:],
                )
            )
            eval_run, eval_summary = self.evaluator.run_evals(layout)
            eval_ok = (
                eval_run.exit_code == 0
                and bool(eval_summary)
                and eval_summary.get("total")
                and eval_summary.get("passed") == eval_summary.get("total")
            )
            checks.append(
                ReviewCheck(
                    name="evals_rerun_pass",
                    passed=bool(eval_ok),
                    detail=(eval_run.stdout + eval_run.stderr)[-500:],
                )
            )
            checks.append(self._mutation_probe(layout))
            _ = test_summary
        else:
            for name in ("tests_rerun_pass", "evals_rerun_pass", "tests_detect_regression"):
                checks.append(
                    ReviewCheck(name=name, passed=False, detail="candidate layout incomplete")
                )

        approved = all(check.passed for check in checks)
        summary = (
            "approved"
            if approved
            else "rejected: " + ", ".join(c.name for c in checks if not c.passed)
        )
        logger.info(
            "skill_review_completed",
            reviewer=self.name,
            capability_id=layout.capability_id,
            approved=approved,
            failed_checks=[c.name for c in checks if not c.passed],
        )
        return ReviewResult(approved=approved, checks=checks, summary=summary)

    # ------------------------------------------------------------------ probe

    def _mutation_probe(self, layout: SkillLayout) -> ReviewCheck:
        """The generated tests must FAIL when the entrypoint is sabotaged."""
        temp_root = Path(tempfile.mkdtemp(prefix="evolution-mutant-"))
        try:
            mutant_root = temp_root / layout.root.name
            shutil.copytree(
                layout.root,
                mutant_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            mutant = SkillLayout(
                root=mutant_root,
                skill_name=layout.skill_name,
                capability_id=layout.capability_id,
                version=layout.version,
            )
            with mutant.module_path.open("a", encoding="utf-8") as handle:
                handle.write(MUTANT_SUFFIX)
            run, _ = self.evaluator.run_tests(mutant)
            detected = run.exit_code != 0
            return ReviewCheck(
                name="tests_detect_regression",
                passed=detected,
                detail=""
                if detected
                else "generated tests passed on a sabotaged entrypoint (vacuous tests)",
            )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


__all__ = ["MUTANT_SUFFIX", "REQUIRED_ENTRYPOINT", "IndependentSkillReviewer"]
