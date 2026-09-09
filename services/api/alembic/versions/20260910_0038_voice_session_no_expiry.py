"""A voice session with no expiry at all: ``realtime_sessions.expires_at`` becomes NULLable.

Revision ID: 0038_voice_session_no_expiry
Revises: 0037_native_builds
Create Date: 2026-09-10

Chains from ``0037_native_builds`` -- the true chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Reversible, expand-only
(dropping NOT NULL widens what the column accepts; no existing row changes).

Owner directive 2026-09-10: "ses oturumu hic kapanmasin ben kapatmadigim surece."

Every web voice session was dying at exactly one hour, mid-conversation, because
``expires_at`` was written ONCE at creation (``created_at + 3600``) and nothing ever renewed
it -- the clock that decided death was BIRTH, not use. Three consecutive production sessions
prove it: 17:35:43 -> 18:35:44, 18:36:13 -> 19:36:16, 20:15:57 -> 21:15:58, the last of them
with a real client event at 21:07:26 (turn 31), eight minutes before it was expired.

A sliding renewal would still close a session the owner walked away from, which is not what
was asked for. So "no expiry" is expressed as the ABSENCE of an expiry rather than as a date
far enough away to look like one: ``expires_at IS NULL`` means this session ends when the
owner ends it, and nothing in the code has to agree about what year counts as "never".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_voice_session_no_expiry"
down_revision: str | Sequence[str] | None = "0037_native_builds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "realtime_sessions",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    # A row with no expiry cannot be represented by the narrower column, and inventing a
    # date for it would be the very fiction this migration removed. Give those rows the
    # historical one-hour horizon from their own creation instant, so the column can be
    # narrowed again without pretending any of them was still live.
    op.execute(
        "UPDATE realtime_sessions SET expires_at = created_at + interval '1 hour' "
        "WHERE expires_at IS NULL"
    )
    op.alter_column(
        "realtime_sessions",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
