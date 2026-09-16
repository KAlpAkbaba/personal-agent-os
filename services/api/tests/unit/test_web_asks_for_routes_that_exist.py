"""B03 req 510/715: every path the Cockpit asks for is a path this API serves.

The creative panel asked for ``/v1/creative/runs``. The API mounted the list at the bare
prefix and ``/{run_id}`` after it, so FastAPI handed "runs" to a ``uuid.UUID`` parameter and
refused it: HTTP 422, for every owner, for ever. Both halves had tests. Neither had a test
that put them in the same room.

This is that test. It reads the path constants out of the web client's own source - not a
copy of them - and asks the real application object whether anything answers there.

B24 widened the reader. It used to see only `export const X_PATH = "/v1/…"`, which is how a
minority of the clients spell their route; the family pages added by 689/693/694/696/697/698/699
spell theirs inline in the `load(…)` call, and eleven routes the product had never asked for
went in with them. A guard that only reads the constants would have called that clean.

What it does NOT yet check: the METHOD. A path served only for GET while the Cockpit POSTs to
it would still pass here. That is a narrower gap than the one this closes, and naming it is
better than implying a completeness the test does not have.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

from app.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[4]
#: The whole web client, not just `app/lib`. B25 put `POST /v1/identity/panic` behind a
#: control in `app/security/PanicControl.tsx`, and a reader that only walked `app/lib`
#: would have called that clean — the kill switch pointed at a route nobody checked.
COCKPIT = REPO_ROOT / "apps" / "web" / "app"
_PATH_CONSTANT = re.compile(r'export const ([A-Z0-9_]+_PATH)\s*=\s*"(/v1/[^"]*)"')

#: A route spelled where it is called: ``load<Row[]>("/v1/alarms/history?limit=40", …)`` or
#: ``apiFetch("/v1/…")``. The query string is dropped; a template literal carrying ``${…}``
#: is skipped, because a path built at runtime is not a literal this reader can check.
_PATH_INLINE = re.compile(r'(?:load|apiFetch|getJson)[^("\n]*\(\s*"(/v1/[^"$]*)"')

#: A constant the web declares but deliberately does not call yet. Each entry must say why,
#: and the list may only shrink: an unserved path with no reason here fails the test.
DECLARED_BUT_NOT_SERVED: dict[str, str] = {}


def _declared_paths() -> dict[str, str]:
    found: dict[str, str] = {}
    for source in sorted([*COCKPIT.rglob("*.ts"), *COCKPIT.rglob("*.tsx")]):
        text = source.read_text("utf-8")
        for name, path in _PATH_CONSTANT.findall(text):
            found[name] = path
        for path in _PATH_INLINE.findall(text):
            # Named by where it is, so a failure says which file to open. The query string
            # is not part of the route the API declares.
            bare = path.split("?", 1)[0]
            found[f"{source.name}:{bare}"] = bare
    return found


@lru_cache(maxsize=1)
def _app():
    return create_app(Settings(_env_file=None))


def _served_paths() -> set[str]:
    """What the application object says it serves.

    Read from the OpenAPI document rather than by walking ``app.routes``: an included router
    appears there as one opaque object, so a walk that only looks at the top level finds a
    single route and every assertion below would fail for the wrong reason.
    """
    return set(_app().openapi()["paths"])


def test_the_reader_finds_the_constants_at_all() -> None:
    """A guard that matches nothing passes for ever."""
    declared = _declared_paths()
    assert len(declared) >= 30, declared
    assert declared.get("CREATIVE_RUNS_PATH") == "/v1/creative/runs"
    # One of each spelling, so neither reader can quietly stop matching.
    assert declared.get("detail.ts:/v1/alarms/history") == "/v1/alarms/history"
    assert declared.get("api.ts:/v1/world") == "/v1/world"
    # A .tsx file outside `app/lib`: the kill switch's route (B25 req 660).
    assert declared.get("PANIC_PATH") == "/v1/identity/panic"


@pytest.mark.parametrize(
    ("name", "path"), sorted(_declared_paths().items()), ids=lambda value: str(value)
)
def test_every_path_the_cockpit_declares_is_served(name: str, path: str) -> None:
    if name in DECLARED_BUT_NOT_SERVED:
        pytest.skip(f"{name}: {DECLARED_BUT_NOT_SERVED[name]}")
    served = _served_paths()
    assert path in served, (
        f"{name} = {path!r} is what the Cockpit asks for and nothing in the API answers there. "
        "Either the route is missing, or a path parameter declared before it is swallowing "
        "the literal segment - which is how /v1/creative/runs became a permanent 422."
    )


def test_the_route_table_is_read_the_way_the_owner_reaches_it() -> None:
    """A guard on the guard: if the reader stops finding routes, every assertion above would
    pass vacuously for a path nobody serves."""
    served = _served_paths()
    assert len(served) > 100, f"only {len(served)} paths found; the reader is broken"
    assert "/v1/system/health" in served
