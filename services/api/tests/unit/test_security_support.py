"""Shared helpers for the M8 security unit tests (no tests of its own).

Everything here is offline and deterministic: a SQLite StaticPool engine, an
in-memory object store, and the tracked `tests/fixtures/security_target`
configuration bundle. No network, no docker, no third-party tools.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, ArtifactRender, ArtifactVersion, Task
from app.security.assessments import AssessmentService
from app.security.models import (
    AuthorizationEvent,
    AuthorizedAsset,
    SecurityAssessment,
    SecurityFinding,
)
from app.security.registry import AuthorizedAssetRegistry
from app.security.remediation import RemediationService
from app.security.scope import ScopeGuard

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "security_target"

SECURITY_TABLES = [
    AuthorizedAsset.__table__,
    SecurityAssessment.__table__,
    SecurityFinding.__table__,
    AuthorizationEvent.__table__,
]

ARTIFACT_TABLES = [
    Task.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
]


def make_engine(*, with_artifacts: bool = False) -> Engine:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = SECURITY_TABLES + (ARTIFACT_TABLES if with_artifacts else [])
    for table in tables:
        table.create(engine)
    return engine


def session_factory(engine: Engine) -> Callable[[], Any]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _open():
        session: Session = factory()
        try:
            yield session
        finally:
            session.close()

    return _open


class Stack:
    """Registry + guard + assessment/remediation services over one engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session = session_factory(engine)
        self.registry = AuthorizedAssetRegistry(self.session)
        self.guard = ScopeGuard(self.registry)
        self.assessments = AssessmentService(self.session, self.registry, self.guard)
        self.remediation = RemediationService(self.session, self.registry, self.guard)


def make_stack(*, with_artifacts: bool = False) -> Stack:
    return Stack(make_engine(with_artifacts=with_artifacts))


def copy_fixture(destination: Path) -> Path:
    """A writable copy of the tracked fixture target (for remediation tests)."""
    root = destination / "security_target"
    shutil.copytree(FIXTURE_ROOT, root)
    return root


def utcnow() -> datetime:
    return datetime.now(UTC)


def enroll_lab_host(
    stack: Stack,
    *,
    asset_ref: str = "lab-web-01",
    locator: str = "10.20.30.40",
    kind: str = "host",
    config_root: Path | None = None,
    max_disruption: str = "low",
    allowed_testing: dict[str, bool] | None = None,
    allowed_permissions: dict[str, list[str]] | None = None,
    valid_until: datetime | None = None,
    valid_from: datetime | None = None,
) -> dict[str, Any]:
    """The canonical SECURITY_MODEL §7 example asset, enrolled."""
    constraints: dict[str, Any] = {"max_disruption": max_disruption}
    if config_root is not None:
        constraints["config_roots"] = [str(config_root)]
    return stack.registry.enroll(
        asset_ref=asset_ref,
        name="Lab web host 01",
        kind=kind,
        locator=locator,
        environment="lab",
        allowed_testing=(
            allowed_testing
            if allowed_testing is not None
            else {"configuration_audit": True, "remediation": True}
        ),
        allowed_permissions=allowed_permissions or {},
        constraints=constraints,
        evidence={
            "authorization": "owner_or_company_authorized",
            "recorded_by": "owner",
            "reference": "OWNER-LAB-2026-01",
        },
        valid_from=valid_from,
        valid_until=valid_until or (utcnow() + timedelta(days=365)),
    )


__all__ = [
    "ARTIFACT_TABLES",
    "FIXTURE_ROOT",
    "SECURITY_TABLES",
    "Stack",
    "copy_fixture",
    "enroll_lab_host",
    "make_engine",
    "make_stack",
    "session_factory",
    "utcnow",
]
