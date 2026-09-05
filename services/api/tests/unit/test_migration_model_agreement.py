"""The migrations and the mapped classes must agree about server defaults.

This test exists because they did not, and nothing noticed until the first real
deployment. Migration 0016 created every M17 ``created_at``/``updated_at`` NOT NULL with no
``server_default``, while the mapped classes all declare ``server_default=func.now()``.
SQLAlchemy therefore leaves those columns out of its INSERT and expects the database to
fill them, and Postgres refused:

    null value in column "created_at" of relation "experience_lessons"

The reason no test caught it is worth stating plainly: the unit suite builds its tables
from the MODEL metadata, so the tables under test carry the defaults the models declare.
A suite that creates its own schema cannot observe migration drift by construction - it is
testing a database the migrations never make.

So this test reads the MIGRATION FILES as text and checks the one property that bit us: a
column the model expects the database to fill must actually have a default in the DDL that
creates or alters it. It is a coarse check on purpose; a precise one would need a real
Postgres, which the integration suite has and the unit suite does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from app.evolution.models import EvolutionOpportunity
from app.experience.models import ExperienceLessonRow
from app.goals.models import Goal, GoalTask
from app.routines.models import Routine, RoutineFiring
from app.selfmodel.models import CodeModule

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

#: The M17 tables and the classes that map them.
M17_MODELS = [Goal, GoalTask, ExperienceLessonRow, CodeModule, EvolutionOpportunity]

#: The M18 Routine Engine tables — same "migration and model must agree" discipline,
#: checked by the same generic test (parametrized below) rather than a copy of it.
M18_MODELS = [Routine, RoutineFiring]


def _migration_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(VERSIONS.glob("*.py")))


def _columns_expecting_a_database_default(model: type) -> list[str]:
    """Columns whose model says "the database fills this" - server_default, not default."""
    mapper = sa_inspect(model)
    out = []
    for column in mapper.columns:
        if column.server_default is not None and not column.nullable:
            out.append(f"{model.__tablename__}.{column.name}")
    return out


@pytest.mark.parametrize("model", M17_MODELS + M18_MODELS, ids=lambda m: m.__tablename__)
def test_every_server_default_the_model_declares_exists_in_a_migration(model: type) -> None:
    """A NOT NULL column with a model-side server_default must be given one in the DDL.

    Either the CREATE TABLE carries it, or a later alter_column adds it. Without one, every
    insert that omits the column fails in Postgres and passes in the metadata-built SQLite
    the unit suite uses - which is exactly how this reached production on 2026-09-05.
    """
    text = _migration_text()
    for qualified in _columns_expecting_a_database_default(model):
        table, column = qualified.split(".", 1)
        # the CREATE TABLE line for this column, if it declares a default
        created_with_default = re.search(
            rf'sa\.Column\(\s*"{re.escape(column)}".*?server_default', text, re.DOTALL
        )
        # ...or an alter_column that adds one to exactly this table and column
        altered = re.search(
            rf'alter_column\(\s*\n?\s*"?{re.escape(table)}"?,\s*\n?\s*"?{re.escape(column)}"?',
            text,
        )
        assert created_with_default or altered, (
            f"{qualified} declares server_default in the model but no migration gives the "
            "column one; an insert that omits it will fail on Postgres and pass on the "
            "metadata-built SQLite this suite uses"
        )


def test_the_timestamp_default_migration_covers_the_columns_that_failed() -> None:
    """Pin the specific repair, so a future squash cannot quietly drop it."""
    repair = VERSIONS / "20260905_0017_cognitive_timestamp_defaults.py"
    assert repair.is_file(), "the 0017 repair migration was removed"
    text = repair.read_text(encoding="utf-8")
    for table in ("goals", "goal_tasks", "experience_lessons", "code_modules",
                  "evolution_opportunities"):
        assert f'"{table}"' in text, f"{table} must be repaired too"
    assert "now()" in text


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    """``alembic_version.version_num`` is VARCHAR(32).

    A longer id does not fail loudly at authoring time: every DDL statement in the
    migration applies, and then the bookkeeping UPDATE truncates and the whole transaction
    rolls back - so the migration looks like it did nothing, having done everything
    (2026-09-05, revision id of 33 characters).
    """
    pattern = re.compile(r'^revision:?\s*(?::\s*str\s*)?=\s*"([^"]+)"', re.MULTILINE)
    checked = 0
    for path in sorted(VERSIONS.glob("*.py")):
        for revision_id in pattern.findall(path.read_text(encoding="utf-8")):
            checked += 1
            assert len(revision_id) <= 32, (
                f"{path.name}: revision id {revision_id!r} is {len(revision_id)} chars; "
                "alembic_version.version_num holds 32"
            )
    assert checked > 0, "no revision ids were found - the pattern stopped matching"
