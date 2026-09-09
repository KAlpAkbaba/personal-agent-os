"""``native_builds``: the Native Application Factory's own durable record
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, ADR-0095). Expand-only migration:
``alembic/versions/20260909_0037_native_builds.py``.

One row per application the factory is building, in the shape M25's ``SceneRow`` and M27's
``CreativeRunRow`` established. What matters here, and what the whole milestone rests on,
is which fields may be written from WHERE:

* ``spec_json`` is the validated :class:`~app.nativefactory.spec.NativeAppSpec` — what was
  asked for, canonical, never re-derived from prose;
* ``artifact_json`` is what the INDEPENDENT reader found in the produced file, and nothing
  else. It is never populated from the spec, never from the build's own stdout, and never
  from a hopeful default: an artefact nobody could read leaves it null and the row says
  ``unverified``, which is a different thing from ``verified``;
* ``log_tail`` is the application's own log, read after the run (spec §5) — a crash the
  owner never saw is still evidence.

The distinction the state vocabulary carries is the one ADR-0095 decision 3 insists on:
``unavailable`` (this machine's toolchain cannot reach the target — no JDK, no macOS) is
never confused with ``failed`` (the build was possible and did not work). The first is a
fact about the world, the second is a defect, and telling the owner one when the other is
true is the failure this milestone exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# ------------------------------------------------------------------------ row states

STATE_PLANNED: Final = "planned"
STATE_GENERATING: Final = "generating"
STATE_BUILDING: Final = "building"
STATE_TESTING: Final = "testing"
STATE_PACKAGING: Final = "packaging"
STATE_VALIDATING: Final = "validating"
#: The artefact exists and an independent reader agreed it is what the spec asked for.
STATE_VERIFIED: Final = "verified"
#: The artefact exists and could not be checked — no version to read, no reader for the
#: format. NOT a failure, and emphatically not a success: the honest middle.
STATE_UNVERIFIED: Final = "unverified"
#: The artefact exists and the independent reader DISAGREES with the spec (the wrong
#: version, a console image where a desktop application was asked for).
STATE_MISMATCH: Final = "mismatch"
#: The toolchain cannot reach this target on this machine (no JDK for Android, no macOS
#: for iOS). A fact about the world, never a defect.
STATE_UNAVAILABLE: Final = "unavailable"
#: The build was possible and did not work.
STATE_FAILED: Final = "failed"

NATIVE_BUILD_STATES: Final[tuple[str, ...]] = (
    STATE_PLANNED,
    STATE_GENERATING,
    STATE_BUILDING,
    STATE_TESTING,
    STATE_PACKAGING,
    STATE_VALIDATING,
    STATE_VERIFIED,
    STATE_UNVERIFIED,
    STATE_MISMATCH,
    STATE_UNAVAILABLE,
    STATE_FAILED,
)

#: A row in one of these will not move again on its own.
TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {STATE_VERIFIED, STATE_UNVERIFIED, STATE_MISMATCH, STATE_UNAVAILABLE, STATE_FAILED}
)

#: The states in which an artefact is expected to EXIST. Used by the service to refuse to
#: settle a row as `verified` with nothing to have verified.
ARTIFACT_BEARING_STATES: Final[frozenset[str]] = frozenset(
    {STATE_VERIFIED, STATE_UNVERIFIED, STATE_MISMATCH}
)


class NativeBuildRow(Base):
    """One application build: what was asked, what was produced, what was read back."""

    __tablename__ = "native_builds"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The closed-alphabet identity every artefact is named by (spec.slug).
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    stack: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The target this row is building. One row per target: "an EXE" and "an installer" are
    #: different artefacts with different verdicts, and collapsing them would mean one
    #: could not fail without the other.
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=STATE_PLANNED)

    #: The validated NativeAppSpec, canonical.
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    #: What the INDEPENDENT reader found in the produced file. Never from the spec.
    artifact_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: The reader's verdict and every disagreement it named.
    verdict_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    #: The generated project's own test run: counts, never a claim.
    tests_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)

    #: Where the project and the artefact are, under the authorised `native` root.
    project_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The application's own log after a run (spec §5), bounded.
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_native_builds_state", "state"),
        Index("ix_native_builds_slug", "slug"),
        Index("ix_native_builds_created_at", "created_at"),
    )


MAX_LOG_TAIL_CHARS: Final = 8_000


__all__ = [
    "ARTIFACT_BEARING_STATES",
    "MAX_LOG_TAIL_CHARS",
    "NATIVE_BUILD_STATES",
    "STATE_BUILDING",
    "STATE_FAILED",
    "STATE_GENERATING",
    "STATE_MISMATCH",
    "STATE_PACKAGING",
    "STATE_PLANNED",
    "STATE_TESTING",
    "STATE_UNAVAILABLE",
    "STATE_UNVERIFIED",
    "STATE_VALIDATING",
    "STATE_VERIFIED",
    "TERMINAL_STATES",
    "NativeBuildRow",
]
