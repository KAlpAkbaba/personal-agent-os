"""Render orchestration: canonical body -> render bytes -> ObjectStore + row.

Bridges the pure `renderers` module, the `ObjectStore` seam and the persistence
`service`. Render bytes live in object storage (MinIO/S3); the row in
`artifact_renders` records the object key, mime type, content_hash and size.
Operations are idempotent and self-healing: a missing object is regenerated from
the canonical body on demand.

M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md, ADR-0085): the SAME artifact_versions row
now holds two different kinds of "canonical body" depending on
``artifact.canonical_format`` — Markdown (M13 research reports; the default, so
every pre-M22 call site that never passes ``canonical_format`` is unaffected) or an
``ArtifactSpec``'s own canonical JSON (M22 factory artifacts). ``ensure_render``
branches on that flag to pick the right renderer AND, for a factory artifact, runs
the independent-reader ``validate()`` on every (re)generation — including the
self-healing path, so a render regenerated after its object was lost is
re-validated, not just trusted from its first creation.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.artifacts import renderers, service
from app.artifacts import validation as validation_module
from app.artifacts.models import (
    CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    CANONICAL_FORMAT_MARKDOWN,
    RENDER_STATE_INVALID,
    RENDER_STATE_VALID,
    ArtifactRender,
    ArtifactVersion,
)
from app.artifacts.spec import ArtifactSpec
from app.object_store import ObjectStore, validate_object_key


def render_object_key(artifact_id: uuid.UUID, version: int, fmt: str) -> str:
    ext = renderers.EXTENSIONS[fmt]
    key = f"artifacts/{artifact_id.hex}/v{version}/report.{ext}"
    return validate_object_key(key)


def _render_bytes(
    *, title: str, canonical_body: str, fmt: str, canonical_format: str
) -> tuple[bytes, str, str, dict[str, Any] | None, str]:
    """(data, mime_type, content_hash, validation_json, state) for one (format,
    canonical_format) pair — the one place ``ensure_render`` decides which renderer
    (and, for a factory artifact, which independent validator) to run."""
    if canonical_format == CANONICAL_FORMAT_ARTIFACT_SPEC_JSON:
        spec = ArtifactSpec.model_validate_json(canonical_body)
        result = renderers.render_factory(fmt, spec)
        report = validation_module.validate(spec, fmt, result.data)
        state = RENDER_STATE_VALID if report.ok else RENDER_STATE_INVALID
        return result.data, result.mime_type, result.content_hash, report.to_dict(), state
    result = renderers.render(fmt, title=title, canonical_markdown=canonical_body)
    return result.data, result.mime_type, result.content_hash, None, RENDER_STATE_VALID


def ensure_render(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    fmt: str,
    force: bool = False,
    canonical_format: str = CANONICAL_FORMAT_MARKDOWN,
) -> ArtifactRender:
    """Return a render row, creating (or repairing) the stored object as needed.

    Idempotent: if the row exists and its object is present in the store, it is
    returned untouched. If the row exists but the object is gone (or force), the
    bytes are regenerated from the canonical body and re-uploaded.
    """
    existing = service.get_render(session, version.id, fmt)
    key = render_object_key(version.artifact_id, version.version, fmt)
    if existing is not None and not force and store.exists(existing.object_key):
        return existing

    data, mime_type, chash, validation_json, state = _render_bytes(
        title=title,
        canonical_body=version.canonical_body,
        fmt=fmt,
        canonical_format=canonical_format,
    )
    store.put(key, data, content_type=mime_type)
    return service.record_render(
        session,
        artifact_version_id=version.id,
        fmt=fmt,
        object_key=key,
        mime_type=mime_type,
        content_hash=chash,
        size_bytes=len(data),
        validation_json=validation_json,
        state=state,
    )


def ensure_renders(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    formats: tuple[str, ...],
    canonical_format: str = CANONICAL_FORMAT_MARKDOWN,
) -> list[ArtifactRender]:
    return [
        ensure_render(
            session,
            store,
            version=version,
            title=title,
            fmt=fmt,
            canonical_format=canonical_format,
        )
        for fmt in formats
    ]


def fetch_render_bytes(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    fmt: str,
    canonical_format: str = CANONICAL_FORMAT_MARKDOWN,
) -> tuple[bytes, str, str]:
    """Return (bytes, mime_type, content_hash), regenerating if the object is
    missing from the store."""
    render = ensure_render(
        session, store, version=version, title=title, fmt=fmt, canonical_format=canonical_format
    )
    try:
        data = store.get(render.object_key)
    except KeyError:
        render = ensure_render(
            session,
            store,
            version=version,
            title=title,
            fmt=fmt,
            force=True,
            canonical_format=canonical_format,
        )
        data = store.get(render.object_key)
    return data, render.mime_type, render.content_hash
