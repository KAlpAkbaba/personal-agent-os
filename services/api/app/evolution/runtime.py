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
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.evolution.gaps import CapabilityComposer, GapDetector, GapService
from app.evolution.registry import CapabilityRegistry
from app.evolution.sandbox import SandboxPolicy
from app.evolution.skills import (
    ClaudeSkillGenerator,
    DeterministicSkillGenerator,
    SkillGenerator,
)
from app.evolution.task_resumption import CapabilityDispatcher, TaskResumer
from app.logging import get_logger

logger = get_logger("app.evolution.runtime")

_REPO_ROOT = Path(__file__).resolve().parents[4]


class EvolutionRuntime:
    def __init__(self, settings: Settings, *, engine: Engine | None = None) -> None:
        self.settings = settings
        self._engine: Engine | None = engine
        self._session_factory: sessionmaker[Session] | None = (
            build_session_factory(engine) if engine is not None else None
        )
        self._registry: CapabilityRegistry | None = None
        self._gaps: GapService | None = None

        self.skills_root = Path(
            os.environ.get(
                "PAGENTOS_EVOLUTION_SKILLS_ROOT", str(_REPO_ROOT / "skills" / "generated")
            )
        ).resolve()
        self.work_root = Path(
            os.environ.get("PAGENTOS_EVOLUTION_WORK_ROOT", str(self.skills_root / ".work"))
        ).resolve()
        self._generator_name = os.environ.get("PAGENTOS_EVOLUTION_GENERATOR", "deterministic")
        self._claude_cli = os.environ.get("PAGENTOS_EVOLUTION_CLAUDE_CLI", "")
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
    def detector(self) -> GapDetector:
        return GapDetector(self.registry, CapabilityComposer(self.registry))

    @property
    def generator(self) -> SkillGenerator:
        if self._generator_name == "claude":
            # Inert without PAGENTOS_EVOLUTION_CLAUDE_CLI (typed error on use).
            return ClaudeSkillGenerator(cli_path=self._claude_cli)
        return DeterministicSkillGenerator()

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
        }


__all__ = ["EvolutionRuntime"]
