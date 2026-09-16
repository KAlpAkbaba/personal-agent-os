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

from collections.abc import Callable
from pathlib import Path

from app.evolution.authorization import AuthorizationProvider
from app.evolution.runtime import EvolutionRuntime
from app.evolution.sandbox import SandboxPolicy
from app.evolution.task_resumption import CapabilityDispatcher
from app.genesis.catalogue import CatalogueStore
from app.genesis.model_generator import AnthropicAdapterCodeModel
from app.genesis.service import GenesisService


class GenesisRuntime:
    def __init__(
        self,
        evolution: EvolutionRuntime,
        *,
        model_generation_enabled: bool | Callable[[], bool] = False,
        authorized_hosts_enabled: bool = True,
        adapter_model: object | None = None,
    ) -> None:
        self._evolution = evolution
        self._service: GenesisService | None = None
        self._catalogue_store: CatalogueStore | None = None
        self._model_generation_enabled = model_generation_enabled
        self._authorized_hosts_enabled = authorized_hosts_enabled
        self._adapter_model = adapter_model

    # ------------------------------------------------------------ B36

    @property
    def catalogue_store(self) -> CatalogueStore:
        """The rows behind the spoken-name catalogue (req 562/563); nothing is read
        until ``load_catalogue`` (the lifespan) or a registration asks."""
        if self._catalogue_store is None:
            self._catalogue_store = CatalogueStore(self._evolution.session)
        return self._catalogue_store

    def load_catalogue(self) -> int:
        return self.catalogue_store.load()

    def host_allowed(self, hostname: str) -> bool:
        """Req 565: a non-loopback host may be researched only when the owner enrolled
        it as an authorized asset whose grants cover network permission on itself."""
        if not self._authorized_hosts_enabled:
            return False
        verified = self.mutation_authorization.verify(hostname)
        if verified is None:
            return False
        approved, _unauthorized = verified.covers({"network_permissions": [hostname]})
        return bool(approved)

    @property
    def adapter_model(self) -> object:
        if self._adapter_model is None:
            self._adapter_model = AnthropicAdapterCodeModel(
                api_key=self._evolution.settings.anthropic_api_key or None
            )
        return self._adapter_model

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
            # NO directory is created here. ``create_app`` builds this service while the
            # application object is being constructed, so a mkdir on this path runs on
            # every process start — and in the image there is nothing writable above the
            # app tree, which is exactly how the 2026-09-08 release found this: the
            # container died with PermissionError before uvicorn could load the app and
            # the blue/green release rolled back. ``GenesisService._publish_and_register``
            # already creates the target tree when a run actually publishes a skill,
            # which is the same discipline ``EvolutionRuntime`` follows (it builds paths
            # at startup and creates nothing).
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
                host_allowed=self.host_allowed,
                adapter_model=self.adapter_model,
                model_generation_enabled=self._model_generation_enabled,
            )
        return self._service


__all__ = ["GenesisRuntime"]
