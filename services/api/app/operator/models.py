"""The generic durable object focus (docs/M19_DIGITAL_OPERATOR_SPEC.md §4, ADR-0082).

Canonical schema: ``alembic/versions/20260907_0025_object_focus.py``. One table for every
M19-M28 object kind the owner can point at with a deictic word ("bunu kapat", "öndeki
pencere") - ``window`` today, more kinds later, never a second focus table per kind. The
same append-only, most-recent-row-wins discipline ``app.research.models.ResearchFocusRow``
already uses (ADR-0076): setting focus on an object that is already current appends again
on purpose, because recency IS the ordering and an UPDATE would erase exactly the history
"the previous one" reads.

Single-owner system (CLAUDE.md): ``owner_session_id`` is nullable and informational only
(which realtime session, if any, was in the room when the focus moved) - never a tenant
key. Portable types throughout so the service layer unit-tests on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.ids import focus_row_id
from app.models import Base

#: Object kinds this table accepts today. Additive: a future kind (``file``, ``tab``, ...)
#: is a new literal here, never a new table (module docstring).
FOCUS_KIND_WINDOW = "window"
FOCUS_KIND_APP = "app"
#: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, ADR-0083 decision 4): additive
#: kinds for File & Document Intelligence's own focus by identity. ``file`` names a
#: LOCATION the owner searched to (a single search hit, or the target of a read before it
#: has been extracted); ``document`` names a CONTENT VERSION (set on every
#: ``document.extract``); ``folder`` names a search root the owner pointed at by a folder
#: pattern ("bu klasördeki PDF'leri bul"). Three independent stacks, the same
#: current/previous discipline every other kind already gets from this module.
FOCUS_KIND_FILE = "file"
FOCUS_KIND_DOCUMENT = "document"
FOCUS_KIND_FOLDER = "folder"
#: M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084 decision 5): additive kinds for Mail &
#: Calendar's own focus by identity. ``message``/``thread`` name a mail item the owner read
#: ("Buna cevap yaz" = the current message); ``draft`` names a prepared ``mail_drafts`` row
#: ("Cevabı oku" = the current draft, "Gönder." = the current draft only after its
#: read-back); ``event``/``proposal`` are the calendar equivalents ("Bunu bir saat ertele" =
#: the current event -> a proposal). Five more independent stacks, the same current/
#: previous discipline every other kind already gets from this module.
FOCUS_KIND_MESSAGE = "message"
FOCUS_KIND_THREAD = "thread"
FOCUS_KIND_DRAFT = "draft"
FOCUS_KIND_EVENT = "event"
FOCUS_KIND_PROPOSAL = "proposal"
#: M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085 decision 5): the Artifact Factory's
#: own focus by identity. ``artifact`` names one ``artifacts`` row (never a render/format —
#: those are chosen at open/render time) so "bunu aç" after "Bana bir bütçe tablosu yap"
#: resolves to the artifact just made, and "önceki" works the same current/previous way
#: every other kind above already gets from this module.
FOCUS_KIND_ARTIFACT = "artifact"
#: M23 (docs/M23_APP_FACTORY_SPEC.md §1, ADR-0086): the App Factory's own focus by
#: identity. ``project`` names one ``app_projects`` row (never a run/port — those are
#: read from the row itself) so "Testleri çalıştır" after "Bana bir görev takip
#: uygulaması yap" resolves to the project just made, and "önceki" works the same
#: current/previous way every other kind above already gets from this module.
FOCUS_KIND_PROJECT = "project"
#: M25 (docs/M25_CREATIVE_3D_SPEC.md §5, ADR-0088): 3D creation's own focus by identity.
#: ``scene`` names one ``scenes`` row (never a render/inspection version — those are
#: read from the row itself) so "Bir küp ekle" after "Blender'da yeni sahne aç" resolves
#: to the scene just created, and "önceki" works the same current/previous way every
#: other kind above already gets from this module.
FOCUS_KIND_SCENE = "scene"
#: M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §2, §5, ADR-0093): the Creative Tools
#: Operator's own focus by identity. ``creative`` names one ``creative_runs`` row
#: (never an inspection/compare version — those are read from the row itself) so
#: "Arka planını kaldır." after a fresh Paint edit resolves to the run just made, and
#: "önceki" works the same current/previous way every other kind above already gets
#: from this module.
FOCUS_KIND_CREATIVE = "creative"
#: B18 req 49: the owner's own MEMORY, by identity. `memory` names one `memories` row,
#: set when `memory.search` reads matches back, so "bunu unut" after hearing them
#: resolves to the one the owner was just told about - and "önceki" works the same
#: current/previous way every other kind above already gets from this module.
#:
#: B16 gave `memory.forget` an id and no way to resolve a deictic word, deliberately:
#: forgetting is a HARD delete and a fuzzy matcher is not something to hand it. Focus
#: is the other answer to the same problem and a stricter one - not a guess about what
#: the owner meant, but a durable record of what they were actually just read.
FOCUS_KIND_MEMORY = "memory"
FOCUS_KINDS: tuple[str, ...] = (
    FOCUS_KIND_WINDOW,
    FOCUS_KIND_APP,
    FOCUS_KIND_FILE,
    FOCUS_KIND_DOCUMENT,
    FOCUS_KIND_FOLDER,
    FOCUS_KIND_MESSAGE,
    FOCUS_KIND_THREAD,
    FOCUS_KIND_DRAFT,
    FOCUS_KIND_EVENT,
    FOCUS_KIND_PROPOSAL,
    FOCUS_KIND_ARTIFACT,
    FOCUS_KIND_PROJECT,
    FOCUS_KIND_SCENE,
    FOCUS_KIND_CREATIVE,
    FOCUS_KIND_MEMORY,
)

#: How many recent rows of ONE kind the stack keeps (bounded, per task brief: "a bounded
#: stack of the last 20 per kind"). Applied on read/prune, never by a CHECK constraint.
FOCUS_STACK_LIMIT = 20


class ObjectFocusRow(Base):
    """One entry of the owner's object focus stack, for one ``kind``."""

    __tablename__ = "object_focus"
    __table_args__ = (Index("ix_object_focus_kind_selected_at", "kind", "selected_at"),)

    #: TIME-ORDERED on purpose (``app.ids.focus_row_id``, an RFC 9562 UUIDv7), never
    #: random: ``app.operator.focus._stack`` reads ``ORDER BY selected_at DESC, id DESC``
    #: and this is that second key. A random v4 made it a coin toss whenever two rows
    #: shared one ``selected_at`` — two callers passing the same explicit ``now=``, or
    #: rows written by two processes, neither of which the module's default clock can
    #: nudge apart. The same guard ``ResearchFocusRow`` carries (ADR-0076 addendum 1).
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=focus_row_id)
    #: Which realtime session (if any) was in the room when the focus moved. Informational
    #: only - the focus itself is the owner's and outlives any one session.
    owner_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The device's own stable id for the object ("w-<hwnd>-<create tick>" for a window,
    #: an OS pid for an app) - identity, never a title (the same rule ADR-0076 gave
    #: research focus: two windows may share a title).
    object_id: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Why the focus moved: "operator_observed" (a step re-observed the foreground window),
    #: "operator_launch" (app.launch produced it), "owner_selected_by_voice", ... - a short
    #: machine token, never enumerated by a CHECK constraint (new sources are additive and
    #: this table is not privacy- or safety-critical the way the eye's durable flag is).
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )


__all__ = [
    "FOCUS_KINDS",
    "FOCUS_KIND_APP",
    "FOCUS_KIND_ARTIFACT",
    "FOCUS_KIND_CREATIVE",
    "FOCUS_KIND_DOCUMENT",
    "FOCUS_KIND_DRAFT",
    "FOCUS_KIND_EVENT",
    "FOCUS_KIND_FILE",
    "FOCUS_KIND_FOLDER",
    "FOCUS_KIND_MESSAGE",
    "FOCUS_KIND_MEMORY",
    "FOCUS_KIND_PROJECT",
    "FOCUS_KIND_PROPOSAL",
    "FOCUS_KIND_SCENE",
    "FOCUS_KIND_THREAD",
    "FOCUS_KIND_WINDOW",
    "FOCUS_STACK_LIMIT",
    "ObjectFocusRow",
]
