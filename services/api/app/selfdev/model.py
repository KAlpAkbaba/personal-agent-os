"""The EngineeringModel seam: what a self-development run asks a model, and nothing more.

Seven questions, each answered with a typed value (never free text the engine has to
parse): analyse the code the defect lives in, plan a change, write the patch, read a test
failure, fix the patch, review a diff, and explain the change to the owner. The engine owns
everything around those answers - the worktree, the tests, the independent review, the
budgets and the policy boundary. A model is trusted with nothing: every edit it proposes is
checked structurally before it touches a file, and every claim it makes is checked by
running something (``app.selfdev.reviewer``).

Two implementations: ``ScriptedEngineeringModel`` (deterministic, for the engine's own
tests and for replaying a recorded run) and ``AnthropicEngineeringModel``
(``app.selfdev.anthropic_model``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DefectSpec:
    """A defect worth fixing, as the engine received it - evidence, never instructions.

    ``failing_test`` is the observed failure when one exists (a test id and its output).
    ``scope`` is the set of repository paths the change is expected to stay inside; the
    reviewer refuses a patch that leaves it.
    """

    defect_id: str
    title: str
    evidence: str
    scope: tuple[str, ...]
    failing_test: str | None = None


@dataclass(frozen=True, slots=True)
class CodebaseAnalysis:
    summary: str
    relevant_paths: tuple[str, ...]
    root_cause: str


@dataclass(frozen=True, slots=True)
class ChangePlan:
    summary: str
    paths_to_change: tuple[str, ...]
    regression_test_path: str
    regression_test_rationale: str


@dataclass(frozen=True, slots=True)
class FileEdit:
    """The complete new text of one file. Whole files, never hunks: a hunk that does not
    apply is a second failure mode the engine would have to diagnose."""

    path: str
    new_text: str


@dataclass(frozen=True, slots=True)
class Patch:
    edits: tuple[FileEdit, ...]
    notes: str = ""

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(edit.path for edit in self.edits)


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    cause: str
    next_step: str


@dataclass(frozen=True, slots=True)
class CodeReview:
    approved: bool
    findings: tuple[str, ...] = ()


@dataclass(slots=True)
class ModelUsage:
    """Tokens a model has spent in this run; the budget reads it after every call."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@runtime_checkable
class EngineeringModel(Protocol):
    name: str
    usage: ModelUsage

    def analyze_codebase(self, defect: DefectSpec, files: dict[str, str]) -> CodebaseAnalysis: ...

    def plan_change(
        self, defect: DefectSpec, analysis: CodebaseAnalysis, files: dict[str, str]
    ) -> ChangePlan: ...

    def generate_patch(
        self, defect: DefectSpec, plan: ChangePlan, files: dict[str, str]
    ) -> Patch: ...

    def review_failure(
        self, defect: DefectSpec, patch: Patch, failure: str
    ) -> FailureDiagnosis: ...

    def fix_patch(
        self,
        defect: DefectSpec,
        patch: Patch,
        diagnosis: FailureDiagnosis,
        files: dict[str, str],
    ) -> Patch: ...

    def review_code(self, defect: DefectSpec, plan: ChangePlan, diff: str) -> CodeReview: ...

    def explain_change(self, defect: DefectSpec, plan: ChangePlan, diff: str) -> str: ...


@dataclass(slots=True)
class ScriptedEngineeringModel:
    """Answers from a script: fixed values, or callables of the same arguments.

    ``patches`` is consumed in order - the first by ``generate_patch``, each later one by a
    ``fix_patch`` - so a test can script "wrong first, right second". ``tokens_per_call``
    lets a test drive the token budget without a network.
    """

    analysis: CodebaseAnalysis
    plan: ChangePlan
    patches: list[Patch]
    review: CodeReview = field(default_factory=lambda: CodeReview(approved=True))
    explanation: str = "Değişiklik açıklandı."
    tokens_per_call: int = 0
    name: str = "scripted"
    usage: ModelUsage = field(default_factory=ModelUsage)
    calls: list[str] = field(default_factory=list)
    on_call: Callable[[str], Any] | None = None

    def _spend(self, method: str) -> None:
        self.calls.append(method)
        self.usage.calls += 1
        self.usage.input_tokens += self.tokens_per_call
        if self.on_call is not None:
            self.on_call(method)

    def analyze_codebase(self, defect, files):  # noqa: ANN001, ANN201
        self._spend("analyze_codebase")
        return self.analysis

    def plan_change(self, defect, analysis, files):  # noqa: ANN001, ANN201
        self._spend("plan_change")
        return self.plan

    def generate_patch(self, defect, plan, files):  # noqa: ANN001, ANN201
        self._spend("generate_patch")
        return self.patches.pop(0)

    def review_failure(self, defect, patch, failure):  # noqa: ANN001, ANN201
        self._spend("review_failure")
        return FailureDiagnosis(cause=failure[:200], next_step="fix and rerun")

    def fix_patch(self, defect, patch, diagnosis, files):  # noqa: ANN001, ANN201
        self._spend("fix_patch")
        if not self.patches:
            return patch
        return self.patches.pop(0)

    def review_code(self, defect, plan, diff):  # noqa: ANN001, ANN201
        self._spend("review_code")
        return self.review

    def explain_change(self, defect, plan, diff):  # noqa: ANN001, ANN201
        self._spend("explain_change")
        return self.explanation
