"""The Artifact Factory: ``ArtifactSpec`` -> artifact + version + renders + validations
(docs/M22_ARTIFACT_FACTORY_SPEC.md, ADR-0085).

Reuses the M13 Task -> Artifact -> Presentation tables (ADR-0020) rather than a
parallel schema (ADR-0085 decision 3/4): the artifact's canonical body is the spec's
own deterministic JSON (``canonical_format = "artifact_spec_json"``, never
Markdown), and every render + its independent validation lives in the SAME
``artifact_renders`` table the research pipeline writes to, distinguished by the new
``state``/``validation_json`` columns (migration 20260908_0028).

``create()`` is idempotent on the spec's own canonical JSON content_hash
(app.artifacts.service.get_artifact_version_by_content_hash): submitting the exact
same spec twice — a retried voice tool call, a retried POST — reuses the existing
artifact/version rather than minting a duplicate, and still re-runs (idempotently,
via ``render_store.ensure_render``'s own upsert) the render+validate step for every
format so a partially-completed prior attempt is finished rather than skipped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.artifacts import render_store, service
from app.artifacts.models import (
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    RENDER_STATE_INVALID,
)
from app.artifacts.renderers import content_hash
from app.artifacts.spec import ArtifactSpec
from app.object_store import ObjectStore


@dataclass(frozen=True, slots=True)
class FactoryRenderResult:
    format: str
    content_hash: str
    size_bytes: int
    mime_type: str
    state: str
    failing_refs: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.state != RENDER_STATE_INVALID


@dataclass(frozen=True, slots=True)
class FactoryResult:
    artifact_id: uuid.UUID
    version: int
    title: str
    kind: str
    created: bool
    renders: list[FactoryRenderResult]

    @property
    def all_valid(self) -> bool:
        return all(r.valid for r in self.renders)

    @property
    def failing(self) -> list[FactoryRenderResult]:
        return [r for r in self.renders if not r.valid]

    def download_path(self, fmt: str) -> str:
        """The EXISTING M13 render-download route (app/artifacts/routes.py) — the
        Artifact Factory does not add a new download route (ADR-0085 decision 4)."""
        return download_path(self.artifact_id, fmt)


def download_path(artifact_id: uuid.UUID, fmt: str) -> str:
    return f"/v1/artifacts/{artifact_id}/renders/{fmt}"


def create(
    session: Session,
    store: ObjectStore,
    *,
    spec: ArtifactSpec,
    conversation_id: uuid.UUID | None = None,
) -> FactoryResult:
    """Create (or idempotently re-use) an artifact for ``spec``, render every format
    its kind produces, and independently validate each one. A render whose
    validation fails is stored ``state="invalid"`` — never silently upgraded to
    "done"; the caller (a voice tool or route) decides what to say about it."""
    canonical = spec.canonical_json()
    chash = content_hash(canonical.encode("utf-8"))

    existing_version = service.get_artifact_version_by_content_hash(session, chash)
    created = existing_version is None
    if existing_version is not None:
        artifact = service.get_artifact(session, existing_version.artifact_id)
        assert artifact is not None
        version = existing_version
    else:
        artifact = service.create_artifact(
            session,
            title=spec.title,
            kind=spec.kind,
            canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
            conversation_id=conversation_id,
        )
        version = service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=canonical,
            content_hash=chash,
        )
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING)

    render_results: list[FactoryRenderResult] = []
    for fmt in spec.formats():
        row = render_store.ensure_render(
            session,
            store,
            version=version,
            title=artifact.title,
            fmt=fmt,
            canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
        )
        failing_refs: list[str] = []
        if isinstance(row.validation_json, dict):
            failing_refs = list(row.validation_json.get("failing_refs") or [])
        render_results.append(
            FactoryRenderResult(
                format=fmt,
                content_hash=row.content_hash,
                size_bytes=row.size_bytes,
                mime_type=row.mime_type,
                state=row.state,
                failing_refs=failing_refs,
            )
        )

    service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)

    return FactoryResult(
        artifact_id=artifact.id,
        version=version.version,
        title=artifact.title,
        kind=artifact.kind,
        created=created,
        renders=render_results,
    )


def render_validation(
    session: Session, *, artifact_id: uuid.UUID, fmt: str
) -> dict[str, Any] | None:
    """The stored validation report for one artifact's render, or None when the
    artifact/render does not exist. Used by ``GET
    /v1/artifacts/{id}/renders/{fmt}/validation``."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        return None
    version = service.get_current_version(session, artifact.id)
    if version is None:
        return None
    row = service.get_render(session, version.id, fmt)
    if row is None:
        return None
    return {
        "artifact_id": str(artifact_id),
        "format": fmt,
        "state": row.state,
        "validation": row.validation_json,
    }


def revalidate(
    session: Session, store: ObjectStore, *, artifact_id: uuid.UUID, fmt: str
) -> dict[str, Any] | None:
    """Re-run validation on demand (spec §5: ``artifact.validate``) — forces a fresh
    render+validate pass rather than trusting the stored row, since the object store
    or the row could in principle have drifted since the row was last written."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        return None
    version = service.get_current_version(session, artifact.id)
    if version is None:
        return None
    row = render_store.ensure_render(
        session,
        store,
        version=version,
        title=artifact.title,
        fmt=fmt,
        force=True,
        canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    )
    return {
        "artifact_id": str(artifact_id),
        "format": fmt,
        "state": row.state,
        "validation": row.validation_json,
    }


__all__ = [
    "FactoryRenderResult",
    "FactoryResult",
    "create",
    "download_path",
    "render_validation",
    "revalidate",
]
