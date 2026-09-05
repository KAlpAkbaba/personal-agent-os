"""Unit tests: the release execution orchestrator (M18 spec §5, ADR-0055 §5).

Everything here runs against fakes — :class:`FakeDeploymentBackend` and
:class:`FakeGitTreeInspector` — never against a real Hetzner host. What each
test proves:

* the full success path reaches LIVE, marks the release row ``active`` and
  supersedes the previous one;
* a preflight refusal never touches the deployment backend at all, and still
  produces an honest FAILED -> ROLLING_BACK -> ROLLED_BACK trail;
* a deploy failure and a verification failure (health OR provenance mismatch)
  both trigger a REAL call to the backend's rollback, restoring the previous
  version;
* a rollback that itself fails lands on QUARANTINED, never on a false
  ROLLED_BACK;
* the whole executor is exactly as authorised as the ``Authority`` it is
  handed: a lab-scoped authority cannot reach any production-side status
  through it, and there is no code path here that constructs an
  ``OwnerCapability`` or an ``Authority`` of its own.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution.authority import (
    Authority,
    LabAuthority,
    ProductionAuthority,
    mint_owner_capability,
)
from app.evolution.backlog import ActorKind, OpportunityStatus
from app.evolution.errors import EvolutionError
from app.evolution.models import CapabilityGap, EvolutionOpportunity
from app.evolution.service import EvolutionService, RecordingUiStatePublisher
from app.ledger.models import ActivityEventRow
from app.release.backend import FakeDeploymentBackend
from app.release.execution import DynamicReleaseEvidenceProvider, ReleaseExecutor
from app.release.preflight import FakeGitTreeInspector, ReleaseCandidate
from app.selfhealing.models import Incident, Release
from app.selfhealing.service import SelfHealingService
from tests.unit.test_evolution_authority import FakeSession

TABLES = [
    EvolutionOpportunity.__table__,
    CapabilityGap.__table__,
    ActivityEventRow.__table__,
    Incident.__table__,
    Release.__table__,
]

SCORES = {
    "owner_relevance": 0.9,
    "expected_utility": 0.8,
    "recurrence": 0.6,
    "confidence": 0.7,
    "engineering_cost": 0.3,
    "operational_risk": 0.1,
}

DIGEST_V1 = "sha256:" + "1" * 64
DIGEST_V2 = "sha256:" + "2" * 64


def make_session_factory():
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

    return session_scope


def insert_ledger_event(session_scope) -> str:
    event_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            ActivityEventRow(
                event_id=event_id,
                occurred_at=datetime.now(UTC),
                recorded_at=datetime.now(UTC),
                event_type="incident.opened",
                subsystem="cloud_core",
                status="completed",
                severity="warning",
                action="incident.opened",
                production_state="n/a",
                evidence_refs=[],
                factual_summary="test evidence",
                detail_json={},
                source="live",
                source_ref=f"test:{event_id}",
            )
        )
        session.commit()
    return str(event_id)


class Stack:
    def __init__(self) -> None:
        self.session_scope = make_session_factory()
        self.ui = RecordingUiStatePublisher()
        self.evolution = EvolutionService(
            self.session_scope, ui=self.ui, release_evidence=DynamicReleaseEvidenceProvider()
        )
        self.selfhealing = SelfHealingService(self.session_scope)

    def opportunity_ready_for_release(self, *, changed_paths=None) -> str:
        """Drive a fresh opportunity all the way to OWNER_AUTHORIZED."""
        event_id = insert_ledger_event(self.session_scope)
        opportunity = self.evolution.create_from_evidence(
            title="Test candidate",
            statement="Kanıta dayalı bir fırsat.",
            evidence_refs=[{"kind": "ledger_event", "ref": event_id}],
            scores=SCORES,
            source="ledger_event",
            source_ref=f"activity_events:{event_id}",
        )
        oid = opportunity["opportunity_id"]
        for status in (
            OpportunityStatus.RESEARCHING,
            OpportunityStatus.DESIGN_READY,
            OpportunityStatus.BUILDING,
            OpportunityStatus.TESTING,
            OpportunityStatus.EVALUATING,
            OpportunityStatus.SHADOW_READY,
        ):
            self.evolution.advance(oid, target=status, actor=ActorKind.LAB)
        self.evolution.advance(
            oid, target=OpportunityStatus.OWNER_APPROVAL_REQUIRED, actor=ActorKind.LAB
        )
        self.evolution.record_release_footprint(
            oid, changed_paths=changed_paths or ["services/api/app/goals/service.py"]
        )
        self.evolution.authorize(oid, mint_owner_capability(FakeSession()), confirm_high_risk=True)
        return oid

    def seed_active_release(self, component: str, version: str, digest: str) -> dict:
        return self.selfhealing.record_release(component, version, digest, status="active")

    def production_authority(self) -> Authority:
        return ProductionAuthority.for_owner(mint_owner_capability(FakeSession()))


GOOD_EVIDENCE = {
    "tests": {"passed": True},
    "security_review": {"acceptable": True},
    "benchmark": {"passed": True},
    "dependencies": {"unpinned": []},
}


def make_candidate(
    stack: Stack, oid: str, *, version="1.1.0", digest=DIGEST_V2, evidence=None
) -> ReleaseCandidate:
    opportunity = stack.evolution.get(oid)
    return ReleaseCandidate(
        opportunity=opportunity,
        component="cloud_core",
        version=version,
        candidate_ref="deadbeef",
        manifest_digest=digest,
        changed_paths=["services/api/app/goals/service.py"],
        evidence=evidence if evidence is not None else GOOD_EVIDENCE,
    )


@pytest.fixture()
def stack() -> Stack:
    return Stack()


def executor(stack: Stack, *, backend: FakeDeploymentBackend | None = None) -> ReleaseExecutor:
    return ReleaseExecutor(
        evolution=stack.evolution,
        selfhealing=stack.selfhealing,
        backend=backend or FakeDeploymentBackend(),
        git_tree=FakeGitTreeInspector(clean=True),
    )


# ------------------------------------------------------------------ success


def test_the_full_path_reaches_live_and_supersedes_the_previous_release(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend()
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )

    assert report.outcome == "live"
    assert report.final_status == str(OpportunityStatus.LIVE)
    assert stack.evolution.get(oid)["status"] == str(OpportunityStatus.LIVE)
    assert [name for name, _ in backend.calls] == ["deploy", "verify"]

    releases = stack.selfhealing.list_releases(component="cloud_core")
    by_version = {r["version"]: r for r in releases}
    assert by_version["1.1.0"]["status"] == "active"
    assert by_version["1.0.0"]["status"] == "superseded"


def test_the_ui_stream_carries_the_risk_tier_through_the_whole_release(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release(
        changed_paths=["services/api/app/selfhealing/service.py"]
    )
    candidate = make_candidate(stack, oid)
    executor(stack).execute(oid, candidate, production_authority=stack.production_authority())
    published = dict(stack.ui.published)
    assert published["release.deploying"]["risk_tier"] == 3
    assert published["release.live"]["risk_tier"] == 3


# ------------------------------------------------------------- preflight refusal


def test_preflight_refusal_never_touches_the_backend_and_still_rolls_back(stack: Stack) -> None:
    """No previous release exists -> rollback_point_exists refuses -> the
    executor never reaches DEPLOYING, yet still produces a full, honest
    FAILED -> ROLLING_BACK -> ROLLED_BACK trail (no silent stop)."""
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend()
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )

    assert report.outcome == "rolled_back"
    assert report.preflight["passed"] is False
    assert "rollback_point_exists" in report.preflight["failed_checks"]
    assert backend.calls == []  # never touched the backend
    assert stack.evolution.get(oid)["status"] == str(OpportunityStatus.ROLLED_BACK)
    assert stack.selfhealing.list_releases(component="cloud_core") == []  # nothing recorded


def test_a_no_op_deployment_is_refused_by_preflight(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid, version="1.1.0", digest=DIGEST_V1)  # same digest!
    report = executor(stack).execute(
        oid, candidate, production_authority=stack.production_authority()
    )
    assert "component_genuinely_needs_deploying" in report.preflight["failed_checks"]
    assert report.outcome == "rolled_back"


# ------------------------------------------------------------------- deploy fails


def test_a_deploy_failure_rolls_back_to_the_previous_version(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend(deploy_succeeds=False)
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )

    assert report.outcome == "rolled_back"
    assert report.deploy["succeeded"] is False
    assert [name for name, _ in backend.calls] == ["deploy", "rollback"]
    rollback_call = next(args for name, args in backend.calls if name == "rollback")
    assert rollback_call["target_version"] == "1.0.0"
    assert stack.evolution.get(oid)["status"] == str(OpportunityStatus.ROLLED_BACK)

    releases = {r["version"]: r for r in stack.selfhealing.list_releases(component="cloud_core")}
    assert releases["1.1.0"]["status"] == "rejected"
    assert releases["1.0.0"]["status"] == "active"  # never touched


# ---------------------------------------------------------------- verify fails


def test_an_unhealthy_verification_rolls_back(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend(verify_healthy=False)
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )
    assert report.outcome == "rolled_back"
    assert report.verify["healthy"] is False
    assert [name for name, _ in backend.calls] == ["deploy", "verify", "rollback"]


def test_a_provenance_mismatch_rolls_back_even_though_the_service_is_healthy(stack: Stack) -> None:
    """The ADR-0053 lesson, proven: healthy alone is not enough. The running
    service reporting a digest that does not match the INSTALLED release is
    exactly as fatal as an unhealthy check."""
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend(verify_healthy=True, verify_digest_matches=False)
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )
    assert report.verify["healthy"] is True
    assert report.verify["provenance_ok"] is False
    assert report.verify["passed"] is False
    assert report.outcome == "rolled_back"

    releases = {r["version"]: r for r in stack.selfhealing.list_releases(component="cloud_core")}
    assert releases["1.1.0"]["status"] == "rolled_back"
    assert releases["1.0.0"]["status"] == "active"


def test_provenance_compares_runtime_to_installed_never_to_source_tree(stack: Stack) -> None:
    """A clean source tree (FakeGitTreeInspector(clean=True)) proves nothing
    about what is running; only a real digest match does."""
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend(verify_digest_matches=False)
    # Tree is clean, yet the release still fails - proving the check is NOT a
    # proxy for "is the working tree clean".
    report = executor(
        stack, backend=backend
    ).execute(oid, candidate, production_authority=stack.production_authority())
    assert report.preflight["checks"][2]["name"] == "source_committed_and_clean"
    assert report.preflight["checks"][2]["passed"] is True
    assert report.outcome == "rolled_back"


# --------------------------------------------------------- rollback itself fails


def test_a_failed_rollback_quarantines_rather_than_lying_about_recovery(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    backend = FakeDeploymentBackend(deploy_succeeds=False, rollback_succeeds=False)
    report = executor(stack, backend=backend).execute(
        oid, candidate, production_authority=stack.production_authority()
    )
    assert report.outcome == "quarantined"
    assert report.rollback["succeeded"] is False
    assert stack.evolution.get(oid)["status"] == str(OpportunityStatus.QUARANTINED)


# --------------------------------------------------------------- authority boundary


def test_the_executor_refuses_a_lab_scoped_authority_at_every_step(stack: Stack) -> None:
    """The orchestrator holds no authority of its own: it is exactly as
    authorised as the Authority object it is handed. A LabAuthority object
    passed as `production_authority` must be refused, same as calling
    EvolutionService.advance() directly with one."""
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    with pytest.raises(EvolutionError):
        executor(stack).execute(
            oid, candidate, production_authority=LabAuthority.issue("evolution.engine")
        )
    # Nothing moved: still sitting at OWNER_AUTHORIZED.
    assert stack.evolution.get(oid)["status"] == str(OpportunityStatus.OWNER_AUTHORIZED)


def test_the_executor_refuses_with_no_authority_at_all(stack: Stack) -> None:
    stack.seed_active_release("cloud_core", "1.0.0", DIGEST_V1)
    oid = stack.opportunity_ready_for_release()
    candidate = make_candidate(stack, oid)
    with pytest.raises(EvolutionError):
        executor(stack).execute(oid, candidate, production_authority=None)  # type: ignore[arg-type]


def test_the_executor_requires_a_dynamic_release_evidence_provider(stack: Stack) -> None:
    """A misconfigured EvolutionService (any other release-evidence provider)
    is refused at construction, not discovered mid-release."""
    from app.evolution.backlog import NullReleaseEvidenceProvider

    misconfigured = EvolutionService(
        stack.session_scope, release_evidence=NullReleaseEvidenceProvider()
    )
    with pytest.raises(TypeError):
        ReleaseExecutor(
            evolution=misconfigured,
            selfhealing=stack.selfhealing,
            backend=FakeDeploymentBackend(),
            git_tree=FakeGitTreeInspector(),
        )


def test_evolution_generated_code_still_cannot_construct_an_owner_capability() -> None:
    """Regression: nothing in app.release (this whole execution path) imports
    or re-implements a way to mint an OwnerCapability or a production
    Authority. The only constructors are in app.evolution.authority, and they
    require a real, verified owner SessionContext this package never has."""
    import ast
    from pathlib import Path

    release_dir = Path(__file__).resolve().parents[2] / "app" / "release"
    forbidden_calls = {"OwnerCapability", "_CAPABILITY_MINT", "_AUTHORITY_MINT"}
    offenders: list[str] = []
    for source_file in release_dir.glob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_calls:
                offenders.append(f"{source_file.name}: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
                offenders.append(f"{source_file.name}: .{node.attr}")
    assert offenders == []
