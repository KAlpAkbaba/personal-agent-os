"""M21 security review addendum 2: bind a confirmation to its own read-back (ADR-0084).

Revision ID: 0029_confirmation_binding
Revises: 0027_mail_calendar
Create Date: 2026-09-08

Reversible, expand-only. ``mail_drafts``/``calendar_proposals`` each gain four columns
so the confirmation gate (``app.actions.confirmation_gate``) can bind a ``send``/
``commit`` call to the SESSION and TURN that actually heard the read-back, instead of a
bare "some read-back happened, some confirmation happened later" clock comparison (H1):

* ``read_back_session_id`` — the realtime session (or ``"rest:<owner session>"``) whose
  read-back this is;
* ``read_back_turn`` — the voice turn the read-back was spoken on (``NULL`` for REST);
* ``confirmed_by`` — ``"rest:<session>"`` / ``"voice:<session>:<turn>"``, who actually
  confirmed, recorded at the moment it happened;
* ``last_error`` — the exception class from the last failed provider send/commit (L1), so
  a failure reverts the row to ``read_back`` rather than leaving it stuck ``sending``/
  ``committing``.

No column is dropped, narrowed, or renamed; ``state`` keeps its existing ``String(16)``
width (new values ``read_back``/``sending``/``committing`` all fit) — an old release still
reading this table sees every row it already understood, plus rows in states it has never
heard of (which it was already tolerating: ``state`` was never an enum at the database
level).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_confirmation_binding"
down_revision: str | None = "0027_mail_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("mail_drafts", "calendar_proposals"):
        op.add_column(
            table, sa.Column("read_back_session_id", sa.String(length=200), nullable=True)
        )
        op.add_column(table, sa.Column("read_back_turn", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("confirmed_by", sa.String(length=200), nullable=True))
        op.add_column(table, sa.Column("last_error", sa.String(length=200), nullable=True))


def downgrade() -> None:
    for table in ("mail_drafts", "calendar_proposals"):
        op.drop_column(table, "last_error")
        op.drop_column(table, "confirmed_by")
        op.drop_column(table, "read_back_turn")
        op.drop_column(table, "read_back_session_id")
