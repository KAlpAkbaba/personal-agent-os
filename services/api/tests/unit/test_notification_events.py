"""B12 req 381-388: the eight things the owner has to be told, each raised where it happens.

The events are declared in one module so the priority judgement lives in one place - scatter
it across eight subsystems and "backup failed" ends up quieter than "research finished". And
so a caller can be checked for: this repository's most repeated defect is a mechanism that
exists and nothing calls, and it has cost three retention sweeps, a speaker verdict and a
reconcile timer already.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.notifications import events
from app.notifications import service as notifications
from app.notifications.models import (
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    NotificationRow,
)

APP = Path(__file__).resolve().parents[2] / "app"
NOON = datetime(2026, 9, 13, 14, 0, tzinfo=UTC)
NIGHT = datetime(2026, 9, 13, 23, 30, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NotificationRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


# ------------------------------------------------------------ every event is wired


def test_every_declared_event_has_an_emitter() -> None:
    assert set(events.EVENTS) == set(events.EMITTERS)
    for event, function in events.EMITTERS.items():
        assert callable(getattr(events, function)), f"{event} names no function"


def test_every_emitter_is_actually_called_from_somewhere() -> None:
    """The guard this repository keeps needing. A declared event nobody raises is a
    notification the owner will never get, and it looks identical to one that works."""
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
        if path.name != "events.py"
    )
    # Who calls whom inside events.py, from its syntax tree rather than from a regex over
    # its text: an emitter may legitimately be raised by a driver in its own module, and
    # working that out by pattern-matching source is how a guard starts lying.
    tree = ast.parse((APP / "notifications" / "events.py").read_text(encoding="utf-8"))
    calls_inside: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            calls_inside[node.name] = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }

    def called_from_app(function: str) -> bool:
        return bool(re.search(rf"(events|notification_events)\.{function}\(", sources))

    def reachable(function: str) -> bool:
        if called_from_app(function):
            return True
        # `backup_failed` is raised by `sweep_backup_failures`, because the failing unit is
        # a systemd service that cannot call into this process. That counts - PROVIDED the
        # driver is itself wired, or an unreachable driver would hide the emitter behind it.
        return any(
            function in callees and called_from_app(driver)
            for driver, callees in calls_inside.items()
        )

    unwired = [f for f in events.EMITTERS.values() if not reachable(f)]
    assert not unwired, (
        f"these events are declared and nothing raises them: {unwired}. A notification "
        "nothing sends is indistinguishable from one that works, until the day it matters."
    )


def test_each_event_says_why_it_is_as_loud_as_it_is() -> None:
    """Urgent is a decision to wake somebody. Making it without a reason recorded is how
    everything drifts to urgent and the owner turns the whole thing off."""
    for event, spec in events.EVENTS.items():
        assert len(spec.why) > 40, f"{event} does not say why it has that priority"


# ------------------------------------------------------------------ the judgement


def test_the_things_that_wake_the_owner_are_the_three_that_should() -> None:
    """Named individually, because this list IS the policy: production rewinding itself,
    the safety net being down, and an alarm that did not fire. Everything else waits for
    morning."""
    urgent = {e for e, spec in events.EVENTS.items() if spec.priority == PRIORITY_URGENT}

    assert urgent == {events.ROLLBACK_HAPPENED, events.BACKUP_FAILED, events.ALARM_FAILED}


def test_a_finished_task_does_not_wake_anybody(db) -> None:
    row = events.task_completed(db, task_id=uuid.uuid4(), what="Rapor", now=NIGHT)

    assert row.priority == PRIORITY_NORMAL
    assert row.deferred_until is not None, "it waits for morning"


def test_a_failed_backup_does_wake_them(db) -> None:
    row = events.backup_failed(db, unit="pagentos-backup.service", now=NIGHT)

    assert row.priority == PRIORITY_URGENT
    assert row.deferred_until is None


def test_a_candidate_is_the_quietest_thing_this_system_produces(db) -> None:
    row = events.candidate_ready(db, opportunity_id=uuid.uuid4(), title_text="Yeni beceri")

    assert row.priority == PRIORITY_LOW


# ------------------------------------------------------------- what the owner reads


def test_the_owner_is_told_what_happened_not_which_script_said_it(db) -> None:
    row = events.backup_failed(db, unit="pagentos-backup.service", now=NOON)

    assert "Yedek alınamadı" in row.title
    assert "exit" not in row.body and "sh" not in row.body.split()


def test_a_failure_carries_the_error_class_and_not_a_traceback(db) -> None:
    row = events.task_failed(
        db, task_id=uuid.uuid4(), what="Klasör karşılaştırması", error_class="timeout", now=NOON
    )

    assert "timeout" in row.body
    assert "Traceback" not in row.body


def test_a_rollback_names_both_versions(db) -> None:
    """"Your system rewound itself" is only useful with what it rewound from and to."""
    row = events.rollback_happened(
        db,
        component="cloud-core",
        from_release="9ddf24307801a2b806",
        to_release="41eb2c342c4bb85fd5",
        now=NOON,
    )

    assert "9ddf243" in row.body and "41eb2c3" in row.body


# ---------------------------------------------------------------------- grouping


def test_a_unit_failing_every_night_leaves_one_unread_notice(db) -> None:
    """Seven nights of the same failure is one thing the owner needs to know, not seven."""
    for _ in range(7):
        events.backup_failed(db, unit="pagentos-backup.service", now=NOON)

    assert len(notifications.inbox(db)) == 1


def test_two_different_units_are_two_different_things(db) -> None:
    events.backup_failed(db, unit="pagentos-backup.service", now=NOON)
    events.backup_failed(db, unit="pagentos-restore-drill.service", now=NOON)

    assert len(notifications.inbox(db)) == 2


def test_a_task_completing_and_then_failing_is_one_story(db) -> None:
    """Same task, same group: the newest word about it is the one that stands."""
    task_id = uuid.uuid4()
    events.task_completed(db, task_id=task_id, what="Rapor", now=NOON)
    events.task_failed(db, task_id=task_id, what="Rapor", now=NOON)

    inbox = notifications.inbox(db)
    assert len(inbox) == 1
    assert inbox[0].kind == events.TASK_FAILED


# ------------------------------------------------------- the backup sweep (385)


def test_the_backup_sweep_turns_a_marker_into_a_notification(db, tmp_path) -> None:
    """B08 made a failed unit VISIBLE - a marker file and a health check that reads it.
    Visible is not told: the owner had to go and look."""
    import json

    root = tmp_path / "backup"
    (root / "failures").mkdir(parents=True)
    (root / "LAST_BACKUP.json").write_text(
        json.dumps({"snapshot": "abc", "finished_at": "2026-09-13T05:00:00Z", "offhost": "ok"}),
        encoding="utf-8",
    )
    (root / "failures" / "pagentos-backup.service.json").write_text(
        json.dumps({"unit": "pagentos-backup.service"}), encoding="utf-8"
    )

    failed = events.sweep_backup_failures(db, backup_root=str(root), now=NOON)

    assert failed == ["pagentos-backup.service"]
    assert notifications.inbox(db)[0].kind == events.BACKUP_FAILED


def test_the_backup_sweep_says_nothing_when_nothing_is_wrong(db, tmp_path) -> None:
    import json

    root = tmp_path / "backup"
    root.mkdir(parents=True)
    (root / "LAST_BACKUP.json").write_text(
        json.dumps({"snapshot": "abc", "finished_at": "2026-09-13T05:00:00Z", "offhost": "ok"}),
        encoding="utf-8",
    )

    assert events.sweep_backup_failures(db, backup_root=str(root), now=NOON) == []
    assert notifications.inbox(db) == []


# --------------------------------------------------- a notification never breaks work


def test_a_broken_notification_table_does_not_undo_a_task_transition() -> None:
    """The hook is best-effort AND rolls back. A swallowed database error leaves the session
    in a failed transaction and the next caller inherits it - B07 learned that on the routine
    clock's sub-ticks, and this is the same trap one subsystem further along."""
    import inspect

    from app.artifacts import service as artifacts

    source = inspect.getsource(artifacts._notify_task_transition)
    assert "session.rollback()" in source
    assert "noqa: BLE001" in source


def test_every_best_effort_notification_hook_rolls_back() -> None:
    """All three of them, read from the source: artifacts, alarms and self-healing each
    swallow a notification fault, and each must leave the session usable."""
    for module, marker in (
        ("artifacts/service.py", "task_transition_notification_failed"),
        ("alarms/service.py", "alarm_failure_notification_failed"),
        ("selfhealing/service.py", "rollback_notification_failed"),
    ):
        text = (APP / module).read_text(encoding="utf-8")
        assert marker in text, module
        before = text.index(marker)
        window = text[max(0, before - 400) : before]
        assert "rollback()" in window, f"{module} swallows without rolling back"
