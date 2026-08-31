"""Unit tests: the M7 self-extension pipeline end to end (offline, SQLite).

Acceptance coverage:
- "new generated skill is created only when necessary";
- "independent review occurs";
- "capability registered only after gates";
- "original user task resumes and completes";
- "rejected candidate does not affect production".
"""

import contextlib
import copy
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.evolution.errors import EvolutionErrorClass
from app.evolution.gaps import CapabilityRequest, GapDetector, GapService
from app.evolution.models import Capability, CapabilityGap, SkillVersion
from app.evolution.pipeline import EvolutionPipeline
from app.evolution.registry import CapabilityRegistry
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import DeterministicSkillGenerator, SkillLayout, SkillSpec
from app.evolution.task_resumption import CapabilityDispatcher, TaskResumer
from tests.unit.test_evolution_registry import register_fully

TABLES = [
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
]

SPEC = {
    "capability_id": "text.slugify",
    "operation": "slugify",
    "version": "0.1.0",
    "summary": "turn a title into a url slug",
}


@dataclass
class Stack:
    registry: CapabilityRegistry
    gaps: GapService
    detector: GapDetector
    resumer: TaskResumer
    sandbox: SandboxPolicy
    skills_root: Path

    def pipeline(self, generator=None) -> EvolutionPipeline:
        return EvolutionPipeline(
            self.registry,
            self.gaps,
            generator=generator or DeterministicSkillGenerator(),
            sandbox=self.sandbox,
            skills_root=self.skills_root,
            resumer=self.resumer,
        )

    def open_gap(self, **overrides) -> uuid.UUID:
        body = {
            "requested_capability": "text.slugify",
            "request_text": "Bir baslik verildiginde url slug uret.",
            "required_inputs": ["text"],
            "required_outputs": ["slug"],
            "spec": dict(SPEC),
        }
        body.update(overrides)
        request = CapabilityRequest.parse(body)
        decision = self.detector.detect(request)
        gap = self.gaps.record(request, decision)
        return uuid.UUID(gap["id"])


@pytest.fixture()
def stack(tmp_path) -> Stack:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    registry = CapabilityRegistry(session_scope)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    dispatcher = CapabilityDispatcher(registry, sandbox=SandboxPolicy(skills_root))
    return Stack(
        registry=registry,
        gaps=GapService(session_scope),
        detector=GapDetector(registry),
        resumer=TaskResumer(session_scope, dispatcher),
        sandbox=SandboxPolicy(tmp_path / "work"),
        skills_root=skills_root,
    )


def stage_names(result) -> list[str]:
    return [stage.name for stage in result.stages]


# --------------------------------------------------------------- happy path


def test_pipeline_generates_evaluates_reviews_and_registers(stack: Stack) -> None:
    gap_id = stack.open_gap()
    result = stack.pipeline().run(gap_id)

    assert result.status == "registered", result.summary
    assert stage_names(result) == [
        "load_gap",
        "verify_composition_attempted",
        "work_order",
        "isolated_workspace",
        "generate",
        "evaluate",
        "independent_review",
        "publish",
        "register_capability",
        "resume_task",
    ]
    resolved = stack.registry.resolve("text.slugify")
    assert resolved is not None
    assert resolved["skill_version"]["status"] == "registered"
    assert resolved["manifest"]["generated_by"] == "deterministic"

    published = Path(resolved["skill_version"]["source_ref"])
    assert published.is_relative_to(stack.skills_root)
    layout = SkillLayout(published, "text_slugify", "text.slugify", "0.1.0")
    assert layout.missing_paths() == []

    gap = stack.gaps.get(gap_id)
    assert gap["status"] == "resolved"
    assert gap["resolution"] == "generation"
    assert gap["resolved_skill_version_id"] == result.skill_version_id
    assert [e["step"] for e in gap["decision_trail"]][-1] == "work_order"


def test_capability_is_registered_only_after_every_gate(stack: Stack) -> None:
    """Order is the assertion: evaluation and the independent review both
    complete before registration, and registration is the last gate."""
    result = stack.pipeline().run(stack.open_gap())
    names = stage_names(result)
    assert names.index("evaluate") < names.index("register_capability")
    assert names.index("independent_review") < names.index("register_capability")

    evaluate = result.stages[names.index("evaluate")].detail
    review = result.stages[names.index("independent_review")].detail
    assert evaluate["passed"] is True
    assert evaluate["score"]["eval_cases"] >= 3
    assert review["approved"] is True
    assert {"tests_rerun_pass", "tests_detect_regression"} <= {
        check["name"] for check in review["checks"]
    }


def test_isolated_workspace_is_disposable(stack: Stack) -> None:
    result = stack.pipeline().run(stack.open_gap())
    assert result.status == "registered"
    # The candidate workspace is removed; only the published copy survives.
    assert list(stack.sandbox.root.iterdir()) == []


# ---------------------------------------------- independent review is a gate


class VacuousTestGenerator:
    """Emits a real skill but replaces the generated tests with a green stub.

    Evaluation still passes (the stub reports checks and the real eval set runs),
    so this candidate would be promoted if the builder's own evidence were
    enough. The INDEPENDENT reviewer's mutation probe is what stops it.
    """

    name = "vacuous-tests"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        layout = DeterministicSkillGenerator().generate(spec, workspace)
        layout.test_path.write_text(
            'import json\nprint(json.dumps({"total": 5, "failed": 0, "failures": []}))\n',
            encoding="utf-8",
        )
        return layout


def test_independent_review_rejects_what_evaluation_alone_would_promote(
    stack: Stack,
) -> None:
    gap_id = stack.open_gap()
    result = stack.pipeline(generator=VacuousTestGenerator()).run(gap_id)

    assert result.status == "rejected"
    names = stage_names(result)
    assert result.stages[names.index("evaluate")].status == "ok"
    assert result.stages[names.index("independent_review")].status == "failed"
    assert "tests_detect_regression" in result.summary or "independent review" in result.summary
    assert "register_capability" not in names
    assert "publish" not in names

    assert stack.registry.resolve("text.slugify") is None
    version = stack.registry.get_skill_version(uuid.UUID(result.skill_version_id))
    assert version["status"] == "rejected"
    assert version["rejected_reason"]


def test_generator_can_never_be_its_own_reviewer(stack: Stack) -> None:
    pipeline = stack.pipeline()
    assert pipeline.reviewer is not pipeline.generator
    assert pipeline.reviewer.evaluator is not pipeline.evaluator


# ------------------------------------- rejected candidate vs. production


BAD_WORD_COUNT_GAP = {
    "requested_capability": "text.word_count",
    "request_text": "Metindeki kelime sayisini dondur.",
    "required_inputs": ["text"],
    "required_outputs": ["count"],
    # Deliberately bad: the requester's acceptance case contradicts the
    # operation, so the generated tests and evals fail for real.
    "spec": {
        "capability_id": "text.word_count",
        "operation": "word_count",
        "version": "0.1.0",
        "summary": "count the words in a text",
        "cases": [
            {"input": "one two three", "expected": 3},
            {"input": "one two", "expected": 99},
        ],
    },
}


def test_rejected_candidate_does_not_affect_production(stack: Stack) -> None:
    good = stack.pipeline().run(stack.open_gap())
    assert good.status == "registered"
    production_before = stack.registry.resolve("text.slugify")

    bad_gap = stack.open_gap(**BAD_WORD_COUNT_GAP)
    bad = stack.pipeline().run(bad_gap)
    assert bad.status == "rejected"
    assert "register_capability" not in stage_names(bad)
    assert "publish" not in stage_names(bad)

    # Production is bit-for-bit what it was.
    assert stack.registry.resolve("text.slugify") == production_before
    assert stack.registry.resolve("text.word_count") is None

    rejected = stack.registry.get_skill_version(uuid.UUID(bad.skill_version_id))
    assert rejected["status"] == "rejected"
    assert "release gates failed" in rejected["rejected_reason"]
    # Nothing was published for the rejected candidate.
    assert sorted(p.name for p in stack.skills_root.iterdir()) == ["text_slugify"]
    # The gap goes back to open rather than silently resolving.
    assert stack.gaps.get(bad_gap)["status"] == "open"


def test_a_rejected_new_version_leaves_the_old_one_serving(stack: Stack) -> None:
    """The same-capability case: a later candidate that never passes the gates
    can never displace the registered version ``resolve`` hands to dispatch."""
    stack.pipeline().run(stack.open_gap())
    registered = stack.registry.resolve("text.slugify")

    candidate = stack.registry.create_skill_version("text.slugify", "0.2.0")
    candidate_id = uuid.UUID(candidate["id"])
    stack.registry.record_evaluation(
        candidate_id, {"passed": False, "failed_gates": ["regressions_present"]}
    )
    stack.registry.reject_skill_version(candidate_id, "release gates failed")

    still = stack.registry.resolve("text.slugify")
    assert still == registered
    assert still["version"] == "0.1.0"
    assert still["skill_version"]["status"] == "registered"


def test_rejected_candidate_leaves_dispatch_working(stack: Stack) -> None:
    stack.pipeline().run(stack.open_gap())
    rejected = stack.pipeline().run(stack.open_gap(**BAD_WORD_COUNT_GAP))
    assert rejected.status == "rejected"

    dispatched = stack.resumer.dispatcher.dispatch("text.slugify", {"text": "Merhaba Dunya"})
    assert dispatched.output == {"slug": "merhaba-dunya"}
    assert dispatched.version == "0.1.0"
    with pytest.raises(Exception) as excinfo:
        stack.resumer.dispatcher.dispatch("text.word_count", {"text": "a b"})
    assert excinfo.value.error_class == EvolutionErrorClass.CAPABILITY_MISSING


# --------------------------------------------------- task resumption story


def test_original_task_resumes_and_completes(stack: Stack) -> None:
    task_id = stack.resumer.record_capability_missing(
        "Bu basligi url slug'a cevir", "text.slugify"
    )
    parked = stack.resumer.get_task(task_id)
    assert parked["status"] == "FAILED_RECOVERABLE"
    assert parked["error_class"] == "capability_missing"

    gap_id = stack.open_gap(
        task_id=str(task_id), resume_payload={"text": "Personal Agent OS"}
    )
    result = stack.pipeline().run(gap_id)

    assert result.status == "registered"
    assert result.resumption["status"] == "COMPLETED"
    assert result.resumption["output"] == {"slug": "personal-agent-os"}
    assert result.resumption["capability_id"] == "text.slugify"

    completed = stack.resumer.get_task(task_id)
    assert completed["status"] == "COMPLETED"
    assert completed["error_class"] is None
    assert completed["completed_at"] is not None


def test_resume_is_refused_before_registration(stack: Stack) -> None:
    task_id = stack.resumer.record_capability_missing("slug lutfen", "text.slugify")
    with pytest.raises(Exception) as excinfo:
        stack.resumer.resume(task_id, "text.slugify", {"text": "x"})
    assert excinfo.value.error_class == EvolutionErrorClass.CAPABILITY_MISSING
    assert stack.resumer.get_task(task_id)["status"] == "FAILED_RECOVERABLE"


def test_only_a_capability_missing_task_resumes(stack: Stack) -> None:
    stack.pipeline().run(stack.open_gap())
    task_id = stack.resumer.record_capability_missing("slug", "text.slugify")
    stack.resumer.resume(task_id, "text.slugify", {"text": "A B"})
    # Already COMPLETED: a second resume is refused.
    with pytest.raises(Exception) as excinfo:
        stack.resumer.resume(task_id, "text.slugify", {"text": "A B"})
    assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR


def test_pipeline_without_a_task_skips_resumption(stack: Stack) -> None:
    result = stack.pipeline().run(stack.open_gap())
    assert result.resumption is None
    resume_stage = next(s for s in result.stages if s.name == "resume_task")
    assert resume_stage.status == "skipped"


# ------------------------------------------- generation-only entry points


def test_pipeline_refuses_a_gap_that_composition_resolved(stack: Stack) -> None:
    """Composition-preferred: a request an existing chain can serve must not
    produce a single skill_version row."""
    register_fully(stack.registry, "text.tokenize", inputs=["text"], outputs=["slug"])
    before = len(stack.registry.list_skill_versions())
    gap_id = stack.open_gap()
    assert stack.gaps.get(gap_id)["resolution"] == "composition"

    result = stack.pipeline().run(gap_id)
    assert result.status == "refused"
    assert "nothing to generate" in result.summary
    assert stage_names(result) == ["pipeline_error"]
    assert len(stack.registry.list_skill_versions()) == before


def test_pipeline_refuses_a_product_change_gap(stack: Stack) -> None:
    gap_id = stack.open_gap(
        requested_capability="recovery.supervisor_patch",
        request_text="Recovery supervisor'i guncelle.",
        target_component="recovery-supervisor",
        spec=None,
    )
    assert stack.gaps.get(gap_id)["resolution"] == "product_change_required"
    result = stack.pipeline().run(gap_id)
    assert result.status == "refused"
    assert stage_names(result) == ["load_gap", "pipeline_error"]
    assert result.stages[-1].detail["error_class"] == "product_change_required"
    assert stack.registry.list_skill_versions() == []


def test_pipeline_refuses_a_forged_trail(stack: Stack) -> None:
    gap_id = stack.open_gap()
    gap = stack.gaps.get(gap_id)
    forged = [e for e in gap["decision_trail"] if e["step"] != "composition"]
    with stack.gaps._session_factory() as session:  # noqa: SLF001 - test fixture surgery
        row = session.get(CapabilityGap, gap_id)
        row.decision_trail_json = forged
        session.commit()

    result = stack.pipeline().run(gap_id)
    assert result.status == "refused"
    assert result.stages[-1].detail["error_class"] == "generation_refused"
    assert stack.registry.list_skill_versions() == []


def test_a_spec_for_a_different_capability_never_reaches_the_table(stack: Stack) -> None:
    with pytest.raises(Exception) as excinfo:
        stack.open_gap(spec={**SPEC, "capability_id": "text.reverse_text"})
    assert excinfo.value.error_class == EvolutionErrorClass.VALIDATION_ERROR
    assert stack.gaps.list() == []


def test_pipeline_re_validates_a_tampered_stored_spec(stack: Stack) -> None:
    """Defense in depth: even if a spec were written straight into the gap row,
    the pipeline re-parses it before generating anything."""
    gap_id = stack.open_gap()
    with stack.gaps._session_factory() as session:  # noqa: SLF001 - test fixture surgery
        row = session.get(CapabilityGap, gap_id)
        trail = copy.deepcopy(list(row.decision_trail_json))
        trail[0]["evidence"]["spec"] = {**SPEC, "function_name": "eval"}
        row.decision_trail_json = trail
        session.commit()

    result = stack.pipeline().run(gap_id)
    assert result.status == "failed"
    assert result.stages[-1].detail["error_class"] == "validation_error"
    assert stack.registry.list_skill_versions() == []


def test_pipeline_refuses_an_unknown_gap(stack: Stack) -> None:
    result = stack.pipeline().run(uuid.uuid4())
    assert result.status == "failed"
    assert result.stages[-1].detail["error_class"] == "not_found"
