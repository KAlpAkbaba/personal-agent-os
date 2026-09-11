"""The self-development engine, end to end, on a real git repository (ADR-0124).

A throwaway repository shaped like this one (``services/api/app``, ``services/api/tests``)
with one real defect. Real ``git worktree``, real pytest in the worktree, real lint; only
the ENGINEERING MODEL is scripted, because what is under test here is the engine - the
worktree, the independent review, the budgets, quarantine and the policy boundary - and a
scripted model is how "wrong first, right second" or "a test that proves nothing" can be
asked for on purpose. ``test_selfdev_anthropic_model`` holds the real model's seam.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.selfdev.budget import Budget
from app.selfdev.ci import CI_FAILURE, CI_NONE, CIStatus
from app.selfdev.engine import (
    STATUS_QUARANTINED,
    STATUS_REFUSED,
    STATUS_STOPPED_AT_POLICY,
    SelfDevEngine,
)
from app.selfdev.model import (
    ChangePlan,
    CodebaseAnalysis,
    CodeReview,
    DefectSpec,
    FileEdit,
    ModelError,
    Patch,
    ScriptedEngineeringModel,
)
from app.selfdev.runner import CandidateRunner
from app.selfdev.workspace import GitWorkspace, WorkspaceLimits

CALC = "services/api/app/calc.py"
REGRESSION = "services/api/tests/unit/test_calc_regression.py"
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef double(x):\n    return x * 2\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return x * 2\n"
WRONG = "def add(a, b):\n    return a * b\n\n\ndef double(x):\n    return x * 2\n"
REGRESSION_TEST = (
    "from app.calc import add\n\n\ndef test_add_adds() -> None:\n    assert add(2, 3) == 5\n"
)
EXISTING_TEST = (
    "from app.calc import double\n\n\ndef test_double() -> None:\n    assert double(4) == 8\n"
)
USELESS_TEST = (
    "from app.calc import double\n\n\ndef test_add_adds() -> None:\n    assert double(1) == 2\n"
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, encoding="utf-8"
    )
    return done.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    files = {
        "services/api/app/__init__.py": "",
        CALC: BUGGY,
        "services/api/app/identity/__init__.py": "",
        "services/api/app/identity/tokens.py": "SECRET_LEN = 32\n",
        "services/api/tests/__init__.py": "",
        "services/api/tests/unit/__init__.py": "",
        "services/api/tests/unit/test_calc_existing.py": EXISTING_TEST,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return root


class FakeCI:
    def __init__(self, state: str = CI_NONE) -> None:
        self.state = state
        self.asked: list[str] = []

    def status(self, sha: str) -> CIStatus:
        self.asked.append(sha)
        return CIStatus(self.state, "fake")


def _defect(scope=(CALC,)) -> DefectSpec:
    return DefectSpec(
        defect_id="calc-add",
        title="add() subtracts",
        evidence="add(2, 3) returned -1",
        scope=tuple(scope),
    )


def _model(
    *patches: Patch,
    review: CodeReview | None = None,
    tokens: int = 0,
    plan_paths=(CALC,),
    test_path=REGRESSION,
) -> ScriptedEngineeringModel:
    return ScriptedEngineeringModel(
        analysis=CodebaseAnalysis("add subtracts", (CALC,), "operator is minus"),
        plan=ChangePlan("add adds", tuple(plan_paths), test_path, "add(2,3) must be 5"),
        patches=list(patches),
        review=review or CodeReview(approved=True),
        explanation="toplama çıkarma yapıyordu; düzeltildi.",
        tokens_per_call=tokens,
    )


def _patch(calc: str, test: str = REGRESSION_TEST, extra: tuple[FileEdit, ...] = ()) -> Patch:
    return Patch(edits=(FileEdit(CALC, calc), FileEdit(REGRESSION, test), *extra))


def _engine(
    repo: Path, tmp_path: Path, model, *, ci=None, budget=None, limits=None
) -> SelfDevEngine:
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff.exe"))
    return SelfDevEngine(
        workspace=GitWorkspace(
            repo, tmp_path / "worktrees", limits=limits or WorkspaceLimits(min_free_bytes=0)
        ),
        runner=CandidateRunner(
            python=sys.executable, ruff=ruff if Path(ruff).exists() else None, timeout_s=120
        ),
        model=model,
        ci=ci or FakeCI(),
        runs_dir=tmp_path / "runs",
        budget=budget or Budget(max_attempts=3, max_seconds=600, max_tokens=10_000),
    )


def _base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


# ------------------------------------------------------------------ the path that works


def test_a_defect_becomes_a_verified_candidate_on_its_own_branch_and_the_engine_stops(
    repo: Path, tmp_path: Path
) -> None:
    engine = _engine(repo, tmp_path, _model(_patch(FIXED)))

    record = engine.run(
        _defect(),
        base_sha=_base(repo),
        targeted_tests=["services/api/tests/unit/test_calc_existing.py"],
    )

    assert record.status == STATUS_STOPPED_AT_POLICY, record.reason
    checks = {c["name"]: c["passed"] for c in record.attempts[-1]["checks"]}
    assert checks == {
        "regression_red_on_base": True,
        "scope": True,
        "regression_green": True,
        "targeted_tests": True,
        "lint": True,
    }
    # The candidate is a real commit on the run's branch, and nothing else moved.
    assert record.branch.startswith("selfdev/")
    assert _git(repo, "rev-parse", record.branch) == record.candidate_sha
    assert _git(repo, "show", f"{record.candidate_sha}:{CALC}") == FIXED.strip()
    assert _git(repo, "rev-parse", "main") == record.base_sha
    assert (repo / CALC).read_text(encoding="utf-8") == BUGGY  # the owner's checkout untouched
    # The policy boundary, from the paths the candidate touched: app code is tier 3.
    assert record.risk["tier"] == 3
    assert record.promotion_class == "OWNER_APPROVAL_REQUIRED"
    assert "owner" in record.next_step
    # The worktree slot is freed; the branch keeps the candidate.
    assert engine.workspace.live() == []
    # The record says all of it, and carries no secret.
    saved = json.loads((tmp_path / "runs" / record.run_id / "record.json").read_text("utf-8"))
    assert saved["status"] == STATUS_STOPPED_AT_POLICY
    assert saved["explanation"].startswith("toplama")
    assert (tmp_path / "runs" / record.run_id / "candidate.diff").read_text("utf-8")


def test_wrong_first_right_second_is_diagnosed_and_fixed_within_budget(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(WRONG), _patch(FIXED))
    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_STOPPED_AT_POLICY, record.reason
    assert len(record.attempts) == 2
    assert record.attempts[0]["failure"].startswith("regression_green")
    assert model.calls.count("review_failure") == 1 and model.calls.count("fix_patch") == 1


def test_a_high_risk_candidate_says_it_is_never_promoted_automatically(
    repo: Path, tmp_path: Path
) -> None:
    tokens = "services/api/app/identity/tokens.py"
    patch = Patch(
        edits=(
            FileEdit(tokens, "SECRET_LEN = 64\n"),
            FileEdit(
                REGRESSION,
                "from app.identity.tokens import SECRET_LEN\n\n\n"
                "def test_len() -> None:\n    assert SECRET_LEN == 64\n",
            ),
        )
    )
    model = _model(patch, plan_paths=(tokens,))
    record = _engine(repo, tmp_path, model).run(
        _defect(scope=(tokens,)), base_sha=_base(repo), targeted_tests=[]
    )

    assert record.status == STATUS_STOPPED_AT_POLICY, record.reason
    assert record.risk["tier"] == 5
    assert record.promotion_class == "NEVER_AUTO_PROMOTE"


# ------------------------------------------------------------------ what it refuses


def test_a_regression_test_that_passes_without_the_fix_proves_nothing_and_ends_in_quarantine(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(
        _patch(FIXED, USELESS_TEST), _patch(FIXED, USELESS_TEST), _patch(FIXED, USELESS_TEST)
    )
    engine = _engine(repo, tmp_path, model)
    record = engine.run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_QUARANTINED
    assert "attempts: 3 of 3" in record.reason
    assert all(a["failure"].startswith("regression_red_on_base") for a in record.attempts)
    # Quarantine keeps the worktree for inspection, and nothing was committed.
    assert len(engine.workspace.live()) == 1
    assert record.candidate_sha == ""


def test_an_edit_outside_the_defects_scope_is_never_written(repo: Path, tmp_path: Path) -> None:
    stray = FileEdit("services/api/app/identity/tokens.py", "SECRET_LEN = 1\n")
    model = _model(_patch(FIXED, extra=(stray,)))
    record = _engine(repo, tmp_path, model, budget=Budget(max_attempts=1)).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_QUARANTINED
    assert "outside this defect's scope" in record.attempts[0]["structural"]


@pytest.mark.parametrize("path", ["../evil.py", "/etc/passwd", "C:/x.py", ".git/config", "a\\b.py"])
def test_a_path_that_leaves_the_tree_is_refused_structurally(
    repo: Path, tmp_path: Path, path: str
) -> None:
    model = _model(_patch(FIXED, extra=(FileEdit(path, "x"),)))
    record = _engine(repo, tmp_path, model, budget=Budget(max_attempts=1)).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_QUARANTINED
    assert "structural" in record.attempts[0]


def test_the_models_review_can_refuse_what_the_reviewer_passed_but_never_the_reverse(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(FIXED), _patch(FIXED), review=CodeReview(False, ("not minimal",)))
    record = _engine(repo, tmp_path, model, budget=Budget(max_attempts=2)).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_QUARANTINED
    assert record.attempts[0]["failure"] == "model review: not minimal"


def test_the_token_budget_quarantines_the_run_and_names_the_bound(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(WRONG), _patch(FIXED), tokens=5_000)
    record = _engine(repo, tmp_path, model, budget=Budget(max_attempts=5, max_tokens=12_000)).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_QUARANTINED
    assert record.reason.startswith("tokens:")


def test_a_plan_that_leaves_the_scope_is_refused_before_any_patch(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(FIXED), plan_paths=("services/api/app/identity/tokens.py",))
    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])
    assert record.status == STATUS_REFUSED
    assert "outside the defect's scope" in record.reason
    assert "generate_patch" not in model.calls
    # Nothing was written, so there is nothing to inspect: the slot is freed, not held.
    assert not Path(record.worktree).exists()
    assert record.worktree not in _git(repo, "worktree", "list")


def test_a_plan_that_names_its_own_regression_test_among_its_paths_is_not_refused(
    repo: Path, tmp_path: Path
) -> None:
    """The second real run: the model listed the regression test it would add among the paths
    it changes - which it does - and the plan check refused it as out of scope, while the
    write check two lines later allows exactly that path."""
    model = _model(_patch(FIXED), plan_paths=(CALC, REGRESSION))
    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])
    assert record.status == STATUS_STOPPED_AT_POLICY, record.reason


def test_a_base_ci_calls_red_is_no_base_to_judge_a_candidate_against(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(FIXED))
    record = _engine(repo, tmp_path, model, ci=FakeCI(CI_FAILURE)).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_REFUSED
    assert "CI is red" in record.reason
    assert model.calls == []


def test_the_worktree_bound_holds_while_a_quarantined_run_keeps_its_worktree(
    repo: Path, tmp_path: Path
) -> None:
    limits = WorkspaceLimits(max_worktrees=1, min_free_bytes=0)
    first = _engine(
        repo,
        tmp_path,
        _model(_patch(FIXED, USELESS_TEST)),
        budget=Budget(max_attempts=1),
        limits=limits,
    )
    quarantined = first.run(_defect(), base_sha=_base(repo), targeted_tests=[])
    assert quarantined.status == STATUS_QUARANTINED
    # It wrote a candidate, so there is something to inspect: the worktree stays.
    assert quarantined.worktree_kept is True and Path(quarantined.worktree).is_dir()

    second = _engine(repo, tmp_path, _model(_patch(FIXED)), limits=limits)
    record = second.run(_defect(), base_sha=_base(repo), targeted_tests=[])
    assert record.status == STATUS_REFUSED
    assert "already live" in record.reason


def test_a_disk_below_the_floor_starts_nothing(repo: Path, tmp_path: Path) -> None:
    limits = WorkspaceLimits(min_free_bytes=10**18)
    record = _engine(repo, tmp_path, _model(_patch(FIXED)), limits=limits).run(
        _defect(), base_sha=_base(repo), targeted_tests=[]
    )
    assert record.status == STATUS_REFUSED
    assert "floor" in record.reason


# ------------------------------------------------------------ every run ends with a record


def _raising(method: str, exc: Exception):
    def on_call(called: str) -> None:
        if called == method:
            raise exc

    return on_call


def test_a_model_error_before_any_write_is_a_quarantine_with_a_record_and_its_slot_freed(
    repo: Path, tmp_path: Path
) -> None:
    """The third real run quarantined like this and still held one of three live slots with
    nothing in it: the record and the exchanges are everything there was to inspect."""
    model = _model(_patch(FIXED))
    model.on_call = _raising("generate_patch", ModelError("Anthropic API answered 529"))

    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_QUARANTINED
    assert record.reason == "model: Anthropic API answered 529"
    saved = json.loads((tmp_path / "runs" / record.run_id / "record.json").read_text("utf-8"))
    assert saved["status"] == STATUS_QUARANTINED
    assert saved["worktree_kept"] is False
    assert not Path(record.worktree).exists()


def test_an_unexpected_exception_is_a_quarantine_naming_its_type_with_the_trace_kept(
    repo: Path, tmp_path: Path
) -> None:
    """The first real run crashed like this and left a worktree and no record."""
    model = _model(_patch(FIXED))
    model.on_call = _raising("generate_patch", TypeError("string indices must be integers"))

    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_QUARANTINED
    assert record.reason == "error: TypeError: string indices must be integers"
    assert "Traceback" in record.error and "on_call" in record.error
    assert (tmp_path / "runs" / record.run_id / "record.json").is_file()


def test_a_green_candidate_whose_explanation_fails_still_stops_at_the_boundary(
    repo: Path, tmp_path: Path
) -> None:
    model = _model(_patch(FIXED))
    model.on_call = _raising("explain_change", ModelError("Anthropic API answered 529"))

    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_STOPPED_AT_POLICY
    assert record.candidate_sha and record.explanation == ""


def test_a_patch_the_model_could_not_apply_is_refused_structurally_and_fed_back(
    repo: Path, tmp_path: Path
) -> None:
    unapplied = Patch(
        edits=(FileEdit(REGRESSION, REGRESSION_TEST),),
        rejected=(f"replacement 0 in {CALC}: the old text occurs 0 times, not exactly once",),
    )
    model = _model(unapplied, _patch(FIXED))

    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    assert record.status == STATUS_STOPPED_AT_POLICY
    assert "did not apply" in record.attempts[0]["structural"]
    assert "occurs 0 times" in record.attempts[0]["failure"]
    assert model.calls.count("fix_patch") == 1


def test_the_models_exchanges_are_written_beside_the_record(repo: Path, tmp_path: Path) -> None:
    model = _model(_patch(FIXED))

    record = _engine(repo, tmp_path, model).run(_defect(), base_sha=_base(repo), targeted_tests=[])

    saved = json.loads(
        (tmp_path / "runs" / record.run_id / "model-exchanges.json").read_text("utf-8")
    )
    assert [e["method"] for e in saved] == model.calls
