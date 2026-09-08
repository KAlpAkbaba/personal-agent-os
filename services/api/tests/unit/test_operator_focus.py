"""Unit tests: app.operator.focus (docs/M19_DIGITAL_OPERATOR_SPEC.md §4, ADR-0082).

The generic durable object-focus stack — current/previous, most-recent-row-wins, bounded
to app.operator.models.FOCUS_STACK_LIMIT per kind — and that the hand-written migration
(0025_object_focus) actually matches the ORM model it claims to.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_APP, FOCUS_KIND_WINDOW, FOCUS_STACK_LIMIT, ObjectFocusRow


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ObjectFocusRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_current_and_previous_on_an_empty_stack_are_none(db) -> None:
    assert focus_module.current(db, FOCUS_KIND_WINDOW) is None
    assert focus_module.previous(db, FOCUS_KIND_WINDOW) is None


def test_current_is_the_most_recently_set_object(db) -> None:
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-1", label="Not Defteri", source="a")
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-2", label="Chrome", source="b")
    current = focus_module.current(db, FOCUS_KIND_WINDOW)
    assert current is not None
    assert current.object_id == "w-2"
    assert current.label == "Chrome"


def test_previous_is_the_most_recent_distinct_object(db) -> None:
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-1", label="Not Defteri", source="a")
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-2", label="Chrome", source="b")
    previous = focus_module.previous(db, FOCUS_KIND_WINDOW)
    assert previous is not None
    assert previous.object_id == "w-1"


def test_setting_focus_on_the_current_object_appends_again_and_previous_still_works(db) -> None:
    """Recency IS the ordering (module docstring): re-focusing the SAME window must not
    collapse the history "the previous one" reads."""
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-1", label="A", source="a")
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-2", label="B", source="a")
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-2", label="B", source="a")  # re-focus B
    assert focus_module.current(db, FOCUS_KIND_WINDOW).object_id == "w-2"
    assert focus_module.previous(db, FOCUS_KIND_WINDOW).object_id == "w-1"


def test_the_stack_is_bounded_to_the_limit_and_counts_distinct_objects(db) -> None:
    now = datetime.now(UTC)
    for n in range(FOCUS_STACK_LIMIT + 10):
        focus_module.set_focus(
            db,
            FOCUS_KIND_WINDOW,
            f"w-{n}",
            label=str(n),
            source="a",
            now=now + timedelta(seconds=n),
        )
    stack = focus_module.stack(db, FOCUS_KIND_WINDOW)
    assert len(stack) == FOCUS_STACK_LIMIT
    assert stack[0].object_id == f"w-{FOCUS_STACK_LIMIT + 9}"  # most recent first


def test_kinds_are_independent_stacks(db) -> None:
    focus_module.set_focus(db, FOCUS_KIND_WINDOW, "w-1", label="Not Defteri", source="a")
    focus_module.set_focus(db, FOCUS_KIND_APP, "4242", label="notepad.exe", source="a")
    assert focus_module.current(db, FOCUS_KIND_WINDOW).object_id == "w-1"
    assert focus_module.current(db, FOCUS_KIND_APP).object_id == "4242"
    assert focus_module.previous(db, FOCUS_KIND_WINDOW) is None


def test_an_unknown_kind_is_refused_never_silently_accepted(db) -> None:
    with pytest.raises(focus_module.UnknownFocusKind):
        focus_module.set_focus(db, "tab", "t-1", label="", source="a")
    with pytest.raises(focus_module.UnknownFocusKind):
        focus_module.current(db, "tab")


def test_focus_entry_as_dict_is_bounded_and_json_shaped(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_WINDOW, "w-1", label="Not Defteri", source="operator_launch"
    )
    entry = focus_module.current(db, FOCUS_KIND_WINDOW)
    payload = entry.as_dict()
    assert payload["kind"] == FOCUS_KIND_WINDOW
    assert payload["object_id"] == "w-1"
    assert payload["source"] == "operator_launch"
    assert payload["selected_at"].endswith("Z")


# ------------------------------------------------- a tie on selected_at is not a coin toss


def test_rows_sharing_one_selected_at_read_back_in_insertion_order(db) -> None:
    """Two focus writes on ONE instant: the later act must still be the current one.

    ``_stack`` reads ``ORDER BY selected_at DESC, id DESC``, and the module's default
    clock can only keep its OWN readings apart: a caller passing an explicit ``now=``
    twice (a device receipt whose timestamp repeats), or two processes writing
    concurrently, still land two rows on one instant. The id was then a random v4 and the
    tiebreak a coin toss — measured on the research focus stack, which reads exactly this
    way (ADR-0076 addendum 1) — so the id is now a counter-backed UUIDv7 and insertion
    order IS id order. Ten rounds, alternating which window is written last, leave luck
    no room; against a v4 id this fails about half of every round.
    """
    moment = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    ids: list[uuid.UUID] = []
    for round_ in range(10):
        first, last = ("w-1", "w-2") if round_ % 2 == 0 else ("w-2", "w-1")
        for object_id in (first, last):
            row = focus_module.set_focus(
                db,
                FOCUS_KIND_WINDOW,
                object_id,
                label=object_id,
                source="a",
                now=moment,  # identical on purpose: the tie the default clock cannot nudge
            )
            ids.append(row.id)
        current = focus_module.current(db, FOCUS_KIND_WINDOW)
        assert current is not None and current.object_id == last, round_
        previous = focus_module.previous(db, FOCUS_KIND_WINDOW)
        assert previous is not None and previous.object_id == first, round_

    assert ids == sorted(ids), "twenty rows on one instant, ids out of insertion order"
    assert all(i.version == 7 for i in ids)


def test_the_id_column_default_is_time_ordered_too(db) -> None:
    """A row written straight through the ORM — no ``set_focus``, so no explicit id —
    carries the same guard, because the column default is ``app.ids.focus_row_id``."""
    moment = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    ids: list[uuid.UUID] = []
    for n in range(5):
        row = ObjectFocusRow(
            kind=FOCUS_KIND_WINDOW,
            object_id=f"w-{n}",
            label=str(n),
            source="a",
            selected_at=moment,  # identical on purpose
            meta_json={},
        )
        db.add(row)
        db.flush()
        ids.append(row.id)
    db.commit()

    assert ids == sorted(ids)
    assert all(i.version == 7 for i in ids)
    current = focus_module.current(db, FOCUS_KIND_WINDOW)
    assert current is not None and current.object_id == "w-4"  # the last one written


# ---------------------------------------------------------- the migration matches the model


def test_migration_creates_every_column_the_model_declares() -> None:
    """A structural check that the hand-written alembic revision (0025_object_focus)
    actually matches ``ObjectFocusRow`` — the same discipline
    ``test_migration_compatibility.py`` applies to every migration's shape, narrowed here
    to "does it declare the columns the model has" rather than re-deriving the whole
    expand-only gate.
    """
    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    migration_path = next(versions_dir.glob("*_object_focus.py"))
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))

    create_call = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            create_call = node
            break
    assert create_call is not None, "no op.create_table(...) found in the migration"

    migration_columns: set[str] = set()
    for arg in create_call.args[1:]:
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Column"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
        ):
            migration_columns.add(str(arg.args[0].value))

    model_columns = {c.name for c in ObjectFocusRow.__table__.columns}
    assert migration_columns == model_columns
