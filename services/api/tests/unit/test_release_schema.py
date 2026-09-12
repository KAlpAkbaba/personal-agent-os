"""B01 req 2: the schema the running process is actually on.

The release script ran ``alembic upgrade head 2>&1 | tail -2`` under ``set -eu``, so the
migration's own exit status was thrown away and a refused migration promoted a colour anyway.
The scripts now stop on it; this module answers the OTHER question - whether the database
actually reached the head this build carries - and a release gates on the answer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings
from app.release import schema as schema_module
from app.release.schema import UNKNOWN_REVISION, head_revision, schema_state

MIGRATIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

#: The smallest thing alembic will accept as a migration file.
_MIGRATION = (
    'revision = "{revision}"\n'
    "down_revision = None\n"
    "\n"
    "def upgrade():\n"
    "    pass\n"
    "\n"
    "def downgrade():\n"
    "    pass\n"
)


def _declared(path: Path, field: str) -> str | None:
    pattern = rf'^{field}(?::[^=]*)?\s*=\s*(?:"([^"]*)"|None)'
    match = re.search(pattern, path.read_text("utf-8"), re.M)
    if match is None:
        return None
    return match.group(1)


def test_head_is_the_revision_no_other_migration_chains_from() -> None:
    """An independent reading of the chain, not alembic asking alembic.

    The tip is the revision that appears as nobody's ``down_revision`` - which is exactly the
    check the 0040 migration says it did by hand before it was written.
    """
    files = [p for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"]
    assert files, "no migrations found"
    revisions = {_declared(p, "revision") for p in files}
    parents = {_declared(p, "down_revision") for p in files}
    tips = {r for r in revisions if r is not None and r not in parents}
    assert len(tips) == 1, f"the migration chain has {len(tips)} tips: {sorted(tips)}"
    assert head_revision() == tips.pop()


def test_head_is_reported_not_guessed() -> None:
    head = head_revision()
    assert head != UNKNOWN_REVISION
    assert re.fullmatch(r"[0-9a-z_]+", head), head


def test_a_database_that_cannot_be_reached_fails_with_a_reason_and_does_not_raise() -> None:
    # A port nothing listens on: the same shape as a database that is simply not there.
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@127.0.0.1:1/x")
    state = schema_state(settings)
    assert state["status"] == "fail"
    assert state["current"] == UNKNOWN_REVISION
    assert state["head"] == head_revision()
    assert "did not answer" in state["error"]


def test_a_database_at_head_is_ok_and_names_both_revisions(monkeypatch) -> None:
    monkeypatch.setattr(schema_module, "current_revision", lambda settings, **kw: head_revision())
    state = schema_state(Settings(_env_file=None))
    assert state["status"] == "ok"
    assert state["current"] == state["head"] == head_revision()
    assert "error" not in state


def test_a_database_behind_the_tree_fails_and_says_which_way(monkeypatch) -> None:
    """The state a FAILED migration leaves behind - the one nothing used to notice."""
    monkeypatch.setattr(schema_module, "current_revision", lambda settings, **kw: "0001_initial")
    state = schema_state(Settings(_env_file=None))
    assert state["status"] == "fail"
    assert state["current"] == "0001_initial"
    assert state["head"] == head_revision()
    assert "0001_initial" in state["error"] and head_revision() in state["error"]


def test_a_never_migrated_database_is_reported_as_none_not_as_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(schema_module, "current_revision", lambda settings, **kw: "none")
    state = schema_state(Settings(_env_file=None))
    assert state["status"] == "fail"
    assert state["current"] == "none"


def test_a_tree_with_two_heads_is_unknown_rather_than_one_arbitrary_branch(
    tmp_path: Path, monkeypatch
) -> None:
    """A release should refuse an unresolved merge, not pick a side."""
    versions = tmp_path / "versions"
    versions.mkdir(parents=True)
    (versions / "a.py").write_text(
        _MIGRATION.format(revision="aaa"),
        encoding="utf-8",
    )
    (versions / "b.py").write_text(
        _MIGRATION.format(revision="bbb"),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema_module, "_ALEMBIC_DIR", tmp_path)
    head_revision.cache_clear()
    try:
        assert head_revision() == UNKNOWN_REVISION
        monkeypatch.setattr(schema_module, "current_revision", lambda settings, **kw: "aaa")
        state = schema_state(Settings(_env_file=None))
        assert state["status"] == "fail"
        assert "no single migration head" in state["error"]
    finally:
        head_revision.cache_clear()


@pytest.fixture(autouse=True)
def _clear_head_cache():
    """The head is cached for the process; a test that redirects the tree must not leak."""
    yield
    head_revision.cache_clear()
