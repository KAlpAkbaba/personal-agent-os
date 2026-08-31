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
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.evolution.authorization import (
    AuthorizationProvider,
    NullAuthorizationProvider,
)
from app.evolution.errors import EvolutionError
from app.evolution.evaluation import SkillEvaluator, static_findings
from app.evolution.manifest import granted_permissions, validate_manifest
from app.evolution.resources import permission_findings
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import SkillLayout, read_manifest
from app.evolution.supply_chain import scan_dependencies
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
        authorization: AuthorizationProvider | None = None,
    ) -> None:
        # A *fresh* evaluator by default: the reviewer re-runs everything itself
        # instead of consuming the builder-side evaluation report.
        self.evaluator = evaluator or SkillEvaluator()
        self.sandbox = sandbox
        # Deny-by-default: with no verifiable authorization source (the M8
        # Authorized Asset Registry), every permission grant is refused.
        self.authorization = authorization or NullAuthorizationProvider()

    def review_record(self, layout: SkillLayout) -> tuple[ReviewResult, dict[str, Any]]:
        """The reviewer's verdict PLUS the JSON payload the registry gates on.

        ``permissions_approved``/``approved_permissions`` are the deny-by-default
        decision: ``registry.register`` refuses a generated manifest whose grants
        are not exactly what this payload approved.
        """
        result = self.review(layout)
        try:
            manifest = read_manifest(layout)
        except EvolutionError:
            manifest = {}
        grants = granted_permissions(manifest)
        approved, reason, unauthorized = self._approve_permissions(grants, manifest)
        payload = result.to_dict()
        payload["reviewer"] = self.name
        payload["permissions_approved"] = approved
        payload["approved_permissions"] = grants if approved else {}
        payload["deny_by_default"] = True
        payload["authorization_provider"] = self.authorization.name
        payload["permission_decision_reason"] = reason
        if unauthorized:
            payload["unauthorized_grants"] = unauthorized
        return result, payload

    def _approve_permissions(
        self, grants: dict[str, list[Any]], manifest: dict[str, Any]
    ) -> tuple[bool, str, list[str]]:
        """DENY-BY-DEFAULT, against a VERIFIED authorization.

        A grant is approved only when an ``AuthorizationProvider`` verifies the
        asset the skill acts on AND the requested grants fall inside what the
        owner recorded for that asset. A caller-asserted
        ``creation_reason.authorized_asset`` string is evidence of nothing: with
        the default provider (no Authorized Asset Registry until M8) every grant
        request is refused, and the skill can still reach production with empty
        grants. This is the seam the owner policy subsystem drives — M8's
        registry implements the provider, so powerful tools can be handed to an
        explicitly authorized device/asset without Evolution touching the
        security root (ACCEPTANCE_TESTS M7 Boundaries, SECURITY_MODEL M7).
        """
        if not grants:
            return True, "no permissions requested (deny-by-default satisfied)", []
        asset_ref = (manifest.get("creation_reason") or {}).get("authorized_asset")
        authorization = self.authorization.verify(asset_ref)
        if authorization is None:
            return (
                False,
                (
                    "permission grants refused: no verifiable owner authorization "
                    f"for asset {asset_ref!r} (provider {self.authorization.name!r}; "
                    "the Authorized Asset Registry is an M8 deliverable)"
                ),
                sorted(f"{k}:{v}" for k, values in grants.items() for v in values),
            )
        covered, unauthorized = authorization.covers(
            {k: [str(v) for v in values] for k, values in grants.items()}
        )
        if not covered:
            return (
                False,
                (
                    f"permission grants exceed what asset {asset_ref!r} is "
                    f"authorized for (source {authorization.source!r})"
                ),
                unauthorized,
            )
        return (
            True,
            f"grants verified against asset {asset_ref!r} (source {authorization.source!r})",
            [],
        )

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
        manifest: dict[str, Any] = {}
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
        source = (
            layout.module_path.read_text(encoding="utf-8")
            if layout.module_path.is_file()
            else ""
        )
        findings = static_findings(source) if source else ["missing_source"]
        checks.append(
            ReviewCheck(
                name="no_forbidden_constructs",
                passed=not findings,
                detail=", ".join(findings),
            )
        )

        # Capability-scoped access, deny-by-default — re-derived by the reviewer
        # from the source and the manifest, never taken from the evaluator.
        loaded_manifest = manifest if manifest_ok else {}
        scope_findings = permission_findings(source, loaded_manifest) if source else []
        checks.append(
            ReviewCheck(
                name="permissions_scoped",
                passed=not scope_findings,
                detail="; ".join(
                    f"{f['construct']} needs {f['permission']}" for f in scope_findings
                ),
            )
        )

        # Supply chain re-scanned independently (pinning, source, digest,
        # install scripts, local availability).
        supply = scan_dependencies(
            list((loaded_manifest or {}).get("dependencies") or [])
            + list((loaded_manifest or {}).get("components") or [])
        )
        checks.append(
            ReviewCheck(
                name="supply_chain_clean",
                passed=supply.ok,
                detail=", ".join(sorted({f.rule for f in supply.findings})),
            )
        )

        # Deny-by-default decision on the requested grants. The reviewer records
        # the exact approved set; registration compares it to the manifest.
        grants = granted_permissions(loaded_manifest or {})
        permissions_approved, permission_reason, _unauthorized = self._approve_permissions(
            grants, loaded_manifest or {}
        )
        if grants:
            checks.append(
                ReviewCheck(
                    name="permission_grants_reviewed",
                    passed=permissions_approved,
                    detail=(
                        "approved: " + json.dumps(grants, sort_keys=True)
                        if permissions_approved
                        else permission_reason
                    ),
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
