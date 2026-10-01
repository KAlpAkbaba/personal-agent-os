"""The models are compared with the shape production's PostgreSQL really has.

Three defects of 2026-10-01 were green on a fake and red on the real Cloud Core, because the
fake did not model one property of the real thing. This file is the schema third: the team
lock's version was 42 characters in a VARCHAR(32) and SQLite never enforced it
(QUALIFICATION 38.15). ``scripts/tests/fixtures/host-snapshot.json`` is a read-only snapshot
of the host (``scripts/cloud/host-snapshot.sh``, collected by the lead with
``scripts/cloud/collect-host-snapshot.ps1``); its ``columns`` are ``information_schema``'s
table / column / data_type / character_maximum_length. Held here:

* a ``String(n)`` column whose width is not the width production has fails, named;
* a column or a table production does not have fails, named - unless a migration that the
  serving release does not contain yet adds it (the fixture's ``markers.release`` is asked
  of git; a new column would otherwise block the very release that creates it);
* while the fixture is still the hand-written one, the tables it does not list are
  REPORTED with their number, not failed: it lists only what the lead read on the host.

Not held: that the fixture is fresh. The inspector's rule is that a change to a host script,
``infra/docker`` or a migration is inspected against a fixture collected after the last
release.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import subprocess
import warnings
from pathlib import Path

import pytest
from sqlalchemy import Enum, String
from sqlalchemy.dialects import postgresql

from app.models import Base

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "scripts" / "tests" / "fixtures" / "host-snapshot.json"
MIGRATIONS = REPO / "services" / "api" / "alembic" / "versions"
HAND_WRITTEN = "hand-written"

#: table -> column -> the declared String width, or None for every other type
Declared = dict[str, dict[str, int | None]]
#: table -> column -> (data_type, character_maximum_length)
Production = dict[str, dict[str, tuple[str, int | None]]]


def _declared() -> Declared:
    """Every column of every mapped model; the models are imported the way
    ``test_postgres_coverage_ratchet.py`` imports them, so the answer does not depend on
    which other tests ran first."""
    import app

    for module in pkgutil.walk_packages(app.__path__, "app."):
        leaf = module.name.rsplit(".", 1)[-1]
        if leaf == "models" or leaf.endswith("_models"):
            importlib.import_module(module.name)
    dialect = postgresql.dialect()
    declared: Declared = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        columns = declared.setdefault(table.name, {})
        for column in table.columns:
            kind = column.type.dialect_impl(dialect)
            width = kind.length if isinstance(kind, String) and not isinstance(kind, Enum) else None
            columns[column.name] = width
    return declared


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text("utf-8"))


def _production(fixture: dict[str, object]) -> Production:
    production: Production = {}
    for row in fixture["columns"]:  # type: ignore[union-attr]
        production.setdefault(row["table"], {})[row["column"]] = (
            row["data_type"],
            row["character_maximum_length"],
        )
    return production


def _unreleased_migrations(release: str) -> str | None:
    """The text of the migrations the serving release does not contain; None when git cannot
    say (no git, or a release this checkout has never seen)."""
    try:
        listed = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", release, "--", "services/api/alembic/versions"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    released = {Path(line).name for line in listed.stdout.splitlines() if line.strip()}
    if not released:
        return None
    return "\n".join(
        path.read_text("utf-8", errors="ignore")
        for path in sorted(MIGRATIONS.glob("*.py"))
        if path.name not in released
    )


def _quoted(name: str, text: str) -> bool:
    return re.search(rf"""["']{re.escape(name)}["']""", text) is not None


def compare(
    declared: Declared,
    production: Production,
    *,
    hand_written: bool,
    unreleased: str | None,
) -> tuple[list[str], list[str], list[str]]:
    """(failures, tables the hand-written fixture does not list, what waits for a release)."""

    def waits(table: str, column: str | None) -> bool:
        if unreleased is None:
            return False
        return _quoted(table, unreleased) and (column is None or _quoted(column, unreleased))

    failures: list[str] = []
    unlisted: list[str] = []
    waiting: list[str] = []
    for table in sorted(declared):
        if table not in production:
            if hand_written:
                unlisted.append(table)
            elif waits(table, None):
                waiting.append(table)
            else:
                failures.append(f"{table}: the model maps a table production does not have")
            continue
        for column, width in sorted(declared[table].items()):
            name = f"{table}.{column}"
            if column not in production[table]:
                if waits(table, column):
                    waiting.append(name)
                else:
                    failures.append(f"{name}: the model maps a column production does not have")
                continue
            data_type, length = production[table][column]
            if width is not None and width != length:
                has = f"{data_type}({length})" if length is not None else data_type
                failures.append(f"{name}: the model declares String({width}), production has {has}")
    return failures, unlisted, waiting


def test_the_models_have_the_shape_production_has() -> None:
    fixture = _fixture()
    hand_written = str(fixture["source"]).startswith(HAND_WRITTEN)
    release = str(fixture["markers"]["release"])  # type: ignore[index]
    unreleased = _unreleased_migrations(release) if re.fullmatch(r"[0-9a-f]{40}", release) else None
    declared = _declared()
    failures, unlisted, waiting = compare(
        declared, _production(fixture), hand_written=hand_written, unreleased=unreleased
    )
    if unlisted:
        warnings.warn(
            f"host snapshot: {len(unlisted)} of {len(declared)} mapped tables are not in the "
            f"fixture yet (it is hand-written; collected_at {fixture['collected_at']}). They are "
            "compared from the first real collection on: scripts/cloud/collect-host-snapshot.ps1",
            stacklevel=1,
        )
    if waiting:
        warnings.warn(
            f"host snapshot: {len(waiting)} table(s)/column(s) wait for a release that carries "
            f"their migration: {waiting}",
            stacklevel=1,
        )
    assert not failures, (
        "the models and production's PostgreSQL disagree (SQLite does not enforce a VARCHAR "
        "length; production does, with an HTTP 500): "
        + "; ".join(failures)
        + f". The fixture is {FIXTURE.name}, collected_at {fixture['collected_at']}, release "
        f"{release[:8]}"
        + (
            ""
            if unreleased is not None or hand_written
            else "; git could not list that release's migrations, so nothing could be "
            "recognised as waiting for a release"
        )
        + ". Fix the model or write the migration; never edit the fixture by hand."
    )


def test_the_column_that_made_the_rule_is_compared() -> None:
    """The comparison is not vacuous: the fixture lists team_state and the model declares the
    widths the test then compares."""
    production = _production(_fixture())
    declared = _declared()
    assert "team_state" in production
    assert declared["team_state"]["updated_at"] is not None
    assert production["team_state"]["updated_at"][1] is not None


# ---- the rules themselves, on small made-up shapes -------------------------------------

_PROD: Production = {
    "team_state": {
        "kind": ("character varying", 16),
        "doc": ("jsonb", None),
        "updated_at": ("character varying", 32),
    }
}


def _run(
    declared: Declared, *, hand_written: bool = False, unreleased: str | None = ""
) -> tuple[list[str], list[str], list[str]]:
    return compare(declared, _PROD, hand_written=hand_written, unreleased=unreleased)


def test_a_wider_model_than_production_fails_and_names_the_column() -> None:
    failures, _, _ = _run({"team_state": {"kind": 16, "doc": None, "updated_at": 64}})
    assert failures == [
        "team_state.updated_at: the model declares String(64), production has character varying(32)"
    ]


def test_the_same_shape_passes() -> None:
    assert _run({"team_state": {"kind": 16, "doc": None, "updated_at": 32}}) == ([], [], [])


def test_a_string_over_a_column_that_is_not_one_in_production_fails() -> None:
    failures, _, _ = _run({"team_state": {"doc": 40}})
    assert failures == ["team_state.doc: the model declares String(40), production has jsonb"]


def test_a_column_production_does_not_have_fails_and_is_named() -> None:
    failures, _, waiting = _run({"team_state": {"kind": 16, "owner": 40}})
    assert failures == ["team_state.owner: the model maps a column production does not have"]
    assert waiting == []


def test_a_column_an_unreleased_migration_adds_waits_instead_of_failing() -> None:
    migration = 'op.add_column("team_state", sa.Column("owner", sa.String(40)))'
    failures, _, waiting = _run({"team_state": {"kind": 16, "owner": 40}}, unreleased=migration)
    assert (failures, waiting) == ([], ["team_state.owner"])


def test_a_column_fails_when_git_cannot_say_what_is_released() -> None:
    failures, _, _ = _run({"team_state": {"owner": 40}}, unreleased=None)
    assert failures == ["team_state.owner: the model maps a column production does not have"]


def test_an_unlisted_table_is_reported_only_while_the_fixture_is_hand_written() -> None:
    declared: Declared = {"wake_alarms": {"id": 36}}
    assert _run(declared, hand_written=True) == ([], ["wake_alarms"], [])
    failures, unlisted, _ = _run(declared, hand_written=False)
    assert failures == ["wake_alarms: the model maps a table production does not have"]
    assert unlisted == []
    create = 'op.create_table("wake_alarms", sa.Column("id", sa.String(36)))'
    assert _run(declared, unreleased=create) == ([], [], ["wake_alarms"])


def test_the_release_of_the_fixture_is_one_git_can_list() -> None:
    """Otherwise 'waits for a release' can never be recognised and every new column fails."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout: nothing can be asked of git here")
    release = str(_fixture()["markers"]["release"])  # type: ignore[index]
    assert re.fullmatch(r"[0-9a-f]{40}", release), release
    assert _unreleased_migrations(release) is not None, f"git does not know release {release}"
