"""Regression: every capability an action receipt carries resolves in the self-model.

The owner's own proof, run as a test (ADR-0116). Against production on 2026-09-10:

    SELECT DISTINCT detail_json->>'capability'
      FROM activity_events WHERE event_type = 'action.receipt';

returned 25 names, and ``GET /v1/selfmodel/capabilities/operator.type_text`` answered 404
with ``['operator.app_open', 'operator.cancel', 'operator.shell']`` — for a defect whose
code lives behind ``operator.type``. Four of the unresolvable names were the operator's,
which is to say all four of the names the owner's real incidents were filed under.

This file reproduces that query against a ledger this test FILLS BY RUNNING THE PRODUCT:
every plan ``app.voice.realtime_sessions.tools_operator`` can build is driven through the
real :class:`app.operator.service.OperatorService` and the real ``record_receipt``, so the
capability names checked are the ones the code actually writes, not a list a test author
retyped. The historical rows are then added as they exist in production — under the retired
vocabulary — because a ledger row is evidence and does not get rewritten to make a later
query tidy.

The Postgres expression is spelled out above; here the same distinct set is taken from
``detail_json`` in Python, which is what makes it run on SQLite too. It is the same set:
``record_receipt`` writes ``ActionReceipt.as_dict()`` into that column.

Watched fail before the fix (2026-09-10): all ten operator plan names answered
``capability_not_indexed``, with the six registered operator tools as candidates.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.evolution import supervisor
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_ACTION_RECEIPT, SUBSYSTEM_OPERATOR
from app.operator.capabilities import (
    PLAN_BY_POINTER_ACTION,
    PLAN_BY_PROCESS_ACTION,
    PLAN_BY_SERVICE_ACTION,
    PLAN_BY_SHELL_QUERY,
    PLAN_BY_UI_ACTION,
    PLAN_BY_WINDOW_ACTION,
    PLAN_CLOSE_APPLICATION,
    PLAN_OPEN_APPLICATION,
    PLAN_PRESS_KEY,
    PLAN_PRESS_SHORTCUT,
    PLAN_TYPE_TEXT,
    PLAN_UI_INSPECT,
    PLAN_UI_READ,
    RECEIPT_BY_PLAN,
    RETIRED_RECEIPT_CAPABILITIES,
)
from app.operator.models import ObjectFocusRow
from app.operator.plans import (
    activate_window,
    close_app,
    close_window,
    maximize_window,
    minimize_window,
    move_window,
    open_application,
    pointer,
    press_key,
    press_shortcut,
    previous_window,
    process_list,
    process_stop,
    resize_window,
    restore_window,
    service_restart,
    service_status,
    ui_invoke,
    ui_read,
    ui_select,
    ui_set_value,
)
from app.operator.plans import shell_query as build_shell_query_steps
from app.operator.plans import type_text as build_type_text_steps
from app.operator.service import OperatorService, Plan
from app.selfmodel import indexer
from app.selfmodel.indexer import build_index
from app.selfmodel.query import where_is_capability
from tests.alarms_support import FakeDeviceAction, happy_operator_device_results
from tests.alarms_support import window_id as window_id_for
from tests.selfmodel_support import make_engine

WINDOW = window_id_for(1)

#: One buildable plan per declared plan name, with the steps the tool would have built.
#: Written here rather than driven through the voice tools so the ledger fills without a
#: relay, a session or a model; ``test_operator_tools.py`` already covers the path from an
#: utterance to these same plans.
PLAN_STEPS: dict[str, Any] = {
    PLAN_OPEN_APPLICATION: lambda: open_application("notepad"),
    PLAN_TYPE_TEXT: lambda: build_type_text_steps(WINDOW, "merhaba"),
    PLAN_BY_WINDOW_ACTION["activate"]: lambda: activate_window(WINDOW),
    PLAN_BY_WINDOW_ACTION["close"]: lambda: close_window(WINDOW),
    PLAN_BY_WINDOW_ACTION["maximize"]: lambda: maximize_window(WINDOW),
    PLAN_BY_WINDOW_ACTION["minimize"]: lambda: minimize_window(WINDOW),
    PLAN_BY_WINDOW_ACTION["previous"]: lambda: previous_window(WINDOW),
    PLAN_BY_WINDOW_ACTION["restore"]: lambda: restore_window(WINDOW),
    PLAN_BY_SHELL_QUERY["ip"]: lambda: build_shell_query_steps("ip"),
    PLAN_BY_SHELL_QUERY["hostname"]: lambda: build_shell_query_steps("hostname"),
    # B28 req 92-98: the input family's seven plans.
    PLAN_PRESS_KEY: lambda: press_key(WINDOW, "enter"),
    PLAN_PRESS_SHORTCUT: lambda: press_shortcut(WINDOW, ["ctrl", "s"]),
    PLAN_BY_POINTER_ACTION["move"]: lambda: pointer(WINDOW, "move", x=10, y=10),
    PLAN_BY_POINTER_ACTION["click"]: lambda: pointer(WINDOW, "click", x=10, y=10),
    PLAN_BY_POINTER_ACTION["double_click"]: lambda: pointer(WINDOW, "double_click", x=10, y=10),
    PLAN_BY_POINTER_ACTION["right_click"]: lambda: pointer(WINDOW, "right_click", x=10, y=10),
    PLAN_BY_POINTER_ACTION["scroll"]: lambda: pointer(WINDOW, "scroll", x=10, y=10, delta=-3),
    # B29 req 99-103: the UI Automation family's five plans.
    PLAN_BY_UI_ACTION["invoke"]: lambda: ui_invoke(WINDOW, {"name": "Tamam"}),
    PLAN_BY_UI_ACTION["set_value"]: lambda: ui_set_value(
        WINDOW, {"control_type": "Edit"}, "merhaba"
    ),
    PLAN_BY_UI_ACTION["select"]: lambda: ui_select(WINDOW, {"name": "Liste"}, "Bir"),
    PLAN_UI_READ: lambda: ui_read(WINDOW, {"control_type": "Edit"}),
    PLAN_UI_INSPECT: lambda: ui_read(WINDOW),
    # B30 req 82/84-88/117-122: geometry, application close, the third shell query, and
    # the process/service family's four plans.
    PLAN_BY_WINDOW_ACTION["move"]: lambda: move_window(WINDOW, 100, 100),
    PLAN_BY_WINDOW_ACTION["resize"]: lambda: resize_window(WINDOW, 800, 600),
    PLAN_CLOSE_APPLICATION: lambda: close_app(WINDOW, force=False),
    PLAN_BY_SHELL_QUERY["whoami"]: lambda: build_shell_query_steps("whoami"),
    PLAN_BY_PROCESS_ACTION["list"]: lambda: process_list("chrome.exe"),
    PLAN_BY_PROCESS_ACTION["stop"]: lambda: process_stop("chrome.exe", force=False),
    PLAN_BY_SERVICE_ACTION["status"]: lambda: service_status("Spooler"),
    PLAN_BY_SERVICE_ACTION["restart"]: lambda: service_restart("Spooler"),
}


@pytest.fixture(scope="module")
def sessions():
    engine = make_engine()
    ObjectFocusRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture(scope="module")
def indexed(sessions) -> None:
    """The real index over this checkout — a fixture tree would prove nothing here, since
    the claim is about the names THIS repository's code writes."""
    with sessions() as session:
        report = build_index(session, repo_root=indexer.default_repo_root())
        session.commit()
        assert report.modules_discovered > 100


def _run_every_plan(session: Session) -> None:
    """Drive every declared plan through the real service, writing real receipts."""
    service = OperatorService()
    device = FakeDeviceAction(results=happy_operator_device_results())
    for plan_name, steps in PLAN_STEPS.items():
        service.start_task(
            session,
            Plan(name=plan_name, goal=f"regression {plan_name}", steps=steps()),
            device,
            session_id=str(uuid.uuid4()),
        )
    session.commit()


def _record_historical_rows(session: Session, *, now: datetime) -> None:
    """The rows production still holds, under the vocabulary that wrote them."""
    for offset, capability in enumerate(sorted(RETIRED_RECEIPT_CAPABILITIES)):
        session.add(
            ActivityEventRow(
                event_id=uuid.uuid4(),
                event_type=EVENT_TYPE_ACTION_RECEIPT,
                subsystem=SUBSYSTEM_OPERATOR,
                action=capability,
                status="failed",
                factual_summary=f"{capability} -> failed",
                occurred_at=now - timedelta(days=30, minutes=offset),
                detail_json={
                    "capability": capability,
                    "execution_status": "failed",
                    "error_class": "validation_error",
                },
                source="live",
                source_ref=f"action_receipt:{capability}:{uuid.uuid4()}",
            )
        )
    session.commit()


def _distinct_receipt_capabilities(session: Session) -> set[str]:
    """``SELECT DISTINCT detail_json->>'capability' ... WHERE event_type='action.receipt'``."""
    rows = session.scalars(
        select(ActivityEventRow).where(
            ActivityEventRow.event_type == EVENT_TYPE_ACTION_RECEIPT
        )
    ).all()
    return {
        str((row.detail_json or {}).get("capability"))
        for row in rows
        if (row.detail_json or {}).get("capability")
    }


def _resolves(session: Session, capability: str) -> tuple[bool, dict[str, Any]]:
    """Whether the self-model can name a file for this capability.

    NOT ``answer.found``: that is True only when a module in THIS service implements the
    capability, and a device capability (``keyboard.type``) is correctly answered by naming
    the files that dispatch it — ADR-0111 says so in as many words. "Resolves" is the
    honest predicate the owner's lookup needs: the index knows the name, and can hand back
    somewhere to open.
    """
    answer = where_is_capability(session, capability).to_dict()
    if answer.get("reason") == "capability_not_indexed":
        return False, answer
    return bool(answer.get("implemented_by") or answer.get("dispatched_by")), answer


# ------------------------------------------------------------------------ the proof


def test_every_capability_a_receipt_carries_resolves_in_the_self_model(
    sessions, indexed
) -> None:
    now = datetime.now(UTC)
    with sessions() as session:
        _run_every_plan(session)
        _record_historical_rows(session, now=now)
        distinct = _distinct_receipt_capabilities(session)

        assert len(distinct) >= len(RETIRED_RECEIPT_CAPABILITIES) + 1

        unresolved = {}
        for capability in sorted(distinct):
            resolved, answer = _resolves(session, capability)
            if not resolved:
                unresolved[capability] = answer.get("candidates")
        assert not unresolved, (
            "action receipts carry capability names the self-model cannot locate; an "
            f"incident titled after one of these is a dead end for the owner: {unresolved}"
        )


def test_the_receipts_the_product_writes_now_use_the_registered_tool_names() -> None:
    """The forward half: nothing written today carries a retired name.

    Its own empty ledger, deliberately — the shared one above is seeded with the
    historical rows, and a test about what the product writes NOW must not be able to read
    a row a fixture wrote.
    """
    engine = make_engine()
    ObjectFocusRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            _run_every_plan(session)
            written = _distinct_receipt_capabilities(session)
    finally:
        engine.dispose()
    assert written == set(RECEIPT_BY_PLAN.values())
    assert not written & set(RETIRED_RECEIPT_CAPABILITIES)


def test_a_historical_name_resolves_to_the_capability_that_answers_it_now(
    sessions, indexed
) -> None:
    """The owner's exact 404, now an answer.

    ``operator.type_text`` is not implemented by anything — it never was — but the index
    can say which file to open to find out what it became, because
    ``app.operator.capabilities`` declares the mapping as literals in a module-level dict
    and ADR-0111's indexer reads exactly that shape.
    """
    with sessions() as session:
        resolved, answer = _resolves(session, "operator.type_text")
        assert resolved, answer
        dispatchers = {entry["module_id"] for entry in answer["dispatched_by"]}
        assert "app.operator.capabilities" in dispatchers, answer


def test_the_operator_tools_stay_indexed_when_only_their_own_file_is_reparsed(
    sessions, indexed
) -> None:
    """ADR-0116 moved the six tool names into ``app.operator.capabilities``, so
    ``ToolSpec(name=operator_capabilities.CAPABILITY_TYPE)`` is now a CROSS-FILE reference.

    That is a shape ADR-0111 supports on purpose, but it has an incremental-index edge the
    old in-file literals did not have: when ``tools_operator.py`` changes and
    ``capabilities.py` does not, the second file is skipped and the constant's value has to
    come back out of the previous run's symbol row. If that ever stopped working, all six
    operator capabilities would quietly leave the index and nothing else would notice —
    the exact silence this work item exists to end.
    """
    from sqlalchemy import delete

    from app.selfmodel.models import CodeModule, CodeSymbol

    module_id = "app.voice.realtime_sessions.tools_operator"
    with sessions() as session:
        session.execute(delete(CodeSymbol).where(CodeSymbol.module_id == module_id))
        session.execute(delete(CodeModule).where(CodeModule.module_id == module_id))
        session.commit()
        report = build_index(session, repo_root=indexer.default_repo_root())
        session.commit()
        assert report.modules_reparsed == 1, "the setup re-parsed more than the tools module"

        for capability in sorted(set(RECEIPT_BY_PLAN.values())):
            answer = where_is_capability(session, capability).to_dict()
            assert answer["implemented_by"], (capability, answer)
            assert answer["implemented_by"]["path"].endswith("tools_operator.py"), answer


# ------------------------------- the whole vocabulary, not just the one that was broken


def _declared_receipt_capabilities() -> dict[str, str]:
    """Every capability name ``app/`` can put in an ``action.receipt``, read statically.

    The ledger-driven tests above prove the operator's rows; this reads the other sixteen
    subsystems that mint receipts without needing a fixture for each. It is the same scan
    ``test_operator_capability_vocabulary`` uses to forbid runtime-assembled names, run for
    the opposite purpose: collect the names that ARE in the source, and require the index to
    hold every one.
    """
    from tests.unit.test_operator_capability_vocabulary import RECEIPT_MINTING_CALLS

    app_dir = Path(__file__).resolve().parents[2] / "app"
    found: dict[str, str] = {}
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.target is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value.value
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in RECEIPT_MINTING_CALLS:
                continue
            for keyword in node.keywords:
                if keyword.arg != "capability":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.setdefault(value.value, path.name)
                elif isinstance(value, ast.Name) and value.id in constants:
                    found.setdefault(constants[value.id], path.name)
    return found


def test_every_capability_this_service_can_mint_a_receipt_under_resolves(
    sessions, indexed
) -> None:
    """The owner's measurement, widened: 25 names in production, 11 of them dead ends.

    Seven of those eleven were fixed by ADR-0111 (device capabilities and receipt names
    that live only as values in a mapping); the remaining four were the operator's, and
    they are this work item. The check is over the whole declared vocabulary rather than
    the four, so the next subsystem to invent a private receipt name is caught here.
    """
    declared = _declared_receipt_capabilities()
    declared.update({name: "capabilities.py" for name in RETIRED_RECEIPT_CAPABILITIES})
    assert len(declared) > 60, "the receipt-capability scan stopped matching"

    with sessions() as session:
        unresolved = {}
        for capability, where in sorted(declared.items()):
            resolved, answer = _resolves(session, capability)
            if not resolved:
                unresolved[capability] = (where, answer.get("candidates"))
    assert not unresolved, unresolved


# ------------------------------------------------- the loop this actually broke: lookup


def test_an_incident_raised_from_a_failed_receipt_names_a_capability_that_resolves(
    sessions, indexed
) -> None:
    """The whole point, end to end.

    The Supervisor titles an incident with the capability off the failed receipt
    (``app/evolution/supervisor.py``: ``Tekrarlayan eylem hatası: {capability}``). Before
    ADR-0116 that title said ``operator.type_text`` and the owner had nowhere to go with
    it. Two failing runs — the recurrence threshold — and the title must now carry a name
    the self-model can turn into a file.
    """
    from app.routines.dispatch import DeviceRunResult

    now = datetime.now(UTC)
    with sessions() as session:
        service = OperatorService()
        device = FakeDeviceAction(
            results={
                **happy_operator_device_results(),
                "keyboard.type": DeviceRunResult(False, "validation_error", "no window"),
            }
        )
        for _ in range(supervisor.RECURRENCE_THRESHOLD):
            task = service.start_task(
                session,
                Plan(
                    name=PLAN_TYPE_TEXT,
                    goal="type into the window",
                    steps=build_type_text_steps(WINDOW, "merhaba"),
                ),
                device,
                session_id=str(uuid.uuid4()),
            )
            assert task.action_receipt is not None
            assert task.action_receipt["execution_status"] == "failed"
        session.commit()

        signals = [
            signal
            for signal in supervisor.signals_from_ledger(session, now=now)
            if signal.kind == supervisor.SIGNAL_ACTION_FAILURE
        ]
        assert signals, "two failed receipts did not raise a recurring-failure signal"
        for signal in signals:
            capability = signal.detail["capability"]
            assert capability in RECEIPT_BY_PLAN.values(), signal.title
            resolved, answer = _resolves(session, capability)
            assert resolved, (signal.title, answer)
            assert answer["implemented_by"]["path"].endswith("tools_operator.py"), answer
