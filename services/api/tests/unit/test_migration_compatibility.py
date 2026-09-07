"""Structural gate: every alembic ``upgrade()`` is expand-only (M18.4 spec §10).

A blue/green release keeps the OLD colour serving during the drain, against the schema the
NEW colour just migrated to. That only works when a migration never removes or narrows
what the old code reads: no ``drop_*``, no ``rename_*``, no ``alter_column`` that makes a
column NOT NULL or changes its type. A contract-phase migration (one that finally removes
the old shape) is allowed only when the module says so in as many words -
``contract-phase: ADR-XXXX`` - which ties it to the decision that the release which stopped
reading the old shape is LIVE. Downgrades are not inspected: rollback never runs them
(spec §12).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
CONTRACT_MARK = re.compile(r"contract-phase:\s*ADR-\d{4}")
#: A type change or a constraint drop that WIDENS what the column accepts (String -> Text,
#: a longer String, a CHECK replaced by a superset) keeps the old reader working; the
#: migration must say so in as many words, next to the operation.
WIDENING_MARK = re.compile(r"compat:\s*widening")

#: Never compatible with a still-running old colour; only a contract-phase migration may.
FORBIDDEN_OPS = {"drop_table", "drop_column", "rename_table", "drop_index"}
#: Compatible only when reviewed as a widening (or as a contract phase).
REVIEWED_OPS = {"drop_constraint"}

MIGRATIONS = sorted(p for p in VERSIONS.glob("*.py") if not p.name.startswith("__"))


def _upgrade_function(tree: ast.Module) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return node
    return None


def _op_calls(fn: ast.FunctionDef):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "op":
                yield func.attr, node


def _operations(source: str) -> tuple[list[str], list[str]]:
    """(contract-phase operations, reviewed operations) found in ``upgrade()``."""
    tree = ast.parse(source)
    upgrade = _upgrade_function(tree)
    if upgrade is None:
        return [], []
    contract: list[str] = []
    reviewed: list[str] = []
    for name, call in _op_calls(upgrade):
        if name in FORBIDDEN_OPS:
            contract.append(name)
        elif name in REVIEWED_OPS:
            reviewed.append(name)
        elif name == "alter_column":
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            nullable = kwargs.get("nullable")
            if isinstance(nullable, ast.Constant) and nullable.value is False:
                contract.append("alter_column(nullable=False)")
            if "new_column_name" in kwargs:
                contract.append("alter_column(new_column_name=...)")
            if "type_" in kwargs:
                reviewed.append("alter_column(type_=...)")
    return contract, reviewed


def test_there_are_migrations_to_gate() -> None:
    assert len(MIGRATIONS) >= 24


@pytest.mark.parametrize("path", MIGRATIONS, ids=[p.stem for p in MIGRATIONS])
def test_upgrade_is_expand_only_or_declares_its_contract_phase(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    contract_ops, reviewed_ops = _operations(source)
    if contract_ops:
        assert CONTRACT_MARK.search(source), (
            f"{path.name} performs a contract-phase operation in upgrade() "
            f"({', '.join(contract_ops)}) without declaring 'contract-phase: ADR-XXXX'. "
            "Expand first; contract only after the release that stopped reading the old "
            "shape is LIVE (docs/M18_4_SELF_EVOLUTION_SPEC.md §10)."
        )
    if reviewed_ops:
        assert CONTRACT_MARK.search(source) or WIDENING_MARK.search(source), (
            f"{path.name} changes a type or drops a constraint in upgrade() "
            f"({', '.join(reviewed_ops)}) without a 'compat: widening' review note "
            "(or a contract-phase declaration)."
        )


def test_the_gate_sees_a_drop_in_upgrade() -> None:
    """The gate itself must not be vacuous."""
    source = (
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.add_column('t', None)\n"
        "    op.drop_column('t', 'c')\n"
        "    op.alter_column('t', 'd', nullable=False)\n"
        "def downgrade():\n"
        "    op.drop_table('t')\n"
    )
    contract, reviewed = _operations(source)
    assert contract == ["drop_column", "alter_column(nullable=False)"]
    assert reviewed == []
    widening = (
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.alter_column('t', 'c', type_=None)\n"
        "    op.drop_constraint('ck', 't', type_='check')\n"
    )
    assert _operations(widening) == ([], ["alter_column(type_=...)", "drop_constraint"])


def test_the_gate_ignores_downgrades_and_additive_upgrades() -> None:
    source = (
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.create_table('t')\n"
        "    op.add_column('t', None)\n"
        "    op.create_index('ix', 't', ['c'])\n"
        "    op.alter_column('t', 'c', nullable=True)\n"
        "def downgrade():\n"
        "    op.drop_table('t')\n"
    )
    assert _operations(source) == ([], [])
