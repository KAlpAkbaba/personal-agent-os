"""Which SCHEMA revision this process is running against (B01 requirement 2).

``alembic upgrade head`` exiting 0 is not proof that the database reached head. The release
script ran it as ``alembic upgrade head 2>&1 | tail -2`` under ``set -eu``: the pipeline's
status was tail's, always 0, so a refused migration stopped nothing and the next colour was
promoted serving code against the schema it had before. That hole is closed in the scripts;
this module closes the other half - the RESULT is reported, so "the migration ran" and "the
schema is where the code expects it" are two different questions with two different answers.

``head`` comes from this tree's own migration directory; ``current`` from the database's
``alembic_version`` row. A release compares them before it switches traffic, and
``/v1/system/health`` shows the same two values to anyone who asks.

Never raises: a database that cannot be read is a failed check with the reason, not a
crashed endpoint.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.config import Settings

#: What the revision fields say when the value could not be determined.
UNKNOWN_REVISION = "unknown"

#: The migration tree that ships with this build. ``app/release/schema.py`` sits two levels
#: under the service root, beside ``alembic/`` in the repo AND in the image (the Dockerfile
#: copies ``alembic`` and ``app`` as siblings into /srv/pagentos).
_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_DIR = _SERVICE_ROOT / "alembic"


@lru_cache(maxsize=1)
def head_revision() -> str:
    """The revision this tree's migrations end at, or ``"unknown"``.

    Read through alembic's own ScriptDirectory rather than by parsing the files: the chain is
    alembic's to interpret, and a second reader of the same directory is a second opinion
    waiting to disagree. A tree with more than one head (a merge that was never resolved) is
    reported as ``unknown`` rather than one arbitrary branch - a release should refuse that,
    not pick.
    """
    try:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory(str(_ALEMBIC_DIR))
        heads = script.get_heads()
    except Exception:  # noqa: BLE001 - a health input must not raise
        return UNKNOWN_REVISION
    if len(heads) != 1:
        return UNKNOWN_REVISION
    return str(heads[0])


def current_revision(settings: Settings, *, timeout_s: int = 2) -> str:
    """The revision the database is on, or ``"unknown"`` when it cannot be read.

    An empty ``alembic_version`` table (a database that has never been migrated) reads as
    ``"none"`` - a real answer, and a different one from "could not ask".
    """
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": timeout_s})
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    except Exception:  # noqa: BLE001 - a health input must not raise
        return UNKNOWN_REVISION
    finally:
        engine.dispose()
    if row is None:
        return "none"
    return str(row[0])


def schema_state(settings: Settings) -> dict[str, Any]:
    """The schema check: where the database is, where this tree expects it, and whether the
    two agree.

    ``status`` is ``ok`` only when both revisions are known AND equal. An unknown on either
    side is a failure: it is exactly the state in which nobody can say the running code
    matches the running schema, which is the thing this check exists to answer.
    """
    head = head_revision()
    current = current_revision(settings)
    ok = head != UNKNOWN_REVISION and current == head
    state: dict[str, Any] = {
        "status": "ok" if ok else "fail",
        "current": current,
        "head": head,
    }
    if not ok:
        if head == UNKNOWN_REVISION:
            state["error"] = "this tree has no single migration head"
        elif current == UNKNOWN_REVISION:
            state["error"] = "the database did not answer which revision it is on"
        else:
            state["error"] = f"database is at {current}, this build expects {head}"
    return state


__all__ = ["UNKNOWN_REVISION", "current_revision", "head_revision", "schema_state"]
