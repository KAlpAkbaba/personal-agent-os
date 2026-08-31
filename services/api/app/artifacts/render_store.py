"""Render orchestration: canonical body -> render bytes -> ObjectStore + row.

Bridges the pure `renderers` module, the `ObjectStore` seam and the persistence
`service`. Render bytes live in object storage (MinIO/S3); the row in
`artifact_renders` records the object key, mime type, content_hash and size.
Operations are idempotent and self-healing: a missing object is regenerated from
the canonical body on demand.
"""

import uuid

from sqlalchemy.orm import Session

from app.artifacts import renderers, service
from app.artifacts.models import ArtifactRender, ArtifactVersion
from app.object_store import ObjectStore, validate_object_key


def render_object_key(artifact_id: uuid.UUID, version: int, fmt: str) -> str:
    ext = renderers.EXTENSIONS[fmt]
    key = f"artifacts/{artifact_id.hex}/v{version}/report.{ext}"
    return validate_object_key(key)


def ensure_render(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    fmt: str,
    force: bool = False,
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

    result = renderers.render(fmt, title=title, canonical_markdown=version.canonical_body)
    store.put(key, result.data, content_type=result.mime_type)
    return service.record_render(
        session,
        artifact_version_id=version.id,
        fmt=fmt,
        object_key=key,
        mime_type=result.mime_type,
        content_hash=result.content_hash,
        size_bytes=len(result.data),
    )


def ensure_renders(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    formats: tuple[str, ...],
) -> list[ArtifactRender]:
    return [
        ensure_render(session, store, version=version, title=title, fmt=fmt)
        for fmt in formats
    ]


def fetch_render_bytes(
    session: Session,
    store: ObjectStore,
    *,
    version: ArtifactVersion,
    title: str,
    fmt: str,
) -> tuple[bytes, str, str]:
    """Return (bytes, mime_type, content_hash), regenerating if the object is
    missing from the store."""
    render = ensure_render(session, store, version=version, title=title, fmt=fmt)
    try:
        data = store.get(render.object_key)
    except KeyError:
        render = ensure_render(
            session, store, version=version, title=title, fmt=fmt, force=True
        )
        data = store.get(render.object_key)
    return data, render.mime_type, render.content_hash
