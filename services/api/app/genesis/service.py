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

from sqlalchemy import select, update
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
from app.genesis.adapter import DEFAULT_VERSION, AdapterSpec, HttpAdapterGenerator
from app.genesis.interface import HostPredicate, InterfaceDescription, fetch_interface
from app.genesis.models import GenesisRun
from app.genesis.security_gate import review_layout
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
        host_allowed: HostPredicate | None = None,
        adapter_model: Any | None = None,
        model_generation_enabled: bool | Callable[[], bool] = False,
    ) -> None:
        self._session_factory = session_factory
        # B36 req 565: which non-loopback hosts may be researched (owner-authorized).
        self.host_allowed = host_allowed
        # B36 req 577: the model seam, asked only under the owner's flag.
        self.adapter_model = adapter_model
        self._model_generation_enabled = model_generation_enabled
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

    @property
    def model_generation_enabled(self) -> bool:
        flag = self._model_generation_enabled
        return bool(flag() if callable(flag) else flag) and self.adapter_model is not None

    def request(
        self,
        *,
        interface_name: str,
        interface_url: str,
        operation_id: str,
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        new_version: bool = False,
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
        if existing is not None and not new_version:
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
        if existing is not None:
            # B36 req 571: a new version of a capability that already resolves - the
            # next patch version after the incumbent's; the registry supersedes the
            # incumbent on registration and keeps it rollback-capable (req 572).
            self._patch_evidence(
                run,
                {
                    "requested_version": _next_version(str(existing.get("version") or "")),
                    "supersedes_version": existing.get("version"),
                },
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
            self._refuse_a_borrowed_name(run, spec)
            layout, skill_version_id = self._build(run, spec, started)
            self._secure(run, layout, skill_version_id, spec)
            self._test(run, layout, skill_version_id, spec, started)
            authority_class, side_effect_class, mutation_authorized = self._classify(run, spec)
        except EvolutionError as exc:
            return self._fail(run, exc)

        model_generated = bool((run.evidence_json or {}).get("model_generated"))
        if (side_effect_class == "mutate_external" and not mutation_authorized) or model_generated:
            # B36 req 580: what the model wrote waits for the owner whatever it does.
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
        interface = fetch_interface(
            run.evidence_json["interface_url"], host_allowed=self.host_allowed
        )
        self._patch_evidence(run, {"interface": interface.to_dict()})
        run.interface_json = interface.to_dict()
        self._commit(run)
        return interface

    def _design(self, run: GenesisRun, interface: InterfaceDescription) -> AdapterSpec:
        self._transition(run, "designing")
        requested = (run.evidence_json or {}).get("requested_version")
        if isinstance(requested, str) and requested:
            return AdapterSpec(
                interface=interface, operation_id=run.operation_id, version=requested
            )
        spec = AdapterSpec(interface=interface, operation_id=run.operation_id)
        return spec

    @staticmethod
    def _refuse_a_borrowed_name(run: GenesisRun, spec: AdapterSpec) -> None:
        """A mutating description that names itself differently from the interface the owner
        registered is refused BEFORE anything is generated or run against it.

        The asset reference is the name the OWNER registered - the catalogue entry this run
        was asked for, carried in the capability id - never the fetched document's own
        ``name``. An application that calls itself "mailserver" must not inherit the authority
        the owner granted a real mailserver (M24 security review, 2026-09-08, live PoC). Until
        2026-09-16 this check sat in ``_classify``, after the adapter had been built and its
        generated tests had called the untrusted host; under load the release gates failed
        first and the refusal surfaced as ``evaluation_failed``.
        """
        if spec.operation.side_effect == "read":
            return
        asset_ref = run.capability_id.rsplit(".", 1)[0]
        if spec.interface.name != asset_ref:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                "the description names itself differently from the interface the "
                "owner registered",
                details={"registered": asset_ref, "self_reported": spec.interface.name},
            )

    def _build(
        self, run: GenesisRun, spec: AdapterSpec, started: datetime
    ) -> tuple[SkillLayout, uuid.UUID]:
        self._enforce_time_bound(started)
        self._transition(run, "building")
        self.sandbox.prepare()
        work_dir = Path(tempfile.mkdtemp(prefix="genesis-", dir=str(self.sandbox.root)))
        self.sandbox.ensure_within(work_dir, label="genesis workspace")
        generator = HttpAdapterGenerator(
            model=self.adapter_model if self.model_generation_enabled else None
        )
        layout = generator.generate(spec, work_dir)
        self._patch_evidence(
            run,
            {
                "generator": generator.generator_name,
                "model_generated": generator.model_generated,
                **(
                    {"model": getattr(self.adapter_model, "name", "model")}
                    if generator.model_generated
                    else {}
                ),
            },
        )
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

    def _secure(
        self,
        run: GenesisRun,
        layout: SkillLayout,
        skill_version_id: uuid.UUID,
        spec: AdapterSpec,
    ) -> None:
        """B36 req 579/680: the security gate on everything the generator (or the
        model) wrote, BEFORE a test runs it. Red rejects the skill version and fails the
        run with ``security_refused``; the findings stay in the evidence."""
        verdict = review_layout(layout, allowed_hosts=(spec.host,))
        self._patch_evidence(run, {"security_review": verdict.as_dict()})
        if not verdict.passed:
            self.registry.reject_skill_version(
                skill_version_id, "security gate: " + verdict.summary()[:400]
            )
            raise EvolutionError(
                EvolutionErrorClass.SECURITY_REFUSED,
                "genesis adapter refused by the security gate: " + verdict.summary(),
                details={"findings": [f.as_dict() for f in verdict.findings[:12]]},
            )

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
            # The asset reference is the name the OWNER registered, never the fetched
            # document's own ``name``; a document that disagrees was already refused by
            # _refuse_a_borrowed_name, before anything was built or run.
            asset_ref = run.capability_id.rsplit(".", 1)[0]
            verified = self.mutation_authorization.verify(asset_ref)
            if verified is not None:
                # Authorization is what ``covers()`` says it is, never merely "a row
                # exists": an asset enrolled with no grants authorises nothing, and the
                # one grant this adapter needs is the narrow network permission it
                # declares in its own manifest (``app.genesis.adapter``).
                approved, unauthorized = verified.covers({"network_permissions": [spec.host]})
                mutation_authorized = approved
                if not approved:
                    self._patch_evidence(run, {"unauthorized_grants": unauthorized})
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
        evidence = run.evidence_json or {}
        manifest["provenance"] = {
            "generator": str(evidence.get("generator") or HttpAdapterGenerator.name),
            "model_generated": bool(evidence.get("model_generated")),
            "security_review_passed": bool(
                (evidence.get("security_review") or {}).get("passed", False)
            ),
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
        if not shared_fields:
            # Never "verified" on an empty comparison (see the parse-time refusal in
            # ``app.genesis.interface``): a read-back that shares no field with what the
            # mutation reported has witnessed nothing, and saying otherwise would be the
            # vacuous gate this project refuses everywhere else.
            raise EvolutionError(
                EvolutionErrorClass.POSTCONDITION_FAILED,
                "genesis read-back shares no field with the mutation's own reported "
                "result, so it verified nothing",
                details={
                    "dispatched_fields": sorted(dispatched.output),
                    "read_back_fields": sorted(read_back_output),
                },
            )
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
        # Claim the run atomically BEFORE any provider work: reading the state,
        # checking it in Python and writing it back later let two racing approvals (a
        # double-tapped "Onayla", or REST and voice at once) both pass the check and both
        # dispatch the mutation. One conditional UPDATE decides — the same CAS the M21
        # confirmation gate uses (ADR-0084) — and the loser is told, never served.
        if not self._claim_approval(run.id, confirmation.session_id):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"genesis run {run_id} was already claimed by another approval",
            )
        run.approval_ref = confirmation.session_id
        interface = self._reload_interface(run)
        # Reaching here means approval was just granted: the manifest built
        # below must carry authority_class=mutating_authorized_asset, never
        # the AdapterSpec default (mutating_unauthorized) — see the matching
        # comment in _drive. B36: a READ operation parked only because the model
        # wrote it (req 580) stays read_only - approval of the code is not an
        # authorization of a mutation.
        mutating = run.side_effect_class == "mutate_external"
        spec = AdapterSpec(
            interface=interface,
            operation_id=run.operation_id,
            version=str(evidence.get("version") or DEFAULT_VERSION),
            authorized_asset=interface.name if mutating else None,
        )
        layout = SkillLayout(
            root=Path(evidence["workspace_root"]),
            skill_name=evidence["skill_name"],
            capability_id=run.capability_id,
            version=evidence["version"],
        )
        skill_version_id = uuid.UUID(evidence["skill_version_id"])
        run.authority_class = "mutating_authorized_asset" if mutating else "read_only"
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

    # ------------------------------------------------- B36: versions, rollback, use

    def versions(self, capability_id: str) -> dict[str, Any]:
        """Req 571: every version the registry holds for one capability, the current
        one named."""
        capability = self.registry.get_capability(capability_id)
        if capability is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND, f"capability {capability_id} is not registered"
            )
        return {
            "capability": capability,
            "versions": self.registry.list_skill_versions(capability_id=capability_id),
        }

    def rollback(self, capability_id: str, version: str) -> dict[str, Any]:
        """Req 572: the registry's own rollback - a previously REGISTERED version
        serves again, the current one is superseded, nothing is deleted."""
        restored = self.registry.rollback_to(capability_id, version)
        self._ledger_capability(capability_id, "rollback", {"to_version": version})
        return restored

    def deactivate(self, capability_id: str) -> dict[str, Any]:
        """Req 570: the capability stops resolving (no dispatch, no voice) until it is
        activated again; its versions and history stay."""
        updated = self.registry.set_capability_status(capability_id, "deprecated")
        self._ledger_capability(capability_id, "deactivate", {})
        return updated

    def activate(self, capability_id: str) -> dict[str, Any]:
        """Req 570: back to production through the registry's ONE gate onto it - the
        current registered version is re-served by the rollback path (never a status
        write, which the registry refuses for 'production')."""
        capability = self.registry.get_capability(capability_id)
        if capability is None:
            raise EvolutionError(
                EvolutionErrorClass.NOT_FOUND, f"capability {capability_id} is not registered"
            )
        restored = self.registry.rollback_to(capability_id, str(capability["version"]))
        self._ledger_capability(capability_id, "activate", {})
        return restored

    def use(self, capability_id: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Req 573: dispatch a REGISTERED capability (the production use path the voice
        tool takes when the registry resolves), or refuse by name."""
        if self.registry.resolve(capability_id) is None:
            raise EvolutionError(
                EvolutionErrorClass.CAPABILITY_MISSING,
                f"capability {capability_id} does not resolve",
            )
        return self._use_and_verify_existing(capability_id, dict(arguments or {}), session_id=None)

    def _ledger_capability(self, capability_id: str, action: str, detail: dict[str, Any]) -> None:
        try:
            with self._session_factory() as session:
                ledger_service.record(
                    session,
                    ledger_service.ActivityEvent(
                        event_type=GENESIS_EVENT_TYPE_BY_STATE["registering"],
                        subsystem=SUBSYSTEM_GENESIS,
                        action=f"genesis.capability.{action}",
                        factual_summary=f"genesis {capability_id} {action}",
                        occurred_at=_utcnow(),
                        detail_json={"capability_id": capability_id, **detail},
                        source="live",
                        source_ref=f"genesis.capability.{action}:{uuid.uuid4()}",
                    ),
                )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the act
            logger.warning("genesis_capability_ledger_failed", action=action)

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

    def find_awaiting_approval(self, session_id: str) -> dict[str, Any] | None:
        """The run THIS voice/REST session parked at ``awaiting_approval``, if
        any — never resolved from a caller-supplied run id (spec §5/§9:
        ``capability.approve``/``capability.cancel`` trust the durable
        session-bound state, never the model's own argument, the same
        "owner's words win, resolved through durable state" rule the M21
        mail/calendar confirmation gate follows for its own prepared
        draft/proposal)."""
        with self._session_factory() as session:
            row = session.execute(
                select(GenesisRun)
                .where(GenesisRun.state == "awaiting_approval")
                .where(GenesisRun.session_id == session_id)
                .order_by(GenesisRun.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return _run_dict(row) if row is not None else None

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

    def _claim_approval(self, run_id: uuid.UUID, session_id: str | None) -> bool:
        """Atomically claim ONE awaiting_approval run for ONE approval. True only for the
        caller whose UPDATE actually matched a row; every later caller gets False and is
        refused rather than served (the M21 CAS discipline, ADR-0084)."""
        with self._session_factory() as session:
            result = session.execute(
                update(GenesisRun)
                .where(
                    GenesisRun.id == run_id,
                    GenesisRun.state == "awaiting_approval",
                    GenesisRun.approval_ref.is_(None),
                )
                .values(approval_ref=session_id or "approved", updated_at=datetime.now(UTC))
            )
            session.commit()
            return bool(result.rowcount)

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


def _next_version(current: str) -> str:
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return DEFAULT_VERSION
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"


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


# ------------------------------------------------------------- process registry
#
# Mirrors app.operator.service.register_operator_service/get_operator_service: the ONE
# router's turn handler (app.voice.realtime_sessions.service) needs to know whether a
# genesis run is awaiting approval in THIS session BEFORE it can decide what a bare
# "Onaylıyorum."/"Vazgeç." means (app.voice.intents.resolve_intent's own
# genesis_awaiting_approval parameter) — a process-wide singleton, not a DB read the
# handler builds a whole service around, the same reason the operator's OWN running-task
# check uses one.

_genesis_service: GenesisService | None = None


def register_genesis_service(service: GenesisService | None) -> None:
    global _genesis_service
    _genesis_service = service


def get_genesis_service() -> GenesisService | None:
    return _genesis_service


__all__ = [
    "MAX_RUNS_PER_HOUR_PER_INTERFACE",
    "MAX_RUN_DURATION_S",
    "GenesisService",
    "get_genesis_service",
    "register_genesis_service",
]
