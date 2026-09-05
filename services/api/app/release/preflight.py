"""Release preflight (M18 spec §5, ADR-0055 §5): refuse rather than warn.

Ten checks, run before ANY production mutation. Each check names exactly what
it verified, and the report as a whole passes only if every check passes — a
partially-green preflight is a red one. The last check is the one this spec
calls out by name: "the component genuinely needs deploying (a no-op
deployment is a failure of the preflight, not a success)."

What can really fail today vs. what is a stub against fakes
-------------------------------------------------------------

- ``candidate_is_shadow_ready``, ``version_diff_known``, ``migration_impact_known``,
  ``dependencies_known`` and ``component_genuinely_needs_deploying`` inspect data
  this process already has (the opportunity snapshot, the candidate's declared
  paths/evidence, the previous release row) — they can genuinely fail against
  real, malformed input, no fake required.
- ``source_committed_and_clean`` calls a real ``git status`` through
  :class:`GitTreeInspector` when wired to :class:`RealGitTreeInspector` — this
  is a real, working check, not a stub, but tests use
  :class:`FakeGitTreeInspector` so they never depend on this process's actual
  working tree.
- ``tests_passed``, ``security_review_acceptable``, ``benchmark_passed`` and
  ``rollback_point_exists`` are exactly as honest as the evidence a caller
  supplies: this module does not (and structurally cannot, from here) go and
  independently re-run the test suite or the security reviewer — those are
  earlier pipeline stages (``app/evolution/evaluation.py``,
  ``app/evolution/review.py``) whose OUTPUT is what this preflight checks for
  presence and a positive verdict. A caller that fabricates
  ``evidence={"tests": {"passed": True}}`` without ever running a test defeats
  this check; the same trust boundary exists in the M6 self-healing pipeline
  (the independent reviewer's verdict is data by the time the deploy stage
  reads it). Closing that gap fully means the evidence itself carrying a
  verifiable reference (a ledger event, a stored eval report) rather than an
  inline boolean — a natural next hardening step, not built here.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.evolution.risk import RiskAssessment, derive_risk_tier

_MIGRATION_PATH_RE = re.compile(r"^services/api/alembic/versions/")

_GIT_STATUS_TIMEOUT_S = 20.0


@dataclass(slots=True)
class ReleaseCandidate:
    """Everything the preflight and the executor need to know about one release.

    ``opportunity`` is the current backlog snapshot (``EvolutionService.get()``
    output) — used to prove the candidate genuinely crossed the wall, not just
    that its status column currently says so. ``evidence`` carries the
    upstream pipeline's verdicts (see module docstring on their limits).
    """

    opportunity: Mapping[str, Any]
    component: str
    version: str
    candidate_ref: str
    manifest_digest: str
    changed_paths: Sequence[str]
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(slots=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]
    risk: RiskAssessment

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "failed_checks": self.failed_checks,
            "risk": self.risk.to_dict(),
        }


# ------------------------------------------------------------- git tree seam


@runtime_checkable
class GitTreeInspector(Protocol):
    def is_clean(self) -> tuple[bool, str]:
        """Return (clean, detail)."""
        ...


class FakeGitTreeInspector:
    """For tests: no subprocess, no real filesystem."""

    def __init__(self, *, clean: bool = True, detail: str = "") -> None:
        self.clean = clean
        self.detail = detail

    def is_clean(self) -> tuple[bool, str]:
        return self.clean, self.detail


class RealGitTreeInspector:
    """``git status --porcelain`` against a real working tree, with a timeout.

    ``scripts/cloud/release-cloud-core.ps1`` runs the equivalent check with no
    bound on how long ``git``/``ssh``/``scp`` may take; this seam is a working
    example of the fix — a real check, but one that can never hang the release
    it gates.
    """

    def __init__(
        self,
        repo_root: Path | str,
        *,
        git: str = "git",
        timeout_s: float = _GIT_STATUS_TIMEOUT_S,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.git = git
        self.timeout_s = timeout_s

    def is_clean(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, bounded timeout
                [self.git, "status", "--porcelain", "--untracked-files=no"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, f"git status did not complete within {self.timeout_s}s"
        except OSError as exc:
            return False, f"could not run git: {exc}"
        if proc.returncode != 0:
            return False, f"git status failed: {proc.stderr.strip()[:500]}"
        dirty = [line for line in proc.stdout.splitlines() if line.strip()]
        if dirty:
            return False, f"{len(dirty)} uncommitted change(s)"
        return True, "working tree clean"


# ------------------------------------------------------------------- checks


def _check(name: str, passed: bool, detail: str) -> PreflightCheck:
    return PreflightCheck(name=name, passed=passed, detail=detail)


def _check_shadow_ready(candidate: ReleaseCandidate) -> PreflightCheck:
    """Prove the candidate genuinely crossed the wall — not just a status label.

    A row's ``status`` column could in principle be anything; the transition
    HISTORY is the durable evidence that it actually walked through
    ``shadow_ready`` and a real owner authorisation, because both are recorded
    by ``app.evolution.backlog.apply_transition`` at the moment they happened.
    """
    history = (candidate.opportunity.get("detail") or {}).get("transitions") or []
    reached = {entry.get("to") for entry in history if isinstance(entry, Mapping)}
    if "shadow_ready" not in reached:
        return _check(
            "candidate_is_shadow_ready",
            False,
            "the opportunity's transition history never recorded reaching shadow_ready",
        )
    if not ({"owner_authorized", "owner_approved"} & reached):
        return _check(
            "candidate_is_shadow_ready",
            False,
            "no recorded owner authorisation in the transition history",
        )
    return _check(
        "candidate_is_shadow_ready", True, "reached shadow_ready and an owner authorisation"
    )


def _check_version_diff_known(candidate: ReleaseCandidate) -> PreflightCheck:
    ok = bool(candidate.version.strip()) and bool(candidate.candidate_ref.strip())
    detail = (
        f"version={candidate.version!r} candidate_ref={candidate.candidate_ref!r}"
        if ok
        else "version and/or candidate_ref (the diff/commit reference) is empty"
    )
    return _check("version_diff_known", ok, detail)


def _check_tree_clean(git_tree: GitTreeInspector) -> PreflightCheck:
    clean, detail = git_tree.is_clean()
    return _check("source_committed_and_clean", clean, detail)


def _evidence_flag(evidence: Mapping[str, Any], key: str, flag: str) -> tuple[bool, str]:
    section = evidence.get(key)
    if not isinstance(section, Mapping):
        return False, f"no {key} evidence was supplied"
    value = section.get(flag)
    if value is not True:
        return False, f"{key}.{flag} is not True ({value!r})"
    return True, f"{key}.{flag} is True"


def _check_tests_passed(candidate: ReleaseCandidate) -> PreflightCheck:
    ok, detail = _evidence_flag(candidate.evidence, "tests", "passed")
    return _check("tests_passed", ok, detail)


def _check_security_review(candidate: ReleaseCandidate) -> PreflightCheck:
    ok, detail = _evidence_flag(candidate.evidence, "security_review", "acceptable")
    return _check("security_review_acceptable", ok, detail)


def _check_benchmark(candidate: ReleaseCandidate) -> PreflightCheck:
    ok, detail = _evidence_flag(candidate.evidence, "benchmark", "passed")
    return _check("benchmark_passed", ok, detail)


def _check_migration_impact(candidate: ReleaseCandidate) -> PreflightCheck:
    touches_migrations = any(
        _MIGRATION_PATH_RE.match(str(p).replace("\\", "/").lstrip("/"))
        for p in candidate.changed_paths
    )
    if not touches_migrations:
        return _check("migration_impact_known", True, "no migrations in this change")
    plan = candidate.evidence.get("migration_plan")
    if not isinstance(plan, Mapping) or not plan.get("summary") or "reversible" not in plan:
        return _check(
            "migration_impact_known",
            False,
            "this change touches a migration but no migration_plan "
            "(summary + reversible) was supplied",
        )
    return _check(
        "migration_impact_known",
        True,
        f"migration_plan: {plan['summary'][:200]!r} (reversible={plan['reversible']})",
    )


def _check_dependencies_known(candidate: ReleaseCandidate) -> PreflightCheck:
    deps = candidate.evidence.get("dependencies")
    if not isinstance(deps, Mapping) or "unpinned" not in deps:
        return _check(
            "dependencies_known", False, "no dependency report supplied (unpinned list is unknown)"
        )
    unpinned = deps.get("unpinned") or []
    if unpinned:
        return _check(
            "dependencies_known", False, f"unpinned/unknown dependencies: {list(unpinned)[:10]}"
        )
    return _check("dependencies_known", True, "every declared dependency is pinned")


def _check_rollback_point(previous_release: Mapping[str, Any] | None) -> PreflightCheck:
    if previous_release is None:
        # Deliberately conservative (DECISIONS.md ADR-0058): the FIRST-EVER
        # deployment of a component has, by definition, no prior active
        # release to fall back to, and the spec's precondition is literal
        # ("a rollback point exists"). This preflight therefore refuses a
        # component's very first automated deployment; bootstrapping a new
        # component's first release is out of scope for this executor and
        # is recorded as a known, reversible limitation rather than silently
        # special-cased away.
        return _check(
            "rollback_point_exists",
            False,
            "no previous active release recorded for this component; there is "
            "nothing to roll back to (see ADR-0058)",
        )
    return _check(
        "rollback_point_exists",
        True,
        f"previous active release {previous_release.get('version')!r} "
        f"({previous_release.get('id')}) is the rollback target",
    )


def _check_needs_deploying(
    candidate: ReleaseCandidate, previous_release: Mapping[str, Any] | None
) -> PreflightCheck:
    if previous_release is None:
        return _check(
            "component_genuinely_needs_deploying",
            True,
            "no previous release exists to compare against",
        )
    previous_digest = previous_release.get("manifest_digest")
    if previous_digest == candidate.manifest_digest:
        return _check(
            "component_genuinely_needs_deploying",
            False,
            "the candidate's manifest digest is identical to the currently "
            "active release; this would be a no-op deployment",
        )
    return _check(
        "component_genuinely_needs_deploying",
        True,
        f"candidate digest differs from active release {previous_release.get('version')!r}",
    )


def run_preflight(
    candidate: ReleaseCandidate,
    *,
    git_tree: GitTreeInspector,
    previous_release: Mapping[str, Any] | None,
) -> PreflightReport:
    """Run every check; refuse (report ``passed=False``) on the first missing proof.

    All ten checks always run (rather than short-circuiting) so a refused
    release comes back with the FULL list of what is missing, not just the
    first thing found — the owner-facing report should not require three
    round trips to learn about three problems.
    """
    risk = derive_risk_tier(candidate.changed_paths)
    checks = (
        _check_shadow_ready(candidate),
        _check_version_diff_known(candidate),
        _check_tree_clean(git_tree),
        _check_tests_passed(candidate),
        _check_security_review(candidate),
        _check_benchmark(candidate),
        _check_migration_impact(candidate),
        _check_dependencies_known(candidate),
        _check_rollback_point(previous_release),
        _check_needs_deploying(candidate, previous_release),
    )
    return PreflightReport(checks=checks, risk=risk)


__all__ = [
    "FakeGitTreeInspector",
    "GitTreeInspector",
    "PreflightCheck",
    "PreflightReport",
    "RealGitTreeInspector",
    "ReleaseCandidate",
    "run_preflight",
]
