"""Speaker verdicts: the last verification result per owner session (B05 req 245/247/665).

Revision ID: 0041_speaker_verdicts
Revises: 0040_device_build_identity
Create Date: 2026-09-13

Chains from ``0040_device_build_identity`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and reversible: one
new table, nothing existing is touched and no data is rewritten.

**Why.** Speaker verification was computed by one route, returned to the caller and then
forgotten, so a sensitive action could not ask the three questions it needs to ask - who is
speaking, how sure are we, and how long ago did we last check. "Advisory" was the literal
truth: nothing could read the verdict even if it wanted to.

**What is stored, and what deliberately is not.** The decision, the score, the device-trust
input that produced it and the moment. Never the probe embedding: that is biometric material
and it lives encrypted behind ``speaker_profiles.embedding_ref`` or nowhere. One row per
owner session, replaced in place - the question is always "how sure are we right now", and
keeping a history of voice measurements would be building exactly the archive the product
principles refuse.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_speaker_verdicts"
down_revision: str | None = "0040_device_build_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "speaker_verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_session_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("device_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("effective_accept", sa.Float(), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_speaker_verdicts_owner_session_id", "speaker_verdicts", ["owner_session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_speaker_verdicts_owner_session_id", table_name="speaker_verdicts")
    op.drop_table("speaker_verdicts")
