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
from app.artifacts.provenance import ACTOR_SYSTEM, Actor, build_provenance, source_manifest
from app.artifacts.renderers import content_hash
from app.artifacts.spec import ArtifactSpec
from app.ledger.vocabulary import SUBSYSTEM_ARTIFACTS
from app.object_store import ObjectStore
from app.uistate import UiState
from app.uistate import publish as publish_ui_state

#: The verdict tokens ``artifact.factory`` publishes in ``metadata.verdict`` (M22 spec
#: §6, contract v7): ``rendering`` while this module writes/reopens the bytes, ``valid``
#: once the independent reader found every element asked for, ``invalid`` when it did
#: not. Spelled once here so the tool layer and this module never drift.
VERDICT_RENDERING = "rendering"
VERDICT_VALID = "valid"
VERDICT_INVALID = "invalid"


def _publish_factory_event(
    *, title: str, fmt: str | None, verdict: str | None, failing_refs: list[str] | None = None
) -> None:
    """``artifact.factory`` on the UI-state bus (contract v7): metadata is bounded,
    short tokens only — never a validation report, never a rendered byte (module
    docstring: the report is a row on ``artifact_renders``, read from the list route,
    never from the bus)."""
    metadata: dict[str, object] = {}
    if fmt:
        metadata["format"] = fmt[:32]
    if verdict:
        metadata["verdict"] = verdict
    if failing_refs:
        metadata["failing_ref"] = str(failing_refs[0])[:64]
    publish_ui_state(
        UiState.ARTIFACT_FACTORY,
        subsystem=SUBSYSTEM_ARTIFACTS,
        label=title[:64] if title else None,
        metadata=metadata,
    )


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
    actor: Actor | None = None,
    sources: list[dict[str, Any]] | None = None,
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
        # B42 (req 405-408): the version says who asked, what rendered it and what it is
        # made of - recorded now, never inferred later.
        who = actor or Actor(ACTOR_SYSTEM)
        version = service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=canonical,
            content_hash=chash,
            source_manifest=source_manifest(spec, content_hash=chash, sources=sources),
            provenance=build_provenance(spec, actor=who),
        )
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING)

    render_results: list[FactoryRenderResult] = []
    for fmt in spec.formats():
        _publish_factory_event(title=artifact.title, fmt=fmt, verdict=VERDICT_RENDERING)
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
        _publish_factory_event(
            title=artifact.title,
            fmt=fmt,
            verdict=VERDICT_INVALID if row.state == RENDER_STATE_INVALID else VERDICT_VALID,
            failing_refs=failing_refs,
        )
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
    is_factory = artifact.canonical_format == CANONICAL_FORMAT_ARTIFACT_SPEC_JSON
    if is_factory:
        _publish_factory_event(title=artifact.title, fmt=fmt, verdict=VERDICT_RENDERING)
    row = render_store.ensure_render(
        session,
        store,
        version=version,
        title=artifact.title,
        fmt=fmt,
        force=True,
        canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    )
    if is_factory:
        failing_refs: list[str] = []
        if isinstance(row.validation_json, dict):
            failing_refs = list(row.validation_json.get("failing_refs") or [])
        _publish_factory_event(
            title=artifact.title,
            fmt=fmt,
            verdict=VERDICT_INVALID if row.state == RENDER_STATE_INVALID else VERDICT_VALID,
            failing_refs=failing_refs,
        )
    return {
        "artifact_id": str(artifact_id),
        "format": fmt,
        "state": row.state,
        "validation": row.validation_json,
    }


def render_format(
    session: Session, store: ObjectStore, *, artifact_id: uuid.UUID, fmt: str
) -> FactoryRenderResult | None:
    """Render (or reuse the cached render for) ONE format of an EXISTING artifact on
    demand (spec §5: ``artifact.render`` — "Bunu PDF yap" after the artifact already
    exists). Publishes the same ``artifact.factory`` events :func:`create` does; ``None``
    when the artifact or its current version does not exist. Raises ``ValueError`` when
    ``fmt`` is not one this artifact's kind can produce (``render_store.ensure_render``'s
    own refusal, e.g. asking a presentation for ``xlsx``)."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        return None
    version = service.get_current_version(session, artifact.id)
    if version is None:
        return None
    is_factory = artifact.canonical_format == CANONICAL_FORMAT_ARTIFACT_SPEC_JSON
    if is_factory:
        _publish_factory_event(title=artifact.title, fmt=fmt, verdict=VERDICT_RENDERING)
    row = render_store.ensure_render(
        session,
        store,
        version=version,
        title=artifact.title,
        fmt=fmt,
        canonical_format=artifact.canonical_format,
    )
    failing_refs: list[str] = []
    if isinstance(row.validation_json, dict):
        failing_refs = list(row.validation_json.get("failing_refs") or [])
    if is_factory:
        _publish_factory_event(
            title=artifact.title,
            fmt=fmt,
            verdict=VERDICT_INVALID if row.state == RENDER_STATE_INVALID else VERDICT_VALID,
            failing_refs=failing_refs,
        )
    return FactoryRenderResult(
        format=fmt,
        content_hash=row.content_hash,
        size_bytes=row.size_bytes,
        mime_type=row.mime_type,
        state=row.state,
        failing_refs=failing_refs,
    )


def revalidate_all(
    session: Session, store: ObjectStore, *, artifact_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    """Re-validate EVERY render this artifact currently has (spec §5: ``artifact.validate``
    names no format — "Bu dosya doğru mu?" asks about the whole artifact). ``None`` when
    the artifact/version does not exist; ``[]`` when it exists but has no renders yet."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        return None
    version = service.get_current_version(session, artifact.id)
    if version is None:
        return None
    results: list[dict[str, Any]] = []
    for row in service.list_renders(session, version.id):
        result = revalidate(session, store, artifact_id=artifact_id, fmt=row.format)
        if result is not None:
            results.append(result)
    return results


__all__ = [
    "FactoryRenderResult",
    "FactoryResult",
    "VERDICT_INVALID",
    "VERDICT_RENDERING",
    "VERDICT_VALID",
    "create",
    "download_path",
    "render_format",
    "render_validation",
    "revalidate",
    "revalidate_all",
]
