"""The Cockpit asks for a path; this file makes sure the Cloud Core serves it.

The vocabulary guards in ``test_uistate_contract_halves.py`` hold the two halves of the
UI-state contract to each other. The ROUTE is a second contract between the same two
halves and had nothing holding it, which cost a real defect the day M28's two tracks met:
``apps/web/app/lib/cockpit/native.ts`` was written from the spec's prose and asked for
``/v1/native/builds``, while ``app/nativefactory/routes.py`` serves the list at
``/v1/native``.

That mismatch is worse than a 404, and this is the part worth remembering. The router's
next route is ``@router.get("/{build_id}")`` typed ``uuid.UUID``, so the extra segment is
parsed as a build id and answered **422**, not 404 — and the web's ``load()`` maps
everything that is not a 404 onto ``failed``. The panel would have shown "Alınamadı: HTTP
422" on every poll for ever: an outage the owner would go looking for, instead of the
honest "henüz yok" the panel was built to show while a route is missing. Both suites were
green either way, because each side tested its own belief about the path.

So this reads the OTHER side's source, the same deliberately dumb way — a regex over the
TypeScript, because anything cleverer would be a second implementation of the thing it is
checking.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.nativefactory.routes import router

_RELATIVE = Path("apps") / "web" / "app" / "lib" / "cockpit" / "native.ts"


def _find_web_client() -> Path:
    """Walk up rather than count parents: a wrong depth would make this guard read
    nothing and pass, which is worse than not having it (ADR-0087 addendum 1)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _RELATIVE
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[-1] / _RELATIVE


_WEB_CLIENT = _find_web_client()


def _served_paths() -> set[str]:
    return {str(route.path) for route in router.routes if hasattr(route, "path")}


def _web_path() -> str:
    text = _WEB_CLIENT.read_text(encoding="utf-8")
    match = re.search(r'export const NATIVE_BUILDS_PATH = "([^"]+)";', text)
    assert match is not None, f"NATIVE_BUILDS_PATH not found in {_WEB_CLIENT}"
    return match.group(1)


def test_the_web_client_is_where_this_guard_thinks_it_is() -> None:
    assert _WEB_CLIENT.is_file(), f"the web client is not at {_WEB_CLIENT}"


def test_the_list_path_the_cockpit_asks_for_is_one_this_router_serves() -> None:
    asked = _web_path()
    served = _served_paths()
    assert asked in served, (
        f"the Cockpit asks for {asked!r}, which this router does not serve ({sorted(served)}). "
        f"Note it is probably NOT a 404 either: '/v1/native/<anything>' matches the "
        f"'{{build_id}}' route, whose uuid.UUID type answers 422, and the web reads any "
        f"non-404 as a failure - so the panel says 'Alınamadı' rather than 'henüz yok'"
    )


def test_that_check_would_notice_the_defect_it_was_written_for() -> None:
    """The detector proves itself before it is trusted: the guess that actually shipped
    must not be in the served set, or this guard would pass against the bug."""
    assert "/v1/native/builds" not in _served_paths()


def test_the_list_route_answers_under_a_key_the_web_reads() -> None:
    """The other half of the same handshake. ``list_builds`` returns ``{"builds": [...]}``
    and the client's ``listAt`` looks under ``builds``/``native_builds``/``items`` (or
    takes a bare array). A route that renamed its envelope key would hand the panel an
    empty list, which renders as "Henüz bir yerel uygulama üretilmedi." — a confident
    statement about the world, from a response that held every build there is.
    """
    source = Path(router.routes[0].endpoint.__code__.co_filename).read_text(encoding="utf-8")
    envelope = re.search(r'return \{"(\w+)": await asyncio\.to_thread\(load\)\}', source)
    assert envelope is not None, "the list route's envelope could not be read"
    accepted = re.findall(
        r"listAt\(raw, \[(.*?)\]\)", _WEB_CLIENT.read_text(encoding="utf-8"), re.S
    )
    assert accepted, "the web client's listAt keys could not be read"
    keys = set(re.findall(r'"(\w+)"', accepted[0]))
    assert envelope.group(1) in keys, (
        f"the route answers under {envelope.group(1)!r} and the web reads {sorted(keys)} - "
        f"the panel would draw an empty list and say so in words"
    )
