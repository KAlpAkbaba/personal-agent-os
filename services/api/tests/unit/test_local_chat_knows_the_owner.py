"""The free conversation knows who it is talking to (ADR-0190).

The memory block — what this system knows about its owner — was built only while minting a
realtime credential, so it reached the PAID model's persona and nothing else. In the local
mode (ADR-0173) there is no persona and no model instruction: a sentence the router does
not understand goes to `assistant.chat`, which got the question and the last few turns of
this session and nothing else. It did not know the owner's name, their preferences, or that
it had run a research an hour ago - and on 2026-09-20 it said so out loud.
"""

from __future__ import annotations

from typing import Any

from app.assistant_chat import ChatAnswer


class _RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def answer(self, question: str, *, history, now_tr: str, about_owner: str = "") -> ChatAnswer:
        self.calls.append(
            {"question": question, "history": list(history), "about_owner": about_owner}
        )
        return ChatAnswer(speech="Peki efendim.", ok=True, model="fake")


def _ctx(monkeypatch, provider, *, memory_block: str) -> Any:
    from app.voice.realtime_sessions import tools_assistant

    monkeypatch.setattr(
        tools_assistant, "_owner_memory_block", lambda _ctx: memory_block, raising=False
    )

    class _Ctx:
        db = None
        session_id = "11111111-2222-4333-8444-555555555555"
        context = {"last_utterance": {"chat_question": "Bugün ne yapmalıyım?"}}
        live = {"chat_provider": provider}
        now = __import__("datetime").datetime(2026, 9, 20, 21, 0, tzinfo=__import__("datetime").UTC)

    return _Ctx()


def test_the_chat_is_given_what_the_system_knows_about_its_owner(monkeypatch) -> None:
    from app.voice.realtime_sessions.tools_assistant import assistant_chat

    provider = _RecordingProvider()
    ctx = _ctx(monkeypatch, provider, memory_block="Sahip Türkçe konuşur; yerel modu kullanır.")

    out = assistant_chat(ctx, {})

    assert out["answered"] is True
    assert provider.calls, "the provider was never asked"
    assert "yerel modu kullanır" in provider.calls[0]["about_owner"]


def test_an_empty_memory_block_changes_nothing(monkeypatch) -> None:
    """A system that knows nothing about its owner yet must still answer."""
    from app.voice.realtime_sessions.tools_assistant import assistant_chat

    provider = _RecordingProvider()
    ctx = _ctx(monkeypatch, provider, memory_block="")

    out = assistant_chat(ctx, {})

    assert out["answered"] is True
    assert provider.calls[0]["about_owner"] == ""


def test_the_provider_puts_it_in_the_system_prompt_not_in_the_question() -> None:
    """What the system knows is CONTEXT, not something the owner said: it rides in the
    system prompt, so the model can never read it back as the owner's own words."""
    from app.assistant_chat import AnthropicChatProvider

    sent: dict = {}

    def fake_send(url, headers, body, timeout_s):
        sent.update(body=body)
        return 200, {"content": [{"type": "text", "text": "peki"}]}

    provider = AnthropicChatProvider("sk-ant-x", send=fake_send)
    provider.answer(
        "Bugün ne yapmalıyım?",
        history=[],
        now_tr="20.09.2026 21:00",
        about_owner="Sahip Kadir; Türkçe konuşur.",
    )

    assert "Sahip Kadir" in sent["body"]["system"]
    assert "Sahip Kadir" not in str(sent["body"]["messages"])
