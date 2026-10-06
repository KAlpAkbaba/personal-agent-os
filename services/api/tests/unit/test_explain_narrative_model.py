"""Unit tests: the production evidence source narrates with the model, held to the auditor,
and tells the failures only when that is what was asked (ROADMAP order 2c).

The provider is a fake: the real API is never called here. The ledger, the artifact and the
narration rows are real (sqlite), and the end-to-end tests go through ``explain_to_briefing``
with the production ``evidence_source_factory`` - the object the voice tool calls.

The provider is the CALLER's (``narrative_chat_provider(ctx.live)``), never the process-wide
settings: a keyed shell must not make a suite call the real API, and that is tested here
with the key really in the environment and the transport replaced by a recorder.
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


# --------------------------------------------------------------------------- the provider seam


class _Settings:
    anthropic_api_key = ""
    assistant_chat_model = ""
    research_anthropic_base_url = ""
    assistant_chat_timeout_s = 20.0


class _KeyedSettings(_Settings):
    anthropic_api_key = "sk-test-not-a-real-key"


@pytest.fixture()
def sent(monkeypatch):
    """Every request the assistant's transport is asked to send. The real API is never
    reached: the transport itself is replaced, and a test asserts on what it recorded."""
    calls: list[str] = []

    def record(url, headers, body, timeout_s):
        calls.append(url)
        return 200, {"content": [{"type": "text", "text": DROPS}], "stop_reason": "end_turn"}

    monkeypatch.setattr("app.assistant_chat._http_send", record)
    return calls


@pytest.fixture()
def keyed_process(monkeypatch):
    """A shell (or ``.env``) that carries the owner's key: what the process-wide settings
    read in a keyed terminal and in production."""
    from app.config import get_settings

    monkeypatch.setenv("PAGENTOS_ANTHROPIC_API_KEY", _KeyedSettings.anthropic_api_key)
    get_settings.cache_clear()
    assert get_settings().anthropic_api_key == _KeyedSettings.anthropic_api_key
    yield
    monkeypatch.delenv("PAGENTOS_ANTHROPIC_API_KEY")
    get_settings.cache_clear()


def test_a_keyed_process_does_not_make_the_factory_call_the_model(db, sent, keyed_process):
    """The inspector's finding: the factory read the process-wide settings, so a keyed
    shell made every suite that reaches ``explain_to_briefing`` call the real API."""
    source = explain_service.evidence_source_factory(db)
    assert source.narrative(WEEK, now=NOW) == tell(db, "bu hafta", None, None, now=NOW)
    assert sent == []


def test_a_keyed_process_does_not_make_explain_call_the_model_end_to_end(db, sent, keyed_process):
    """The call the voice tool makes today (no provider handed over): the rule text."""
    record = explain_to_briefing(db, "bu hafta ne oldu", now=NOW)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.executive[0].text == tell(db, "bu hafta", None, None, now=NOW)
    assert sent == []


def test_the_session_provider_is_taken_before_any_settings(sent):
    provider = _Provider(DROPS)
    live = {"chat_provider": provider, "settings": _KeyedSettings}
    assert explain_service.narrative_chat_provider(live) is provider
    assert sent == []


def test_the_session_settings_build_the_provider_with_the_assistant_chat_builder(db, sent):
    provider = explain_service.narrative_chat_provider({"settings": _KeyedSettings})
    assert isinstance(provider, AnthropicChatProvider) and provider.configured
    assert sent == [], "building sends nothing"
    text = LedgerEvidenceSource(db, chat_provider=provider).narrative(WEEK, now=NOW)
    assert len(sent) == 1 and text.startswith(DROPS)
    assert FAIL_RESEARCH in text and FAIL_MAIL in text


@pytest.mark.parametrize("live", [None, {}, {"settings": None}, {"settings": _Settings}])
def test_a_session_without_a_key_gives_no_provider_and_does_not_raise(live, sent, keyed_process):
    """Not the process-wide key either: only what the session carries counts."""
    assert explain_service.narrative_chat_provider(live) is None
    assert sent == []


def test_a_session_provider_that_is_not_configured_gives_no_provider():
    live = {"chat_provider": _Provider(DROPS, configured=False)}
    assert explain_service.narrative_chat_provider(live) is None


def test_settings_that_cannot_be_read_give_no_provider():
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("no environment")

    assert explain_service.narrative_chat_provider({"settings": Broken()}) is None


def test_the_factory_hands_the_provider_to_the_ledger_source(db):
    provider = _Provider(DROPS)
    source = explain_service.evidence_source_factory(db, chat_provider=provider)
    assert isinstance(source, LedgerEvidenceSource)
    assert source.narrative(WEEK, now=NOW).startswith(DROPS)
    assert len(provider.questions) == 1


def test_a_one_argument_factory_still_serves_a_call_without_a_provider(db, monkeypatch):
    """The suites that replace the factory do it with ``lambda db: source``."""
    plain = LedgerEvidenceSource(db)
    monkeypatch.setattr(explain_service, "evidence_source_factory", lambda db: plain)
    record = explain_to_briefing(db, "bu hafta ne oldu", now=NOW)
    assert record.briefing.executive[0].text == tell(db, "bu hafta", None, None, now=NOW)


# --------------------------------------------------------------------------- end to end


def test_bu_hafta_ne_oldu_end_to_end_is_narrated_by_the_model_and_audited(db, sent):
    provider = _Provider(DROPS)
    record = explain_to_briefing(db, "bu hafta ne oldu", now=NOW, chat_provider=provider)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert len(provider.questions) == 1 and sent == []
    told = record.briefing.executive[0].text
    assert told.startswith(DROPS) and FAIL_RESEARCH in told and FAIL_MAIL in told
    assert FAIL_RESEARCH in record.speech and FAIL_MAIL in record.speech
    assert record.narration_session_id is not None


#: the failure question in the one spelling the ROUTER hands to the narrative today.
ROUTED_FAILURE_QUESTION = "bu hafta ne basarisiz oldu"


def test_a_failure_question_the_router_hands_over_is_told_failures_only_end_to_end(db):
    """The real router, the real query_for, the real factory: nothing is substituted, and
    the provider is the one the caller hands over."""
    assert resolve_intent(ROUTED_FAILURE_QUESTION).query_kind == QUERY_NARRATIVE
    provider = _Provider(f"İki iş olmadı: {FAIL_RESEARCH}; {FAIL_MAIL}.")
    record = explain_to_briefing(db, ROUTED_FAILURE_QUESTION, now=NOW, chat_provider=provider)
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


#: the owner's own spellings. The router gives the ones with a period or in the plural
#: ('bu hafta', 'bugün', 'neler') to the narrative told the failures only (ADR-0244 A);
#: the bare singular 'ne başarısız oldu' stays with the explain ``failures`` family,
#: which speaks the LATEST failure.
OWNER_FAILURE_QUESTIONS = (
    "ne başarısız oldu",
    "bu hafta ne başarısız oldu",
    "bugün ne başarısız oldu",
    "neler başarısız oldu",
)


@pytest.mark.parametrize("question", OWNER_FAILURE_QUESTIONS)
def test_the_owners_spelling_is_answered_without_the_model_whoever_owns_it(db, question):
    """Through the real router, nothing substituted. A question with a period or in the
    plural is a narrative told the failures only: it names the period's failures, or -
    when the period has none ('bugün' in the seed) - says so with the rule text and never
    asks the model. The bare singular stays with the explain ``failures`` family: it names
    the latest failure and asks no provider. No branch lists completed work."""
    provider = _Provider(f"İki iş olmadı: {FAIL_RESEARCH}; {FAIL_MAIL}.")
    record = explain_to_briefing(db, question, now=NOW, chat_provider=provider)
    assert "Tamamlananlar" not in record.speech
    if record.briefing.query.kind == QUERY_NARRATIVE:
        assert record.briefing.facts["failures_only"] is True
        if record.speech == NO_FAILURES_TEXT:
            assert provider.questions == [], "no failure never reaches the provider"
        else:
            assert FAIL_RESEARCH in record.speech or FAIL_MAIL in record.speech
            assert all('"tamamlanan": []' in asked for asked in provider.questions)
    else:
        assert FAIL_RESEARCH in record.speech or FAIL_MAIL in record.speech
        assert provider.questions == []


def test_ne_basarisiz_oldu_reaches_the_narrative_failures_only_once_it_is_routed_there(
    db, monkeypatch
):
    """ROUTER SUBSTITUTED - this is not the owner's path today. 'ne başarısız oldu' with
    its Turkish letters is OWNED by the explain ``failures`` family (the router is not
    this task's to change), so the router's decision is the one thing replaced here;
    everything after it - query_for, the engine, the factory, the source, ``tell`` - is
    the real path. It proves what the router's decision would switch on, nothing more."""
    question = "ne başarısız oldu"
    assert recognise(question) == WEEK_FAILURES
    routed = resolve_intent(ROUTED_FAILURE_QUESTION)
    monkeypatch.setattr(explain_service, "resolve_intent", lambda text: routed)
    provider = _Provider(f"Şu iş olmadı: {FAIL_MAIL}.")  # drops the research failure
    record = explain_to_briefing(db, question, now=NOW, chat_provider=provider)
    assert record.briefing.query.kind == QUERY_NARRATIVE
    assert record.briefing.facts["failures_only"] is True
    told = record.briefing.executive[0].text
    assert told.startswith(provider.speech) and FAIL_RESEARCH in told
    assert "tarayıcı" not in told and "Tamamlananlar" not in told
