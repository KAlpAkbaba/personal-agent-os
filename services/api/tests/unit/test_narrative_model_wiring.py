"""Unit tests: ``activity.explain`` hands the session's chat provider to the narrative only
behind ``narrative_model_enabled`` (default OFF), and the turn's ledger note says which
narrator spoke and why the rule narrator did when it did (ROADMAP order 2c, ADR-0244 B).

The path is the real one: the tool-call route of the real application object, the real
router, ``explain_to_briefing``, the production evidence source, ``tell`` and the auditor.
The provider is a fake or the real ``AnthropicChatProvider`` over a replaced transport - the
real API is never called, and the OFF tests hold that with the key really in the environment.

The ledger rows are seeded against the clock the tool reads (the session's real ``now``),
not a fixed date: "bu hafta" is the last seven days ending now.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.models import ArtifactVersion
from app.assistant_chat import ERROR_CHAT_UNAVAILABLE, AnthropicChatProvider, ChatAnswer
from app.config import Settings, get_settings
from app.explain import service as explain_service
from app.explain.engine import QUERY_NARRATIVE
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_BROWSER_SEARCH,
    EVENT_TYPE_MAIL_SENT,
    EVENT_TYPE_RESEARCH_COMPLETED,
    EVENT_TYPE_RESEARCH_FAILED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_BROWSER,
    SUBSYSTEM_MAIL,
    SUBSYSTEM_RESEARCH,
)
from app.narrative.service import tell
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, add_row
from tests.unit.test_voice_realtime_sessions import _create, wired  # noqa: F401

QUESTION = "bu hafta ne oldu"
FAKE_KEY = "sk-test-not-a-real-key"

#: a draft that names the completed work and drops BOTH failures. "ses" is there because
#: creating the voice session writes its own completed ledger row, as it does in production.
DROPS = "Hafta yoğundu; araştırma, tarayıcı, ses ve posta alanlarında işler tamamlandı."
#: a draft that names everything and then invents a number.
INVENTS = (
    f"{FAIL_RESEARCH}; {FAIL_MAIL}. Araştırma, tarayıcı, ses ve posta alanlarında işler "
    "tamamlandı. Ayrıca 47 dosya silindi."
)


class _Provider:
    """A scripted ChatProvider; counts how often it was asked."""

    name = "fake"
    configured = True

    def __init__(self, speech: str = DROPS, *, raises: Exception | None = None) -> None:
        self.speech = speech
        self.raises = raises
        self.questions: list[str] = []

    def answer(self, question, *, history, now_tr, about_owner=""):
        self.questions.append(question)
        if self.raises is not None:
            raise self.raises
        return ChatAnswer(self.speech, True, None)


def _settings(*, enabled: bool | None, key: str = FAKE_KEY) -> Settings:
    extra = {} if enabled is None else {"narrative_model_enabled": enabled}
    return Settings(_env_file=None, anthropic_api_key=key, **extra)


def _seed(engine) -> None:
    now = datetime.now(UTC)
    with Session(engine) as db:
        for ref, ago, etype, sub, status, summary, device, result, detail in (
            ("r1", 30, EVENT_TYPE_RESEARCH_FAILED, SUBSYSTEM_RESEARCH, STATUS_FAILED,
             FAIL_RESEARCH, "ofis", "timeout", None),
            ("r2", 29, EVENT_TYPE_RESEARCH_COMPLETED, SUBSYSTEM_RESEARCH, STATUS_COMPLETED,
             "Rapor hazırlandı", "ofis", None, None),
            ("r3", 5, EVENT_TYPE_BROWSER_SEARCH, SUBSYSTEM_BROWSER, STATUS_COMPLETED,
             "Sayfa açıldı", "ev", None, None),
            ("r4", 80, EVENT_TYPE_MAIL_SENT, SUBSYSTEM_MAIL, STATUS_FAILED,
             FAIL_MAIL, "ev", "no_capable_device", {"error_class": "no_capable_device"}),
        ):  # fmt: skip
            add_row(
                db, ref, now - timedelta(hours=ago), etype, sub, status, summary, device,
                result, detail,
            )  # fmt: skip
        db.commit()


def _rule_text(engine) -> str:
    """What the rule narrator says over the ledger as it stands (before the ask adds its
    own ``voice.explained`` row)."""
    with Session(engine) as db:
        return tell(db, "bu hafta", None, None)


def _ask(client, sid: str, question: str = QUESTION, call_id: str = "n1") -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": "activity.explain", "arguments": {"question": question}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    return body["result"]


def _told(engine) -> str:
    """The briefing as it was stored: the canonical body, before the narration plan's
    pronunciation pass turns "2" into "iki" for the ear."""
    with Session(engine) as db:
        versions = db.scalars(select(ArtifactVersion)).all()
        assert len(versions) == 1
        return versions[0].canonical_body


def _note(engine) -> dict:
    """The detail of the turn's ``voice.explained`` ledger row."""
    with Session(engine) as db:
        rows = db.scalars(
            select(ActivityEventRow).where(ActivityEventRow.event_type == "voice.explained")
        ).all()
        assert len(rows) == 1, [r.factual_summary for r in rows]
        return dict(rows[0].detail_json)


@pytest.fixture()
def sent(monkeypatch):
    """Every request the assistant's transport is asked to send (none reaches the API)."""
    calls: list[str] = []

    def record(url, headers, body, timeout_s):
        calls.append(url)
        return 200, {"content": [{"type": "text", "text": DROPS}], "stop_reason": "end_turn"}

    monkeypatch.setattr("app.assistant_chat._http_send", record)
    return calls


@pytest.fixture()
def built(monkeypatch):
    """Every provider the explain seam builds from settings."""
    made: list[object] = []
    real = explain_service.build_chat_provider

    def spy(settings):
        provider = real(settings)
        made.append(provider)
        return provider

    monkeypatch.setattr(explain_service, "build_chat_provider", spy)
    return made


@pytest.fixture()
def handed(monkeypatch):
    """The keyword arguments ``activity.explain`` calls ``explain_to_briefing`` with."""
    seen: list[dict] = []
    real = explain_service.explain_to_briefing

    def spy(*args, **kwargs):
        seen.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(explain_service, "explain_to_briefing", spy)
    return seen


@pytest.fixture()
def keyed_process(monkeypatch):
    """A shell (or ``.env``) that carries the owner's key - production's own environment."""
    monkeypatch.setenv("PAGENTOS_ANTHROPIC_API_KEY", FAKE_KEY)
    get_settings.cache_clear()
    assert get_settings().anthropic_api_key == FAKE_KEY
    yield
    monkeypatch.delenv("PAGENTOS_ANTHROPIC_API_KEY")
    get_settings.cache_clear()


# --------------------------------------------------------------------------- the setting


def test_the_setting_is_off_by_default_and_read_from_its_env_name(monkeypatch):
    monkeypatch.delenv("PAGENTOS_NARRATIVE_MODEL_ENABLED", raising=False)
    assert Settings(_env_file=None).narrative_model_enabled is False
    monkeypatch.setenv("PAGENTOS_NARRATIVE_MODEL_ENABLED", "true")
    assert Settings(_env_file=None).narrative_model_enabled is True


# --------------------------------------------------------------------------- OFF


@pytest.mark.parametrize("enabled", [None, False], ids=["default", "false"])
def test_off_builds_no_provider_and_sends_nothing_with_a_key_everywhere(
    wired, sent, built, handed, keyed_process, enabled
):
    """The keyed-shell rule: the key is in the environment AND in the session's settings,
    the transport is a recorder - and with the setting off nothing is built or sent."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    runtime.register_live(settings=_settings(enabled=enabled))
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    result = _ask(client, sid)

    assert result["query"]["kind"] == QUERY_NARRATIVE
    assert expected in _told(engine)
    assert built == [] and sent == []
    assert len(handed) == 1 and "chat_provider" not in handed[0], "the call is today's"
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "setting_off")


def test_off_never_asks_the_provider_the_session_carries_for_assistant_chat(wired, sent):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(DROPS)
    runtime.register_live(settings=_settings(enabled=False), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    result = _ask(client, sid)
    assert provider.questions == [] and sent == []
    assert result["query"]["kind"] == QUERY_NARRATIVE and expected in _told(engine)


def test_a_session_without_settings_is_off(wired, sent, built, keyed_process):
    """The switch is the SESSION's settings (as the provider is), never the process's."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(DROPS)
    runtime.register_live(chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    _ask(client, sid)
    assert provider.questions == [] and built == [] and sent == []
    assert _note(engine)["narrator_reason"] == "setting_off"


# --------------------------------------------------------------------------- ON


def test_on_the_model_narrates_and_a_dropped_failure_is_put_back(wired, sent):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(DROPS)
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    result = _ask(client, sid)

    assert len(provider.questions) == 1 and sent == []
    assert result["speech"].startswith(DROPS), "the model's words are spoken"
    assert FAIL_RESEARCH in result["speech"] and FAIL_MAIL in result["speech"]
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("model", None)
    assert DROPS not in json.dumps(note, ensure_ascii=False), "never the provider's text"


def test_on_a_draft_that_invents_a_number_is_replaced_by_the_rule_text(wired):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(INVENTS)
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    result = _ask(client, sid)

    assert len(provider.questions) == 1
    assert expected in _told(engine)
    assert "silindi" not in _told(engine) and "silindi" not in result["speech"]
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "audit_rejected")
    assert "silindi" not in json.dumps(note, ensure_ascii=False)


@pytest.mark.parametrize("upto", [". ", " Tamamlananlar:"], ids=["first-sentence", "failures"])
def test_on_a_rejected_draft_that_opens_the_rule_text_is_still_the_rule_narrators(wired, upto):
    """The draft IS the opening of the rule text ("2 iş başarısız oldu.", or that plus the
    failure sentences) and skips the completed areas, so the auditor rejects it and the rule
    text is spoken. What was spoken then starts with the draft - and the note must still say
    the rule narrator spoke: only the auditor's own repair may follow a model's draft."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider()
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    assert expected.startswith("2 iş başarısız oldu. ")
    provider.speech = expected[: expected.index(upto)].rstrip() + ("." if upto == ". " else "")
    assert expected.startswith(provider.speech + " "), "the trap is set"
    _ask(client, sid)

    assert len(provider.questions) == 1
    told = _told(engine)
    assert expected in told
    assert "Ayrıca başarısız" not in told, "the rule text, not a repaired draft"
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "audit_rejected")


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out"), httpx.ReadTimeout("timed out")],
    ids=["TimeoutError", "httpx.ReadTimeout"],
)
def test_on_a_provider_that_times_out_falls_back_and_the_note_says_timeout(wired, error):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(raises=error)
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    result = _ask(client, sid)

    assert len(provider.questions) == 1
    assert result["query"]["kind"] == QUERY_NARRATIVE and expected in _told(engine)
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "timeout")


def test_on_a_provider_that_raises_anything_else_is_a_provider_error(wired):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(raises=RuntimeError("boom"))
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    _ask(client, sid)
    assert expected in _told(engine)
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "provider_error")


def test_on_the_session_settings_build_the_real_provider_and_one_request_is_sent(
    wired, sent, built
):
    """No injected provider: the one the tool hands on is ``narrative_chat_provider``'s -
    the assistant-chat builder over the session's settings - and an ask is ONE request."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    runtime.register_live(settings=_settings(enabled=True))
    _seed(engine)
    sid = _create(client)["session_id"]
    result = _ask(client, sid)
    assert len(built) == 1 and isinstance(built[0], AnthropicChatProvider)
    assert len(sent) == 1
    assert result["speech"].startswith(DROPS) and FAIL_RESEARCH in result["speech"]
    assert _note(engine)["narrator"] == "model"


def test_on_the_real_provider_swallows_a_transport_timeout_into_its_own_error_class(
    wired, built, monkeypatch
):
    """KNOWN GAP, pinned: ``AnthropicChatProvider.answer`` catches every ``httpx.HTTPError``
    (a timeout among them) and returns ``chat_unavailable`` - so over the REAL provider a
    timeout is recorded under that class, not as "timeout". Telling them apart is
    ``app/assistant_chat.py``'s to do (outside this task's area). The fallback itself holds:
    the rule text is spoken, one attempt is made, and the note names the rule narrator."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    attempts: list[float] = []

    def times_out(url, headers, body, timeout_s):
        attempts.append(timeout_s)
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.assistant_chat._http_send", times_out)
    runtime.register_live(settings=_settings(enabled=True))
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    result = _ask(client, sid)

    assert attempts == [20.0], "one attempt, bounded by assistant_chat_timeout_s"
    assert result["query"]["kind"] == QUERY_NARRATIVE and expected in _told(engine)
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", ERROR_CHAT_UNAVAILABLE)


def test_on_without_a_key_the_rule_narrator_answers_and_the_note_says_no_provider(wired, sent):
    client, _identity, runtime, _sideband, _issued, engine = wired
    runtime.register_live(settings=_settings(enabled=True, key=""))
    _seed(engine)
    sid = _create(client)["session_id"]
    expected = _rule_text(engine)
    _ask(client, sid)
    assert expected in _told(engine) and sent == []
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "no_provider")


def test_on_a_window_with_no_rows_never_reaches_the_provider(wired):
    """Nothing happened on that device: the rule narrator says so and the model is not
    asked (the session's own row is a cloud row, so "ofiste" selects nothing)."""
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(DROPS)
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    result = _ask(client, _create(client)["session_id"], "bu hafta ofiste ne oldu")
    assert result["query"]["kind"] == QUERY_NARRATIVE
    assert provider.questions == []
    note = _note(engine)
    assert (note["narrator"], note["narrator_reason"]) == ("rule", "not_asked")


def test_on_a_question_that_is_not_a_narrative_asks_nobody_and_names_no_narrator(wired, sent):
    client, _identity, runtime, _sideband, _issued, engine = wired
    provider = _Provider(DROPS)
    runtime.register_live(settings=_settings(enabled=True), chat_provider=provider)
    _seed(engine)
    sid = _create(client)["session_id"]
    result = _ask(client, sid, "Son yaptıklarını anlat")
    assert result["query"]["kind"] != QUERY_NARRATIVE
    assert provider.questions == [] and sent == []
    note = _note(engine)
    assert "narrator" not in note and "narrator_reason" not in note
