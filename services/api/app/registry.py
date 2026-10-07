"""Shared registries found from the tree, not kept by hand (card registry-models-and-routers).

Two hand-kept lists were the line every parallel branch edited at the same place and then
collided on at integration: alembic/env.py's ``import app.X.models`` lines and main.py's
``app.include_router`` lines. A package now declares itself:

- its ORM tables live in a module named ``models`` or ``*_models`` - found by name;
- its routers are listed in a module-level ``ROUTERS`` in its ``routes.py`` (optionally with
  ``ROUTER_ORDER``, default 1000). A router is never inferred from a file existing or from a
  variable named ``router``: binding an endpoint is an explicit act.

Discovery walks the file system (stdlib ``pkgutil.iter_modules``), so naming a module runs
nothing; only ``register_models`` / ``discover_routers`` import, and only those modules (with
the package ``__init__`` files Python imports on the way). ``tests`` and ``scripts`` are never
walked. Results are sorted, so the order never depends on the file system.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from fastapi import APIRouter, FastAPI
from fastapi.routing import _IncludedRouter

DEFAULT_ROUTER_ORDER = 1000
_NEVER_WALKED = frozenset({"tests", "scripts"})


def _walk(package: str) -> Iterator[str]:
    """Every module name under ``package``, from the file system, without importing them."""
    root = importlib.import_module(package)

    def walk(paths: list[str], prefix: str) -> Iterator[str]:
        for info in pkgutil.iter_modules(paths, prefix):
            short = info.name.rsplit(".", 1)[1]
            if info.ispkg:
                if short in _NEVER_WALKED:
                    continue
                location = getattr(info.module_finder, "path", None)
                if location is not None:
                    yield from walk([f"{location}/{short}"], info.name + ".")
            else:
                yield info.name

    yield from walk(list(root.__path__), package + ".")


def _is_models(name: str) -> bool:
    short = name.rsplit(".", 1)[1]
    return short == "models" or short.endswith("_models")


def discover_model_modules(package: str = "app") -> list[str]:
    """The names of the ``models`` / ``*_models`` modules under ``package``, alphabetically."""
    return sorted(name for name in _walk(package) if _is_models(name))


def register_models(package: str = "app") -> list[str]:
    """Import every models module so its tables are on ``Base.metadata``; return their names."""
    names = discover_model_modules(package)
    for name in names:
        importlib.import_module(name)
    return names


def discover_routers(package: str = "app") -> list[APIRouter]:
    """The routers ``routes`` modules declare in ``ROUTERS``, by ``ROUTER_ORDER`` then name."""
    declared: list[tuple[int, str, list[APIRouter]]] = []
    for name in sorted(n for n in _walk(package) if n.rsplit(".", 1)[1] == "routes"):
        module = importlib.import_module(name)
        routers = getattr(module, "ROUTERS", None)
        if routers is None:
            continue
        if not isinstance(routers, list | tuple) or not all(
            isinstance(r, APIRouter) for r in routers
        ):
            raise TypeError(f"{name}.ROUTERS bir APIRouter listesi olmalı, bulunan: {routers!r}")
        order = getattr(module, "ROUTER_ORDER", DEFAULT_ROUTER_ORDER)
        declared.append((int(order), name, list(routers)))
    declared.sort(key=lambda item: (item[0], item[1]))
    return [router for _, _, routers in declared for router in routers]


def include_discovered_routers(app: FastAPI, package: str = "app") -> list[APIRouter]:
    """Bind every declared router; a router main.py already binds stops start-up loudly."""
    bound = {id(r.original_router) for r in app.router.routes if isinstance(r, _IncludedRouter)}
    routers = discover_routers(package)
    for router in routers:
        if id(router) in bound:
            raise RuntimeError(
                f"Router iki kez bağlanıyor (prefix {router.prefix!r}): hem main.py'de "
                "app.include_router satırı hem routes.py'de ROUTERS var. Birini kaldır - "
                "yeni yol ROUTERS'tır."
            )
        app.include_router(router)
        bound.add(id(router))
    return routers
