"""B20 req 235: the web client's idea of "no provider" is the server's own.

The browser cannot import `VoiceErrorClass`, so it restates the classes that mean "there is
no voice to be had" and the HTTP statuses they arrive with. Two lists in two languages is
exactly the shape this repository has been bitten by before — both sides green, both sides
describing a different contract — so this reads the TypeScript and fails when it drifts.

The direction matters in both senses:

* a class the client lists that the server does not map to 502/503 would make the client
  wait for a "condition" that never arrives that way;
* a class the server maps to 503 and the client does not list would be rendered as
  `HTTP 503` beside a hopeful "Bağlan" button, which is the defect req 235 names.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.voice.errors import VoiceErrorClass
from app.voice.realtime_sessions.routes import _STATUS_BY_CLASS

WEB_API = (
    Path(__file__).resolve().parents[4] / "apps" / "web" / "app" / "lib" / "voice" / "api.ts"
)


def _web_classes() -> set[str]:
    source = WEB_API.read_text(encoding="utf-8")
    match = re.search(
        r"PROVIDER_UNAVAILABLE_CLASSES: ReadonlySet<string> = new Set\(\[(.*?)\]\)",
        source,
        re.S,
    )
    assert match, "PROVIDER_UNAVAILABLE_CLASSES not found in the web client"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _web_statuses() -> set[int]:
    source = WEB_API.read_text(encoding="utf-8")
    match = re.search(r"get providerUnavailable\(\): boolean \{(.*?)\n  \}", source, re.S)
    assert match, "providerUnavailable getter not found in the web client"
    return {int(n) for n in re.findall(r"this\.status !== (\d{3})", match.group(1))}


def test_the_web_client_knows_the_servers_unavailable_classes():
    server_503 = {
        cls.value for cls, status in _STATUS_BY_CLASS.items() if status == 503
    }
    assert server_503, "the routes no longer map anything to 503; this test is stale"
    assert server_503 <= _web_classes()


def test_the_web_client_invents_no_class_of_its_own():
    known = {cls.value for cls in VoiceErrorClass}
    listed = _web_classes()
    assert listed <= known
    # And each one really does arrive with a status the client watches for.
    watched = _web_statuses()
    for value in listed:
        status = _STATUS_BY_CLASS.get(VoiceErrorClass(value))
        assert status in watched, f"{value} arrives as {status}, which the client ignores"


def test_a_class_that_is_the_owners_own_fault_is_not_treated_as_unavailable():
    # The condition means "nothing to call". A bad request or an internal bug is neither,
    # and rendering those as a standing condition would hide a real fault behind a
    # sentence about credentials.
    listed = _web_classes()
    assert VoiceErrorClass.VALIDATION_ERROR.value not in listed
    assert VoiceErrorClass.INTERNAL_BUG.value not in listed
    assert VoiceErrorClass.EMPTY_RESULT.value not in listed
