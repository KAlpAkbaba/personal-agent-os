"""Evolution runtime: DB session factory, registry/gap services, generator
selection, sandbox + skills roots.

Lives on ``app.state.evolution`` (mirrors SelfHealingRuntime/MemoryRuntime).
Tests inject an ``engine`` (SQLite StaticPool).

Configuration is read from environment variables directly (documented here; no
app.config change — config.py is outside this module's ownership):

    PAGENTOS_EVOLUTION_SKILLS_ROOT   where APPROVED generated skills are
                                     published and dispatched from;
                                     default: <repo>/skills/generated
    PAGENTOS_EVOLUTION_WORK_ROOT     disposable sandbox root for candidate
                                     workspaces (§13);
                                     default: <skills_root>/.work
    PAGENTOS_EVOLUTION_GENERATOR     "deterministic" (default) | "claude"
    PAGENTOS_EVOLUTION_CLAUDE_CLI    Claude CLI path; without it the claude
                                     generator stays inert (typed
                                     generator_not_configured error).

Both roots go through ``SandboxPolicy``, which refuses any root overlapping the
recovery supervisor, the API source, the migrations, the tests, config, state or
docs trees — so a misconfigured environment variable cannot aim the generator at
the product's own code.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.evolution.authority import ROOT_POLICY_DIGEST, Scope
from app.evolution.authorization import (
    AuthorizationProvider,
    NullAuthorizationProvider,
    StaticAuthorizationProvider,
)
from app.evolution.components import ComponentCatalog
from app.evolution.gaps import CapabilityComposer, GapDetector, GapService
from app.evolution.improvement import ImprovementDetector, SkillImprover
from app.evolution.registry import CapabilityRegistry
from app.evolution.release_evidence import LedgerReleaseEvidenceProvider
from app.evolution.resources import ResourceBudget
from app.evolution.review import IndependentSkillReviewer
from app.evolution.sandbox import SandboxPolicy
from app.evolution.service import EvolutionService
from app.evolution.skills import (
    ClaudeSkillGenerator,
    DeterministicSkillGenerator,
    SkillGenerator,
)
from app.evolution.supervisor import EvolutionSupervisor
from app.evolution.task_resumption import CapabilityDispatcher, TaskResumer
from app.logging import get_logger

logger = get_logger("app.evolution.runtime")

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: The app tree itself (``services/api`` in a checkout, ``/srv/pagentos`` in the image).
_APP_ROOT = Path(__file__).resolve().parents[2]


def _default_skills_root() -> Path:
    """Where generated skills live when the deployment names no root.

    A checkout has ``services/api`` beneath ``_REPO_ROOT``; an image has the app tree
    copied in with nothing writable above it, so ``_REPO_ROOT`` there is the filesystem
    root and ``<root>/skills/generated`` is both unwritable and meaningless. Only trust
    the repository root when it really looks like one (2026-09-08: the M24 release found
    the container computing ``/skills`` and failing to start).
    """
    if (_REPO_ROOT / "services" / "api").is_dir():
        return _REPO_ROOT / "skills" / "generated"
    return _APP_ROOT / "skills" / "generated"


class EvolutionRuntime:
    def __init__(self, settings: Settings, *, engine: Engine | None = None) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._registry: CapabilityRegistry | None = None
        self._gaps: GapService | None = None
        self._evolution_service: EvolutionService | None = None
        #: M18.4 (spec §3): the clock-driven observer. Bound to its sources by the app.
        self.supervisor = EvolutionSupervisor(
            enabled=settings.evolution_supervisor_enabled,
            interval_s=settings.evolution_supervisor_interval_s,
        )

        self.skills_root = Path(
            os.environ.get("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(_default_skills_root()))
        ).resolve()
        self.work_root = Path(
            os.environ.get("PAGENTOS_EVOLUTION_WORK_ROOT", str(self.skills_root / ".work"))
        ).resolve()
        self._generator_name = os.environ.get("PAGENTOS_EVOLUTION_GENERATOR", "deterministic")
        self._claude_cli = os.environ.get("PAGENTOS_EVOLUTION_CLAUDE_CLI", "")
        # Resource controls (§ Isolation, supply chain, resources). Every value
        # is configurable; the defaults are the safe ones.
        self.budget = ResourceBudget(
            timeout_s=float(os.environ.get("PAGENTOS_EVOLUTION_TIMEOUT_S", "60")),
            cpu_seconds=int(os.environ.get("PAGENTOS_EVOLUTION_CPU_SECONDS", "30")),
            memory_mb=int(os.environ.get("PAGENTOS_EVOLUTION_MEMORY_MB", "512")),
            disk_mb=int(os.environ.get("PAGENTOS_EVOLUTION_DISK_MB", "8")),
            retry_limit=int(os.environ.get("PAGENTOS_EVOLUTION_RETRY_LIMIT", "1")),
            max_depth=int(os.environ.get("PAGENTOS_EVOLUTION_MAX_DEPTH", "1")),
        )
        self.catalog = ComponentCatalog.load()
        # Constructed eagerly so a protected-tree misconfiguration fails at
        # startup rather than at the first generation request.
        self.sandbox = SandboxPolicy(self.work_root)
        self.skills_sandbox = SandboxPolicy(self.skills_root)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = build_engine(self.settings.database_url)
            self._session_factory = build_session_factory(self._engine)
        return self._engine

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        _ = self.engine
        assert self._session_factory is not None
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    @property
    def registry(self) -> CapabilityRegistry:
        if self._registry is None:
            self._registry = CapabilityRegistry(self.session)
        return self._registry

    @property
    def gaps(self) -> GapService:
        if self._gaps is None:
            self._gaps = GapService(self.session)
        return self._gaps

    @property
    def evolution_service(self) -> EvolutionService:
        """The backlog service, running LAB-SCOPED.

        Constructed with the default ``LabAuthority``, so nothing reachable
        from ``app.state.evolution`` holds a production grant. Production
        authority is minted per request from a verified owner session in
        ``app/evolution/routes.py`` and never stored here.

        ``release_evidence`` is the ledger (ADR-0081 addendum 3): an opportunity is
        release-ready exactly when a ``deployment.cloud_core.released`` row names its
        candidate's sha - rows the blue/green harness and driver record under the owner's
        session after the switch was verified through the edge. Nothing a caller sends
        can stand in for that row, so ``LIVE`` still points at a durable deployment.
        """
        if self._evolution_service is None:
            self._evolution_service = EvolutionService(
                self.session, release_evidence=LedgerReleaseEvidenceProvider(self.session)
            )
        return self._evolution_service

    @property
    def detector(self) -> GapDetector:
        return GapDetector(
            self.registry,
            CapabilityComposer(self.registry),
            catalog=self.catalog,
            budget=self.budget,
        )

    @property
    def improvement_detector(self) -> ImprovementDetector:
        return ImprovementDetector(self.registry)

    @property
    def improver(self) -> SkillImprover:
        # Pass the registry-backed reviewer explicitly. Omitting it would let
        # this path fall back to a reviewer with no authorization source the
        # day self-improvement is wired to a route — the same "built but not
        # wired" failure the M8 review caught in EvolutionRuntime.authorization
        # (ADR-0026 security addendum). It fails safe, but silently.
        return SkillImprover(
            self.registry,
            sandbox=self.sandbox,
            skills_root=self.skills_root,
            generator=self.generator,
            budget=self.budget,
            reviewer=self.reviewer,
        )

    @property
    def generator(self) -> SkillGenerator:
        if self._generator_name == "claude":
            # Inert without PAGENTOS_EVOLUTION_CLAUDE_CLI (typed error on use).
            return ClaudeSkillGenerator(cli_path=self._claude_cli)
        return DeterministicSkillGenerator()

    @property
    def authorization(self) -> AuthorizationProvider:
        """Verified source of owner authorization for permission grants.

        The M8 Authorized Asset Registry is the real source (ADR-0026): an
        evolution grant is approved because an enrolled, active, in-window
        asset records that permission — never because a requester asserted an
        asset name (M7 security review #1). `PAGENTOS_EVOLUTION_AUTHORIZATIONS`
        remains a local/dev override for environments with no registry; with
        neither, nothing is verifiable and every grant is refused
        (deny-by-default still holds).
        """
        raw = os.environ.get("PAGENTOS_EVOLUTION_AUTHORIZATIONS", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                return NullAuthorizationProvider()
            if isinstance(parsed, dict):
                return StaticAuthorizationProvider(parsed)
            return NullAuthorizationProvider()
        # Registry-backed by default. Imported lazily so the evolution package
        # keeps no import-time dependency on the security package.
        from app.security.provider import RegistryAuthorizationProvider

        return RegistryAuthorizationProvider(self.session)

    @property
    def reviewer(self) -> IndependentSkillReviewer:
        return IndependentSkillReviewer(sandbox=self.sandbox, authorization=self.authorization)

    @property
    def dispatcher(self) -> CapabilityDispatcher:
        return CapabilityDispatcher(self.registry, sandbox=self.skills_sandbox)

    @property
    def resumer(self) -> TaskResumer:
        return TaskResumer(self.session, self.dispatcher)

    def health_check(self) -> dict[str, object]:
        """Generator identity + sandbox posture for /v1/system/health.

        No I/O beyond two directory stats: DB reachability is covered by the
        'db' check, and the sandbox roots were already validated at
        construction.
        """
        return {
            "status": "ok",
            "latency_ms": 0.0,  # no I/O; keeps the uniform check shape
            "skill_generator": self.generator.name,
            "skills_root_present": self.skills_root.is_dir(),
            "sandbox_root": str(self.sandbox.root),
            "sandbox_isolated_from_core": True,
            "component_catalog": len(self.catalog.components),
            "resource_budget": self.budget.to_dict(),
            # The production boundary, visible in the health surface: the
            # engine runs lab-scoped and holds zero production grants.
            "engine_authority_scope": str(Scope.LAB),
            "production_grants_held": 0,
            "root_policy_digest": ROOT_POLICY_DIGEST,
            # M18.4 (spec §3.4): whether the observer runs, and what its last scan did.
            "supervisor": self.supervisor.health(),
        }


__all__ = ["EvolutionRuntime"]
