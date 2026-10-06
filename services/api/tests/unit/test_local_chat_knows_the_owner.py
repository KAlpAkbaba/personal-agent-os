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

    def answer(
        self, question: str, *, history, now_tr: str, about_owner: str = "", humor: str = "dry"
    ) -> ChatAnswer:
        self.calls.append(
            {
                "question": question,
                "history": list(history),
                "about_owner": about_owner,
                "humor": humor,
            }
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


# ------------------------------------------------------------ humor (persona-dry-wit)


def test_the_owners_humor_off_reaches_the_local_chat(monkeypatch) -> None:
    from app import assistant_chat as chat
    from app.voice.realtime_sessions.tools_assistant import assistant_chat

    provider = _RecordingProvider()
    ctx = _ctx(monkeypatch, provider, memory_block="")
    monkeypatch.setattr(chat, "owner_humor", lambda _db: "off")

    assistant_chat(ctx, {})

    assert provider.calls[0]["humor"] == "off"


def test_the_local_chat_is_dry_when_the_preference_says_so(monkeypatch) -> None:
    from app import assistant_chat as chat
    from app.voice.realtime_sessions.tools_assistant import assistant_chat

    provider = _RecordingProvider()
    ctx = _ctx(monkeypatch, provider, memory_block="")
    monkeypatch.setattr(chat, "owner_humor", lambda _db: "dry")

    assistant_chat(ctx, {})

    assert provider.calls[0]["humor"] == "dry"


class _Rows:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Db:
    def __init__(self, value=None, *, fails: bool = False) -> None:
        self._value, self._fails = value, fails
        self.writes = 0

    def execute(self, _statement):
        if self._fails:
            raise RuntimeError("database gone")
        return _Rows(self._value)

    def add(self, *_a) -> None:
        self.writes += 1

    def commit(self) -> None:
        self.writes += 1


def test_owner_humor_reads_the_saved_switch_without_writing() -> None:
    from app.assistant_chat import owner_humor

    db = _Db({"humor": "off", "owner_set": ["humor"]})
    assert owner_humor(db) == "off"
    assert db.writes == 0


def test_owner_humor_falls_back_to_dry() -> None:
    from app.assistant_chat import owner_humor

    assert owner_humor(None) == "dry"
    assert owner_humor(_Db(None)) == "dry"  # no profile row yet
    assert owner_humor(_Db({})) == "dry"  # a row written before the switch existed
    assert owner_humor(_Db({"humor": "kahkaha"})) == "dry"
    assert owner_humor(_Db(fails=True)) == "dry"  # never raising into the conversation


def test_owner_humor_reads_the_real_profile_row() -> None:
    """The real query against the real table: what the preferences route saves is what the
    local chat reads."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.assistant_chat import owner_humor
    from app.voice.models import VoiceProfile
    from app.voice.preferences import VoicePreferences

    engine = create_engine("sqlite://")
    VoiceProfile.__table__.create(engine)
    with Session(engine) as db:
        assert owner_humor(db) == "dry"
        prefs = VoicePreferences().apply_update({"humor": "off"}, source="owner")
        db.add(VoiceProfile(label="owner", narration_settings_json=prefs.to_narration_settings()))
        db.commit()
        assert owner_humor(db) == "off"
