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
tasks that are READY with `announced_at IS NULL`, delivers, and stamps the
column. That makes the announcement durable (an API restart mid-delivery
retries rather than loses it), exactly-once per task, and free of any long-lived
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
