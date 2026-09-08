"""``GenesisService`` — the state machine driving one ``GenesisRun`` end to end
(M24_CAPABILITY_GENESIS_SPEC.md §5, ADR-0087).

Reuses the M7 pipeline's INDIVIDUAL, security-critical pieces verbatim rather
than re-implementing them: ``SandboxPolicy`` (the isolated workspace),
``SkillEvaluator`` (the generated tests/evals really run, scored per §9),
``IndependentSkillReviewer`` (re-runs everything itself, deny-by-default on
permission grants), ``ShadowRunner``/``CanaryRunner`` (the rollout
comparison), ``CapabilityRegistry`` (the ONLY gate onto ``production``),
``CapabilityDispatcher`` (the ONLY runtime entry to generated code) and
``app.evolution.lifecycle`` (candidate -> sandbox -> validated -> shadow ->
canary -> active, with its own required-evidence checks). The ONE thing NOT
reused verbatim is ``EvolutionPipeline.run()`` itself: that orchestrator is
built around ``SkillSpec``/``DeterministicSkillGenerator``, whose ``operation``
field is a closed allowlist of pure string transforms (``slugify``,
``word_count`` …) — incompatible by construction with an operation id derived
from a researched interface. This module is the
SAME orchestration, stage for stage, driven by ``AdapterSpec`` /
``HttpAdapterGenerator`` instead.

State machine (spec §5)::

    capability_missing -> researching -> designing -> building -> testing
        -> classifying -> (awaiting_approval ->) rolling_out -> registering
        -> available -> used -> verified
    (any state) -> failed | cancelled

Every transition is a row update (this module) AND a ledger row
(``genesis.<state>``) AND a UiState publish (``capability.genesis``) — the
three-way discipline M18.4 §15/§16 established, mirrored here in
``_transition``.

Authority (spec §5, §9): a MUTATING operation against an interface with no
recorded owner authorization parks at ``awaiting_approval``; ``approve()``
resumes it ONLY on a ``Confirmation`` the ONE router recorded for this
session + turn (``app.actions.confirmation_gate``, reused via
``check_gate`` — never the model's own argument). A READ operation never
asks: its network reach is authorized by the mere fact that the owner's own
request caused this bounded interface to be researched (a
``StaticAuthorizationProvider`` scoped to exactly that host, constructed
per run — never a blanket network grant).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.confirmation_gate import Confirmation, check_gate
from app.evolution import lifecycle as lifecycle_module
from app.evolution.authorization import AuthorizationProvider, StaticAuthorizationProvider
from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.evaluation import SkillEvaluator
from app.evolution.gaps import CapabilityRequest, GapDetector, GapService
from app.evolution.registry import CapabilityRegistry
from app.evolution.resources import ResourceBudget
from app.evolution.review import IndependentSkillReviewer
from app.evolution.rollout import CanaryRunner, ShadowRunner
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import SkillLayout, dump_manifest_yaml, read_manifest
from app.evolution.supply_chain import scan_dependencies
from app.evolution.task_resumption import CapabilityDispatcher, DispatchResult
from app.genesis.adapter import AdapterSpec, HttpAdapterGenerator
from app.genesis.interface import InterfaceDescription, fetch_interface
from app.genesis.models import GenesisRun
from app.ledger import service as ledger_service
from app.ledger.vocabulary import GENESIS_EVENT_TYPE_BY_STATE, SUBSYSTEM_GENESIS
from app.logging import get_logger
from app.selfhealing.service import compute_manifest_digest
from app.uistate.contract import UiState
from app.uistate.publisher import publish as publish_ui_state

logger = get_logger("app.genesis.service")

SessionFactory = Callable[[], AbstractContextManager[Session]]

#: spec §5 bounds.
MAX_RUN_DURATION_S = 600.0
MAX_RUNS_PER_HOUR_PER_INTERFACE = 3
RATE_WINDOW = timedelta(hours=1)

#: Blocks a second run for the same capability while one of these holds.
ACTIVE_RUN_STATES = frozenset(
    {
        "capability_missing",
        "researching",
        "designing",
        "building",
        "testing",
        "classifying",
        "awaiting_approval",
        "rolling_out",
        "registering",
        "available",
    }
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _BuiltCapability:
    """What building+registering ONE operation produced."""

    capability_id: str
    skill_version_id: uuid.UUID
    layout: SkillLayout
    manifest: dict[str, Any]


class GenesisService:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        registry: CapabilityRegistry,
        gaps: GapService,
        detector: GapDetector,
        sandbox: SandboxPolicy,
        skills_root: Path,
        dispatcher: CapabilityDispatcher,
        mutation_authorization: AuthorizationProvider,
        evaluator: SkillEvaluator | None = None,
        reviewer_sandbox: SandboxPolicy | None = None,
        budget: ResourceBudget | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.registry = registry
        # spec §5 "capability_missing": the gap is recorded through the SAME
        # M7 decision tree every other missing-capability request uses —
        # composition genuinely attempted (and reported insufficient) before
        # anything is built, reused rather than reimplemented.
        self.gaps = gaps
        self.detector = detector
        self.sandbox = sandbox
        self.skills_root = Path(skills_root)
        self.dispatcher = dispatcher
        # The REAL owner-authorization source for MUTATING operations (spec
        # §5/§9): an asset ref (the interface's own name) verifies only when
        # the owner recorded it — app.security.provider.RegistryAuthorizationProvider
        # in production, a StaticAuthorizationProvider or NullAuthorizationProvider
        # in tests. Never confused with the SEPARATE, always-granted network-reach
        # authorization built fresh per run below.
        self.mutation_authorization = mutation_authorization
        self.evaluator = evaluator or SkillEvaluator()
        self.reviewer_sandbox = reviewer_sandbox or sandbox
        self.budget = budget or ResourceBudget()

    # ------------------------------------------------------------- request

    def request(
        self,
        *,
        interface_name: str,
        interface_url: str,
        operation_id: str,
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        turn: int | None = None,
    ) -> dict[str, Any]:
        """Drives ONE ``GenesisRun`` synchronously from ``capability_missing``
        through to ``verified``/``awaiting_approval``/``failed``.

        Idempotent in the sense spec §7 requires: a capability that already
        resolves is used directly (no new run); an ACTIVE run for the same
        capability returns that run's own status (no second run).
        """
        arguments = dict(arguments or {})
        capability_id = f"{interface_name}.{operation_id}"

        existing = self.registry.resolve(capability_id)
        if existing is not None:
            return self._use_and_verify_existing(capability_id, arguments, session_id=session_id)

        active = self._find_active_run(capability_id)
        if active is not None:
            return self._run_dict(active)

        self._enforce_rate_bound(interface_name)

        gap_id = self._record_gap(capability_id, operation_id, interface_name, interface_url)
        run = self._create_run(
            capability_id=capability_id,
            operation_id=operation_id,
            interface_name=interface_name,
            interface_url=interface_url,
            session_id=session_id,
            gap_id=gap_id,
        )
        return self._drive(run, arguments, session_id=session_id, turn=turn)

    def _record_gap(
        self, capability_id: str, operation_id: str, interface_name: str, interface_url: str
    ) -> uuid.UUID:
        """spec §5 ``capability_missing``: composition really attempted first,
        through the SAME M7 decision tree (``GapDetector``) every other
        missing-capability request uses — reused, not reimplemented. Required
        inputs/outputs are unknown before research, so composition's own
        "no required outputs" branch is the honest evidence recorded (still a
        REAL composer run against live registry state, never fabricated)."""
        request = CapabilityRequest.parse(
            {
                "requested_capability": capability_id,
                "request_text": (
                    f"capability genesis: {operation_id} on {interface_name} ({interface_url})"
                )[:4000],
            }
        )
        decision = self.detector.detect(request)
        gap = self.gaps.record(request, decision)
        return uuid.UUID(gap["id"])

    # -------------------------------------------------------------- drive

    def _drive(
        self,
        run: GenesisRun,
        arguments: dict[str, Any],
        *,
        session_id: str | None,
        turn: int | None,
    ) -> dict[str, Any]:
        started = _utcnow()
        try:
            interface = self._research(run)
            spec = self._design(run, interface)
            layout, skill_version_id = self._build(run, spec, started)
            self._test(run, layout, skill_version_id, spec, started)
            authority_class, side_effect_class, mutation_authorized = self._classify(run, spec)
        except EvolutionError as exc:
            return self._fail(run, exc)

        if side_effect_class == "mutate_external" and not mutation_authorized:
            return self._park_awaiting_approval(run, arguments, session_id=session_id, turn=turn)
        if mutation_authorized:
            # _classify ran AFTER _design built `spec`, so the manifest
            # AdapterSpec.capability_manifest() will build still has
            # authorized_asset=None unless rebuilt here — an immutable
            # dataclass, so a fresh instance rather than a mutation.
            spec = AdapterSpec(
                interface=spec.interface,
                operation_id=spec.operation_id,
                version=spec.version,
                authorized_asset=interface.name,
            )

        try:
            self._roll_out(run, layout, skill_version_id, started)
            built = self._register(run, spec, layout, skill_version_id)
            self._mark_available(run, built)
            self._maybe_register_read_back(run, spec, interface, started)
            dispatched = self._use(run, built.capability_id, arguments)
            self._verify(run, spec, interface, dispatched)
        except EvolutionError as exc:
            return self._fail(run, exc)
        finally:
            self._cleanup_workspace(run)
        return self._run_dict(run)

    # ------------------------------------------------------------- stages

    def _research(self, run: GenesisRun) -> InterfaceDescription:
        self._transition(run, "researching")
        interface = fetch_interface(run.evidence_json["interface_url"])
        self._patch_evidence(run, {"interface": interface.to_dict()})
        run.interface_json = interface.to_dict()
        self._commit(run)
        return interface

    def _design(self, run: GenesisRun, interface: InterfaceDescription) -> AdapterSpec:
        self._transition(run, "designing")
        spec = AdapterSpec(interface=interface, operation_id=run.operation_id)
        return spec

    def _build(
        self, run: GenesisRun, spec: AdapterSpec, started: datetime
    ) -> tuple[SkillLayout, uuid.UUID]:
        self._enforce_time_bound(started)
        self._transition(run, "building")
        self.sandbox.prepare()
        work_dir = Path(tempfile.mkdtemp(prefix="genesis-", dir=str(self.sandbox.root)))
        self.sandbox.ensure_within(work_dir, label="genesis workspace")
        generator = HttpAdapterGenerator()
        layout = generator.generate(spec, work_dir)
        self.sandbox.ensure_within(layout.root, label="generated adapter root")
        missing = layout.missing_paths()
        if missing:
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED,
                f"genesis adapter is incomplete; missing {missing}",
            )
        version_row = self.registry.create_skill_version(
            spec.capability_id, spec.version, status="draft"
        )
        skill_version_id = uuid.UUID(version_row["id"])
        digest = compute_manifest_digest(layout.root)
        self.registry.record_build(
            skill_version_id, source_ref=str(layout.root), manifest_digest=digest
        )
        self.registry.advance_lifecycle(
            skill_version_id,
            lifecycle_module.STAGE_SANDBOX,
            {"workspace": work_dir.name, "manifest_digest": digest},
        )
        self._patch_evidence(
            run,
            {
                "workspace_root": str(layout.root),
                "skill_name": layout.skill_name,
                "version": layout.version,
                "skill_version_id": str(skill_version_id),
            },
        )
        return layout, skill_version_id

    def _test(
        self,
        run: GenesisRun,
        layout: SkillLayout,
        skill_version_id: uuid.UUID,
        spec: AdapterSpec,
        started: datetime,
    ) -> None:
        self._enforce_time_bound(started)
        self._transition(run, "testing")
        manifest = read_manifest(layout)
        supply = scan_dependencies(
            list(manifest.get("dependencies") or []) + list(manifest.get("components") or [])
        )
        if not supply.ok:
            self.registry.reject_skill_version(
                skill_version_id,
                "supply chain rejected: " + ", ".join(sorted({f.rule for f in supply.findings})),
            )
            raise EvolutionError(
                EvolutionErrorClass.SUPPLY_CHAIN_REJECTED, "genesis adapter supply chain rejected"
            )
        evaluation = self.evaluator.evaluate(layout)
        self.registry.record_evaluation(skill_version_id, evaluation.to_dict())
        if not evaluation.passed:
            self.registry.reject_skill_version(
                skill_version_id, "release gates failed: " + ", ".join(evaluation.failed_gates)
            )
            raise EvolutionError(
                self._classify_evaluation_failure(evaluation),
                "genesis adapter failed its release gates: " + ", ".join(evaluation.failed_gates),
            )
        self._patch_evidence(run, {"evaluation": evaluation.score.to_dict()})

    @staticmethod
    def _classify_evaluation_failure(evaluation: Any) -> EvolutionErrorClass:
        """spec §7's failure matrix: the app down -> ``dependency_unavailable``,
        an off-schema response -> ``postcondition_failed``. The generated
        test/eval scripts embed the ADAPTER's OWN ``AdapterError.error_class``
        verbatim in every failure string (``adapter.py``'s rendered
        ``_render_tests``/``_render_evals``), so the taxonomy the adapter
        itself observed survives into what the evaluator reports."""
        blob = " ".join(
            [
                *(evaluation.tests.get("failures") or []),
                *(str(c) for c in (evaluation.evals.get("failed_cases") or [])),
            ]
        )
        if "postcondition_failed" in blob:
            return EvolutionErrorClass.POSTCONDITION_FAILED
        if "dependency_unavailable" in blob or "generated_tests_failed" in evaluation.failed_gates:
            return EvolutionErrorClass.DEPENDENCY_UNAVAILABLE
        return EvolutionErrorClass.EVALUATION_FAILED

    def _classify(self, run: GenesisRun, spec: AdapterSpec) -> tuple[str, str, bool]:
        self._transition(run, "classifying")
        op = spec.operation
        side_effect_class = "read" if op.side_effect == "read" else "mutate_external"
        mutation_authorized = False
        if side_effect_class == "mutate_external":
            verified = self.mutation_authorization.verify(spec.interface.name)
            mutation_authorized = verified is not None
        authority_class = (
            "read_only"
            if side_effect_class == "read"
            else ("mutating_authorized_asset" if mutation_authorized else "mutating_unauthorized")
        )
        run.authority_class = authority_class
        run.side_effect_class = side_effect_class
        self._commit(run)
        return authority_class, side_effect_class, mutation_authorized

    def _park_awaiting_approval(
        self,
        run: GenesisRun,
        arguments: dict[str, Any],
        *,
        session_id: str | None,
        turn: int | None,
    ) -> dict[str, Any]:
        run.approval_required = True
        self._patch_evidence(
            run,
            {
                "pending_arguments": arguments,
                "awaiting_session_id": session_id,
                "awaiting_turn": turn,
            },
        )
        self._transition(run, "awaiting_approval")
        return self._run_dict(run)

    def _roll_out(
        self, run: GenesisRun, layout: SkillLayout, skill_version_id: uuid.UUID, started: datetime
    ) -> None:
        """The run's OWN transition into ``rolling_out``, then the reusable
        review/shadow/canary stages (``_rollout_stages``) — split so the
        internal read_back sub-build (``_maybe_register_read_back``) can reuse
        the stages WITHOUT re-entering the primary run's own state column."""
        self._enforce_time_bound(started)
        self._transition(run, "rolling_out")
        self._rollout_stages(layout, skill_version_id)

    def _rollout_stages(self, layout: SkillLayout, skill_version_id: uuid.UUID) -> None:
        manifest = read_manifest(layout)
        auth = StaticAuthorizationProvider(
            {
                manifest["external_services"][0]: {
                    "network_permissions": manifest["network_permissions"]
                }
            }
        )
        reviewer = IndependentSkillReviewer(
            evaluator=self.evaluator, sandbox=self.reviewer_sandbox, authorization=auth
        )
        review, payload = reviewer.review_record(layout)
        self.registry.record_review(skill_version_id, payload)
        if not review.approved:
            self.registry.reject_skill_version(
                skill_version_id, f"independent review: {review.summary}"
            )
            raise EvolutionError(
                EvolutionErrorClass.REVIEW_REJECTED, f"genesis adapter review: {review.summary}"
            )
        self.registry.advance_lifecycle(
            skill_version_id,
            lifecycle_module.STAGE_VALIDATED,
            {"evaluation_passed": True, "review_approved": True, "supply_chain_ok": True},
        )
        incumbent = self._incumbent_layout(layout.capability_id)
        shadow_report = ShadowRunner(evaluator=self.evaluator).run(layout, incumbent)
        if shadow_report.mismatches:
            self.registry.reject_skill_version(
                skill_version_id, f"shadow recorded {shadow_report.mismatches} mismatches"
            )
            raise EvolutionError(
                EvolutionErrorClass.EVALUATION_FAILED, "genesis adapter failed its shadow replay"
            )
        self.registry.advance_lifecycle(
            skill_version_id,
            lifecycle_module.STAGE_SHADOW,
            {"samples": shadow_report.candidate.samples, "mismatches": shadow_report.mismatches},
        )
        canary_report = CanaryRunner(evaluator=self.evaluator).run(layout, incumbent)
        if not canary_report.not_worse_than_incumbent:
            self.registry.reject_skill_version(
                skill_version_id, "canary performed worse than the incumbent"
            )
            raise EvolutionError(
                EvolutionErrorClass.NOT_SUPERIOR, "genesis adapter canary regressed"
            )
        self.registry.advance_lifecycle(
            skill_version_id,
            lifecycle_module.STAGE_CANARY,
            {
                "samples": canary_report.candidate.samples,
                "not_worse_than_incumbent": True,
            },
        )

    def _register(
        self, run: GenesisRun, spec: AdapterSpec, layout: SkillLayout, skill_version_id: uuid.UUID
    ) -> _BuiltCapability:
        """The run's OWN transition into ``registering``, then the reusable
        publish+register step (``_publish_and_register``) — see ``_roll_out``'s
        docstring for why this split exists."""
        self._transition(run, "registering")
        return self._publish_and_register(run, spec, layout, skill_version_id)

    def _publish_and_register(
        self, run: GenesisRun, spec: AdapterSpec, layout: SkillLayout, skill_version_id: uuid.UUID
    ) -> _BuiltCapability:
        published = self._publish(layout)
        manifest = spec.capability_manifest()
        manifest["source_ref"] = str(published)
        manifest["provenance"] = {
            "generator": HttpAdapterGenerator.name,
            "published_at": _utcnow().isoformat(),
            "genesis_run_id": str(run.id),
        }
        # ``spec`` may have been rebuilt with ``authorized_asset`` set AFTER
        # ``_build`` already wrote the ORIGINAL manifest.yaml to
        # ``layout.root`` (classification happens after building — spec §5's
        # own stage order); ``CapabilityDispatcher`` reads authority_class
        # from the manifest.yaml FILE, not the registry row, so the published
        # copy must carry the FINAL manifest, not the stale generated one, or
        # a just-approved mutation would refuse to dispatch against its own
        # freshly-authorized manifest.
        (published / "manifest.yaml").write_text(dump_manifest_yaml(manifest), encoding="utf-8")
        digest = compute_manifest_digest(published)
        manifest["provenance"]["published_digest"] = digest
        self.registry.record_source_ref(
            skill_version_id, source_ref=str(published), manifest_digest=digest
        )
        registered = self.registry.register(spec.capability_id, skill_version_id, manifest)
        return _BuiltCapability(
            capability_id=spec.capability_id,
            skill_version_id=skill_version_id,
            layout=SkillLayout(
                root=published,
                skill_name=layout.skill_name,
                capability_id=spec.capability_id,
                version=layout.version,
            ),
            manifest=registered.get("manifest", manifest),
        )

    def _mark_available(self, run: GenesisRun, built: _BuiltCapability) -> None:
        run.skill_version_id = built.skill_version_id
        self._transition(run, "available")
        if run.gap_id is not None:
            try:
                self.gaps.mark_resolved(
                    run.gap_id, resolution="generation", skill_version_id=built.skill_version_id
                )
            except EvolutionError:
                pass

    def _maybe_register_read_back(
        self,
        run: GenesisRun,
        spec: AdapterSpec,
        interface: InterfaceDescription,
        started: datetime,
    ) -> None:
        """The read_back operation must be dispatchable for ``verified`` to
        mean anything. If it is not the SAME capability just registered and it
        is not registered yet, build+register it too (always read_only, so it
        never pauses for approval) — a bounded, non-recursive internal step
        (this never calls ``request()``, so it can never spawn a genesis run
        of its own; spec §9's "a genesis run may not trigger another")."""
        self._enforce_time_bound(started)
        read_back_id = interface.evidence["read_back"]
        if read_back_id == spec.operation_id:
            return
        read_back_capability_id = f"{interface.name}.{read_back_id}"
        if self.registry.resolve(read_back_capability_id) is not None:
            return
        read_back_spec = AdapterSpec(interface=interface, operation_id=read_back_id)
        self.sandbox.prepare()
        work_dir = Path(tempfile.mkdtemp(prefix="genesis-readback-", dir=str(self.sandbox.root)))
        try:
            self.sandbox.ensure_within(work_dir, label="genesis read_back workspace")
            layout = HttpAdapterGenerator().generate(read_back_spec, work_dir)
            version_row = self.registry.create_skill_version(
                read_back_spec.capability_id, read_back_spec.version, status="draft"
            )
            skill_version_id = uuid.UUID(version_row["id"])
            digest = compute_manifest_digest(layout.root)
            self.registry.record_build(
                skill_version_id, source_ref=str(layout.root), manifest_digest=digest
            )
            self.registry.advance_lifecycle(
                skill_version_id,
                lifecycle_module.STAGE_SANDBOX,
                {"workspace": work_dir.name, "manifest_digest": digest},
            )
            evaluation = self.evaluator.evaluate(layout)
            self.registry.record_evaluation(skill_version_id, evaluation.to_dict())
            if not evaluation.passed:
                self.registry.reject_skill_version(
                    skill_version_id, "release gates failed: " + ", ".join(evaluation.failed_gates)
                )
                raise EvolutionError(
                    EvolutionErrorClass.EVALUATION_FAILED,
                    "genesis read_back adapter failed its release gates",
                )
            self._rollout_stages(layout, skill_version_id)
            self._publish_and_register(run, read_back_spec, layout, skill_version_id)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _use(
        self, run: GenesisRun, capability_id: str, arguments: dict[str, Any]
    ) -> DispatchResult:
        self._transition(run, "used")
        dispatched = self.dispatcher.dispatch(capability_id, arguments)
        self._patch_evidence(run, {"dispatch": dispatched.to_dict()})
        return dispatched

    def _verify(
        self,
        run: GenesisRun,
        spec: AdapterSpec,
        interface: InterfaceDescription,
        dispatched: DispatchResult,
    ) -> None:
        self._transition(run, "verified")
        op = spec.operation
        read_back_id = interface.evidence["read_back"]
        if read_back_id == op.id:
            read_back_output = dispatched.output
        else:
            read_back_capability_id = f"{interface.name}.{read_back_id}"
            read_back_output = self.dispatcher.dispatch(read_back_capability_id, {}).output
        shared_fields = set(dispatched.output) & set(read_back_output)
        mismatched = {
            name: (dispatched.output[name], read_back_output[name])
            for name in shared_fields
            if dispatched.output[name] != read_back_output[name]
        }
        if mismatched:
            raise EvolutionError(
                EvolutionErrorClass.POSTCONDITION_FAILED,
                "genesis read-back did not match the mutation's own reported result",
                details={"mismatched": mismatched},
            )
        self._patch_evidence(run, {"read_back": read_back_output})

    def _use_and_verify_existing(
        self, capability_id: str, arguments: dict[str, Any], *, session_id: str | None
    ) -> dict[str, Any]:
        dispatched = self.dispatcher.dispatch(capability_id, arguments)
        return {
            "capability_id": capability_id,
            "state": "verified",
            "new_run": False,
            "output": dispatched.output,
        }

    # ------------------------------------------------------------- approve

    def approve(self, run_id: uuid.UUID, confirmation: Confirmation) -> dict[str, Any]:
        run = self._require(run_id)
        if run.state != "awaiting_approval":
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"genesis run {run_id} is {run.state!r}; only awaiting_approval may be approved",
            )
        evidence = run.evidence_json or {}
        gate = check_gate(
            state="awaiting_approval",
            prepared_state="__never__",
            read_back_state="awaiting_approval",
            read_back_at=run.updated_at,
            read_back_session_id=evidence.get("awaiting_session_id"),
            read_back_turn=evidence.get("awaiting_turn"),
            confirmation=confirmation,
            host_flag_enabled=True,
            provider_available=True,
        )
        if not gate.ok:
            raise EvolutionError(
                EvolutionErrorClass.PERMISSION_DENIED,
                f"genesis approval refused: {gate.reason}",
                details={"reason": gate.reason},
            )
        run.approval_ref = confirmation.session_id
        interface = self._reload_interface(run)
        # Reaching here means approval was just granted: the manifest built
        # below must carry authority_class=mutating_authorized_asset, never
        # the AdapterSpec default (mutating_unauthorized) — see the matching
        # comment in _drive.
        spec = AdapterSpec(
            interface=interface, operation_id=run.operation_id, authorized_asset=interface.name
        )
        layout = SkillLayout(
            root=Path(evidence["workspace_root"]),
            skill_name=evidence["skill_name"],
            capability_id=run.capability_id,
            version=evidence["version"],
        )
        skill_version_id = uuid.UUID(evidence["skill_version_id"])
        run.authority_class = "mutating_authorized_asset"
        arguments = dict(evidence.get("pending_arguments") or {})
        started = _utcnow()
        try:
            self._roll_out(run, layout, skill_version_id, started)
            built = self._register(run, spec, layout, skill_version_id)
            self._mark_available(run, built)
            self._maybe_register_read_back(run, spec, interface, started)
            dispatched = self._use(run, built.capability_id, arguments)
            self._verify(run, spec, interface, dispatched)
        except EvolutionError as exc:
            return self._fail(run, exc)
        finally:
            self._cleanup_workspace(run)
        return self._run_dict(run)

    def cancel(self, run_id: uuid.UUID) -> dict[str, Any]:
        run = self._require(run_id)
        if run.state in ("used", "verified", "failed", "cancelled"):
            return self._run_dict(run)
        evidence = run.evidence_json or {}
        skill_version_id = evidence.get("skill_version_id")
        if skill_version_id:
            try:
                self.registry.reject_skill_version(
                    uuid.UUID(skill_version_id), "cancelled by owner"
                )
            except EvolutionError:
                pass
        self._transition(run, "cancelled")
        self._cleanup_workspace(run)
        return self._run_dict(run)

    # -------------------------------------------------------------- reads

    def status(
        self, *, capability_id: str | None = None, run_id: uuid.UUID | None = None
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            if run_id is not None:
                row = session.get(GenesisRun, run_id)
                return _run_dict(row) if row is not None else None
            if capability_id is not None:
                row = session.execute(
                    select(GenesisRun)
                    .where(GenesisRun.capability_id == capability_id)
                    .order_by(GenesisRun.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                return _run_dict(row) if row is not None else None
        return None

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(GenesisRun).order_by(GenesisRun.created_at.desc()).limit(limit)
                )
                .scalars()
                .all()
            )
            return [_run_dict(r) for r in rows]

    def get(self, run_id: uuid.UUID) -> dict[str, Any]:
        return self._run_dict(self._require(run_id))

    # ---------------------------------------------------------------- bounds

    def _find_active_run(self, capability_id: str) -> GenesisRun | None:
        with self._session_factory() as session:
            return session.execute(
                select(GenesisRun)
                .where(GenesisRun.capability_id == capability_id)
                .where(GenesisRun.state.in_(ACTIVE_RUN_STATES))
                .order_by(GenesisRun.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

    def _enforce_rate_bound(self, interface_name: str) -> None:
        cutoff = _utcnow() - RATE_WINDOW
        with self._session_factory() as session:
            rows = (
                session.execute(select(GenesisRun).where(GenesisRun.created_at >= cutoff))
                .scalars()
                .all()
            )
        recent = [r for r in rows if (r.interface_json or {}).get("name") == interface_name]
        if len(recent) >= MAX_RUNS_PER_HOUR_PER_INTERFACE:
            raise EvolutionError(
                EvolutionErrorClass.RATE_LIMITED,
                f"more than {MAX_RUNS_PER_HOUR_PER_INTERFACE} genesis runs against "
                f"{interface_name!r} in the last hour",
            )

    def _enforce_time_bound(self, started: datetime) -> None:
        if (_utcnow() - started).total_seconds() > MAX_RUN_DURATION_S:
            raise EvolutionError(
                EvolutionErrorClass.RATE_LIMITED,
                f"genesis run exceeded the {MAX_RUN_DURATION_S:.0f}s bound",
            )

    # --------------------------------------------------------------- pieces

    def _reload_interface(self, run: GenesisRun) -> InterfaceDescription:
        """Reconstruct the ``InterfaceDescription`` ``_research`` stored on the
        row (``interface.to_dict()``) — ``source`` is carried separately from
        ``parse()``'s own closed-key check, never spliced back into the raw
        body it produced."""
        stored = dict(run.interface_json or {})
        source = stored.pop("source", None) or {"kind": "inline"}
        return InterfaceDescription.parse(stored, source=source)

    def _incumbent_layout(self, capability_id: str) -> SkillLayout | None:
        resolved = self.registry.resolve(capability_id)
        if resolved is None:
            return None
        source_ref = (resolved.get("skill_version") or {}).get("source_ref")
        manifest = resolved.get("manifest") or {}
        skill_name = manifest.get("skill")
        if not source_ref or not skill_name or not Path(source_ref).is_dir():
            return None
        return SkillLayout(
            root=Path(source_ref),
            skill_name=skill_name,
            capability_id=capability_id,
            version=resolved["version"],
        )

    def _publish(self, layout: SkillLayout) -> Path:
        target = self.skills_root / layout.skill_name / layout.version
        if target.exists():
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"published skill {layout.skill_name}/{layout.version} already exists",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(layout.root, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return target

    def _cleanup_workspace(self, run: GenesisRun) -> None:
        root = (run.evidence_json or {}).get("workspace_root")
        if not root:
            return
        try:
            self.sandbox.ensure_within(Path(root), label="genesis workspace cleanup")
        except EvolutionError:
            return
        shutil.rmtree(Path(root).parent, ignore_errors=True)

    # ----------------------------------------------------------- bookkeeping

    def _create_run(
        self,
        *,
        capability_id: str,
        operation_id: str,
        interface_name: str,
        interface_url: str,
        session_id: str | None,
        gap_id: uuid.UUID | None = None,
    ) -> GenesisRun:
        with self._session_factory() as session:
            row = GenesisRun(
                capability_id=capability_id,
                operation_id=operation_id,
                gap_id=gap_id,
                state="capability_missing",
                interface_json={"name": interface_name},
                evidence_json={"interface_url": interface_url, "interface_name": interface_name},
                session_id=session_id,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            self._ledger(row, "capability_missing")
            self._publish_ui_state(row)
            return row

    def _require(self, run_id: uuid.UUID) -> GenesisRun:
        with self._session_factory() as session:
            row = session.get(GenesisRun, run_id)
            if row is None:
                raise EvolutionError(
                    EvolutionErrorClass.NOT_FOUND, f"genesis run {run_id} not found"
                )
            return row

    def _transition(self, run: GenesisRun, state: str) -> None:
        run.state = state
        run.updated_at = _utcnow()
        self._commit(run)
        self._ledger(run, state)
        self._publish_ui_state(run)
        logger.info("genesis_transition", run_id=str(run.id), state=state)

    def _fail(self, run: GenesisRun, exc: EvolutionError) -> dict[str, Any]:
        run.error_class = str(exc.error_class)[:32]
        run.error_message = exc.message[:512]
        self._transition(run, "failed")
        self._cleanup_workspace(run)
        logger.info(
            "genesis_run_failed",
            run_id=str(run.id),
            capability_id=run.capability_id,
            error_class=str(exc.error_class),
        )
        return self._run_dict(run)

    def _patch_evidence(self, run: GenesisRun, patch: dict[str, Any]) -> None:
        evidence = dict(run.evidence_json or {})
        evidence.update(patch)
        run.evidence_json = evidence
        self._commit(run)

    #: Columns the long-lived in-memory ``run`` object may mutate between
    #: separate session-scoped commits (module docstring: each stage runs its
    #: own ``with self._session_factory()`` block, the same idiom
    #: ``app.evolution.gaps.GapService`` uses — never a cross-session
    #: ``merge()``/``add()`` on a detached instance).
    _MUTABLE_FIELDS: tuple[str, ...] = (
        "state",
        "authority_class",
        "side_effect_class",
        "approval_required",
        "approval_ref",
        "skill_version_id",
        "interface_json",
        "evidence_json",
        "error_class",
        "error_message",
        "updated_at",
    )

    def _commit(self, run: GenesisRun) -> None:
        with self._session_factory() as session:
            row = session.get(GenesisRun, run.id)
            if row is None:
                return
            for field in self._MUTABLE_FIELDS:
                setattr(row, field, getattr(run, field))
            session.commit()

    def _ledger(self, run: GenesisRun, state: str) -> None:
        event_type = GENESIS_EVENT_TYPE_BY_STATE.get(state)
        if event_type is None:
            return
        try:
            with self._session_factory() as session:
                ledger_service.record(
                    session,
                    ledger_service.ActivityEvent(
                        event_type=event_type,
                        subsystem=SUBSYSTEM_GENESIS,
                        action=f"genesis.{state}",
                        factual_summary=f"genesis {run.capability_id} -> {state}",
                        occurred_at=_utcnow(),
                        detail_json={
                            "run_id": str(run.id),
                            "capability_id": run.capability_id,
                            "state": state,
                            "error_class": run.error_class,
                        },
                        source="live",
                        source_ref=f"genesis:{run.id}:{state}",
                    ),
                )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the run
            logger.warning("genesis_ledger_failed", run_id=str(run.id), state=state)

    def _publish_ui_state(self, run: GenesisRun) -> None:
        try:
            publish_ui_state(
                UiState.CAPABILITY_GENESIS,
                subsystem=SUBSYSTEM_GENESIS,
                label=run.capability_id[:64],
                metadata={
                    "capability": run.capability_id,
                    "state": run.state,
                    "approval_required": bool(run.approval_required),
                    **({"error_class": run.error_class} if run.error_class else {}),
                },
            )
        except Exception:  # noqa: BLE001 - a UI signal must not fail the run
            logger.warning("genesis_uistate_failed", run_id=str(run.id))

    def _run_dict(self, run: GenesisRun) -> dict[str, Any]:
        return _run_dict(run)


def _run_dict(run: GenesisRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "capability_id": run.capability_id,
        "operation_id": run.operation_id,
        "gap_id": str(run.gap_id) if run.gap_id else None,
        "state": run.state,
        "authority_class": run.authority_class,
        "side_effect_class": run.side_effect_class,
        "approval_required": bool(run.approval_required),
        "approval_ref": run.approval_ref,
        "skill_version_id": str(run.skill_version_id) if run.skill_version_id else None,
        "evidence": dict(run.evidence_json or {}),
        "error_class": run.error_class,
        "error_message": run.error_message,
        "session_id": run.session_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "new_run": True,
    }


__all__ = ["MAX_RUNS_PER_HOUR_PER_INTERFACE", "MAX_RUN_DURATION_S", "GenesisService"]
