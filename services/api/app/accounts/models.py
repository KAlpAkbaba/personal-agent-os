"""``mail_accounts`` / ``mail_account_pending`` (card mail-accounts-connect, ADR in
team/plans/mail-accounts-connect-adr.md).

One row per account the owner connected from Ayarlar > Hesaplar, under the name he chose
("Kişisel", "İş", "Aktivra"). The tokens are Fernet ciphertext (``app.accounts.service``);
no column holds a token in clear, and no route ever returns these columns. A pending row is
one authorization in flight: the HASH of its ``state`` (never the state itself) and the PKCE
verifier encrypted, consumed by the first callback that names it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, LargeBinary, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Base

PROVIDER_GMAIL = "gmail"
PROVIDER_MICROSOFT = "microsoft"
PROVIDERS: tuple[str, ...] = (PROVIDER_GMAIL, PROVIDER_MICROSOFT)

#: ``connected`` - tokens work; ``error`` - the last refresh/read failed (``last_error``
#: says the class); the owner reconnects or disconnects from the page.
STATE_CONNECTED = "connected"
STATE_ERROR = "error"

#: The names the env (IMAP/SMTP, CalDAV/ICS) account answers to beside the connected ones
#: (``app.accounts.wiring``); no owner account may take them, or "IMAP hesabından gönder"
#: would pick whichever of the two came first.
ENV_MAIL_ACCOUNT_NAME = "IMAP"
ENV_CALENDAR_ACCOUNT_NAME = "Takvim"
RESERVED_NAMES: tuple[str, ...] = (ENV_MAIL_ACCOUNT_NAME, ENV_CALENDAR_ACCOUNT_NAME)


class MailAccountRow(Base):
    __tablename__ = "mail_accounts"
    __table_args__ = (Index("ix_mail_accounts_name_key", "name_key", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: The owner's own words, as typed.
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``name`` under Turkish casefold - "İş" and "iş" are one account.
    name_key: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    scopes_json: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    refresh_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=STATE_CONNECTED)
    #: An exception CLASS name, never a message (a provider's message can carry a token).
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MailAccountPendingRow(Base):
    __tablename__ = "mail_account_pending"
    __table_args__ = (Index("ix_mail_account_pending_state_hash", "state_hash", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    code_verifier_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PROVIDERS",
    "PROVIDER_GMAIL",
    "PROVIDER_MICROSOFT",
    "STATE_CONNECTED",
    "STATE_ERROR",
    "MailAccountPendingRow",
    "MailAccountRow",
]
