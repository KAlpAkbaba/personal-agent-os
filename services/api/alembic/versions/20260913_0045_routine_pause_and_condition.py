"""Routines can be paused, and can be triggered by a condition (B14 req 290/291/294).

Revision ID: 0045_routine_pause_and_condition
Revises: 0044_notifications
Create Date: 2026-09-13

Chains from ``0044_notifications`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file.

**Why pause.** Cancelling is a decision about the routine; pausing is a decision about this
week. The owner who says "sabah rutinini bu hafta durdur" is not asking for it to be
deleted, and before this state the only way to honour that was cancel-and-recreate -- which
loses the routine's id, its firing history, and everything the ledger recorded about it.
``paused`` is deliberately NOT terminal: ``evaluate_due`` skips it, ``resume_routine`` puts
it back, and the row is the same row.

**Why a condition trigger.** The other three triggers are about an INSTANT: a time, a wall
clock, an event on the bus. "Bilgisayar boşta kalınca" is about a STATE the owner's machine
is in, and a state is true for as long as it is true -- a trigger that fired whenever the
condition held would fire on every tick for as long as the owner was away from the keyboard.
So it fires on the CROSSING, and ``last_condition_met`` is what remembers which side of the
edge the routine was last on. It defaults to false, which is also the right starting point:
the first crossing is a crossing.

**Expand-only and reversible.** Four nullable/defaulted columns and two widened CHECK
constraints. A row that exists today satisfies the new constraints unchanged, and the
downgrade narrows them back -- refusing to run if any row is actually using the new values,
because silently deleting a paused routine on a rollback would be the migration doing
something the owner never asked for.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_routine_pause_and_condition"
down_revision: str | None = "0044_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_BEFORE = ("armed", "completed", "cancelled")
_STATUSES_AFTER = ("armed", "paused", "completed", "cancelled")
_TRIGGERS_BEFORE = ("at", "schedule", "presence")
_TRIGGERS_AFTER = ("at", "schedule", "presence", "condition")


def _in_list(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def _swap_check(name: str, column: str, values: Sequence[str]) -> None:
    """SQLite cannot ALTER a CHECK, so both dialects go through batch mode."""
    with op.batch_alter_table("routines") as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, _in_list(column, values))


def upgrade() -> None:
    with op.batch_alter_table("routines") as batch:
        batch.add_column(
            sa.Column(
                "last_condition_met",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("last_condition_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("pause_reason", sa.String(length=500), nullable=True))

    _swap_check("ck_routines_status", "status", _STATUSES_AFTER)
    _swap_check("ck_routines_trigger_kind", "trigger_kind", _TRIGGERS_AFTER)


def downgrade() -> None:
    # Refuse rather than destroy. A paused routine or a condition trigger cannot satisfy the
    # narrower constraint, and the only ways to proceed would be to delete the row or to
    # re-arm something the owner deliberately turned off - neither of which is a migration's
    # decision to make.
    bind = op.get_bind()
    paused = bind.execute(
        sa.text("SELECT count(*) FROM routines WHERE status = 'paused'")
    ).scalar_one()
    conditioned = bind.execute(
        sa.text("SELECT count(*) FROM routines WHERE trigger_kind = 'condition'")
    ).scalar_one()
    if paused or conditioned:
        raise RuntimeError(
            f"cannot downgrade: {paused} paused routine(s) and {conditioned} condition "
            "trigger(s) exist. Resume or cancel them, and delete the condition routines, "
            "before rolling back - this migration will not decide that for the owner."
        )

    _swap_check("ck_routines_status", "status", _STATUSES_BEFORE)
    _swap_check("ck_routines_trigger_kind", "trigger_kind", _TRIGGERS_BEFORE)

    with op.batch_alter_table("routines") as batch:
        batch.drop_column("pause_reason")
        batch.drop_column("paused_at")
        batch.drop_column("last_condition_at")
        batch.drop_column("last_condition_met")
