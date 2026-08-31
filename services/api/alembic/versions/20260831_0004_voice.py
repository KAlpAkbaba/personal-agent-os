"""Voice + narration schema (M4): voice_profiles, pronunciation_entries,
speaker_profiles, narration_sessions.

Revision ID: 0004_voice
Revises: 0003_research_artifact
Create Date: 2026-08-31

Reversible: downgrade drops all M4 tables and leaves the M0-M3 schema intact.

Shared migration authored by the lead so the M4 voice and narration modules can
be built in parallel without racing on the alembic chain. ORM models in
app/voice/models.py and app/narration/models.py must match these tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_voice"
down_revision: str | None = "0003_research_artifact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    # Owner voice/narration preferences (VOICE_SPEC §12). Single-owner system, so
    # rows are few; a nullable label distinguishes named profiles if ever needed.
    op.create_table(
        "voice_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label", sa.String(length=128), nullable=False, server_default="owner"),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="tr-TR"),
        sa.Column(
            "tts_provider_preference_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "narration_settings_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Per-owner pronunciation dictionary (VOICE_SPEC §5). Explicit owner
    # corrections carry the highest confidence; inferred entries store evidence.
    op.create_table(
        "pronunciation_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("token", sa.String(length=256), nullable=False),
        sa.Column("spoken_form", sa.String(length=512), nullable=False),
        sa.Column(
            "provider_payload_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("context", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("explicit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("token", "context", name="uq_pronunciation_token_context"),
    )
    op.create_index("ix_pronunciation_entries_token", "pronunciation_entries", ["token"])

    # Owner speaker-verification profiles (VOICE_SPEC §10). We store a reference
    # to an encrypted embedding blob (object store) plus derived metadata, not
    # unbounded raw audio; the column holds the object key / ciphertext ref.
    op.create_table(
        "speaker_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label", sa.String(length=128), nullable=False, server_default="owner"),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("embedding_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "enrollment_metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Cross-device narration cursor (VOICE_SPEC §3, ARCHITECTURE narration).
    # The semantic cursor is source of truth; playback_seconds is cache metadata.
    op.create_table(
        "narration_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "semantic_cursor_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("playback_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("speed", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="IDLE"),
        sa.Column("voice_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_narration_sessions_artifact_id", "narration_sessions", ["artifact_id"]
    )


def downgrade() -> None:
    op.drop_table("narration_sessions")
    op.drop_table("speaker_profiles")
    op.drop_table("pronunciation_entries")
    op.drop_table("voice_profiles")
