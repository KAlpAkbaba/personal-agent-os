"""Ambient policy: the owner's device-camera mode (B48).

Revision ID: 0059_ambient_camera_mode
Revises: 0058_calendar_recurrence
Create Date: 2026-09-16

Chains from ``0058_calendar_recurrence`` -- the chain tip when B48 was written. A parallel
batch that also chains from 0058 must be re-pointed at merge time (one head only).

* ``ambient_policy.camera_mode`` (rows 300, 331, 671): ``off`` | ``periodic`` |
  ``continuous``. The server default is ``off`` so a row an older process created keeps
  meaning "the device camera is not in use" - consent is an act, never a migration.

**Expand-only and reversible.** ORM model: app/alarms/models.py::AmbientPolicyRow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_ambient_camera_mode"
down_revision: str | None = "0058_calendar_recurrence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "ambient_policy"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("camera_mode", sa.String(16), nullable=False, server_default="off"),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "camera_mode")
