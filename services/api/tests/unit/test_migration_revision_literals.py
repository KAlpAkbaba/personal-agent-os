"""No Postgres test types a migration revision; it asks the tree (``migration_ids.py``).

The integration step renumbers a branch's new migrations onto the integration tip (card
migration-rechain-on-merge). A test holding ``"0065_misheard_utterances"`` then names a
revision that moved: red for a reason unrelated to its claim, or - worse - green against the
wrong point of the chain. The dev-db-branch-migration-leak inspector found the same defect on
the PowerShell side on 2026-10-04. This guard reads every ``tests/integration`` file's string
constants (the AST, not a regex over text, so a docstring saying "migration 0066" or a test
NAME is not a hit) and refuses a whole-string revision. Unit tests are out of scope: their
"0040_x" ids are fixtures of fake chains, not names of real migrations.

The helper itself is checked here too, against the tree and against a scratch copy - never
against a typed revision, which would break this file's own rule.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

from tests.integration.migration_ids import head, parent_of, revision_named

API_ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = API_ROOT / "tests" / "integration"
ALEMBIC = API_ROOT / "alembic"
VERSIONS = ALEMBIC / "versions"
HELPER = "migration_ids.py"
LITERAL = re.compile(r"0\d{3}_[a-z0-9_]+")
DOWN_REVISION = re.compile(r"^down_revision[^=]*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def revision_literals(root: Path) -> list[str]:
    """``<file>:<line> <literal>`` for every string constant under ``root`` that IS a
    revision name. A pure function of the directory so the rule is proved on a scratch copy."""
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == HELPER:
            continue
        tree = ast.parse(path.read_text("utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and LITERAL.fullmatch(node.value)
            ):
                rel = path.relative_to(root).as_posix()
                hits.append(f"{rel}:{node.lineno} {node.value}")
    return sorted(hits)


def test_no_integration_test_types_a_migration_revision() -> None:
    hits = revision_literals(INTEGRATION)
    if hits:
        pytest.fail(
            "\n".join(
                f"testte sabit göç numarası: {hit}; migration_ids.revision_named/parent_of kullan"
                for hit in hits
            )
        )


def test_the_guard_names_file_and_line_of_a_literal_in_a_scratch_copy(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text('"""Migration 0066 adds watches."""\nX = 1\n', "utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "test_typed.py").write_text(
        'import x\n\nBEFORE = "0065_misheard_utterances"\n', "utf-8"
    )
    (tmp_path / HELPER).write_text('KNOWN = "0001_initial"\n', "utf-8")
    assert revision_literals(tmp_path) == ["sub/test_typed.py:3 0065_misheard_utterances"]


def test_parent_of_is_what_the_migration_file_itself_says() -> None:
    """Read the file's text independently of alembic: the helper must agree with it."""
    files = list(VERSIONS.glob("*_watches.py"))
    assert len(files) == 1, files
    declared = DOWN_REVISION.search(files[0].read_text("utf-8"))
    assert declared is not None
    assert parent_of(revision_named("watches")) == declared.group(1)
    # and the file a test asks for is the file it gets
    assert revision_named("watches") in files[0].stem


def test_the_chain_has_one_head_and_every_parent_exists() -> None:
    texts = [p.read_text("utf-8") for p in VERSIONS.glob("*.py")]
    revisions = set()
    for text in texts:
        match = re.search(r"^revision[^=]*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
        assert match is not None
        revisions.add(match.group(1))
    parents = {m.group(1) for t in texts if (m := DOWN_REVISION.search(t))}
    assert parents <= revisions
    assert head() in revisions
    assert head() not in parents


def _scratch_alembic(tmp_path: Path) -> Path:
    location = tmp_path / "alembic"
    shutil.copytree(ALEMBIC, location, ignore=shutil.ignore_patterns("__pycache__"))
    return location


def test_two_files_with_one_suffix_is_an_error_not_a_guess(tmp_path: Path) -> None:
    location = _scratch_alembic(tmp_path)
    tip = head(location)
    (location / "versions" / "20991231_9999_more_watches.py").write_text(
        f'revision = "9999_more_watches"\ndown_revision = "{tip}"\n'
        "branch_labels = None\ndepends_on = None\n\n\n"
        "def upgrade() -> None:\n    pass\n\n\ndef downgrade() -> None:\n    pass\n",
        "utf-8",
    )
    with pytest.raises(LookupError, match="2 bulundu"):
        revision_named("watches", location)
    with pytest.raises(LookupError, match="0 bulundu"):
        revision_named("no_such_migration_anywhere", location)
