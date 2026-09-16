"""B25 req 701: "Neler yapabilirsin?" is derived, and derived correctly.

Two claims, and they fail in different ways, so they are tested apart.

**Derived.** The requirement forbids a hand-written list in four words — *elle liste
yasak* — because a written list of a system's own abilities is a second source of truth
that starts drifting the day after it is written. The test for that is not "the list has
130 entries"; it is that a DIFFERENT registry produces a different list, which a hard-coded
answer could never do.

**Correct in Turkish.** The example phrases are lifted out of the tool descriptions, and the
obvious way to lift them is wrong here for the reason this repository keeps meeting: the
apostrophe is a suffix separator. ``Active Eye'ı (kamerayı) KAPATIR: 'gözünü kapat'`` opens a
naive quote at ``Eye'`` and closes it before ``gözünü``, extracting the label and losing the
phrase. Three of the six families checked by hand were damaged that way in the first draft.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from app.voice import capabilities as caps
from app.voice.realtime_sessions.tools import ToolRegistry, ToolSpec, default_registry
from app.voice.realtime_sessions.tools_assistant import (
    TOOL_NAME,
    assistant_capabilities,
)
from tests.identity_support import issue_token, make_identity_engine


def _noop(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    del ctx, arguments
    return {}


def tiny_registry() -> ToolRegistry:
    """Two tools nobody ships, so a hard-coded answer cannot pass for a derived one."""
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="teapot.brew",
            description="Çay DEMLER: 'çay demle', 'bir demlik çay yap'. Dönen 'speech' oku.",
            parameters={"type": "object", "properties": {}},
            handler=_noop,
        )
    )
    reg.register(
        ToolSpec(
            name="teapot.status",
            description="Demliğin durumunu söyler.",
            parameters={"type": "object", "properties": {}},
            handler=_noop,
        )
    )
    return reg


# ------------------------------------------------------------------- derived


def test_the_list_comes_from_the_registry_it_is_given() -> None:
    items = caps.capabilities(tiny_registry())
    assert [item.name for item in items] == ["teapot.brew", "teapot.status"]
    assert [item.phrases for item in items] == [["çay demle", "bir demlik çay yap"], []]
    # The family has no Turkish name, and the bare key is used rather than a blank: an
    # unnamed family should look unfinished, not invisible.
    assert items[0].family_tr == "teapot"


def test_the_real_registry_is_what_the_product_answers_from() -> None:
    items = caps.capabilities()
    names = {item.name for item in items}
    assert len(items) > 100, "the registry should be the whole tool surface"
    assert "alarm.create" in names
    assert TOOL_NAME in names, "the tool that answers the question is itself an answer"


def test_every_family_has_a_turkish_name_and_no_name_is_orphaned() -> None:
    """Both directions, because each fails differently.

    A family with no Turkish name reaches the owner as a bare English prefix
    (``selfmodel``). A Turkish name for a family that no longer exists is dead weight that
    quietly claims the list is more complete than it is.
    """
    registry_families = {caps.family_of(name) for name in default_registry().names()}
    named = set(caps.FAMILY_TR)
    assert registry_families - named == set(), "a family reached the owner with no Turkish name"
    assert named - registry_families == set(), "a Turkish name with no family behind it"


def test_internal_tools_are_not_offered_as_things_to_say() -> None:
    names = {item.name for item in caps.capabilities()}
    for internal in caps.INTERNAL_TOOLS:
        assert internal not in names
    # And the guard is not vacuous: those tools do exist in the registry.
    assert caps.INTERNAL_TOOLS <= set(default_registry().names())


# ------------------------------------------------------ correct in Turkish


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Active Eye'ı (kamerayı) KAPATIR: 'gözünü kapat', 'kamerayı kapat', 'beni izleme'.",
            ["gözünü kapat", "kamerayı kapat", "beni izleme"],
        ),
        (
            "Güncel hava durumunu bildirir: 'Hava nasıl?', 'İstanbul'da hava nasıl?'.",
            ["Hava nasıl?", "İstanbul'da hava nasıl?"],
        ),
        (
            "Bir MASAÜSTÜ UYGULAMASINI AÇAR: 'Not Defteri'ni aç', 'Chrome'u aç'.",
            ["Not Defteri'ni aç", "Chrome'u aç"],
        ),
    ],
)
def test_a_turkish_suffix_apostrophe_is_not_a_quote(
    description: str, expected: list[str]
) -> None:
    assert caps.phrases_in(description) == expected


def test_a_field_name_is_not_something_anybody_says() -> None:
    assert caps.phrases_in("Dönen 'speech' metnini oku; 'alarm_id' alarm.status'tan gelir.") == []


def test_the_summary_stops_at_the_earliest_break() -> None:
    # ": " comes first here and the sentence break is twenty words later; taking the later
    # one would put every example phrase into the summary as well as into the phrase list.
    summary = caps.summarise(
        "Uyandırma alarmı KURAR: 'yarın sabah 07:30'da beni uyandır', 'saat 08:00'e alarm "
        "kur'. Dönen 'speech' metnini aynen oku."
    )
    assert summary == "Uyandırma alarmı KURAR"

    # And a description with no break at all survives whole rather than being emptied.
    assert caps.summarise("Demliğin durumunu söyler.") == "Demliğin durumunu söyler."


def test_the_phrase_list_is_bounded() -> None:
    many = "YAPAR: " + ", ".join(f"'komut {i} burada'" for i in range(12))
    assert len(caps.phrases_in(many)) == caps.MAX_PHRASES


# ------------------------------------------------------------------- spoken


def test_the_spoken_answer_is_not_the_list() -> None:
    """Reading 130 tool names aloud is a refusal wearing an answer's clothes."""
    items = caps.capabilities()
    spoken = caps.speech(items)
    assert len(spoken) < 250, spoken
    assert str(len(items)) in spoken
    for item in items:
        assert item.name not in spoken, "a tool NAME reached the speaker"
    assert "Ses sayfasında" in spoken, "the owner is told where the whole list is"


def test_the_spoken_answer_is_honest_about_an_empty_registry() -> None:
    assert "yeteneğim yok" in caps.speech([])


# --------------------------------------------------------------- the tool


def test_the_tool_answers_the_whole_question() -> None:
    result = assistant_capabilities(None, {})
    assert result["known"] is True
    assert result["count"] == len(caps.capabilities())
    assert result["families"], "the areas are named"
    assert result["capabilities"], "the full list travels in the result, not in the speech"


def test_the_tool_narrows_to_one_area() -> None:
    result = assistant_capabilities(None, {"family": "alarm"})
    assert result["known"] is True
    assert {item["family"] for item in result["capabilities"]} == {"alarm"}
    assert "Alarmlar" in result["speech"]
    assert "alarmı" in result["speech"], "it says something the owner could repeat"


def test_an_unknown_area_still_answers() -> None:
    """Refusing the whole question over one mistyped word is the failure 701 removes."""
    result = assistant_capabilities(None, {"family": "zzz"})
    assert result["known"] is False
    assert result["capabilities"] == []
    assert "yok" in result["speech"]
    assert result["families"], "and it still says what it DOES have"


def test_the_tool_is_registered_once() -> None:
    names = default_registry().names()
    assert names.count(TOOL_NAME) == 1


# --------------------------------------------------------------- the route


def _client() -> tuple[TestClient, str]:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    runtime = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    app.state.identity = runtime
    runtime.service.bootstrap()
    return TestClient(app), issue_token(runtime)


def test_the_route_serves_the_derived_list() -> None:
    client, token = _client()
    response = client.get("/v1/voice/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(caps.capabilities())
    assert body["families"], "the areas, for a page that groups them"
    assert body["speech"]
    names = {row["name"] for row in body["capabilities"]}
    assert "alarm.create" in names
    # The phrase the page tells the owner to say is the phrase the model was told to hear.
    alarm = next(row for row in body["capabilities"] if row["name"] == "alarm.snooze")
    assert "beş dakika ertele" in alarm["phrases"]


def test_the_route_narrows_by_family() -> None:
    client, token = _client()
    response = client.get(
        "/v1/voice/capabilities?family=alarm", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["family"] for row in body["capabilities"]} == {"alarm"}
    assert body["count"] == len(body["capabilities"])
    # The family index stays whole so the page can still offer the other areas.
    assert len(body["families"]) > 1


def test_the_route_refuses_without_an_owner_session() -> None:
    client, _ = _client()
    assert client.get("/v1/voice/capabilities").status_code == 401
