"""Migration revisions as the TREE names them, never as a test types them.

A Postgres test that writes ``BEFORE = "0065_misheard_utterances"`` is right until the
integration step renumbers a migration (card migration-rechain-on-merge): then the literal
names a revision that no longer exists, or the wrong one, and the test goes red for a reason
that has nothing to do with what it checks. These helpers read the revision graph with
alembic's own ``ScriptDirectory`` - the same ``alembic.ini`` / ``script_location`` set-up the
tests upgrade with - so the name a test uses is always the one alembic will accept.

The guard ``tests/unit/test_migration_revision_literals.py`` keeps literals out of
``tests/integration``; this file is the one place allowed to know how a revision is found.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import Script, ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_LOCATION = API_ROOT / "alembic"


def _scripts(script_location: Path | None = None) -> ScriptDirectory:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(script_location or SCRIPT_LOCATION))
    return ScriptDirectory.from_config(cfg)


def _all(script_location: Path | None = None) -> list[Script]:
    return list(_scripts(script_location).walk_revisions())


def revision_named(suffix: str, script_location: Path | None = None) -> str:
    """The revision of the ONE migration file whose name ends with ``_<suffix>.py``."""
    tail = f"_{suffix}.py"
    found = [s for s in _all(script_location) if Path(s.path).name.endswith(tail)]
    if len(found) != 1:
        names = ", ".join(sorted(Path(s.path).name for s in found)) or "yok"
        raise LookupError(
            f"'*{tail}' adında tam bir göç dosyası bekleniyordu, {len(found)} bulundu: {names}"
        )
    return found[0].revision


def parent_of(revision: str, script_location: Path | None = None) -> str:
    """The ``down_revision`` of ``revision`` - the point a test downgrades to, to stand just
    before that migration."""
    script = _scripts(script_location).get_revision(revision)
    if script is None:
        raise LookupError(f"'{revision}' adında bir göç yok")
    parent = script.down_revision
    if not isinstance(parent, str):
        raise LookupError(
            f"'{revision}' göçünün tek bir öncülü yok (down_revision = {parent!r})"
        )
    return parent


def head(script_location: Path | None = None) -> str:
    """The single head of the chain; two heads is a broken chain, not a choice."""
    heads = _scripts(script_location).get_heads()
    if len(heads) != 1:
        raise LookupError(f"göç zincirinin tek başı olmalı, {len(heads)} var: {heads}")
    return heads[0]
