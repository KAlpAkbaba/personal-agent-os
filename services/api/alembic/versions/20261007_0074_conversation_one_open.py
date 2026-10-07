"""One open conversation, held by the database (test team, 2026-10-06).

Revision ID: 0074_conversation_one_open
Revises: 0073_memory_text_trgm
Create Date: 2026-10-06

Round t-manual-20261006d: eight concurrent 'konuşmayı başlat' (phone and web at once) left
four conversations open. ``start_conversation`` checks for an open one and then inserts; the
racers all passed the check. ``uq_conversations_one_open`` is a unique index on a constant,
partial on ``ended_at IS NULL``: at most one row may be open, and a second insert waits on the
first and fails with a unique violation the route answers as ``409 already_open``.

The file name is the card's; it chains from ``0072_money_ledger``, the chain tip of the base
this card started on, and is re-pointed at merge if another migration lands first.

**Expand-only, one data fold.** A database the old race already reached (staging: four open)
could not build the index, so the upgrade first ends every open conversation but the newest
(``ended_at`` = the moment of the upgrade; the lines stay, no row is deleted). The
downgrade drops the index; the ended ones stay ended (nothing to undo: an open
conversation the owner was not writing into is the bug, not data).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0074_conversation_one_open"
down_revision: str | None = "0073_memory_text_trgm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_conversations_one_open"


def upgrade() -> None:
    op.execute(
        "UPDATE conversations SET ended_at = CURRENT_TIMESTAMP "
        "WHERE ended_at IS NULL AND id <> ("
        "  SELECT id FROM conversations WHERE ended_at IS NULL "
        "  ORDER BY started_at DESC, id DESC LIMIT 1"
        ")"
    )
    op.execute(f"CREATE UNIQUE INDEX {INDEX} ON conversations ((true)) WHERE ended_at IS NULL")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
