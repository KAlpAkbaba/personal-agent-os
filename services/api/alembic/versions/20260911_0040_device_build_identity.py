"""Device build identity: build_id / source_revision on devices and sessions (ADR-0118).

Revision ID: 0040_device_build_identity
Revises: 0039_owner_media_playbacks
Create Date: 2026-09-11

Chains from ``0039_owner_media_playbacks`` -- the true chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and reversible:
three nullable columns, nothing existing is touched and no data is rewritten.

**Why.** The staged updater decides whether a candidate took by comparing what the device
ANNOUNCES, and the only identity it announced was ``software_version`` -- a *product*
version, deliberately still across builds. On 2026-09-11 the deployed agent advertised the
full 85-capability M28 manifest while announcing ``0.6.0``, the M25 number, so two different
builds were indistinguishable to the one comparison that decides a rollback. The 2026-09-08
incident, where a healthy release was rolled back over this same field, is the cost already
paid once.

``build_id`` is derived on the device from its own assemblies (``AgentInfo.BuildId``) and
changes by construction when the agent's code changes; ``source_revision`` is the commit the
SDK stamped, kept for a human to find the source and never compared. Both are nullable: an
agent built before this change still handshakes, and a row with no identity is treated by
the updater as "unknown", never as a match.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_device_build_identity"
down_revision: str | None = "0039_owner_media_playbacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("build_id", sa.String(length=64), nullable=True))
    op.add_column("devices", sa.Column("source_revision", sa.String(length=64), nullable=True))
    op.add_column("device_sessions", sa.Column("build_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("device_sessions", "build_id")
    op.drop_column("devices", "source_revision")
    op.drop_column("devices", "build_id")
