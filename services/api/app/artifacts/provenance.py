"""Artifact provenance (B42 req 405-408): WHO asked for a version, WHAT produced it, and
FROM WHAT it was made - written on the version row, never inferred later.

* the actor (req 406): the owner's voice session, a REST call, a research task, an
  executive run, an edit or a clone of an earlier version - a kind and a reference;
* the runtime (req 407): the Python and the rendering libraries' versions, read from the
  installed distributions at the moment of rendering (the proof a render is
  reproducible: the same spec under the same library versions gives the same bytes);
* the source manifest (req 408): the spec's identity (kind, title, hash), the numbers
  the owner said, the formats the kind renders to, the counts of what the spec carries,
  and the sources a research task collected - the answer to "what is this file made of".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from typing import Any, Final

from app.artifacts.spec import ArtifactSpec

ACTOR_OWNER_VOICE: Final = "owner_voice"
ACTOR_OWNER_REST: Final = "owner_rest"
ACTOR_RESEARCH_TASK: Final = "research_task"
ACTOR_EXECUTIVE_RUN: Final = "executive_run"
ACTOR_EDIT: Final = "edit"
ACTOR_CLONE: Final = "clone"
ACTOR_SYSTEM: Final = "system"
ACTOR_KINDS: Final[tuple[str, ...]] = (
    ACTOR_OWNER_VOICE,
    ACTOR_OWNER_REST,
    ACTOR_RESEARCH_TASK,
    ACTOR_EXECUTIVE_RUN,
    ACTOR_EDIT,
    ACTOR_CLONE,
    ACTOR_SYSTEM,
)

#: The distributions whose versions decide a render's bytes, by the format they render.
LIBRARIES_BY_FORMAT: Final[dict[str, tuple[str, ...]]] = {
    "docx": ("python-docx",),
    "xlsx": ("openpyxl",),
    "pptx": ("python-pptx",),
    "pdf": ("fpdf2", "pypdf"),
    "html": (),
    "md": (),
    "txt": (),
    "csv": (),
    "json": (),
    # B42 req 400: an image artifact (registered from the creative path) is Pillow's.
    "png": ("pillow",),
    "jpg": ("pillow",),
    "svg": (),
}
#: The renderer module every format's bytes come from (app.artifacts.renderers).
RENDERER_MODULE: Final = "app.artifacts.renderers"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    ref: str | None = None
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "session_id": self.session_id}

    def __post_init__(self) -> None:
        if self.kind not in ACTOR_KINDS:
            raise ValueError(f"unknown actor kind {self.kind!r}")


def library_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


def runtime_provenance(formats: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Req 407: the Python and the libraries behind the formats named (all when None)."""
    wanted = formats or tuple(LIBRARIES_BY_FORMAT)
    libraries: dict[str, str] = {}
    for fmt in wanted:
        for lib in LIBRARIES_BY_FORMAT.get(fmt, ()):
            libraries.setdefault(lib, library_version(lib))
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "libraries": libraries,
        "renderer_module": RENDERER_MODULE,
    }


def source_manifest(
    spec: ArtifactSpec, *, content_hash: str, sources: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Req 408: what the version is made of, as data."""
    counts: dict[str, int] = {}
    if spec.sections is not None:
        counts["sections"] = len(spec.sections)
    if spec.sheets is not None:
        counts["sheets"] = len(spec.sheets)
        counts["rows"] = sum(len(s.rows) for s in spec.sheets)
    if spec.slides is not None:
        counts["slides"] = len(spec.slides)
    if spec.rows is not None:
        counts["rows"] = len(spec.rows)
        counts["columns"] = len(spec.columns or [])
    return {
        "kind": spec.kind,
        "title": spec.title,
        "language": spec.language,
        "spec_hash": content_hash,
        "formats": list(spec.formats()),
        "spoken_numbers": list(spec.spoken_numbers) if spec.spoken_numbers is not None else None,
        "counts": counts,
        "sources": [dict(s) for s in (sources or [])][:200],
    }


def provenance_record(
    *,
    actor: Actor,
    formats: tuple[str, ...],
    derived_from: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """The version's provenance record: actor + runtime + lineage + the moment."""
    return {
        "actor": actor.as_dict(),
        "runtime": runtime_provenance(formats),
        "derived_from": dict(derived_from) if derived_from else None,
        "note": note,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def build_provenance(
    spec: ArtifactSpec,
    *,
    actor: Actor,
    derived_from: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """The provenance of a version rendered from a spec (the factory, an edit, a clone)."""
    return provenance_record(
        actor=actor, formats=spec.formats(), derived_from=derived_from, note=note
    )


__all__ = [
    "ACTOR_CLONE",
    "ACTOR_EDIT",
    "ACTOR_EXECUTIVE_RUN",
    "ACTOR_KINDS",
    "ACTOR_OWNER_REST",
    "ACTOR_OWNER_VOICE",
    "ACTOR_RESEARCH_TASK",
    "ACTOR_SYSTEM",
    "LIBRARIES_BY_FORMAT",
    "Actor",
    "build_provenance",
    "library_version",
    "provenance_record",
    "runtime_provenance",
    "source_manifest",
]
