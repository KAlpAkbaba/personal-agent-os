"""Personality, dry wit (ROADMAP JARVIS row; ADR-0063 truthful speech stays above it).

ONE text, ``app.voice.wit.WIT_TR``, says when a dry remark may come, how much, and where it
NEVER does. The paid realtime persona and the local mode's chat prompt carry that same object,
and a single owner preference (``humor``: 'dry' | 'off') switches it off in both. Whether a
model actually lands a remark in the right place is heard by the owner, not measured here
(READY_FOR_OWNER); these tests prove the text, the single source, the order and the switch.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from app import assistant_chat
from app.actions.receipt import contains_fake_completion
from app.voice import wit
from app.voice.preferences import VoicePreferences
from app.voice.realtime_sessions import persona
from app.voice.realtime_sessions.persona import build_instructions, pronunciation_block

# --------------------------------------------------------------------------- the text


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def test_the_wit_rule_names_every_required_phrase() -> None:
    assert len(wit.WIT_REQUIRED_PHRASES) >= 8
    folded = _fold(wit.WIT_TR)
    missing = [p for p in wit.WIT_REQUIRED_PHRASES if _fold(p) not in folded]
    assert not missing, f"WIT_TR does not say: {missing}"


@pytest.mark.parametrize(
    "phrase",
    [
        "en çok bir",
        "aynen",
        "para",
        "güvenlik",
        "hata",
        "alarm",
        "sağlık",
        "önemli bildirim",
        "ciddi ol",
        "espri yapma",
        "yaptım",
        "soru",
    ],
)
def test_the_closed_list_holds_the_cards_phrases(phrase: str) -> None:
    # The list is closed: a later edit that drops a forbidden zone from the list AND the text
    # together would otherwise pass the test above.
    assert any(_fold(phrase) in _fold(p) for p in wit.WIT_REQUIRED_PHRASES), phrase


def test_a_tools_speech_is_read_verbatim_with_nothing_added() -> None:
    folded = _fold(wit.WIT_TR)
    assert "'speech'" in folded
    sentences = [s for s in folded.split(". ") if "'speech'" in s and "aynen" in s]
    assert sentences, "no sentence says a tool's 'speech' is read verbatim"


def test_every_forbidden_zone_is_named_in_the_text() -> None:
    assert len(wit.WIT_FORBIDDEN_ZONES_TR) >= 10
    folded = _fold(wit.WIT_TR)
    for zone in wit.WIT_FORBIDDEN_ZONES_TR:
        assert _fold(zone) in folded, zone


def test_the_wit_rule_never_carries_a_fake_completion_phrase() -> None:
    assert not contains_fake_completion(wit.WIT_TR)


def test_humor_values_are_the_two_the_card_names() -> None:
    assert wit.HUMOR_DRY == "dry"
    assert wit.HUMOR_OFF == "off"
    assert wit.normalize_humor("off") == "off"
    assert wit.normalize_humor("OFF ") == "off"
    for value in ("dry", "", None, "loud", 3):
        assert wit.normalize_humor(value) == "dry"


# --------------------------------------------------------------------------- persona


def test_the_persona_carries_the_wit_rule_by_default() -> None:
    assert wit.WIT_TR in build_instructions(VoicePreferences())


def test_humor_off_leaves_the_wit_rule_out() -> None:
    assert wit.WIT_TR not in build_instructions(VoicePreferences(humor="off"))


def test_an_unknown_humor_value_behaves_as_dry() -> None:
    assert wit.WIT_TR in build_instructions(VoicePreferences(humor="kahkaha"))


def test_the_wit_rule_sits_after_the_style_and_before_pronunciation_and_memory() -> None:
    pron = {"PDF": "pe de fe"}
    memory = "Sahip hakkında: kahveyi şekersiz içer."
    text = build_instructions(
        VoicePreferences(read_urls=True),
        voice_profile="arbor",
        pronunciation=pron,
        memory_block=memory,
    )
    at = text.index(wit.WIT_TR)
    assert text.index(persona.VOICE_STYLE_ARBOR_TR) < at
    assert text.index("Sahibi bağlantı adreslerinin okunmasını istiyor.") < at
    assert at < text.index(pronunciation_block(pron))
    assert at < text.index(memory)
    assert text.rstrip().endswith(memory), "the memory block must stay LAST"


# --------------------------------------------------------------------------- local mode


def _recorded_body(**kwargs: Any) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def send(url, headers, body, timeout_s):
        seen.append(body)
        return 200, {"content": [{"type": "text", "text": "Peki efendim."}]}

    provider = assistant_chat.AnthropicChatProvider("test-key", send=send)
    out = provider.answer(
        "Günaydın, bugün nasılsın?", history=[], now_tr="06.10.2026 08:00", **kwargs
    )
    assert out.ok
    return seen[0]


def test_local_chat_system_carries_the_same_wit_rule_in_order() -> None:
    body = _recorded_body(humor="dry", about_owner="Sahip kahveyi şekersiz içer.")
    system = body["system"]
    assert system.startswith(assistant_chat.SYSTEM_PROMPT_TR)
    assert assistant_chat.SYSTEM_PROMPT_TR in system
    assert system.index(assistant_chat.SYSTEM_PROMPT_TR) < system.index(wit.WIT_TR)
    assert system.index(wit.WIT_TR) < system.index("Sahip kahveyi şekersiz içer.")


def test_local_chat_default_is_dry_and_without_about_owner() -> None:
    system = _recorded_body()["system"]
    assert system.index(assistant_chat.SYSTEM_PROMPT_TR) < system.index(wit.WIT_TR)


def test_local_chat_humor_off_has_no_wit_rule() -> None:
    body = _recorded_body(humor="off", about_owner="Sahip kahveyi şekersiz içer.")
    assert wit.WIT_TR not in body["system"]
    assert "Sahip kahveyi şekersiz içer." in body["system"]


@pytest.mark.parametrize("humor", ["dry", "off"])
def test_the_wit_rule_never_rides_in_the_question(humor: str) -> None:
    body = _recorded_body(humor=humor)
    for message in body["messages"]:
        assert wit.WIT_TR not in message["content"]
        assert "nükte" not in _fold(message["content"])


# --------------------------------------------------------------------------- one source


def test_both_prompts_use_the_one_wit_object() -> None:
    assert persona.WIT_TR is wit.WIT_TR
    assert assistant_chat.WIT_TR is wit.WIT_TR


def _assigned_string_constants(module) -> dict[str, str]:
    tree = ast.parse(inspect.getsource(module))
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            value = node.value
            if isinstance(target, ast.Name) and value is not None:
                try:
                    literal = ast.literal_eval(value)
                except ValueError:
                    continue
                if isinstance(literal, str):
                    found[target.id] = literal
    return found


@pytest.mark.parametrize("module", [persona, assistant_chat])
def test_no_module_keeps_its_own_copy_of_the_wit_rule(module) -> None:
    source = inspect.getsource(module)
    assert "from app.voice.wit import" in source
    constants = _assigned_string_constants(module)
    assert "WIT_TR" not in constants, f"{module.__name__} defines its own WIT_TR"
    for name, text in constants.items():
        assert "nükte" not in _fold(text), f"{module.__name__}.{name} carries its own wit rule"


def test_the_wit_module_imports_none_of_its_readers() -> None:
    tree = ast.parse(Path(wit.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in (
        "app.voice.realtime_sessions.persona",
        "app.voice.preferences",
        "app.assistant_chat",
        "app.voice.realtime_sessions",
    ):
        assert banned not in imported, banned
    assert not any(name.startswith("app.") for name in imported), imported
