"""``mail_index`` / ``mail_drafts`` (docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084).

No polling (ADR-0084 decision 4): ``mail_index`` grows only from owner-initiated reads and
searches (``app.mail.service.MailService``), never a background sync. Portable types
throughout (generic ``Uuid``, ``JSON`` with a ``JSONB`` variant on Postgres) — the same
discipline ``app.documents.models.DocumentIndexRow`` and ``app.operator.models.ObjectFocusRow``
already use, so the service layer unit-tests on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

#: mail_drafts.state (spec §3): a draft is reversible right up until it is SENT.
DRAFT_STATE_PREPARED = "prepared"
DRAFT_STATE_SENT = "sent"
DRAFT_STATE_DISCARDED = "discarded"
DRAFT_STATES: tuple[str, ...] = (DRAFT_STATE_PREPARED, DRAFT_STATE_SENT, DRAFT_STATE_DISCARDED)

DRAFT_KIND_REPLY = "reply"
DRAFT_KIND_NEW = "new"
DRAFT_KINDS: tuple[str, ...] = (DRAFT_KIND_REPLY, DRAFT_KIND_NEW)


class MailIndexRow(Base):
    """One mail message the owner has actually read or searched to — never polled."""

    __tablename__ = "mail_index"
    __table_args__ = (
        Index("ix_mail_index_provider_message_id", "provider_message_id", unique=True),
        Index("ix_mail_index_thread_key", "thread_key"),
        Index("ix_mail_index_last_used_at", "last_used_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The provider's own Message-ID header (spec §3) — identity across folders/providers,
    #: never a folder-local IMAP UID (which is not stable across a provider swap).
    provider_message_id: Mapped[str] = mapped_column(String(500), nullable=False)
    folder: Mapped[str] = mapped_column(String(200), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    from_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_json: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snippet: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    thread_key: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MailDraftRow(Base):
    """A PREPARE-tier object (spec §1): reversible, read back in Turkish, and the ONLY
    thing ``MailService.send`` is ever allowed to act on."""

    __tablename__ = "mail_drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_json: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    cc_json: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    body: Mapped[str] = mapped_column(String(20000), nullable=False, default="")
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=DRAFT_STATE_PREPARED)
    #: Set every time the draft's own content was spoken back to the owner verbatim
    #: (``draft_reply``/``draft_new``/``edit_draft``/``read_draft``) — the gate's own
    #: precondition (docs/M21_MAIL_CALENDAR_SPEC.md §1, ADR-0084 decision 1).
    read_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "DRAFT_KINDS",
    "DRAFT_KIND_NEW",
    "DRAFT_KIND_REPLY",
    "DRAFT_STATES",
    "DRAFT_STATE_DISCARDED",
    "DRAFT_STATE_PREPARED",
    "DRAFT_STATE_SENT",
    "MailDraftRow",
    "MailIndexRow",
]
