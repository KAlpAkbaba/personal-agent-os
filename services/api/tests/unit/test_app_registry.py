"""app.registry's discovery rules, on a throw-away package built in tmp (registry-models-and-routers).

The registry replaces two hand-kept lists (alembic/env.py's model imports, main.py's
include_router lines) that every parallel branch edited at the same place. Its rules:

- a models module is one whose last name is ``models`` or ends in ``_models``;
- a router is reached ONLY through an explicit module-level ``ROUTERS`` list in a module named
  ``routes`` - never inferred from a file existing or a variable named ``router``;
- order is ``ROUTER_ORDER`` (default 1000), then the module name, so it is deterministic;
- discovery loads nothing but those modules (and the package ``__init__`` files on the way);
  ``tests`` and ``scripts`` are never walked.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import app.registry as registry
from app.config import Settings
from app.main import create_app

#: Every module body below records that it RAN; a module discovery only names never runs.
SIDE_EFFECT = "import sys\nsys.modules['_regloaded:' + __name__] = sys\n"


def _router_module(prefix: str, *, declare: bool = True, order: int | None = None) -> str:
    text = (
        "from fastapi import APIRouter\n"
        f"router = APIRouter(prefix='{prefix}')\n\n"
        "@router.get('/ping')\n"
        "def ping() -> dict:\n"
        f"    return {{'from': '{prefix}'}}\n"
    )
    if declare:
        text += "ROUTERS = [router]\n"
    if order is not None:
        text += f"ROUTER_ORDER = {order}\n"
    return text


@pytest.fixture
def fake_package(tmp_path: Path) -> Iterator[str]:
    """A package under tmp with a unique name, so sys.modules never serves a previous test's."""
    name = f"regfake_{uuid.uuid4().hex[:10]}"
    files = {
        "__init__.py": "",
        "models.py": SIDE_EFFECT,
        "alpha/__init__.py": "",
        "alpha/models.py": SIDE_EFFECT,
        "alpha/helpers.py": SIDE_EFFECT,
        "beta/__init__.py": "",
        "beta/deep/__init__.py": "",
        "beta/deep/models.py": SIDE_EFFECT,
        "gamma/__init__.py": "",
        "gamma/mission_models.py": SIDE_EFFECT,
        "gamma/notmodels.py": SIDE_EFFECT,
        "gamma/models_extra.py": SIDE_EFFECT,
        "tests/__init__.py": "",
        "tests/models.py": SIDE_EFFECT,
        "tests/routes.py": _router_module("/tests"),
        "scripts/__init__.py": "",
        "scripts/models.py": SIDE_EFFECT,
        "zeta/__init__.py": "",
        "zeta/routes.py": _router_module("/zeta"),
        "able/__init__.py": "",
        "able/routes.py": _router_module("/able"),
        "early/__init__.py": "",
        "early/routes.py": _router_module("/early", order=5),
        "silent/__init__.py": "",
        "silent/routes.py": _router_module("/silent", declare=False),
        "silent/router.py": _router_module("/silent2"),
    }
    for rel, text in files.items():
        path = tmp_path / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        yield name
    finally:
        sys.path.remove(str(tmp_path))
        mine = (name, f"{name}.", f"_regloaded:{name}.")
        for module in [m for m in sys.modules if m == name or m.startswith(mine[1:])]:
            del sys.modules[module]


def _loaded(package: str) -> set[str]:
    mark = f"_regloaded:{package}."
    return {m[len(mark) :] for m in sys.modules if m.startswith(mark)}


def test_models_modules_are_found_by_name_in_alphabetical_order(fake_package: str) -> None:
    assert registry.discover_model_modules(fake_package) == [
        f"{fake_package}.alpha.models",
        f"{fake_package}.beta.deep.models",
        f"{fake_package}.gamma.mission_models",
        f"{fake_package}.models",
    ]


def test_discovering_models_imports_nothing_and_registering_imports_only_them(
    fake_package: str,
) -> None:
    registry.discover_model_modules(fake_package)
    assert _loaded(fake_package) == set()
    registry.register_models(fake_package)
    # helpers/notmodels/models_extra and the tests/scripts models were never executed
    assert _loaded(fake_package) == {"models", "alpha.models", "beta.deep.models", "gamma.mission_models"}
    for never in ("alpha.helpers", "gamma.notmodels", "gamma.models_extra", "tests.models", "scripts.models"):
        assert f"{fake_package}.{never}" not in sys.modules, never


def test_only_an_explicit_routers_list_is_bound_in_declared_order(fake_package: str) -> None:
    routers = registry.discover_routers(fake_package)
    prefixes = [r.prefix for r in routers]
    # early (ROUTER_ORDER 5) first, then the default 1000 by module name; silent/routes.py has a
    # `router` but no ROUTERS, silent/router.py is not named routes, tests/ is never walked.
    assert prefixes == ["/early", "/able", "/zeta"]
    assert f"{fake_package}.silent.router" not in sys.modules
    assert f"{fake_package}.alpha.helpers" not in sys.modules


def test_routers_must_be_api_routers(fake_package: str, tmp_path: Path) -> None:
    (tmp_path / fake_package / "zeta" / "routes.py").write_text("ROUTERS = ['not a router']\n", encoding="utf-8")
    with pytest.raises(TypeError, match="ROUTERS"):
        registry.discover_routers(fake_package)


def test_a_router_bound_both_by_a_line_and_by_routers_stops_start_up(fake_package: str) -> None:
    app = FastAPI()
    explicit = registry.discover_routers(fake_package)[1]
    app.include_router(explicit)
    with pytest.raises(RuntimeError, match="iki kez"):
        registry.include_discovered_routers(app, fake_package)


def test_discovered_routers_are_served(fake_package: str) -> None:
    app = FastAPI()
    included = registry.include_discovered_routers(app, fake_package)
    assert len(included) == 3
    client = TestClient(app)
    assert client.get("/zeta/ping").json() == {"from": "/zeta"}
    assert client.get("/silent/ping").status_code == 404


def test_the_real_application_binds_what_the_registry_discovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through create_app(), not a test app: a ROUTERS router is served by the real object."""
    extra = APIRouter(prefix="/v1/registry-probe")

    @extra.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "evet"}

    monkeypatch.setattr(registry, "discover_routers", lambda package="app": [extra])
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get("/v1/registry-probe/ping").json() == {"ok": "evet"}


def test_the_real_tree_declares_no_router_twice() -> None:
    """Today no package declares ROUTERS; whatever does later must not repeat a main.py line."""
    create_app(Settings(_env_file=None))  # raises on a router bound twice
