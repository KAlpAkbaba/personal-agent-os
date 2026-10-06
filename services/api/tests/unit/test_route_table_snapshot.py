"""The real application's route table, pinned line by line (card registry-models-and-routers).

Routers are now declared by their own package (``ROUTERS`` in ``routes.py``, found by
``app.registry``) instead of by a new ``app.include_router`` line in ``main.py``. Two things
must hold while that happens:

1. Moving or adding a router changes the route table only where it means to. The table of
   ``create_app()`` - method(s), path and name, WebSockets as ``WS`` - is compared with
   ``route_table_snapshot.txt``. A new endpoint adds its own lines to that file; a lost one is
   named in the failure. Refresh: ``PAGENTOS_UPDATE_ROUTE_SNAPSHOT=1`` and rerun this file.
2. ``main.py`` stops growing (the ratchet). Every new ``include_router`` line there is the
   line two parallel branches collide on at integration. The ceiling only goes down.

The same method+path twice is refused on its own: which one serves depends on order, and a
snapshot of a shadowed table would pin the shadowing.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute, _IncludedRouter
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

from app.config import Settings
from app.main import create_app

SNAPSHOT = Path(__file__).with_name("route_table_snapshot.txt")
MAIN = Path(__file__).resolve().parents[2] / "app" / "main.py"

#: The ratchet: ``app.include_router(`` calls in main.py on 2026-10-06. A new router declares
#: ``ROUTERS`` in its own routes.py; when existing lines move there, LOWER this number.
MAIN_INCLUDE_ROUTER_CEILING = 56


def _flatten(routes: list[BaseRoute]) -> Iterator[tuple[str, BaseRoute]]:
    for route in routes:
        if isinstance(route, _IncludedRouter):
            for candidate in route.effective_candidates():
                if isinstance(candidate, _IncludedRouter):
                    yield from _flatten([candidate])
                else:
                    # a WebSocket's effective context carries no path; its route's own does
                    original = candidate.original_route
                    yield candidate.path or getattr(original, "path", ""), original
        else:
            yield getattr(route, "path", ""), route


def route_table() -> list[str]:
    lines = []
    for path, route in _flatten(create_app(Settings(_env_file=None)).routes):
        name = getattr(route, "name", "") or ""
        if isinstance(route, APIWebSocketRoute | WebSocketRoute):
            verbs = "WS"
        elif isinstance(route, APIRoute | Route):
            verbs = ",".join(sorted(route.methods or ()))
        elif isinstance(route, Mount):
            verbs = "MOUNT"
        else:
            verbs = type(route).__name__
        lines.append(f"{verbs} {path} {name}")
    return sorted(lines)


def test_the_route_table_matches_the_snapshot() -> None:
    actual = route_table()
    if os.environ.get("PAGENTOS_UPDATE_ROUTE_SNAPSHOT") == "1":
        SNAPSHOT.write_bytes(("\n".join(actual) + "\n").encode("utf-8"))
    expected = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    lost = sorted(set(expected) - set(actual))
    added = sorted(set(actual) - set(expected))
    assert actual == expected, (
        "Yol tablosu görüntüden farklı.\n"
        f"  kaybolan ({len(lost)}): {lost}\n"
        f"  yeni ({len(added)}): {added}\n"
        "Bilerek yaptıysan görüntüyü yenile: PAGENTOS_UPDATE_ROUTE_SNAPSHOT=1 uv run pytest "
        "tests/unit/test_route_table_snapshot.py"
    )


def test_no_method_and_path_is_served_twice() -> None:
    counts: Counter[tuple[str, str]] = Counter()
    for line in route_table():
        verbs, path, _ = (line.split(" ", 2) + [""])[:3]
        for verb in verbs.split(","):
            counts[(verb, path)] += 1
    twice = sorted(key for key, n in counts.items() if n > 1)
    assert not twice, f"aynı yöntem+yol iki kez bağlı (sıra hangisinin cevap verdiğini seçer): {twice}"


def test_main_py_gets_no_new_include_router_line() -> None:
    calls = len(re.findall(r"\bapp\.include_router\(", MAIN.read_text(encoding="utf-8")))
    assert calls <= MAIN_INCLUDE_ROUTER_CEILING, (
        f"main.py'de {calls} app.include_router çağrısı var, tavan {MAIN_INCLUDE_ROUTER_CEILING}. "
        "Yeni router main.py'ye satır eklemez: kendi routes.py'nde ROUTERS = [router] ilan et "
        "(app/registry.py bulur)."
    )
    if calls < MAIN_INCLUDE_ROUTER_CEILING:
        # Not a failure: the ceiling follows the count down so it cannot climb back.
        print(f"main.py {calls} < tavan {MAIN_INCLUDE_ROUTER_CEILING}: sabiti {calls}'e indir")
