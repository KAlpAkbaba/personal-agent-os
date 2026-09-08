"""Genesis runtime: wires ``GenesisService`` onto the EXISTING M7
``EvolutionRuntime`` (M24, ADR-0087) — no separate database engine, no
separate registry, no separate authorization source. Lives on
``app.state.genesis`` (mirrors ``app.state.evolution``).

Genesis-generated capabilities publish under ``<skills_root>/genesis`` and
build in ``<work_root>/genesis`` — sibling trees under the SAME
``EvolutionRuntime`` roots, both already validated by ``SandboxPolicy``
against the protected core/recovery trees, so a genesis run can never write
outside the M7 sandbox boundary any more than a pure-transform skill can.
"""

from __future__ import annotations

from pathlib import Path

from app.evolution.authorization import AuthorizationProvider
from app.evolution.runtime import EvolutionRuntime
from app.evolution.sandbox import SandboxPolicy
from app.evolution.task_resumption import CapabilityDispatcher
from app.genesis.service import GenesisService


class GenesisRuntime:
    def __init__(self, evolution: EvolutionRuntime) -> None:
        self._evolution = evolution
        self._service: GenesisService | None = None

    @property
    def skills_root(self) -> Path:
        return self._evolution.skills_root / "genesis"

    @property
    def work_root(self) -> Path:
        return self._evolution.work_root / "genesis"

    @property
    def sandbox(self) -> SandboxPolicy:
        return SandboxPolicy(self.work_root)

    @property
    def skills_sandbox(self) -> SandboxPolicy:
        return SandboxPolicy(self.skills_root)

    @property
    def dispatcher(self) -> CapabilityDispatcher:
        return CapabilityDispatcher(self._evolution.registry, sandbox=self.skills_sandbox)

    @property
    def mutation_authorization(self) -> AuthorizationProvider:
        """The REAL owner-authorization source for a MUTATING operation (spec
        §5/§9) — the SAME provider the M7 reviewer uses for permission grants
        (``app.security.provider.RegistryAuthorizationProvider`` in
        production; deny-by-default with no registry configured)."""
        return self._evolution.authorization

    @property
    def service(self) -> GenesisService:
        if self._service is None:
            self.skills_root.mkdir(parents=True, exist_ok=True)
            self._service = GenesisService(
                self._evolution.session,
                registry=self._evolution.registry,
                gaps=self._evolution.gaps,
                detector=self._evolution.detector,
                sandbox=self.sandbox,
                skills_root=self.skills_root,
                dispatcher=self.dispatcher,
                mutation_authorization=self.mutation_authorization,
                budget=self._evolution.budget,
            )
        return self._service


__all__ = ["GenesisRuntime"]
