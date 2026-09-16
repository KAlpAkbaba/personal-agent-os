"""An artifact's life after its first render (B42 req 409-412, 415, 416): a version that
carries its provenance, an EDIT that is a new version of the SAME artifact (never a new
artifact whose past is lost), a CLONE that is a new artifact whose provenance names the
one it came from, a DELETE that answers to the owner's policy, and a COMPARE / DIFF
between two versions or two artifacts that names what changed.

Every render of a new version goes through the same ``render_store.ensure_render`` and
the same independent validation the factory uses (ADR-0085): an edit that breaks the
"numbers never invented" rule is refused by the spec itself before any byte is made.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.artifacts import render_store, service
from app.artifacts.models import (
    ARTIFACT_STATE_ARCHIVED,
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_DRAFT,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
    RENDER_STATE_INVALID,
    RENDER_STATE_VALID,
    Artifact,
    ArtifactVersion,
)
from app.artifacts.provenance import (
    ACTOR_CLONE,
    ACTOR_EDIT,
    Actor,
    build_provenance,
    provenance_record,
    source_manifest,
)
from app.artifacts.renderers import content_hash
from app.artifacts.spec import ArtifactSpec, Section, Sheet, Slide
from app.object_store import ObjectStore, validate_object_key

DELETE_POLICY_CONFIRM: Final = "confirm"
DELETE_POLICY_DENY: Final = "deny"
DELETE_POLICY_FREE: Final = "free"
DELETE_POLICIES: Final[tuple[str, ...]] = (
    DELETE_POLICY_CONFIRM,
    DELETE_POLICY_DENY,
    DELETE_POLICY_FREE,
)
MAX_DIFF_LINES: Final = 400
MAX_EDIT_OPS: Final = 20

#: The owner-facing error classes of the lifecycle (app/errors/catalog.py names each one).
ERROR_NOT_FOUND = "not_found"
ERROR_INVALID_EDIT = "invalid_edit"
ERROR_ARCHIVED = "archived"
ERROR_INVALID_POLICY = "invalid_policy"
ERROR_DELETE_DENIED = "delete_denied"
ERROR_NO_PREVIOUS_VERSION = "no_previous_version"


class ArtifactLifecycleError(Exception):
    def __init__(self, code: str, speech: str) -> None:
        self.code = code
        self.speech = speech
        super().__init__(speech)


# ------------------------------------------------------------------- image (400)

KIND_IMAGE: Final = "image"
CANONICAL_FORMAT_IMAGE_REF: Final = "image_ref_json"
IMAGE_MIME_BY_FORMAT: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}
ERROR_IMAGE_NOT_EDITABLE = "image_not_editable"
ERROR_OBJECT_MISSING = "object_missing"


def _image_format(object_key: str) -> str:
    suffix = object_key.rsplit(".", 1)[-1].lower() if "." in object_key else ""
    if suffix not in IMAGE_MIME_BY_FORMAT:
        raise ArtifactLifecycleError(ERROR_INVALID_EDIT, "Bu nesne bir görsel değil efendim.")
    return "jpg" if suffix == "jpeg" else suffix


def _image_ref(version: ArtifactVersion) -> dict[str, Any]:
    return json.loads(version.canonical_body)


def register_image_artifact(
    session: Session,
    store: ObjectStore,
    *,
    title: str,
    object_key: str,
    actor: Actor,
    sources: list[dict[str, Any]] | None = None,
    derived_from: dict[str, Any] | None = None,
    conversation_id: uuid.UUID | None = None,
) -> VersionResult:
    """Req 400: an image the creative path (or anything else) already stored becomes an
    artifact of its own: the bytes are COPIED under the artifact's own key (so deleting
    the artifact never deletes the creative run's output, and vice versa), the version
    carries the same provenance and source manifest every other kind carries, and the
    render row is validated against the bytes' own hash."""
    key = validate_object_key(object_key)
    if not store.exists(key):
        raise ArtifactLifecycleError(
            ERROR_OBJECT_MISSING, "Kaynak görseli depoda bulamadım efendim."
        )
    fmt = _image_format(key)
    data = store.get(key)
    sha = hashlib.sha256(data).hexdigest()
    artifact = service.create_artifact(
        session,
        title=title[:500],
        kind=KIND_IMAGE,
        canonical_format=CANONICAL_FORMAT_IMAGE_REF,
        conversation_id=conversation_id,
    )
    own_key = validate_object_key(f"artifacts/{artifact.id}/v1/{artifact.id}.{fmt}")
    store.put(own_key, data, content_type=IMAGE_MIME_BY_FORMAT[fmt])
    ref = {
        "kind": KIND_IMAGE,
        "title": title[:500],
        "format": fmt,
        "sha256": sha,
        "size_bytes": len(data),
        "source_key": key,
    }
    canonical = json.dumps(ref, ensure_ascii=False, sort_keys=True)
    manifest = {
        "kind": KIND_IMAGE,
        "title": title[:500],
        "language": None,
        "spec_hash": sha,
        "formats": [fmt],
        "spoken_numbers": None,
        "counts": {"bytes": len(data)},
        "sources": [dict(s) for s in (sources or [])][:200],
    }
    version = service.add_artifact_version(
        session,
        artifact_id=artifact.id,
        canonical_body=canonical,
        content_hash=sha,
        source_manifest=manifest,
        provenance=provenance_record(actor=actor, formats=(fmt,), derived_from=derived_from),
    )
    stored = store.get(own_key)
    valid = hashlib.sha256(stored).hexdigest() == sha and len(stored) == len(data)
    render = service.record_render(
        session,
        artifact_version_id=version.id,
        fmt=fmt,
        object_key=own_key,
        mime_type=IMAGE_MIME_BY_FORMAT[fmt],
        content_hash=sha,
        size_bytes=len(data),
        validation_json={
            "checks": [{"check": "bytes_match_source", "ok": valid}],
            "failing_refs": [] if valid else ["bytes"],
        },
        state=RENDER_STATE_VALID if valid else RENDER_STATE_INVALID,
    )
    # The same legal path the factory walks (app.artifacts.state): never DRAFT -> READY.
    service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
    service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING)
    service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)
    return VersionResult(
        artifact.id,
        version.version,
        True,
        [
            {
                "format": fmt,
                "state": render.state,
                "content_hash": render.content_hash,
                "size_bytes": render.size_bytes,
            }
        ],
    )


def _clone_image(
    session: Session,
    store: ObjectStore,
    source: Artifact,
    version: ArtifactVersion,
    *,
    title: str | None,
    actor: Actor,
) -> VersionResult:
    renders = service.list_renders(session, version.id)
    if not renders:
        raise ArtifactLifecycleError(
            ERROR_NOT_FOUND, "Kopyalanacak görsel dosyasını bulamadım efendim."
        )
    return register_image_artifact(
        session,
        store,
        title=title or source.title,
        object_key=renders[0].object_key,
        actor=Actor(ACTOR_CLONE, ref=actor.ref, session_id=actor.session_id),
        derived_from={"artifact_id": str(source.id), "version": version.version, "how": "clone"},
        conversation_id=source.conversation_id,
    )


def _compare_images(
    art_a: Artifact, ver_a: ArtifactVersion, art_b: Artifact, ver_b: ArtifactVersion
) -> dict[str, Any]:
    ref_a = _image_ref(ver_a) if art_a.kind == KIND_IMAGE else None
    ref_b = _image_ref(ver_b) if art_b.kind == KIND_IMAGE else None
    label_a = f"{art_a.title} v{ver_a.version}"
    label_b = f"{art_b.title} v{ver_b.version}"
    same = ref_a is not None and ref_b is not None and ref_a["sha256"] == ref_b["sha256"]
    comparison = {
        "same": same,
        "kind": {"a": art_a.kind, "b": art_b.kind, "same": art_a.kind == art_b.kind},
        "title": {"a": art_a.title, "b": art_b.title, "same": art_a.title == art_b.title},
        "parts": {
            "added": [],
            "removed": [],
            "changed": [] if same else ["görsel"],
            "kept": 1 if same else 0,
        },
        "rows": {"a": 0, "b": 0},
        "numbers": {"only_in_a": [], "only_in_b": []},
        "bytes": {
            "a": ref_a["size_bytes"] if ref_a else None,
            "b": ref_b["size_bytes"] if ref_b else None,
        },
    }
    if same:
        speech = f"{label_a} ile {label_b} birebir aynı görsel efendim."
    elif ref_a is None or ref_b is None:
        speech = (
            f"{label_a} ile {label_b} arasında: türleri farklı "
            f"({art_a.kind} / {art_b.kind}) efendim."
        )
    else:
        speech = (
            f"{label_a} ile {label_b} farklı görseller efendim "
            f"({ref_a['size_bytes']} / {ref_b['size_bytes']} bayt)."
        )
    lines = (
        []
        if same
        else [
            f"--- {label_a}",
            f"+++ {label_b}",
            f"-sha256 {ref_a['sha256'] if ref_a else '-'}",
            f"+sha256 {ref_b['sha256'] if ref_b else '-'}",
        ]
    )
    return {
        "a": {"artifact_id": str(art_a.id), "version": ver_a.version, "title": art_a.title},
        "b": {"artifact_id": str(art_b.id), "version": ver_b.version, "title": art_b.title},
        "comparison": comparison,
        "speech": speech,
        "diff": {
            "lines": lines,
            "truncated": False,
            "added": 0 if same else 1,
            "removed": 0 if same else 1,
        },
    }


# ------------------------------------------------------------------------ edit (410)


class EditOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal[
        "set_title",
        "append_section",
        "replace_section",
        "remove_section",
        "append_slide",
        "append_row",
        "append_bullet",
    ]
    title: str | None = Field(default=None, max_length=500)
    heading: str | None = Field(default=None, max_length=500)
    paragraphs: list[str] | None = None
    bullets: list[str] | None = None
    sheet: str | None = Field(default=None, max_length=200)
    row: list[Any] | None = None
    notes: str | None = None


class ArtifactEdit(BaseModel):
    """A bounded set of edits, with the numbers the owner said in the edit sentence so the
    spec's own "never invented" rule can admit them."""

    model_config = ConfigDict(extra="forbid")
    ops: list[EditOp] = Field(min_length=1, max_length=MAX_EDIT_OPS)
    spoken_numbers: list[float] | None = None


def _find_section(sections: list[Section], heading: str) -> int:
    wanted = heading.strip().casefold()
    for i, section in enumerate(sections):
        if section.heading.strip().casefold() == wanted:
            return i
    return -1


_CELL_RE = re.compile(r"([A-Z]{1,3})(\d+)")


def _shift_cell(text: str, *, from_row: int, by: int) -> str:
    """Every A1-style reference at or below ``from_row`` moved down by ``by`` rows."""

    def move(match: re.Match[str]) -> str:
        row = int(match.group(2))
        return f"{match.group(1)}{row + by}" if row >= from_row else match.group(0)

    return _CELL_RE.sub(move, text)


def _shift_formulas(
    formulas: dict[str, str] | None, *, from_row: int, by: int = 1
) -> dict[str, str] | None:
    """A sheet's fixed-cell formulas (``{"B6": "=B5*0.2"}``) after a row is inserted above
    them: the cell keys and the references inside move together, so a KDV row that read
    the totals row still reads the totals row."""
    if not formulas:
        return formulas
    return {
        _shift_cell(key, from_row=from_row, by=by): _shift_cell(value, from_row=from_row, by=by)
        for key, value in formulas.items()
    }


def apply_edit(spec: ArtifactSpec, edit: ArtifactEdit) -> ArtifactSpec:
    """The spec with the edits applied, re-validated by the spec's own rules."""
    data = spec.model_dump(mode="python", exclude_none=True)
    sections = (
        [Section.model_validate(s) for s in data.get("sections") or []]
        if spec.sections is not None
        else None
    )
    slides = (
        [Slide.model_validate(s) for s in data.get("slides") or []]
        if spec.slides is not None
        else None
    )
    sheets = (
        [Sheet.model_validate(s) for s in data.get("sheets") or []]
        if spec.sheets is not None
        else None
    )
    rows = [list(r) for r in data.get("rows") or []] if spec.rows is not None else None
    for op in edit.ops:
        if op.op == "set_title":
            if not op.title:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Yeni başlığı anlayamadım efendim."
                )
            data["title"] = op.title
        elif op.op in ("append_section", "replace_section", "remove_section", "append_bullet"):
            if sections is None:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT,
                    "Bu artefaktın bölümleri yok efendim; bu düzenleme ona uymaz.",
                )
            if not op.heading:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Hangi bölüm olduğunu söyler misiniz efendim?"
                )
            at = _find_section(sections, op.heading)
            if op.op == "append_section":
                if at >= 0:
                    raise ArtifactLifecycleError(
                        ERROR_INVALID_EDIT, f"'{op.heading}' diye bir bölüm zaten var efendim."
                    )
                sections.append(
                    Section(
                        heading=op.heading,
                        paragraphs=list(op.paragraphs or []),
                        bullets=list(op.bullets) if op.bullets else None,
                    )
                )
            elif at < 0:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, f"'{op.heading}' diye bir bölüm yok efendim."
                )
            elif op.op == "replace_section":
                current = sections[at]
                sections[at] = Section(
                    heading=current.heading,
                    level=current.level,
                    paragraphs=list(op.paragraphs)
                    if op.paragraphs is not None
                    else list(current.paragraphs),
                    bullets=list(op.bullets) if op.bullets is not None else current.bullets,
                    table=current.table,
                )
            elif op.op == "remove_section":
                del sections[at]
            else:
                current = sections[at]
                sections[at] = Section(
                    heading=current.heading,
                    level=current.level,
                    paragraphs=list(current.paragraphs),
                    bullets=list(current.bullets or []) + list(op.bullets or []),
                    table=current.table,
                )
        elif op.op == "append_slide":
            if slides is None:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Bu artefaktın slaytları yok efendim."
                )
            if not op.title:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Slaytın başlığını söyler misiniz efendim?"
                )
            slides.append(Slide(title=op.title, bullets=list(op.bullets or []), notes=op.notes))
        elif op.op == "append_row":
            if op.row is None:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Eklenecek satırı anlayamadım efendim."
                )
            if sheets is not None:
                index = 0
                if op.sheet:
                    index = next(
                        (
                            i
                            for i, s in enumerate(sheets)
                            if s.name.casefold() == op.sheet.casefold()
                        ),
                        -1,
                    )
                    if index < 0:
                        raise ArtifactLifecycleError(
                            ERROR_INVALID_EDIT, f"'{op.sheet}' diye bir sayfa yok efendim."
                        )
                sheet = sheets[index]
                # The totals row and every fixed-cell formula below the data move down
                # with the new row (a formula that pointed at the totals still does).
                first_below = len(sheet.rows) + 2  # header + data rows, 1-based
                sheets[index] = Sheet(
                    name=sheet.name,
                    columns=list(sheet.columns),
                    rows=[list(r) for r in sheet.rows] + [list(op.row)],
                    totals=sheet.totals,
                    formulas=_shift_formulas(sheet.formulas, from_row=first_below),
                )
            elif rows is not None:
                rows.append(list(op.row))
            else:
                raise ArtifactLifecycleError(
                    ERROR_INVALID_EDIT, "Bu artefaktın satırları yok efendim."
                )
    if sections is not None:
        data["sections"] = [s.model_dump(mode="python", exclude_none=True) for s in sections]
    if slides is not None:
        data["slides"] = [s.model_dump(mode="python", exclude_none=True) for s in slides]
    if sheets is not None:
        data["sheets"] = [s.model_dump(mode="python", exclude_none=True) for s in sheets]
    if rows is not None:
        data["rows"] = rows
    if edit.spoken_numbers is not None and spec.spoken_numbers is not None:
        data["spoken_numbers"] = sorted(set(spec.spoken_numbers) | set(edit.spoken_numbers))
    try:
        return ArtifactSpec.model_validate(data)
    except ValidationError as exc:
        # The spec's own refusals (never invented, no formula injection) keep their
        # class: the callers map them the way create does. Anything else is an edit
        # that does not fit, named.
        if "invented_number[" in str(exc) or "formula_injection[" in str(exc):
            raise
        first = exc.errors()[0] if exc.errors() else {}
        message = str(first.get("msg") or "")[:200]
        raise ArtifactLifecycleError(
            ERROR_INVALID_EDIT, f"Düzenleme artefaktın kurallarına uymadı efendim: {message}"
        ) from exc


@dataclass(slots=True)
class VersionResult:
    artifact_id: uuid.UUID
    version: int
    created: bool
    renders: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return all(r["state"] != RENDER_STATE_INVALID for r in self.renders)

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": str(self.artifact_id),
            "version": self.version,
            "created": self.created,
            "all_valid": self.all_valid,
            "renders": list(self.renders),
        }


def _render_all(
    session: Session,
    store: ObjectStore,
    artifact: Artifact,
    version: ArtifactVersion,
    spec: ArtifactSpec,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fmt in spec.formats():
        row = render_store.ensure_render(
            session,
            store,
            version=version,
            title=artifact.title,
            fmt=fmt,
            canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
        )
        out.append(
            {
                "format": fmt,
                "state": row.state,
                "content_hash": row.content_hash,
                "size_bytes": row.size_bytes,
            }
        )
    return out


def _spec_of(version: ArtifactVersion) -> ArtifactSpec:
    return ArtifactSpec.model_validate(json.loads(version.canonical_body))


def add_version(
    session: Session,
    store: ObjectStore,
    artifact: Artifact,
    spec: ArtifactSpec,
    *,
    actor: Actor,
    derived_from: dict[str, Any] | None,
    note: str | None = None,
) -> VersionResult:
    """A new version of ``artifact`` for ``spec`` (idempotent on the spec's hash), with
    its provenance and source manifest, rendered and validated in every format."""
    canonical = spec.canonical_json()
    chash = content_hash(canonical.encode("utf-8"))
    before = artifact.current_version
    if artifact.state in (ARTIFACT_STATE_READY, ARTIFACT_STATE_DRAFT):
        # A fresh clone starts in DRAFT; an edited artifact leaves READY - both reach
        # CANONICAL_READY, the only legal door to the render states (app.artifacts.state).
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_CANONICAL_READY)
    version = service.add_artifact_version(
        session,
        artifact_id=artifact.id,
        canonical_body=canonical,
        content_hash=chash,
        source_manifest=source_manifest(spec, content_hash=chash),
        provenance=build_provenance(spec, actor=actor, derived_from=derived_from, note=note),
    )
    created = version.version != before
    if spec.title != artifact.title:
        artifact.title = spec.title
        artifact.updated_at = datetime.now(UTC)
        session.commit()
    if artifact.state == ARTIFACT_STATE_CANONICAL_READY:
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING)
    renders = _render_all(session, store, artifact, version, spec)
    if artifact.state != ARTIFACT_STATE_READY:
        service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)
    return VersionResult(
        artifact_id=artifact.id, version=version.version, created=created, renders=renders
    )


def edit_artifact(
    session: Session,
    store: ObjectStore,
    artifact_id: uuid.UUID,
    edit: ArtifactEdit,
    *,
    actor: Actor,
) -> VersionResult:
    """Req 409/410: the edit applied to the CURRENT version's spec becomes the next
    version of the same artifact; the past stays readable."""
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Böyle bir artefakt bulamadım efendim.")
    if artifact.state == ARTIFACT_STATE_ARCHIVED:
        raise ArtifactLifecycleError(ERROR_ARCHIVED, "Bu artefakt silinmiş; düzenlenemez efendim.")
    if artifact.kind == KIND_IMAGE:
        raise ArtifactLifecycleError(
            ERROR_IMAGE_NOT_EDITABLE,
            "Görsel artefakt burada düzenlenmez efendim; yaratıcı araçla yeni bir sürüm üretilir.",
        )
    current = service.get_current_version(session, artifact_id)
    if current is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Bu artefaktın henüz bir sürümü yok efendim.")
    new_spec = apply_edit(_spec_of(current), edit)
    lineage = {
        "artifact_id": str(artifact.id),
        "version": current.version,
        "how": "edit",
        "ops": [op.op for op in edit.ops],
    }
    return add_version(
        session,
        store,
        artifact,
        new_spec,
        actor=Actor(ACTOR_EDIT, ref=actor.ref, session_id=actor.session_id),
        derived_from=lineage,
    )


# ------------------------------------------------------------------------ clone (411)


def clone_artifact(
    session: Session,
    store: ObjectStore,
    artifact_id: uuid.UUID,
    *,
    title: str | None,
    actor: Actor,
    version_number: int | None = None,
) -> VersionResult:
    """Req 411: a new artifact whose first version is a copy of the source version's spec,
    and whose provenance names the source."""
    source = service.get_artifact(session, artifact_id)
    if source is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Böyle bir artefakt bulamadım efendim.")
    version = (
        service.get_version(session, artifact_id, version_number)
        if version_number
        else service.get_current_version(session, artifact_id)
    )
    if version is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Kopyalanacak sürümü bulamadım efendim.")
    if source.kind == KIND_IMAGE:
        return _clone_image(session, store, source, version, title=title, actor=actor)
    spec = _spec_of(version)
    if title:
        spec = ArtifactSpec.model_validate(
            {**spec.model_dump(mode="python", exclude_none=True), "title": title[:500]}
        )
    new_artifact = service.create_artifact(
        session,
        title=spec.title,
        kind=spec.kind,
        canonical_format=CANONICAL_FORMAT_ARTIFACT_SPEC_JSON,
        conversation_id=source.conversation_id,
        project_id=source.project_id,
    )
    lineage = {"artifact_id": str(source.id), "version": version.version, "how": "clone"}
    return add_version(
        session,
        store,
        new_artifact,
        spec,
        actor=Actor(ACTOR_CLONE, ref=actor.ref, session_id=actor.session_id),
        derived_from=lineage,
    )


# ----------------------------------------------------------------------- delete (412)


@dataclass(slots=True)
class DeleteOutcome:
    status: str  # deleted | needs_confirmation | denied
    artifact_id: uuid.UUID
    renders_removed: int = 0
    speech: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": str(self.artifact_id),
            "renders_removed": self.renders_removed,
            "speech": self.speech,
        }


def delete_artifact(
    session: Session, store: ObjectStore, artifact_id: uuid.UUID, *, policy: str, confirmed: bool
) -> DeleteOutcome:
    """Req 412: the owner's policy decides. ``deny`` never deletes; ``confirm`` deletes
    only with an explicit yes in the same call; ``free`` deletes at once. Deleting
    removes every rendered object from the store and archives the artifact: the row,
    its versions and their provenance stay as the record that it existed."""
    if policy not in DELETE_POLICIES:
        raise ArtifactLifecycleError(ERROR_INVALID_POLICY, f"Bilinmeyen silme politikası: {policy}")
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Böyle bir artefakt bulamadım efendim.")
    if artifact.state == ARTIFACT_STATE_ARCHIVED:
        return DeleteOutcome("deleted", artifact.id, 0, f"{artifact.title} zaten silinmiş efendim.")
    if policy == DELETE_POLICY_DENY:
        return DeleteOutcome(
            "denied", artifact.id, 0, "Silme politikanız artefakt silmeye izin vermiyor efendim."
        )
    if policy == DELETE_POLICY_CONFIRM and not confirmed:
        return DeleteOutcome(
            "needs_confirmation",
            artifact.id,
            0,
            f"{artifact.title} artefaktını silmemi onaylıyor musunuz efendim? 'Evet, sil' deyin.",
        )
    removed = 0
    for version in service.list_versions(session, artifact.id):
        for render in service.list_renders(session, version.id):
            try:
                store.delete(render.object_key)
                removed += 1
            except Exception:  # noqa: BLE001 - an object already gone is still gone
                pass
    service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_ARCHIVED)
    return DeleteOutcome(
        "deleted",
        artifact.id,
        removed,
        f"{artifact.title} silindi efendim: {removed} dosya kaldırıldı, kaydı ve geçmişi duruyor.",
    )


# ------------------------------------------------------------ compare and diff (415, 416)


def _headings(spec: ArtifactSpec) -> list[str]:
    if spec.sections is not None:
        return [s.heading for s in spec.sections]
    if spec.slides is not None:
        return [s.title for s in spec.slides]
    if spec.sheets is not None:
        return [s.name for s in spec.sheets]
    return []


def _numbers(spec: ArtifactSpec) -> set[float]:
    return {n for n, _ref in spec._scan_numbers_with_refs()} | spec._structure_numbers()


def compare_specs(a: ArtifactSpec, b: ArtifactSpec) -> dict[str, Any]:
    """Req 415: what differs between two specs, named - never a bare "different"."""
    heads_a, heads_b = _headings(a), _headings(b)
    added = [h for h in heads_b if h not in heads_a]
    removed = [h for h in heads_a if h not in heads_b]
    kept = [h for h in heads_a if h in heads_b]
    changed: list[str] = []
    if a.sections is not None and b.sections is not None:
        by_b = {s.heading: s for s in b.sections}
        for s in a.sections:
            other = by_b.get(s.heading)
            if other is not None and (
                s.paragraphs != other.paragraphs or (s.bullets or []) != (other.bullets or [])
            ):
                changed.append(s.heading)
    nums_a, nums_b = _numbers(a), _numbers(b)
    rows_a = sum(len(s.rows) for s in a.sheets) if a.sheets else len(a.rows or [])
    rows_b = sum(len(s.rows) for s in b.sheets) if b.sheets else len(b.rows or [])
    same = a.canonical_json() == b.canonical_json()
    return {
        "same": same,
        "kind": {"a": a.kind, "b": b.kind, "same": a.kind == b.kind},
        "title": {"a": a.title, "b": b.title, "same": a.title == b.title},
        "parts": {"added": added, "removed": removed, "changed": changed, "kept": len(kept)},
        "rows": {"a": rows_a, "b": rows_b},
        "numbers": {"only_in_a": sorted(nums_a - nums_b), "only_in_b": sorted(nums_b - nums_a)},
    }


def compare_sentence(result: dict[str, Any], *, label_a: str, label_b: str) -> str:
    if result["same"]:
        return f"{label_a} ile {label_b} birebir aynı efendim."
    parts: list[str] = []
    if not result["kind"]["same"]:
        parts.append(f"türleri farklı ({result['kind']['a']} / {result['kind']['b']})")
    if not result["title"]["same"]:
        parts.append("başlık değişmiş")
    p = result["parts"]
    if p["added"]:
        parts.append("eklenen: " + ", ".join(p["added"][:5]))
    if p["removed"]:
        parts.append("çıkarılan: " + ", ".join(p["removed"][:5]))
    if p["changed"]:
        parts.append("değişen: " + ", ".join(p["changed"][:5]))
    if result["rows"]["a"] != result["rows"]["b"]:
        parts.append(f"satır {result['rows']['a']} → {result['rows']['b']}")
    n = result["numbers"]
    if n["only_in_a"] or n["only_in_b"]:
        parts.append(f"{len(n['only_in_a'])} sayı çıkmış, {len(n['only_in_b'])} sayı eklenmiş")
    return f"{label_a} ile {label_b} arasında: " + "; ".join(parts) + " efendim."


def _pretty(spec: ArtifactSpec) -> list[str]:
    return json.dumps(
        spec.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).splitlines()


def diff_specs(a: ArtifactSpec, b: ArtifactSpec, *, label_a: str, label_b: str) -> dict[str, Any]:
    """Req 416: a unified diff of the two canonical specs, bounded."""
    lines = list(
        difflib.unified_diff(_pretty(a), _pretty(b), fromfile=label_a, tofile=label_b, lineterm="")
    )
    truncated = len(lines) > MAX_DIFF_LINES
    return {
        "lines": lines[:MAX_DIFF_LINES],
        "truncated": truncated,
        "added": sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
        "removed": sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---")),
    }


def resolve_version(
    session: Session, artifact_id: uuid.UUID, version_number: int | None
) -> tuple[Artifact, ArtifactVersion]:
    artifact = service.get_artifact(session, artifact_id)
    if artifact is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "Böyle bir artefakt bulamadım efendim.")
    version = (
        service.get_version(session, artifact_id, version_number)
        if version_number
        else service.get_current_version(session, artifact_id)
    )
    if version is None:
        raise ArtifactLifecycleError(ERROR_NOT_FOUND, "İstenen sürümü bulamadım efendim.")
    return artifact, version


def compare_versions(
    session: Session,
    a_id: uuid.UUID,
    b_id: uuid.UUID,
    *,
    a_version: int | None = None,
    b_version: int | None = None,
) -> dict[str, Any]:
    art_a, ver_a = resolve_version(session, a_id, a_version)
    art_b, ver_b = resolve_version(session, b_id, b_version)
    if art_a.kind == KIND_IMAGE or art_b.kind == KIND_IMAGE:
        return _compare_images(art_a, ver_a, art_b, ver_b)
    label_a = f"{art_a.title} v{ver_a.version}"
    label_b = f"{art_b.title} v{ver_b.version}"
    spec_a, spec_b = _spec_of(ver_a), _spec_of(ver_b)
    result = compare_specs(spec_a, spec_b)
    return {
        "a": {"artifact_id": str(art_a.id), "version": ver_a.version, "title": art_a.title},
        "b": {"artifact_id": str(art_b.id), "version": ver_b.version, "title": art_b.title},
        "comparison": result,
        "speech": compare_sentence(result, label_a=label_a, label_b=label_b),
        "diff": diff_specs(spec_a, spec_b, label_a=label_a, label_b=label_b),
    }


def version_payload(version: ArtifactVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "content_hash": version.content_hash,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "source_manifest": version.source_manifest_json,
        "provenance": version.provenance_json,
    }


__all__ = [
    "DELETE_POLICIES",
    "DELETE_POLICY_CONFIRM",
    "DELETE_POLICY_DENY",
    "DELETE_POLICY_FREE",
    "ArtifactEdit",
    "ArtifactLifecycleError",
    "ERROR_ARCHIVED",
    "ERROR_DELETE_DENIED",
    "ERROR_IMAGE_NOT_EDITABLE",
    "ERROR_INVALID_EDIT",
    "ERROR_OBJECT_MISSING",
    "KIND_IMAGE",
    "ERROR_INVALID_POLICY",
    "ERROR_NOT_FOUND",
    "ERROR_NO_PREVIOUS_VERSION",
    "DeleteOutcome",
    "EditOp",
    "VersionResult",
    "add_version",
    "apply_edit",
    "clone_artifact",
    "compare_sentence",
    "compare_specs",
    "compare_versions",
    "delete_artifact",
    "diff_specs",
    "edit_artifact",
    "resolve_version",
    "register_image_artifact",
    "version_payload",
]
