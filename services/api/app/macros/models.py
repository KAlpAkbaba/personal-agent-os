"""The ``voice_macros`` row: one named, recorded sequence of tool calls (ADR-0196).

One row per name (``name_key`` is unique): saying a name that already exists REPLACES
the macro's steps rather than growing a second one under the same words - the owner
re-recording "yeni mail sekmesi" means the new steps, and a list that reads the same
name twice answers nothing. Steps are the tool calls as they were made (tool name,
arguments, the turn record the tool read), never the owner's raw sentence: the relay
already refuses transcripts as tool arguments and this table keeps the same line.

Singleton owner (CLAUDE.md): no owner column, the same reasoning ``app.routines.models``
and ``app.webpush.models`` document for their own tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.routines.models import JSONColumn

#: ``source`` of a row made by the owner's voice; the REST/panel path, when one exists,
#: names its own.
SOURCE_VOICE = "voice"


class VoiceMacroRow(Base):
    __tablename__ = "voice_macros"

    macro_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The name as the owner SAID it (their casing), for reading back.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: ``app.macros.naming.name_key(name)`` - the one spelling a sentence is matched on.
    name_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    #: [{"tool": str, "arguments": {...}, "turn": {...}, "recorded_at": iso}, ...] -
    #: ``app.macros.service`` is the only writer; ``tools_macros.macro_run`` the reader.
    steps_json: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default=SOURCE_VOICE)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)


__all__ = ["SOURCE_VOICE", "VoiceMacroRow"]
