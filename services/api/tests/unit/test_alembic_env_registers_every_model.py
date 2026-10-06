"""alembic/env.py registers EVERY models module, found not listed (registry-models-and-routers).

Autogenerate compares Base.metadata with the database, so a models module env.py forgets
makes its tables look like tables to DROP. The hand-kept list had drifted: on 2026-10-06 it
imported 21 modules besides app.models while the tree held 46, and 46 tables
(conversations, mail_accounts, household_items, goals, ...) were invisible to autogenerate.
env.py now calls ``app.registry.register_models()``; this test pins that the discovered set
IS the tree, and that env.py really calls it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.models import Base
from app.registry import discover_model_modules, register_models

API = Path(__file__).resolve().parents[2]
ENV_PY = API / "alembic" / "env.py"


def _models_files_in_the_tree() -> set[str]:
    names = set()
    for path in (API / "app").rglob("*.py"):
        stem = path.stem
        if stem == "models" or stem.endswith("_models"):
            names.add(".".join(path.relative_to(API).with_suffix("").parts))
    return names


def test_discovery_finds_exactly_the_models_files_of_the_tree() -> None:
    found = discover_model_modules()
    assert found == sorted(found)
    tree = _models_files_in_the_tree()
    assert set(found) == tree, (
        f"ağaçta olup bulunmayan: {sorted(tree - set(found))}; "
        f"bulunup ağaçta olmayan: {sorted(set(found) - tree)}"
    )
    assert "app.operator.mission_models" in found


def test_every_mapped_table_comes_from_a_registered_module() -> None:
    registered = set(register_models())
    assert registered == _models_files_in_the_tree()
    mapped_tables = set()
    for mapper in Base.registry.mappers:
        assert mapper.class_.__module__ in registered, (
            f"{mapper.class_.__name__} ({mapper.class_.__module__}) bir models modülünde değil: "
            "autogenerate onun tablosunu ancak bir models modülü kaydederse görür"
        )
        mapped_tables.update(t.name for t in mapper.tables)
    unmapped = set(Base.metadata.tables) - mapped_tables
    assert not unmapped, f"eşlenmemiş tablo (modülü bilinmiyor): {sorted(unmapped)}"
    # the tables the hand-kept list had lost are registered now
    for table in (
        "conversations",
        "mail_accounts",
        "household_items",
        "goals",
        "webpush_subscriptions",
    ):
        assert table in Base.metadata.tables, table


def test_env_py_imports_no_app_module_by_hand() -> None:
    text = ENV_PY.read_text(encoding="utf-8")
    by_hand = [line for line in text.splitlines() if line.lstrip().startswith("import app.")]
    assert not by_hand, f"env.py'de elle yazılmış model içe aktarımı kaldı: {by_hand}"


def test_env_py_calls_register_models_before_reading_the_metadata() -> None:
    # A statement, not text: the header comment names register_models() too, so a
    # substring check stayed green with the call deleted (inspector, 2026-10-06) and
    # every table but app.models' would have looked like a table to DROP.
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"))
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.registry"
        and any(alias.name == "register_models" and alias.asname is None for alias in node.names)
        for node in tree.body
    )
    assert imported, "env.py 'from app.registry import register_models' satırını kaybetti"
    calls = [
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "register_models"
    ]
    assert calls, (
        "env.py modül düzeyinde register_models() çağırmıyor: autogenerate yalnız "
        "app.models tablolarını görür, gerisini SİLİNECEK sanar"
    )
    # the migrations run from the module-level `if context.is_offline_mode():`
    runs = [
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Attribute)
        and node.test.func.attr == "is_offline_mode"
    ]
    assert runs, "env.py'de göçleri koşan modül düzeyi if bloğu yok"
    assert calls[0] < runs[0], "register_models() göçler koşulduktan SONRA çağrılıyor"
