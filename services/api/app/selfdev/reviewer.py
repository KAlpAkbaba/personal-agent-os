"""The independent reviewer: a candidate is judged by what is observed, not by what is said.

A different object from the model, and nothing in it asks the model anything. For one
candidate it establishes, by running things:

  regression_red_on_base  the candidate's own regression test FAILS with only the test
                          added - the test really catches the defect it claims to;
  regression_green        the same test PASSES with the full patch;
  targeted_tests          the tests the plan names as guarding the touched code pass;
  lint                    every changed Python file lints clean;
  scope                   every changed path is inside the defect's scope or is the
                          regression test the plan declared.

The model's own ``review_code`` verdict is recorded beside these and is required too, but it
can only ever REFUSE a candidate the reviewer passed - never pass one it refused.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.selfdev.model import ChangePlan, Patch
from app.selfdev.runner import CandidateRunner, CommandResult
from app.selfdev.workspace import GitWorkspace


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ReviewVerdict:
    checks: list[Check] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def first_failure(self) -> Check | None:
        return next((c for c in self.checks if not c.passed), None)

    def as_dicts(self) -> list[dict[str, object]]:
        return [
            {"name": c.name, "passed": c.passed, "detail": c.detail[-1500:]} for c in self.checks
        ]


def _tail(result: CommandResult) -> str:
    return result.output[-1500:]


@dataclass(slots=True)
class IndependentReviewer:
    workspace: GitWorkspace
    runner: CandidateRunner

    def review(
        self,
        worktree: Path,
        patch: Patch,
        plan: ChangePlan,
        *,
        in_scope: Callable[[str], bool],
        targeted_tests: list[str],
    ) -> ReviewVerdict:
        verdict = ReviewVerdict()
        test_path = plan.regression_test_path
        test_edits = tuple(e for e in patch.edits if e.path == test_path)
        if not test_edits:
            verdict.checks.append(
                Check("regression_test_present", False, f"the patch does not write {test_path}")
            )
            return verdict

        # 1. The test alone, on the base: it must fail, or it does not test this defect.
        self.workspace.reset(worktree)
        self.workspace.write(worktree, Patch(edits=test_edits))
        alone = self.runner.pytest(worktree, [test_path])
        verdict.checks.append(
            Check(
                "regression_red_on_base",
                not alone.ok and not alone.timed_out,
                "the regression test failed on the base, as it must"
                if not alone.ok and not alone.timed_out
                else "the regression test PASSED (or hung) without the fix: it does not test "
                "this defect\n" + _tail(alone),
            )
        )
        if not verdict.checks[-1].passed:
            return verdict

        # 2. The whole patch.
        self.workspace.reset(worktree)
        self.workspace.write(worktree, patch)
        changed = self.workspace.changed_paths(worktree)
        outside = [p for p in changed if p != test_path and not in_scope(p)]
        verdict.checks.append(
            Check("scope", not outside, "outside scope: " + ", ".join(outside) if outside else "")
        )
        green = self.runner.pytest(worktree, [test_path])
        verdict.checks.append(Check("regression_green", green.ok, "" if green.ok else _tail(green)))
        if targeted_tests:
            suite = self.runner.pytest(worktree, targeted_tests)
            verdict.checks.append(
                Check("targeted_tests", suite.ok, "" if suite.ok else _tail(suite))
            )
        lint = self.runner.lint(worktree, changed)
        verdict.checks.append(Check("lint", lint.ok, "" if lint.ok else _tail(lint)))
        return verdict
