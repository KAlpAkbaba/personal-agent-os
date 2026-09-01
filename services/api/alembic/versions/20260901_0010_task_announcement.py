"""Task announcement marker (M9).

Revision ID: 0010_task_announcement
Revises: 0009_identity_mobile
Create Date: 2026-09-01

Reversible: adds one nullable column to `tasks`.

Why: artifact-ready push must be delivered by the API process, because that is
where live push registrations and vendor tokens live — a delivery attempted
from the Temporal worker would succeed at delivering nothing. But the worker is
what knows a task reached READY. Rather than give the worker an owner
credential so it can call the API, the two processes communicate the way the M6
recovery supervisor already does: through a durable record they both see.

The worker only transitions the task to READY (it already does). The API drains
tasks that are READY with `announced_at IS NULL`, **delivers, and only then**
stamps the column, with one transaction around the whole pass (the rows are
selected `FOR UPDATE SKIP LOCKED`, so a second API process skips them rather
than announcing them again). That ordering is the durability guarantee: a crash
before the commit costs at most a duplicate notice — the provider's collapse key
makes artifact-ready idempotent for the owner — whereas stamping first would
silently drop the notification forever. At-least-once, not exactly-once: the
honest description of what this design provides, and free of any long-lived
service credential.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_task_announcement"
down_revision: str | None = "0009_identity_mobile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True))
    # Partial index: the sweeper only ever asks for READY-and-unannounced.
    op.execute(
        "CREATE INDEX ix_tasks_pending_announcement ON tasks (ready_at) "
        "WHERE announced_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tasks_pending_announcement")
    op.drop_column("tasks", "announced_at")
