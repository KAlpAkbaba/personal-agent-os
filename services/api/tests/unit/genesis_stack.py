"""Shared offline (SQLite + temp roots) M24 Capability Genesis test stack.

Mirrors ``tests/unit/test_evolution_pipeline.py::make_stack`` for the same
reason: sibling test modules build their own fixture without importing (and
shadowing) each other's. NOT a ``test_*.py`` module itself — pytest never
collects it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.evolution.authorization import (
    AuthorizationProvider,
    NullAuthorizationProvider,
    StaticAuthorizationProvider,
)
from app.evolution.components import ComponentCatalog
from app.evolution.gaps import CapabilityComposer, GapDetector, GapService
from app.evolution.models import Capability, CapabilityGap, SkillVersion
from app.evolution.registry import CapabilityRegistry
from app.evolution.sandbox import SandboxPolicy
from app.evolution.task_resumption import CapabilityDispatcher
from app.genesis.models import GenesisRun
from app.genesis.service import GenesisService
from app.ledger.models import ActivityEventRow

TABLES = [
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    GenesisRun.__table__,
    ActivityEventRow.__table__,
]


@dataclass
class GenesisStack:
    registry: CapabilityRegistry
    gaps: GapService
    detector: GapDetector
    sandbox: SandboxPolicy
    skills_root: Path
    dispatcher: CapabilityDispatcher
    service: GenesisService


def make_stack(
    tmp_path: Path, *, mutation_authorization: AuthorizationProvider | None = None
) -> GenesisStack:
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
    catalog = ComponentCatalog.load()
    gaps = GapService(session_scope)
    detector = GapDetector(registry, CapabilityComposer(registry), catalog=catalog)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    skills_sandbox = SandboxPolicy(skills_root)
    dispatcher = CapabilityDispatcher(registry, sandbox=skills_sandbox)
    service = GenesisService(
        session_scope,
        registry=registry,
        gaps=gaps,
        detector=detector,
        sandbox=SandboxPolicy(tmp_path / "work"),
        skills_root=skills_root,
        dispatcher=dispatcher,
        mutation_authorization=mutation_authorization or NullAuthorizationProvider(),
    )
    return GenesisStack(
        registry=registry,
        gaps=gaps,
        detector=detector,
        sandbox=SandboxPolicy(tmp_path / "work"),
        skills_root=skills_root,
        dispatcher=dispatcher,
        service=service,
    )


def authorized(*asset_names: str) -> StaticAuthorizationProvider:
    """A mutation-authorization provider pre-recording the owner having
    authorized each named asset for every permission class genesis needs."""
    return StaticAuthorizationProvider(
        {name: {"network_permissions": ["127.0.0.1"]} for name in asset_names}
    )


__all__ = ["GenesisStack", "authorized", "make_stack"]
