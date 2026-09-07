"""M18.3 ambient hardening (ADR-0079): keep-on, quiet-hours threshold, camera grace.

Revision ID: 0024_ambient_hardening
Revises: 0023_tool_call_clarification
Create Date: 2026-09-07

Reversible. ORM model: app/alarms/models.py::AmbientPolicyRow.

Three owner-configurable settings on the single ambient policy row, each with the
conservative default the model carries: ``keep_on`` (false — "Ekranı açık tut." sets it
and it outranks every inference), ``asleep_after_outside_quiet_s`` (1800 — LIKELY_ASLEEP
must hold three times longer outside the owner's quiet hours than inside them) and
``camera_unknown_grace_s`` (120 — the newest camera observation behind a presence
assertion may be at most this old for an automatic off, so a camera that stopped delivering
is a degraded perception, never an owner who left). Server defaults are set so the row an
older process created keeps meaning what it meant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_ambient_hardening"
down_revision: str | None = "0023_tool_call_clarification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ambient_policy"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("keep_on", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "asleep_after_outside_quiet_s", sa.Integer(), nullable=False, server_default="1800"
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("camera_unknown_grace_s", sa.Integer(), nullable=False, server_default="120"),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "camera_unknown_grace_s")
    op.drop_column(_TABLE, "asleep_after_outside_quiet_s")
    op.drop_column(_TABLE, "keep_on")
