"""``TaskGraph``: the structured plan (spec §1), spec-first.

Pydantic, the same discipline ``app.artifacts.spec.ArtifactSpec`` / ``app.creative3d.spec.
ScenePlan`` already follow for a model-facing structured input: strict (``extra="forbid"``)
so a stray field is refused rather than silently ignored, and every bound (``le=...``) is
enforced at CONSTRUCTION time — a graph that violates a numeric bound never exists as an
object, let alone reaches ``app.executive.graph.validate_graph`` (which owns the checks a
field type cannot express: the DAG, closed-vocabulary cross-references, and "no kind may
carry a risk class it wasn't built for").

This module is DATA ONLY. It never calls a service, never touches the database, and never
imports Temporal — the planner (``app.executive.planner``) produces a ``TaskGraph``, the
validator (``app.executive.graph``) checks it, and only then does anything execute
(``app.executive.workflow``). Keeping the vocabulary here and nowhere else is what makes
"no new capability is implemented in M26" a checkable fact rather than a promise: every
step kind in :data:`STEP_KIND_PROFILES` is the complete list of what a graph can ever ask
for, and its risk class / compensation / evidence kind are fixed by the TABLE, never by
what a particular planner happened to choose that day.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- bounds
#
# Every number here is the measurement the spec names, not a guess: §1's own table
# (timeout_s <= 900, retry.max_attempts <= 3, retry.backoff_s <= 60) and §4's run-level
# bounds (<= 24 steps, <= 2 active runs, <= 60 min wall clock, <= 15 min per activity).

MAX_STEPS = 24
MAX_TIMEOUT_S = 900  # <= 15 min per activity (spec §4)
MAX_RETRY_ATTEMPTS = 3
MAX_BACKOFF_S = 60
MAX_ACTIVE_RUNS = 2
MAX_RUN_WALL_CLOCK_S = 60 * 60  # <= 60 min per run (spec §4)
MAX_GOAL_CHARS = 2000
MAX_STEP_ID_CHARS = 8
MAX_INPUT_VALUE_CHARS = 2000

# ------------------------------------------------------------------- closed vocabularies

#: Every step kind maps to ONE existing service call (spec §1). Adding a kind here is a
#: spec change, not an implementation detail — it is the one place "no new capability" is
#: enforced as a closed set rather than as a promise in a docstring.
STEP_KIND_RESEARCH_RUN = "research.run"
STEP_KIND_RESEARCH_SYNTHESIZE = "research.synthesize"
STEP_KIND_DOCUMENTS_FIND = "documents.find"
STEP_KIND_DOCUMENTS_COMPARE = "documents.compare"
STEP_KIND_DOCUMENTS_EXTRACT = "documents.extract"
STEP_KIND_ARTIFACTS_CREATE = "artifacts.create"
STEP_KIND_ARTIFACTS_RENDER = "artifacts.render"
STEP_KIND_MAIL_ANALYZE_THREAD = "mail.analyze_thread"
STEP_KIND_MAIL_DRAFT = "mail.draft"
STEP_KIND_CALENDAR_PROPOSE = "calendar.propose"
STEP_KIND_APPS_CREATE = "apps.create"
STEP_KIND_APPS_TEST = "apps.test"
STEP_KIND_SCENE_CREATE = "scene.create"
STEP_KIND_SCENE_RENDER = "scene.render"
STEP_KIND_SYNTHESIS = "synthesis"

#: Risk classes (spec §1). ``high_risk`` is named in the spec's own vocabulary line but
#: deliberately has NO step kind mapped to it below — sending, paying, deleting and
#: publishing are not steps a graph can carry at all (spec §1, §4, §8); a plan that would
#: need one ends at ``mail.draft``/``calendar.propose`` instead. Kept as a string constant
#: (never assigned to any kind) so ``graph.py`` can assert its absence positively rather
#: than by omission.
RISK_READ = "read"
RISK_MUTATE_LOCAL = "mutate_local"
RISK_MUTATE_EXTERNAL = "mutate_external"
RISK_HIGH_RISK = "high_risk"
RISK_CLASSES: tuple[str, ...] = (RISK_READ, RISK_MUTATE_LOCAL, RISK_MUTATE_EXTERNAL, RISK_HIGH_RISK)

#: Compensations (spec §1): closed, and note what is absent — there is no
#: "discard_proposal" and no "delete_evidence". A calendar proposal that is never
#: confirmed commits nothing on its own (the M21 gate leaves it simply pending), and a
#: partial research's evidence is exactly the honest partial state spec §3 asks the run to
#: keep — neither needs undoing, so both compensate with ``none`` rather than inventing a
#: fifth word.
COMPENSATION_NONE = "none"


#: B10 req 539: what a compensation actually DID. Recorded on the step's evidence, because
#: "compensated" used to be written unconditionally - a branch that matched nothing, an
#: evidence dict with no id to act on, and a provider that raised all ended up saying the
#: same thing as a genuine undo.
COMPENSATION_OUTCOME_UNDONE = "undone"
COMPENSATION_OUTCOME_NOTHING_TO_UNDO = "nothing_to_undo"
COMPENSATION_OUTCOME_ATTEMPTED_AND_FAILED = "attempted_and_failed"
COMPENSATION_OUTCOMES: tuple[str, ...] = (
    COMPENSATION_OUTCOME_UNDONE,
    COMPENSATION_OUTCOME_NOTHING_TO_UNDO,
    COMPENSATION_OUTCOME_ATTEMPTED_AND_FAILED,
)
COMPENSATION_DISCARD_DRAFT = "discard_draft"
COMPENSATION_STOP_PROJECT = "stop_project"
COMPENSATION_DELETE_RENDER = "delete_render"
COMPENSATIONS: tuple[str, ...] = (
    COMPENSATION_NONE,
    COMPENSATION_DISCARD_DRAFT,
    COMPENSATION_STOP_PROJECT,
    COMPENSATION_DELETE_RENDER,
)

#: Postcondition evidence kinds (spec §1). What a step's row must carry, READ BACK by the
#: activity, before the step may be marked verified (spec §3's central rule).
EVIDENCE_ARTIFACT_ID = "artifact_id"
EVIDENCE_DOCUMENT_REFS = "document_refs"
EVIDENCE_RESEARCH_REPORT = "research_report"
EVIDENCE_DRAFT_ID = "draft_id"
EVIDENCE_PROPOSAL_ID = "proposal_id"
EVIDENCE_PROJECT_ID = "project_id"
EVIDENCE_SCENE_ID = "scene_id"
EVIDENCE_TEXT = "text"
EVIDENCE_KINDS: tuple[str, ...] = (
    EVIDENCE_ARTIFACT_ID,
    EVIDENCE_DOCUMENT_REFS,
    EVIDENCE_RESEARCH_REPORT,
    EVIDENCE_DRAFT_ID,
    EVIDENCE_PROPOSAL_ID,
    EVIDENCE_PROJECT_ID,
    EVIDENCE_SCENE_ID,
    EVIDENCE_TEXT,
)

#: Precondition checks (spec §1).
PRECONDITION_FOCUS_EXISTS = "focus_exists"
PRECONDITION_STEP_DONE = "step_done"
PRECONDITION_DEVICE_CAPABILITY = "device_capability"
PRECONDITION_ARTIFACT_VALID = "artifact_valid"
PRECONDITION_ACCOUNT_PRESENT = "account_present"
PRECONDITION_NONE = "none"
#: B38 (req 554/557): a fallback branch runs only when the named step FAILED (any
#: unsuccessful terminal state); a strict dependency runs only when it VERIFIED.
PRECONDITION_STEP_FAILED = "step_failed"
PRECONDITION_STEP_VERIFIED = "step_verified"
#: B38 (req 544): the step waits for the owner's explicit yes before it runs.
PRECONDITION_OWNER_APPROVAL = "owner_approval"

#: B38 (req 555): a step may run again, bounded, until its postcondition minimum is met.
MAX_REPEAT_ROUNDS = 3

#: B38 (req 550/551): who built the graph - the deterministic shapes, the model under
#: the owner's flag, or the owner's own graph through the REST route.
PLANNER_RULE = "rule"
PLANNER_MODEL = "model"
PLANNER_OWNER = "owner"
PLANNERS: tuple[str, ...] = (PLANNER_RULE, PLANNER_MODEL, PLANNER_OWNER)
#: B38 (req 556): the timeout a kind gets when a proposal names none - the rule-based
#: shapes' own numbers, so the model's plan and the rules' plan wait the same.
DEFAULT_TIMEOUT_S_BY_KIND: dict[str, int] = {
    STEP_KIND_RESEARCH_RUN: 900,
    STEP_KIND_RESEARCH_SYNTHESIZE: 120,
    STEP_KIND_DOCUMENTS_FIND: 120,
    STEP_KIND_DOCUMENTS_COMPARE: 180,
    STEP_KIND_DOCUMENTS_EXTRACT: 180,
    STEP_KIND_ARTIFACTS_CREATE: 180,
    STEP_KIND_ARTIFACTS_RENDER: 300,
    STEP_KIND_MAIL_ANALYZE_THREAD: 120,
    STEP_KIND_MAIL_DRAFT: 120,
    STEP_KIND_CALENDAR_PROPOSE: 120,
    STEP_KIND_APPS_CREATE: 600,
    STEP_KIND_APPS_TEST: 600,
    STEP_KIND_SCENE_CREATE: 300,
    STEP_KIND_SCENE_RENDER: 600,
    STEP_KIND_SYNTHESIS: 60,
}

PRECONDITION_CHECKS: tuple[str, ...] = (
    PRECONDITION_FOCUS_EXISTS,
    PRECONDITION_STEP_DONE,
    PRECONDITION_DEVICE_CAPABILITY,
    PRECONDITION_ARTIFACT_VALID,
    PRECONDITION_ACCOUNT_PRESENT,
    PRECONDITION_NONE,
    PRECONDITION_STEP_FAILED,
    PRECONDITION_STEP_VERIFIED,
    PRECONDITION_OWNER_APPROVAL,
)

#: Only these two error classes may ever appear in a step's ``retry.only_on`` (spec §1,
#: §3's failure matrix): a step that failed for any other reason (validation, a refused
#: capability, a security refusal) is wrong to retry blindly, and retrying it would not
#: change the outcome — only genuinely transient conditions are retryable.
RETRYABLE_ERROR_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
RETRYABLE_ERROR_TIMEOUT = "timeout"
RETRYABLE_ERROR_CLASSES: tuple[str, ...] = (
    RETRYABLE_ERROR_DEPENDENCY_UNAVAILABLE,
    RETRYABLE_ERROR_TIMEOUT,
)


class StepKindProfile(BaseModel):
    """The FIXED risk class / compensation / evidence kind for one step kind — never
    chosen per-graph. A planner names a kind; everything else about its authority is
    looked up here, not proposed."""

    model_config = ConfigDict(frozen=True)

    risk_class: str
    compensation: str
    evidence_kind: str
    #: Human-readable, for the receipt/explain sentence — never used for authority.
    description: str


#: The COMPLETE closed vocabulary (spec §1's step-kind list, verbatim) with its fixed
#: profile. A kind not in this dict does not exist as far as ``graph.py`` is concerned.
STEP_KIND_PROFILES: dict[str, StepKindProfile] = {
    STEP_KIND_RESEARCH_RUN: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_RESEARCH_REPORT,
        description="the M13 browser research through the device",
    ),
    STEP_KIND_RESEARCH_SYNTHESIZE: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_TEXT,
        description="the report's executive summary as text",
    ),
    STEP_KIND_DOCUMENTS_FIND: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_DOCUMENT_REFS,
        description="find files matching a folder/pattern (M20)",
    ),
    STEP_KIND_DOCUMENTS_COMPARE: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_DOCUMENT_REFS,
        description="compare two documents (M20)",
    ),
    STEP_KIND_DOCUMENTS_EXTRACT: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_DOCUMENT_REFS,
        description="extract an answer from a document (M20)",
    ),
    STEP_KIND_ARTIFACTS_CREATE: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_DELETE_RENDER,
        evidence_kind=EVIDENCE_ARTIFACT_ID,
        description="build a document/spreadsheet/presentation from earlier outputs (M22)",
    ),
    STEP_KIND_ARTIFACTS_RENDER: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_DELETE_RENDER,
        evidence_kind=EVIDENCE_ARTIFACT_ID,
        description="render an existing artifact to a requested format (M22)",
    ),
    STEP_KIND_MAIL_ANALYZE_THREAD: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_TEXT,
        description="read a mail thread (M21 read tier)",
    ),
    STEP_KIND_MAIL_DRAFT: StepKindProfile(
        risk_class=RISK_MUTATE_EXTERNAL,
        compensation=COMPENSATION_DISCARD_DRAFT,
        evidence_kind=EVIDENCE_DRAFT_ID,
        description="prepare a mail draft — NEVER mail.send; the M21 gate stands",
    ),
    STEP_KIND_CALENDAR_PROPOSE: StepKindProfile(
        risk_class=RISK_MUTATE_EXTERNAL,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_PROPOSAL_ID,
        description="propose a calendar event — NEVER calendar.commit; the M21 gate stands",
    ),
    STEP_KIND_APPS_CREATE: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_STOP_PROJECT,
        evidence_kind=EVIDENCE_PROJECT_ID,
        description="scaffold an App Factory project (M23)",
    ),
    STEP_KIND_APPS_TEST: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_STOP_PROJECT,
        evidence_kind=EVIDENCE_PROJECT_ID,
        description="run an App Factory project's tests (M23)",
    ),
    STEP_KIND_SCENE_CREATE: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_DELETE_RENDER,
        evidence_kind=EVIDENCE_SCENE_ID,
        description="create a 3D scene (M25)",
    ),
    STEP_KIND_SCENE_RENDER: StepKindProfile(
        risk_class=RISK_MUTATE_LOCAL,
        compensation=COMPENSATION_DELETE_RENDER,
        evidence_kind=EVIDENCE_SCENE_ID,
        description="render a 3D scene (M25)",
    ),
    STEP_KIND_SYNTHESIS: StepKindProfile(
        risk_class=RISK_READ,
        compensation=COMPENSATION_NONE,
        evidence_kind=EVIDENCE_TEXT,
        description="the final owner-facing summary — what was made, where, what is missing",
    ),
}

STEP_KINDS: tuple[str, ...] = tuple(STEP_KIND_PROFILES.keys())


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Precondition(_StrictModel):
    check: Literal[
        "focus_exists",
        "step_done",
        "device_capability",
        "artifact_valid",
        "account_present",
        "none",
        "step_failed",
        "step_verified",
        "owner_approval",
    ] = PRECONDITION_NONE
    arg: str | None = Field(default=None, max_length=200)


class Postcondition(_StrictModel):
    evidence: Literal[
        "artifact_id",
        "document_refs",
        "research_report",
        "draft_id",
        "proposal_id",
        "project_id",
        "scene_id",
        "text",
    ]
    #: The minimum count/length the evidence must meet to verify (e.g. document_refs
    #: needs >= 1 ref, text needs >= 1 non-whitespace char). ``None`` = the evidence
    #: kind's own default minimum (app.executive.graph.DEFAULT_EVIDENCE_MIN).
    min: int | None = Field(default=None, ge=0, le=1000)


class Retry(_StrictModel):
    max_attempts: int = Field(default=1, ge=1, le=MAX_RETRY_ATTEMPTS)
    backoff_s: float = Field(default=1.0, ge=0.0, le=MAX_BACKOFF_S)
    #: Empty = never auto-retried (a step whose failure is never transient, e.g.
    #: synthesis). Every entry must be one of RETRYABLE_ERROR_CLASSES (graph.py checks).
    only_on: list[str] = Field(default_factory=list, max_length=2)


class Repeat(_StrictModel):
    #: B38 (req 555): how many times the SAME step may run, bounded, until its
    #: postcondition minimum is met; 1 = no loop.
    max_rounds: int = Field(default=1, ge=1, le=MAX_REPEAT_ROUNDS)


class Step(_StrictModel):
    id: str = Field(min_length=1, max_length=MAX_STEP_ID_CHARS)
    kind: str
    #: {output_name: "<step_id>.<output_name>" | literal value}. A reference into an
    #: EARLIER step is the only cross-step wiring a graph has (graph.py enforces the DAG
    #: and the "only earlier steps" rule — this field's own type cannot).
    inputs: dict[str, str] = Field(default_factory=dict, max_length=16)
    precondition: Precondition = Field(default_factory=Precondition)
    postcondition: Postcondition
    timeout_s: int = Field(default=60, ge=1, le=MAX_TIMEOUT_S)
    retry: Retry = Field(default_factory=Retry)
    risk_class: str
    compensation: str = COMPENSATION_NONE
    #: B38 (req 555): the bounded loop; graph.py refuses a loop with no minimum to meet.
    repeat: Repeat = Field(default_factory=Repeat)
    #: B38 (req 546): why this step is in the plan, in the owner's language.
    rationale: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _inputs_bounded(self) -> Step:
        for value in self.inputs.values():
            if len(value) > MAX_INPUT_VALUE_CHARS:
                raise ValueError(
                    f"step {self.id!r}: an input value exceeds {MAX_INPUT_VALUE_CHARS} chars"
                )
        return self


class TaskGraph(_StrictModel):
    #: The owner's own words, bounded (spec §1) — never re-derived from a paraphrase.
    goal: str = Field(min_length=1, max_length=MAX_GOAL_CHARS)
    steps: Annotated[list[Step], Field(min_length=1, max_length=MAX_STEPS)]
    #: B38: rule | model | owner - who built it; None on graphs from before B38.
    planner: str | None = Field(default=None, max_length=16)

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def canonical_json(self) -> str:
        """Deterministic JSON for the run row's ``graph_json`` — the SAME shape every
        time the same graph is built, so a retried plan does not drift from what already
        ran (mirrors ``app.creative3d.spec.ScenePlan.as_dict`` / ``ArtifactSpec``'s own
        canonical-body discipline)."""
        return self.model_dump_json(exclude_none=False)


__all__ = [
    "COMPENSATIONS",
    "COMPENSATION_DELETE_RENDER",
    "COMPENSATION_DISCARD_DRAFT",
    "COMPENSATION_NONE",
    "COMPENSATION_STOP_PROJECT",
    "EVIDENCE_ARTIFACT_ID",
    "EVIDENCE_DOCUMENT_REFS",
    "EVIDENCE_DRAFT_ID",
    "EVIDENCE_KINDS",
    "EVIDENCE_PROJECT_ID",
    "EVIDENCE_PROPOSAL_ID",
    "EVIDENCE_RESEARCH_REPORT",
    "EVIDENCE_SCENE_ID",
    "EVIDENCE_TEXT",
    "MAX_ACTIVE_RUNS",
    "MAX_BACKOFF_S",
    "MAX_GOAL_CHARS",
    "MAX_RETRY_ATTEMPTS",
    "MAX_RUN_WALL_CLOCK_S",
    "MAX_STEPS",
    "MAX_STEP_ID_CHARS",
    "MAX_TIMEOUT_S",
    "PRECONDITION_ACCOUNT_PRESENT",
    "PRECONDITION_ARTIFACT_VALID",
    "PRECONDITION_CHECKS",
    "PRECONDITION_DEVICE_CAPABILITY",
    "PRECONDITION_FOCUS_EXISTS",
    "PRECONDITION_NONE",
    "PRECONDITION_STEP_DONE",
    "Postcondition",
    "Precondition",
    "RETRYABLE_ERROR_CLASSES",
    "RETRYABLE_ERROR_DEPENDENCY_UNAVAILABLE",
    "RETRYABLE_ERROR_TIMEOUT",
    "RISK_CLASSES",
    "RISK_HIGH_RISK",
    "RISK_MUTATE_EXTERNAL",
    "RISK_MUTATE_LOCAL",
    "RISK_READ",
    "Retry",
    "STEP_KINDS",
    "STEP_KIND_APPS_CREATE",
    "STEP_KIND_APPS_TEST",
    "STEP_KIND_ARTIFACTS_CREATE",
    "STEP_KIND_ARTIFACTS_RENDER",
    "STEP_KIND_CALENDAR_PROPOSE",
    "STEP_KIND_DOCUMENTS_COMPARE",
    "STEP_KIND_DOCUMENTS_EXTRACT",
    "STEP_KIND_DOCUMENTS_FIND",
    "STEP_KIND_MAIL_ANALYZE_THREAD",
    "STEP_KIND_MAIL_DRAFT",
    "STEP_KIND_PROFILES",
    "STEP_KIND_RESEARCH_RUN",
    "STEP_KIND_RESEARCH_SYNTHESIZE",
    "STEP_KIND_SCENE_CREATE",
    "STEP_KIND_SCENE_RENDER",
    "STEP_KIND_SYNTHESIS",
    "Step",
    "StepKindProfile",
    "TaskGraph",
]
