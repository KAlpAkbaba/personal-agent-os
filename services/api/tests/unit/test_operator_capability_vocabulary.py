"""Structural guards on the Digital Operator's capability vocabulary (ADR-0116).

The same technique and the same purpose as ``tests/unit/test_alarms_structure.py``'s
receipt guards: read the SOURCE, because the property being protected — "every action this
package can perform is recorded under a name that exists in the source" — is not observable
from a passing test. It is observable only from the code being unable to violate it.

What went wrong without these. ``app.operator.service`` minted its receipt as
``f"operator.{task.plan_name}"``. The registry declared ``operator.type``; the ledger
recorded ``operator.type_text``; the Evolution Supervisor read the ledger and titled a real
incident after a capability that was a string in no file. Both halves passed their own
suites throughout, because neither suite ever read the other side.

Five properties:

1. **Every plan the tools can build has a declared receipt capability.** An
   :class:`app.operator.service.Plan` name that this scan cannot resolve to a literal — an
   f-string, a call, a computed name — fails here, at authoring time, rather than at 07:30
   in a receipt nobody can look up.
2. **The table has no dead rows.** A receipt capability for a plan nothing builds is a
   claim the record does not support.
3. **Both vocabularies are the same six names.** What ``ToolSpec`` registers, what
   ``RECEIPT_BY_PLAN`` records under, and what ``OPERATOR_CAPABILITIES`` declares.
4. **No receipt anywhere in this service is minted under a name built at runtime.** The
   general form of the defect, checked across ``app/`` rather than in the one package that
   had it.
5. **The retired list is closed and derived.** Every name in it is
   ``"operator." + <a plan this package runs>`` — the old rule, written down — and the list
   is exactly the ten names that rule ever produced.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.operator.capabilities import (
    OPERATOR_CAPABILITIES,
    PLAN_BY_SHELL_QUERY,
    PLAN_BY_WINDOW_ACTION,
    RECEIPT_BY_PLAN,
    RETIRED_RECEIPT_CAPABILITIES,
    RETIRED_RECEIPT_PREFIX,
)

APP = Path(__file__).resolve().parents[2] / "app"
TOOLS_OPERATOR = APP / "voice" / "realtime_sessions" / "tools_operator.py"
OPERATOR_SERVICE = APP / "operator" / "service.py"

#: The calls that put a capability name into an ``action.receipt`` ledger row.
#: ``_receipt`` is the per-module helper every voice tool family defines; it forwards its
#: ``capability`` straight into an :class:`app.actions.receipt.ActionReceipt`.
RECEIPT_MINTING_CALLS = frozenset({"ActionReceipt", "record_receipt", "_receipt"})


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# ------------------------------------------------- 1 & 2. plans and their receipt names


def _plan_names_built_by(path: Path) -> set[str]:
    """Every value that reaches ``Plan(name=...)`` in this module, resolved statically.

    Two shapes are accepted and they are the only two a declared plan name can have:
    a module constant (``PLAN_TYPE_TEXT``) and a subscript of a declared mapping
    (``PLAN_BY_WINDOW_ACTION[action]``, whose every value is a plan name whatever the
    subscript evaluates to). Anything else raises: a name this scan cannot read is a name
    the self-model cannot read either, which is the whole defect.
    """
    import app.voice.realtime_sessions.tools_operator as tools_operator

    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "Plan":
            continue
        keyword = next((kw for kw in node.keywords if kw.arg == "name"), None)
        assert keyword is not None, f"Plan(...) at line {node.lineno} with no name="
        value: Any = keyword.value
        if isinstance(value, ast.Name):
            found.add(getattr(tools_operator, value.id))
        elif isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name):
            mapping = getattr(tools_operator, value.value.id)
            assert isinstance(mapping, dict), f"{value.value.id} is not a mapping"
            found.update(mapping.values())
        else:
            raise AssertionError(
                f"{path.name}:{node.lineno} builds a plan name from "
                f"{ast.dump(value)[:80]} — a plan name must be a declared constant or a "
                "lookup in a declared mapping, so app.operator.capabilities can carry its "
                "receipt capability and the self-model can find it"
            )
    return found


def test_the_scan_finds_the_plans_it_is_meant_to_guard() -> None:
    """A guard on the guard: a refactor that moved plan construction elsewhere would make
    every check below pass vacuously."""
    assert len(_plan_names_built_by(TOOLS_OPERATOR)) >= 10


def test_every_plan_the_tools_build_has_a_declared_receipt_capability() -> None:
    """The property the owner asked for: a plan added without its receipt name being
    reachable fails the suite rather than quietly becoming an unlookupable ledger row."""
    for plan_name in sorted(_plan_names_built_by(TOOLS_OPERATOR)):
        assert plan_name in RECEIPT_BY_PLAN, (
            f"plan {plan_name!r} has no entry in "
            "app.operator.capabilities.RECEIPT_BY_PLAN; its receipts would be recorded "
            "under a name nothing declares"
        )


def test_the_receipt_table_has_no_dead_entries() -> None:
    """The other direction, exactly as ``test_alarms_structure`` checks its own table."""
    assert set(RECEIPT_BY_PLAN) == _plan_names_built_by(TOOLS_OPERATOR)


def test_the_declared_argument_maps_agree_with_the_receipt_table() -> None:
    """``PLAN_BY_WINDOW_ACTION`` / ``PLAN_BY_SHELL_QUERY`` name plans, not capabilities."""
    for mapping in (PLAN_BY_WINDOW_ACTION, PLAN_BY_SHELL_QUERY):
        for argument, plan_name in mapping.items():
            assert plan_name in RECEIPT_BY_PLAN, f"{argument} -> {plan_name} is not a plan"


def test_every_window_intent_maps_to_an_action_the_tool_declares() -> None:
    """``_INTENT_TO_WINDOW_ACTION`` feeds the same lookup the plan name comes from, so an
    intent naming an action with no plan would raise a ``KeyError`` mid-command."""
    from app.voice.realtime_sessions.tools_operator import _INTENT_TO_WINDOW_ACTION

    for intent, action in _INTENT_TO_WINDOW_ACTION.items():
        assert action in PLAN_BY_WINDOW_ACTION, f"intent {intent} -> unknown action {action}"


# ---------------------------------------------------------- 3. one vocabulary, six names


def test_the_registered_tools_are_the_declared_capabilities() -> None:
    """The registry and the declaration are the same set — read through the REAL registry,
    not through the constants the registration happens to use."""
    from app.voice.realtime_sessions.tools import ToolRegistry
    from app.voice.realtime_sessions.tools_operator import register_operator_tools

    registered = {
        name for name in register_operator_tools(ToolRegistry()).names() if name.startswith(
            RETIRED_RECEIPT_PREFIX
        )
    }
    assert registered == set(OPERATOR_CAPABILITIES)


def test_every_receipt_capability_is_a_registered_tool() -> None:
    assert set(RECEIPT_BY_PLAN.values()) <= set(OPERATOR_CAPABILITIES)


def test_a_plan_the_table_does_not_carry_cannot_be_constructed() -> None:
    """The runtime half of property 1. The plan is refused BEFORE the service touches a
    device, so an unmapped plan can never leave an action behind with no receipt."""
    from app.operator.service import Plan

    with pytest.raises(ValueError, match="no receipt capability"):
        Plan(name="drag_and_drop", goal="drag", steps=[])


# ------------------------------------------ 4. no receipt name is built at runtime, ever


#: The two ways a Python expression makes a string out of parts. Named rather than
#: allow-listed, the way ``test_alarms_structure`` names the forbidden power APIs: the
#: property is "the name exists as a literal in the source", and these are exactly the
#: shapes that guarantee it does not. A ``Name`` (a constant, or a ``capability`` parameter
#: a helper forwards), a ``Call`` into a declared table
#: (``receipt_capability_for_plan(...)``) and a ``Subscript`` of one all still resolve to a
#: string somebody typed.
ASSEMBLED_AT_RUNTIME = (ast.JoinedStr, ast.BinOp)


def _non_literal_receipt_capabilities(path: Path) -> list[tuple[int, str]]:
    """Receipt-minting call sites whose ``capability=`` is assembled from parts."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in RECEIPT_MINTING_CALLS:
            continue
        for keyword in node.keywords:
            if keyword.arg != "capability":
                continue
            if isinstance(keyword.value, ASSEMBLED_AT_RUNTIME):
                out.append((node.lineno, ast.unparse(keyword.value)[:80]))
    return out


APP_SOURCES = sorted(p for p in APP.rglob("*.py") if p.is_file())


def test_there_are_app_sources_to_scan() -> None:
    assert len(APP_SOURCES) > 200


def test_the_scan_catches_the_line_that_caused_this(tmp_path: Path) -> None:
    """The guard, proved against the exact source it was written for.

    Verbatim from ``app/operator/service.py`` before ADR-0116. A structural test whose
    scanner has quietly stopped matching passes forever and protects nothing, so the
    scanner is shown catching the real defect rather than trusted to.
    """
    source = tmp_path / "service_before_adr_0112.py"
    source.write_text(
        "receipt = ActionReceipt(\n"
        '    action_id=str(task.id),\n'
        '    capability=f"operator.{task.plan_name}",\n'
        ")\n",
        encoding="utf-8",
    )
    assert _non_literal_receipt_capabilities(source) == [
        (1, "f'operator.{task.plan_name}'")
    ]


def test_the_scan_accepts_a_lookup_in_a_declared_table(tmp_path: Path) -> None:
    """And the fix's own shape is not flagged: the string still comes from the source."""
    source = tmp_path / "service_after.py"
    source.write_text(
        "receipt = ActionReceipt(\n"
        "    capability=receipt_capability_for_plan(task.plan_name),\n"
        ")\n",
        encoding="utf-8",
    )
    assert _non_literal_receipt_capabilities(source) == []


@pytest.mark.parametrize("path", APP_SOURCES, ids=lambda p: p.name)
def test_no_receipt_capability_is_assembled_at_runtime(path: Path) -> None:
    """The general form of ADR-0116's defect, across the whole service.

    On 2026-09-10 ``app/operator/service.py`` was the only place in ``app/`` that did this,
    and it was enough to make every operator incident the owner ever had unlookupable. The
    check is repository-wide so the next one is caught in the pull request that adds it.
    """
    offenders = _non_literal_receipt_capabilities(path)
    assert not offenders, (
        f"{path.relative_to(APP.parent)} mints an action receipt under a capability built "
        f"at runtime: {offenders}. A receipt capability must be a string that exists in "
        "the source — declare it (see app/operator/capabilities.py) and look it up."
    )


# ------------------------------------------------------- 5. the retired list is closed


def test_every_retired_name_is_one_the_old_rule_really_produced() -> None:
    """``"operator." + plan_name`` was the rule. Each retired key must be an application of
    it to a plan this package actually runs — not a name invented to make a lookup pass."""
    for retired, successor in sorted(RETIRED_RECEIPT_CAPABILITIES.items()):
        assert retired.startswith(RETIRED_RECEIPT_PREFIX), retired
        plan_name = retired[len(RETIRED_RECEIPT_PREFIX) :]
        assert plan_name in RECEIPT_BY_PLAN, (
            f"{retired} is not 'operator.' + a plan this package runs"
        )
        assert successor == RECEIPT_BY_PLAN[plan_name], (
            f"{retired} points at {successor}, but plan {plan_name} now records under "
            f"{RECEIPT_BY_PLAN[plan_name]}"
        )


def test_the_retired_list_is_exactly_the_names_that_were_written() -> None:
    """Closed on 2026-09-10. A plan added later mints under the tool capability from its
    first run, so it has no retired name; changing this set is a deliberate act with a test
    to edit, which is the point."""
    assert set(RETIRED_RECEIPT_CAPABILITIES) == {
        "operator.activate_window",
        "operator.close_window",
        "operator.maximize_window",
        "operator.minimize_window",
        "operator.open_application",
        "operator.previous_window",
        "operator.restore_window",
        "operator.shell_query_hostname",
        "operator.shell_query_ip",
        "operator.type_text",
    }


def test_the_retired_names_are_literals_the_self_model_can_index() -> None:
    """``app.selfmodel.indexer`` reads capability ids out of module-level dict literals
    (ADR-0111, ``_mapped_capabilities``) — the same rule that makes
    ``app.alarms.sequence.RECEIPT_BY_DEVICE_CALL``'s receipt names findable. If these keys
    stop being literals in a module-level dict, every historical incident title stops
    resolving and nothing else would say so.
    """
    capabilities_module = APP / "operator" / "capabilities.py"
    literals: set[str] = set()
    for node in _tree(capabilities_module).body:
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                literals.add(key.value)
    assert set(RETIRED_RECEIPT_CAPABILITIES) <= literals


# --------------------------------------------------- the service reads the declaration


def test_the_service_mints_from_the_declared_table() -> None:
    """``app/operator/service.py`` must not name the capability itself.

    Read by name rather than by import because the failure mode is textual: someone
    re-adding ``f"operator.{...}"`` in either the receipt or the activity event.
    """
    source = OPERATOR_SERVICE.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'f"operator.{' not in body, (
        "app/operator/service.py builds an operator name from a plan again; "
        "app.operator.capabilities.receipt_capability_for_plan is the one declared source"
    )
    assert "receipt_capability_for_plan" in body
