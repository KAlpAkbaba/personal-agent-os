"""Bounded delivery: attempts / backoff / quarantine on the three queues (B07 req 14-17).

Revision ID: 0042_bounded_delivery
Revises: 0041_speaker_verdicts
Create Date: 2026-09-13

Chains from ``0041_speaker_verdicts`` -- the chain tip, confirmed against every
``down_revision`` in this directory before writing this file. Expand-only and reversible:
nine nullable-or-defaulted columns across three existing tables, nothing is rewritten.

**Why.** None of the three announcers could tell whether it had already tried. The push
announcer left a failing task unstamped and re-attempted it every five seconds - 17,280
attempts a day against a provider that was never going to answer. The briefing announcer
picked the highest-priority pending row and, when speaking it failed, returned without
touching anything, so the same row was picked again on the next pass and every pass after:
one row the speaker could not handle blocked every notification behind it, permanently. The
research announcer retried a throwing tool call unbounded.

Three facts fix all three: how many times have we tried, when may we try again, and have we
given up. ``quarantined_at`` is the important one - giving up on ONE item is what lets the
queue behind it move, and a queue that cannot give up has a lock rather than a retry policy.

``attempts`` defaults to 0 rather than being nullable: an existing row has not failed, and
"unknown" is not a useful third state for a counter.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_bounded_delivery"
down_revision: str | None = "0041_speaker_verdicts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, attempts column, next-attempt column, quarantine column)
_QUEUES: tuple[tuple[str, str, str, str], ...] = (
    ("tasks", "announce_attempts", "announce_next_at", "announce_quarantined_at"),
    ("pending_briefings", "attempts", "next_attempt_at", "quarantined_at"),
    (
        "realtime_tool_calls",
        "announce_attempts",
        "announce_next_at",
        "announce_quarantined_at",
    ),
)


def upgrade() -> None:
    for table, attempts, next_at, quarantined in _QUEUES:
        op.add_column(
            table,
            sa.Column(attempts, sa.Integer(), nullable=False, server_default="0"),
        )
        op.add_column(table, sa.Column(next_at, sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column(quarantined, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table, attempts, next_at, quarantined in _QUEUES:
        op.drop_column(table, quarantined)
        op.drop_column(table, next_at)
        op.drop_column(table, attempts)
