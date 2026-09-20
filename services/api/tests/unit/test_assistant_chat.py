"""Free conversation in the local voice mode (ADR-0173 addendum; owner decision 2026-09-19,
"Claude Haiku (çok ucuz ama ücretli)").

The provider is exercised through an injected ``send`` - no network - and the whole path runs
through the REAL relay the web client uses: an utterance the router does not understand is
named ``assistant.chat`` in a local session, and the tool answers from the turn record.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app import assistant_chat as chat
from app.voice.providers_local_router import LocalRouterRealtimeProvider
from tests.unit.test_voice_local_mode import TRANSPORT_TEXT, _create, _say, wired  # noqa: F401


class _Send:
    def __init__(self, *answers: tuple[int, dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float):
        self.calls.append((url, headers, body))
        return self.answers.pop(0)


def _ok(text: str) -> tuple[int, dict[str, Any]]:
    return 200, {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def _provider(send: _Send, **kw: Any) -> chat.AnthropicChatProvider:
    return chat.AnthropicChatProvider("k-test", send=send, sleep=lambda _s: None, **kw)


# ------------------------------------------------------------------ the provider


def test_the_request_is_a_plain_messages_call_with_the_alias_and_no_tools() -> None:
    send = _Send(_ok("Kuantum bilgisayar, kübitlerle hesap yapan bir makinedir efendim."))
    answer = _provider(send).answer(
        "Kuantum bilgisayar nedir?", history=[], now_tr="19.09.2026 22:40"
    )
    assert answer.ok and answer.speech.startswith("Kuantum bilgisayar")
    url, headers, body = send.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["anthropic-version"] == "2023-06-01" and headers["x-api-key"] == "k-test"
    assert body["model"] == "claude-haiku-4-5"
    assert body["system"] == chat.SYSTEM_PROMPT_TR
    assert "tools" not in body and "thinking" not in body
    assert body["messages"] == [
        {"role": "user", "content": "[Şu an: 19.09.2026 22:40]\nKuantum bilgisayar nedir?"}
    ]
    assert (answer.input_tokens, answer.output_tokens) == (120, 30)


def test_history_is_sent_before_the_new_question() -> None:
    send = _Send(_ok("Evet efendim."))
    history = [
        {"role": "user", "content": "Ankara'nın nüfusu kaç?"},
        {"role": "assistant", "content": "Yaklaşık beş milyon yedi yüz bin efendim."},
    ]
    _provider(send).answer("Peki İzmir?", history=history, now_tr="x")
    assert send.calls[0][2]["messages"][:2] == history


def test_a_retired_model_is_said_once_and_never_retried() -> None:
    send = _Send((404, {"type": "error", "error": {"type": "not_found_error"}}))
    answer = _provider(send).answer("Merhaba", history=[], now_tr="x")
    assert not answer.ok and answer.error_class == chat.ERROR_CHAT_MODEL_RETIRED
    assert len(send.calls) == 1


def test_overloaded_is_asked_once_more_then_said_as_busy() -> None:
    assert (
        _provider(_Send((529, {}), _ok("Buradayım efendim.")))
        .answer("Orada mısın?", history=[], now_tr="x")
        .ok
    )
    busy = _Send((429, {}), (429, {}))
    answer = _provider(busy).answer("Orada mısın?", history=[], now_tr="x")
    assert answer.error_class == chat.ERROR_CHAT_BUSY and len(busy.calls) == 2


def test_a_refusal_is_said_as_one_and_nothing_else_is_read() -> None:
    send = _Send((200, {"content": [{"type": "text", "text": "x"}], "stop_reason": "refusal"}))
    answer = _provider(send).answer("...", history=[], now_tr="x")
    assert not answer.ok and answer.speech == chat.SPEECH_REFUSED


def test_no_key_is_said_plainly_and_nothing_is_sent() -> None:
    send = _Send()
    provider = chat.AnthropicChatProvider("", send=send)
    answer = provider.answer("Merhaba", history=[], now_tr="x")
    assert answer.error_class == chat.ERROR_CHAT_UNAVAILABLE and send.calls == []


def test_memory_keeps_the_last_turns_and_forgets_old_sessions() -> None:
    clock = {"t": 0.0}
    memory = chat.ChatMemory(clock=lambda: clock["t"])
    for i in range(chat.MAX_TURNS + 3):
        memory.remember("s", f"soru {i}", f"yanıt {i}")
    kept = memory.history("s")
    assert len(kept) == 2 * chat.MAX_TURNS and kept[-1]["content"] == f"yanıt {chat.MAX_TURNS + 2}"
    clock["t"] += chat.SESSION_TTL_S + 1
    assert memory.history("s") == []


# ------------------------------------------------------------- through the real relay


class _FakeChat:
    name = "fake"
    configured = True

    def __init__(self) -> None:
        self.asked: list[tuple[str, list[dict[str, str]]]] = []
        #: ADR-0190: what the caller told it about the owner, so a test can see whether
        #: the memory block travelled.
        self.about_owner: list[str] = []

    def answer(
        self,
        question: str,
        *,
        history: list[dict[str, str]],
        now_tr: str,
        about_owner: str = "",
    ) -> chat.ChatAnswer:
        self.asked.append((question, history))
        self.about_owner.append(about_owner)
        return chat.ChatAnswer(f"Yanıt: {question}", True, None, "fake")


def _tool(client, sid: str, name: str) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"local-{uuid.uuid4()}", "name": name, "arguments": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_sentence_the_router_does_not_know_is_free_conversation_in_a_local_session(
    wired,  # noqa: F811
) -> None:
    client, _identity, runtime, *_ = wired
    local = LocalRouterRealtimeProvider()
    runtime.providers[local.name] = local
    fake = _FakeChat()
    runtime.register_live(chat_provider=fake)
    sid = _create(client, transport=TRANSPORT_TEXT)["session_id"]

    said = _say(client, sid, "Kuantum bilgisayar nedir?", turn=1)
    assert said["resolved_intents"][0]["intent"] == "none"
    assert said["resolved_intents"][0]["tool"] == "assistant.chat"
    body = _tool(client, sid, "assistant.chat")
    assert body["status"] == "succeeded", body
    assert body["result"]["speech"] == "Yanıt: Kuantum bilgisayar nedir?"
    assert fake.asked[0] == ("Kuantum bilgisayar nedir?", [])

    # The second question carries the first turn as context.
    _say(client, sid, "Peki ne işe yarar?", turn=2)
    _tool(client, sid, "assistant.chat")
    assert fake.asked[1][1][0] == {"role": "user", "content": "Kuantum bilgisayar nedir?"}

    # A COMMAND is still the router's and never goes to the chat model.
    said = _say(client, sid, "Gözünü kapat.", turn=3)
    assert said["resolved_intents"][0]["tool"] == "eye.disable"
    assert len(fake.asked) == 2


def test_the_chat_tool_answers_nothing_without_a_local_question(wired) -> None:  # noqa: F811
    """A paid session's model calling assistant.chat finds no question on the turn and
    spends nothing: the realtime model answers its own conversation."""
    client, _identity, runtime, *_ = wired
    fake = _FakeChat()
    runtime.register_live(chat_provider=fake)
    sid = _create(client)["session_id"]
    said = _say(client, sid, "Kuantum bilgisayar nedir?", turn=1)
    assert said["resolved_intents"][0]["tool"] is None  # never named outside the local mode
    body = _tool(client, sid, "assistant.chat")
    assert body["result"]["answered"] is False and fake.asked == []
    assert body["result"]["speech"] == ""  # nothing is read aloud


@pytest.mark.parametrize("model", ["claude-haiku-4-5"])
def test_the_shipped_chat_model_is_an_alias_not_a_dated_id(model: str) -> None:
    from app.config import Settings

    assert Settings(_env_file=None).assistant_chat_model == model
    assert chat.DEFAULT_MODEL == model
