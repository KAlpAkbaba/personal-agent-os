"""The self-development engine: defect in, verified candidate out - and then it STOPS.

    defect -> worktree on selfdev/<run> from an exact base SHA
           -> analyse -> plan -> patch
           -> [independent review: regression red on base, green with the patch, targeted
               tests, lint, scope] -> model review
           -> (on failure: diagnose -> fix -> review again, within budget)
           -> commit on the run's branch -> risk tier from the paths it touched
           -> STOPPED_AT_POLICY_BOUNDARY, or QUARANTINED, or REFUSED

It never merges, never pushes and never releases. The constitution's path - "isolated
branch/worktree -> code -> tests -> review -> build -> sandbox -> canary -> promote" - goes
on from the candidate through the pipeline the owner already runs, and the engine's last act
is to say which promotion class the candidate falls in (``promotion_class_for_tier``): a
tier-4/5 candidate is NEVER_AUTO_PROMOTE and says so; tier 3 needs the owner.

Every bound is hard (``app.selfdev.budget``): the first one crossed quarantines the run with
the bound named, keeps its worktree for inspection, and writes the record. Nothing retries
past a budget.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from app.evolution.risk import derive_risk_tier
from app.evolution.supervisor import (
    PROMOTION_AUTO_CANARY,
    PROMOTION_AUTO_SAFE,
    PROMOTION_NEVER_AUTO_PROMOTE,
    PROMOTION_OWNER_APPROVAL_REQUIRED,
    promotion_class_for_tier,
)
from app.selfdev.budget import Budget, BudgetMeter
from app.selfdev.ci import CI_FAILURE, CIReader, CIStatus
from app.selfdev.model import ChangePlan, DefectSpec, EngineeringModel, Patch
from app.selfdev.reviewer import IndependentReviewer
from app.selfdev.runner import CandidateRunner
from app.selfdev.workspace import BRANCH_PREFIX, GitWorkspace, WorkspaceError, safe_relative_path

STATUS_STOPPED_AT_POLICY = "STOPPED_AT_POLICY_BOUNDARY"
STATUS_QUARANTINED = "QUARANTINED"
STATUS_REFUSED = "REFUSED"

#: Where a run goes next, by promotion class. The engine does none of it.
NEXT_STEP: dict[str, str] = {
    PROMOTION_AUTO_SAFE: "eligible for the release pipeline once CI is green on this exact SHA",
    PROMOTION_AUTO_CANARY: "eligible for a canary release once CI is green on this exact SHA",
    PROMOTION_OWNER_APPROVAL_REQUIRED: "needs the owner's approval before any release",
    PROMOTION_NEVER_AUTO_PROMOTE: "never promoted automatically; the owner decides by hand",
}

#: Files the model may be shown from a scope that names a directory.
MAX_CONTEXT_FILES = 12

T = TypeVar("T")


class _Exhausted(Exception):
    pass


@dataclass(slots=True)
class RunRecord:
    run_id: str
    defect: dict[str, Any]
    base_sha: str
    started_at: str
    status: str = "started"
    reason: str = ""
    branch: str = ""
    worktree: str = ""
    base_ci: dict[str, str] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    model_review: dict[str, Any] = field(default_factory=dict)
    candidate_sha: str = ""
    changed_paths: list[str] = field(default_factory=list)
    risk: dict[str, Any] = field(default_factory=dict)
    promotion_class: str = ""
    next_step: str = ""
    candidate_ci: dict[str, str] = field(default_factory=dict)
    explanation: str = ""
    model: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    finished_at: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")[:40] or "defect"


@dataclass(slots=True)
class SelfDevEngine:
    workspace: GitWorkspace
    runner: CandidateRunner
    model: EngineeringModel
    ci: CIReader
    runs_dir: Path
    budget: Budget = field(default_factory=Budget)

    # ------------------------------------------------------------------ helpers

    def _in_scope(self, defect: DefectSpec) -> Callable[[str], bool]:
        roots = [safe_relative_path(s).rstrip("/") for s in defect.scope]

        def check(path: str) -> bool:
            return any(path == r or path.startswith(r + "/") for r in roots)

        return check

    def _context(
        self, worktree: Path, defect: DefectSpec, extra: tuple[str, ...] = ()
    ) -> dict[str, str]:
        paths: list[str] = []
        for entry in (*defect.scope, *extra):
            target = worktree / safe_relative_path(entry)
            if target.is_dir():
                paths.extend(
                    p.relative_to(worktree).as_posix()
                    for p in sorted(target.rglob("*.py"))
                    if "__pycache__" not in p.parts
                )
            else:
                paths.append(entry)
        return self.workspace.read(worktree, list(dict.fromkeys(paths))[:MAX_CONTEXT_FILES])

    def _ask(self, meter: BudgetMeter, fn: Callable[[], T]) -> T:
        reason = meter.exhausted(self.model.usage)
        if reason:
            raise _Exhausted(reason)
        return fn()

    def _write(self, record: RunRecord, diff: str | None = None) -> None:
        folder = self.runs_dir / record.run_id
        folder.mkdir(parents=True, exist_ok=True)
        record.model = {
            "name": self.model.name,
            "calls": self.model.usage.calls,
            "input_tokens": self.model.usage.input_tokens,
            "output_tokens": self.model.usage.output_tokens,
        }
        (folder / "record.json").write_text(
            json.dumps(asdict(record), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if diff is not None:
            (folder / "candidate.diff").write_text(diff, encoding="utf-8")

    def _finish(
        self,
        record: RunRecord,
        meter: BudgetMeter,
        status: str,
        reason: str = "",
        diff: str | None = None,
    ) -> RunRecord:
        record.status = status
        record.reason = reason
        record.seconds = round(meter.elapsed(), 1)
        record.finished_at = _now()
        self._write(record, diff)
        return record

    @staticmethod
    def _validate_plan(
        plan: ChangePlan, in_scope: Callable[[str], bool], package_tests: str
    ) -> str | None:
        for path in plan.paths_to_change:
            if not in_scope(safe_relative_path(path)):
                return f"the plan changes {path}, outside the defect's scope"
        test_path = safe_relative_path(plan.regression_test_path)
        if not test_path.startswith(package_tests) or not test_path.endswith(".py"):
            return f"the regression test {test_path} is not a test file under {package_tests}"
        return None

    # --------------------------------------------------------------------- run

    def run(self, defect: DefectSpec, *, base_sha: str, targeted_tests: list[str]) -> RunRecord:
        run_id = (
            f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{_slug(defect.defect_id)}-{uuid.uuid4().hex[:6]}"
        )
        record = RunRecord(
            run_id=run_id, defect=asdict(defect), base_sha=base_sha, started_at=_now()
        )
        meter = BudgetMeter(self.budget)

        base_ci = self.ci.status(base_sha)
        record.base_ci = {"state": base_ci.state, "detail": base_ci.detail}
        if base_ci.state == CI_FAILURE:
            return self._finish(
                record,
                meter,
                STATUS_REFUSED,
                "CI is red on the base: no candidate's failures could be told from the base's",
            )
        try:
            in_scope = self._in_scope(defect)
            worktree = self.workspace.create(run_id, base_sha)
        except WorkspaceError as exc:
            return self._finish(record, meter, STATUS_REFUSED, str(exc))
        record.branch = f"{BRANCH_PREFIX}{run_id}"
        record.worktree = str(worktree)
        reviewer = IndependentReviewer(self.workspace, self.runner)
        package_tests = self.runner.package_root.rstrip("/") + "/tests/"

        try:
            files = self._context(worktree, defect)
            analysis = self._ask(meter, lambda: self.model.analyze_codebase(defect, files))
            record.analysis = asdict(analysis)
            plan = self._ask(meter, lambda: self.model.plan_change(defect, analysis, files))
            record.plan = asdict(plan)
            problem = self._validate_plan(plan, in_scope, package_tests)
            if problem:
                return self._finish(record, meter, STATUS_REFUSED, problem)
            allowed = lambda p: in_scope(p) or p == plan.regression_test_path  # noqa: E731
            patch: Patch = self._ask(meter, lambda: self.model.generate_patch(defect, plan, files))

            while True:
                meter.attempts += 1
                reason = meter.exhausted(self.model.usage)
                if reason:
                    raise _Exhausted(reason)
                attempt: dict[str, Any] = {"n": meter.attempts, "paths": list(patch.paths)}
                failure = ""
                try:
                    self.workspace.validate(patch, allowed=allowed)
                except WorkspaceError as exc:
                    failure = f"structural: {exc}"
                    attempt["structural"] = str(exc)
                else:
                    verdict = reviewer.review(
                        worktree, patch, plan, in_scope=in_scope, targeted_tests=targeted_tests
                    )
                    attempt["checks"] = verdict.as_dicts()
                    if verdict.approved:
                        diff = self.workspace.diff(worktree)
                        review = self._ask(
                            meter, lambda d=diff: self.model.review_code(defect, plan, d)
                        )
                        attempt["model_review"] = asdict(review)
                        if review.approved:
                            record.attempts.append(attempt)
                            record.model_review = asdict(review)
                            break
                        failure = "model review: " + "; ".join(review.findings)
                    else:
                        first = verdict.first_failure
                        failure = f"{first.name}: {first.detail}" if first else "review failed"
                attempt["failure"] = failure[-1500:]
                record.attempts.append(attempt)
                if meter.attempts >= self.budget.max_attempts:
                    raise _Exhausted(
                        f"attempts: {meter.attempts} of {self.budget.max_attempts} used; "
                        f"last failure: {failure[:300]}"
                    )
                diagnosis = self._ask(
                    meter,
                    lambda p=patch, f=failure: self.model.review_failure(defect, p, f),
                )
                self.workspace.reset(worktree)
                base_files = self._context(worktree, defect, extra=patch.paths)
                patch = self._ask(
                    meter,
                    lambda p=patch, d=diagnosis, b=base_files: self.model.fix_patch(
                        defect, p, d, b
                    ),
                )
        except _Exhausted as exc:
            diff = self.workspace.diff(worktree)
            return self._finish(record, meter, STATUS_QUARANTINED, str(exc), diff)

        diff = self.workspace.diff(worktree)
        record.changed_paths = self.workspace.changed_paths(worktree)
        try:
            record.explanation = self._ask(
                meter, lambda: self.model.explain_change(defect, plan, diff)
            )
        except _Exhausted:
            record.explanation = ""
        record.candidate_sha = self.workspace.commit(
            worktree, f"selfdev({defect.defect_id}): {plan.summary}"[:200]
        )
        assessment = derive_risk_tier(record.changed_paths)
        record.risk = {
            "tier": int(assessment.tier),
            "reasons": list(assessment.reasons),
            "requires_second_confirmation": assessment.requires_second_confirmation,
        }
        record.promotion_class = promotion_class_for_tier(int(assessment.tier))
        record.next_step = NEXT_STEP[record.promotion_class]
        candidate_ci: CIStatus = self.ci.status(record.candidate_sha)
        record.candidate_ci = {"state": candidate_ci.state, "detail": candidate_ci.detail}
        # The branch keeps the candidate; the worktree slot is freed for the next run.
        self.workspace.remove(worktree)
        return self._finish(record, meter, STATUS_STOPPED_AT_POLICY, "", diff)
