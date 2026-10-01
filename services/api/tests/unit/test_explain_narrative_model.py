"""Unit tests: the production evidence source narrates with the model, held to the auditor,
and tells the failures only when that is what was asked (ROADMAP order 2c).

The provider is a fake: the real API is never called here. The ledger, the artifact and the
narration rows are real (sqlite), and the end-to-end tests go through ``explain_to_briefing``
with the production ``evidence_source_factory`` - the object the voice tool calls.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, ArtifactVersion
from app.assistant_chat import AnthropicChatProvider, ChatAnswer
from app.explain import service as explain_service
from app.explain.engine import QUERY_NARRATIVE
from app.explain.service import LedgerEvidenceSource, explain_to_briefing
from app.ledger.models import ActivityEventRow
from app.narration.models import NarrationSession, PronunciationEntry
from app.narrative.collector import collect
from app.narrative.facts import only_failures
from app.narrative.intent import NarrativeAsk, recognise
from app.narrative.narrator import RuleNarrator
from app.narrative.service import NO_FAILURES_TEXT, tell
from app.voice.intents import resolve_intent
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, NOW, seed

#: what ``explain_to_briefing`` writes: the ledger, the briefing artifact, its narration.
TABLES = (ActivityEventRow, Artifact, ArtifactVersion, NarrationSession, PronunciationEntry)

WEEK = NarrativeAsk("bu hafta", None)
WEEK_FAILURES = NarrativeAsk("bu hafta", None, failures_only=True)

#: a draft that names the completed work and drops BOTH failures.
DROPS = "Hafta yoğundu; araştırma, tarayıcı ve posta alanlarında işler tamamlandı."
#: a draft that names everything and then invents a number.
INVENTS = (
    f"{FAIL_RESEARCH}; {FAIL_MAIL}. Araştırma, tarayıcı ve posta alanlarında işler "
    "tamamlandı. Ayrıca 47 dosya silindi."
)


class _Provider:
    """A scripted ChatProvider; counts how often it was asked."""

    name = "fake"

    def __init__(self, speech: str, *, configured: bool = True) -> None:
        self.speech = speech
        self.configured = configured
        self.questions: list[str] = []

    def answer(self, question, *, history, now_tr, about_owner=""):
        self.questions.append(question)
        return ChatAnswer(self.speech, True, None)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for model in TABLES:
        model.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    seed(session)
    yield session
    session.close()


# --------------------------------------------------------------------------- the source


def test_a_draft_that_drops_a_failure_is_spoken_with_the_failure_put_back(db):
    provider = _Provider(DROPS)
    text = LedgerEvidenceSource(db, chat_provider=provider).narrative(WEEK, now=NOW)
    assert len(provider.questions) == 1
    assert text.startswith(DROPS), "the model's words are spoken"
    assert FAIL_RESEARCH in text and FAIL_MAIL in text, "and the auditor put both failures back"


def test_a_draft_that_invents_a_number_is_replaced_by_the_rule_text(db):
    provider = _Provider(INVENTS)
    text = LedgerEvidenceSource(db, chat_provider=provider).narrative(WEEK, now=NOW)
    assert len(provider.questions) == 1
    assert text == tell(db, "bu hafta", None, None, now=NOW)
    assert "47" not in text and "silindi" not in text


def test_a_provider_that_is_not_configured_is_never_called(db):
    provider = _Provider(DROPS, configured=False)
    text = LedgerEvidenceSource(db, chat_provider=provider).narrative(WEEK, now=NOW)
    assert provider.questions == []
    assert text == tell(db, "bu hafta", None, None, now=NOW)


def test_without_a_provider_the_rule_narrator_answers_as_before(db):
    assert LedgerEvidenceSource(db).narrative(WEEK, now=NOW) == tell(
        db, "bu hafta", None, None, now=NOW
    )


def test_the_source_tells_the_failures_only_when_the_ask_says_so(db):
    text = LedgerEvidenceSource(db).narrative(WEEK_FAILURES, now=NOW)
    assert text == RuleNarrator().tell(only_failures(collect(db, "bu hafta", now=NOW)))
    assert FAIL_RESEARCH in text and FAIL_MAIL in text
    assert "Tamamlananlar" not in text and "Toplam 2 kayıt." in text


def test_a_failures_only_draft_that_drops_a_failure_has_it_put_back(db):
    provider = _Provider(f"Bu hafta şu iş olmadı: {FAIL_MAIL}.")
    text = LedgerEvidenceSource(db, chat_provider=provider).narrative(WEEK_FAILURES, now=NOW)
    assert text.startswith(provider.speech) and FAIL_RESEARCH in text
    assert '"tamamlanan": []' in provider.questions[0]


def test_no_failure_never_reaches_the_provider(db):
    provider = _Provider("Her şey yolunda.")
    ask = NarrativeAsk("bugün", None, failures_only=True)
    assert LedgerEvidenceSource(db, chat_provider=provider).narrative(ask, now=NOW) == (
        NO_FAILURES_TEXT
    )
    assert provider.questions == []


# --------------------------------------------------------------------------- the factory


class _Settings:
    anthropic_api_key = ""
    assistant_chat_model = ""
    research_anthropic_base_url = ""
    assistant_chat_timeout_s = 20.0


def test_the_factory_builds_the_provider_with_the_assistant_chat_builder(db, monkeypatch):
    seen = []
    provider = _Provider(DROPS)

    def build(settings):
        seen.append(settings)
        return provider

    monkeypatch.setattr(explain_service, "get_settings", lambda: _Settings)
    monkeypatch.setattr(explain_service, "build_chat_provider", build)
    source = explain_service.evidence_source_factory(db)
    assert isinstance(source, LedgerEvidenceSource)
    assert seen == [_Settings]
    assert source.narrative(WEEK, now=NOW).startswith(DROPS)


def test_an_empty_key_does_not_raise_and_the_rule_narrator_answers(db, monkeypatch):
    def no_network(*args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("an unconfigured provider must not send a request")

    monkeypatch.setattr(explain_service, "get_settings", lambda: _Settings)
    monkeypatch.setattr("app.assistant_chat._http_send", no_network)
    source = explain_service.evidence_source_factory(db)
    assert isinstance(source._chat_provider, AnthropicChatProvider)
    assert source._chat_provider.configured is False
    assert source.narrative(WEEK, now=NOW) == tell(db, "bu hafta", None, None, now=NOW)


def test_settings_that_cannot_be_read_leave_the_rule_narrator(db, monkeypatch):
    def broken():
        raise RuntimeError("no environment")

    monkeypatch.setattr(explain_service, "get_settings", broken)
    source = explain_service.evidence_source_factory(db)
    assert source.narrative(WEEK, now=NOW) == tell(db, "bu hafta", None, None, now=NOW)


# --------------------------------------------------------------------------- end to end


def _factory_with(monkeypatch, provider) -> None:
    monkeypatch.setattr(explain_service, "get_settings", lambda: _Settings)
    monkeypatch.setattr(explain_service, "build_chat_provider", lambda settings: provider)


def test_bu_hafta_ne_oldu_end_to_end_is_narrated_by_the_model_and_audited(db, monkeypatch):
    provider = _Provider(DROPS)
    _factory_with(monkeypatch, provider)
    record = explain_to_briefing(db, "bu hafta ne oldu", now=NOW)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert len(provider.questions) == 1
    told = record.briefing.executive[0].text
    assert told.startswith(DROPS) and FAIL_RESEARCH in told and FAIL_MAIL in told
    assert FAIL_RESEARCH in record.speech and FAIL_MAIL in record.speech
    assert record.narration_session_id is not None


#: the failure question in the one spelling the ROUTER hands to the narrative today.
ROUTED_FAILURE_QUESTION = "bu hafta ne basarisiz oldu"


def test_a_failure_question_the_router_hands_over_is_told_failures_only_end_to_end(db, monkeypatch):
    """The real router, the real query_for, the real factory: nothing is substituted but
    the provider."""
    assert resolve_intent(ROUTED_FAILURE_QUESTION).query_kind == QUERY_NARRATIVE
    provider = _Provider(f"İki iş olmadı: {FAIL_RESEARCH}; {FAIL_MAIL}.")
    _factory_with(monkeypatch, provider)
    record = explain_to_briefing(db, ROUTED_FAILURE_QUESTION, now=NOW)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.facts["failures_only"] is True
    assert record.briefing.executive[0].text == provider.speech
    assert '"tamamlanan": []' in provider.questions[0]
    assert '"toplam_kayıt": 2' in provider.questions[0]


def test_the_failure_question_without_a_model_lists_no_completed_work_end_to_end(db):
    record = explain_to_briefing(
        db, ROUTED_FAILURE_QUESTION, now=NOW, source=LedgerEvidenceSource(db)
    )
    told = record.briefing.executive[0].text
    assert FAIL_RESEARCH in told and FAIL_MAIL in told
    assert "Tamamlananlar" not in told and "Toplam 2 kayıt." in told
    assert FAIL_RESEARCH in record.speech and "Tamamlananlar" not in record.speech


def test_ne_basarisiz_oldu_reaches_the_narrative_failures_only_once_it_is_routed_there(
    db, monkeypatch
):
    """'ne başarısız oldu' with its Turkish letters is OWNED by the explain ``failures``
    family (the router is not this task's to change), so the router's decision is the one
    thing substituted here; everything after it - query_for, the engine, the production
    factory, the source, ``tell`` - is the real path."""
    question = "ne başarısız oldu"
    assert recognise(question) == WEEK_FAILURES
    routed = resolve_intent(ROUTED_FAILURE_QUESTION)
    monkeypatch.setattr(explain_service, "resolve_intent", lambda text: routed)
    provider = _Provider(f"Şu iş olmadı: {FAIL_MAIL}.")  # drops the research failure
    _factory_with(monkeypatch, provider)
    record = explain_to_briefing(db, question, now=NOW)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.facts["failures_only"] is True
    told = record.briefing.executive[0].text
    assert told.startswith(provider.speech) and FAIL_RESEARCH in told
    assert "tarayıcı" not in told and "Tamamlananlar" not in told
