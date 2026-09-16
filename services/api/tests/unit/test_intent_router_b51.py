"""B51 - routing that knows how sure it is (docs/DECISIONS.md ADR-0158).

The deterministic router is not weakened (row 741); these tests pin what grows around it:
confidence, the clarification question, the flag-off model and its refusals, the candidate
list's safety, the deictic reference, and robustness to the ASR variants a real
transcriber produces.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT, ObjectFocusRow
from app.voice import intents as intents_module
from app.voice.intent_router import (
    MIN_MODEL_CONFIDENCE,
    NEVER_FROM_MODEL,
    AnthropicIntentModel,
    CompositeIntentRouter,
    ScriptedIntentModel,
    clarification_for,
    model_candidates,
    registry_candidates,
    resolve_deictic_reference,
    score_confidence,
)
from app.voice.intents import (
    Intent,
    ResolvedIntent,
    polite_imperative_readings,
    resolve_intent,
    turkish_casefold,
)
from app.voice.route_telemetry import ACTING_INTENTS
from tests.voice_corpus import routing

NOW = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)


def _none(*tokens: str) -> ResolvedIntent:
    return ResolvedIntent(Intent.NONE, tokens=tuple(tokens), confidence=0.0)


# ------------------------------------------------------------------------------ 743


def test_a_rule_route_is_confident_and_nothing_is_not() -> None:
    assert score_confidence(resolve_intent("Gelen kutumda ne var?")) == 0.95
    assert score_confidence(_none("xyz")) == 0.0


def test_one_word_that_acts_on_the_world_is_the_least_confident_rule_route() -> None:
    sent = resolve_intent("Gönder.", draft_pending=True)
    assert sent.intent in ACTING_INTENTS
    assert score_confidence(sent) == 0.7


def test_removed_fillers_cost_a_little() -> None:
    plain = resolve_intent("Gelen kutumda ne var?")
    with_filler = resolve_intent("Şey, gelen kutumda ne var?")
    if with_filler.fillers_removed:
        assert score_confidence(with_filler) == round(score_confidence(plain) - 0.05, 2)


# ------------------------------------------------------------------------------ 744


def test_two_families_ask_which_one() -> None:
    question = clarification_for(_none("mail", "takvim"))
    assert question is not None and "mail" in question and "takvim" in question


def test_one_family_asks_what_to_do_with_it() -> None:
    question = clarification_for(_none("belge"))
    assert question is not None and question.startswith("Belge ile")


def test_nothing_named_asks_nothing_and_a_route_never_asks() -> None:
    assert clarification_for(_none("xyz")) is None
    assert clarification_for(resolve_intent("Gelen kutumda ne var?")) is None


def test_the_clarification_is_spoken_only_under_the_owners_flag() -> None:
    assert CompositeIntentRouter().clarify_aloud is False
    assert CompositeIntentRouter(clarify_aloud=lambda: True).clarify_aloud is True


# ---------------------------------------------------------------------------- 740/742


def test_the_candidate_list_never_offers_an_acting_intent_a_stop_or_eye_disable() -> None:
    candidates = registry_candidates()
    assert candidates, "the registry offers some non-acting tools"
    assert not (set(candidates) & NEVER_FROM_MODEL)
    assert Intent.STOP not in candidates and Intent.EYE_DISABLE not in candidates


def test_a_candidate_needs_a_registered_tool() -> None:
    assert model_candidates(set()) == ()


def _router(answer, *, enabled=True, candidates=None):
    model = ScriptedIntentModel(answer)
    options = candidates if candidates is not None else registry_candidates()
    return model, CompositeIntentRouter(model, enabled=enabled, candidates=lambda: options)


def test_with_the_flag_off_the_model_is_never_asked() -> None:
    model, router = _router({"intent": "news_latest", "confidence": 0.9}, enabled=False)
    routed = router.route("haberlere bir göz at sonra", _none("haberlere", "bir", "göz", "at"))
    assert model.asked == [] and routed.source == "none"


def test_a_rule_route_never_asks_the_model() -> None:
    model, router = _router({"intent": "news_latest", "confidence": 0.9})
    routed = router.route("Gelen kutumda ne var?", resolve_intent("Gelen kutumda ne var?"))
    assert model.asked == [] and routed.source == "rule"


def test_the_model_routes_an_unrouted_sentence_to_a_safe_candidate() -> None:
    safe = next(i for i in registry_candidates())
    model, router = _router({"intent": safe.value, "confidence": 0.9})
    routed = router.route("bir şey", _none("bir", "şey"))
    assert routed.source == "model" and routed.resolved.intent is safe
    assert routed.resolved.matched == "model:scripted" and routed.confidence == 0.9


@pytest.mark.parametrize(
    "answer",
    [
        {"intent": Intent.MAIL_SEND.value, "confidence": 0.99},
        {"intent": Intent.STOP.value, "confidence": 0.99},
        {"intent": Intent.EYE_DISABLE.value, "confidence": 0.99},
        {"intent": "no_such_intent", "confidence": 0.99},
        {"intent": "none", "confidence": 0.99},
        {"confidence": 0.99},
        None,
    ],
)
def test_the_model_is_refused_anything_unsafe_unknown_or_empty(answer) -> None:
    _model, router = _router(answer)
    routed = router.route("mail ile takvim", _none("mail", "takvim"))
    assert routed.source == "none" and routed.resolved.intent is Intent.NONE
    assert routed.clarification is not None


def test_a_low_confidence_choice_is_not_taken() -> None:
    safe = next(i for i in registry_candidates())
    _model, router = _router({"intent": safe.value, "confidence": MIN_MODEL_CONFIDENCE - 0.01})
    assert router.route("bir şey", _none("bir", "şey")).source == "none"


def test_the_anthropic_model_forces_one_tool_over_the_candidates_only() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "tool_use", "input": {"intent": "news_latest", "confidence": 0.8}}
                ]
            },
        )

    model = AnthropicIntentModel(api_key="test-key", transport=httpx.MockTransport(handler))
    answer = model.choose("haberler", [{"intent": "news_latest", "tool": "news.latest"}])
    assert answer == {"intent": "news_latest", "confidence": 0.8}
    body = seen["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "choose_intent"}
    assert body["tools"][0]["input_schema"]["properties"]["intent"]["enum"] == [
        "news_latest",
        "none",
    ]


def test_the_anthropic_model_fails_to_silence() -> None:
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(529, json={})

    assert (
        AnthropicIntentModel(api_key="k", transport=httpx.MockTransport(handler)).choose(
            "x", [{"intent": "news_latest", "tool": "news.latest"}]
        )
        is None
    )
    no_key = AnthropicIntentModel(api_key=None, transport=httpx.MockTransport(handler))
    import os

    saved = {
        k: os.environ.pop(k)
        for k in ("PAGENTOS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        if k in os.environ
    }
    try:
        before = called["n"]
        assert no_key.choose("x", [{"intent": "news_latest", "tool": "news.latest"}]) is None
        assert called["n"] == before, "no key means no request at all"
    finally:
        os.environ.update(saved)


# ------------------------------------------------------------------------------ 745


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ObjectFocusRow.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def test_bunu_points_at_the_most_recent_fresh_focus(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_DOCUMENT, "doc:abc", label="Sözleşme", source="test", now=NOW
    )
    reference = resolve_deictic_reference(db, ("bunu", "özetle"), now=NOW + timedelta(minutes=5))
    assert reference == {"kind": FOCUS_KIND_DOCUMENT, "object_id": "doc:abc", "label": "Sözleşme"}


def test_a_time_is_not_a_reference_and_a_stale_focus_is_not_this(db) -> None:
    focus_module.set_focus(
        db, FOCUS_KIND_DOCUMENT, "doc:abc", label="Sözleşme", source="test", now=NOW
    )
    assert resolve_deictic_reference(db, ("bu", "hafta", "ne", "var"), now=NOW) is None
    assert resolve_deictic_reference(db, ("bunu", "özetle"), now=NOW + timedelta(hours=2)) is None
    assert resolve_deictic_reference(db, ("özetle",), now=NOW) is None


# ------------------------------------------------------------------------------ 746-748


_TURKISH_TO_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def _asr_variants(text: str) -> dict[str, str]:
    """What transcribers and clients really do to an owner's sentence, by name (747):

    * ``no_apostrophe`` - "Chromeu" for "Chrome'u";
    * ``filler`` - a leading "yani";
    * ``dotted_i`` - a dotted i where the dotless one belongs (the loss B51 first pinned);
    * ``ascii`` - every Turkish letter lost, lower case, no punctuation;
    * ``upper`` - a caption-style ALL CAPS transcript, upper-cased the non-Turkish way
      ("i" -> "I", which a Turkish casefold reads back as the dotless "ı");
    * ``nfd`` - the same text delivered decomposed ("s" + U+0327);
    * ``py_lower`` - lower-cased the non-Turkish way ("İ" -> "i" + U+0307).
    """
    base = text.strip()
    bare = base.rstrip(".?!")
    variants = {
        "no_apostrophe": base.replace("'", "").replace("’", ""),
        "filler": "yani " + turkish_casefold(base[:1]) + base[1:] if base else base,
        "dotted_i": base.replace("ı", "i"),
        "ascii": bare.translate(_TURKISH_TO_ASCII).lower(),
        "upper": base.upper(),
        "nfd": unicodedata.normalize("NFD", base),
        "py_lower": bare.lower(),
    }
    return {name: v for name, v in variants.items() if v and v != base}


def test_asr_variants_of_the_routing_set_never_misroute() -> None:
    wrong: list[tuple[str, str, str]] = []
    for case in routing.ROUTING_SET:
        for variant in _asr_variants(case.utterance).values():
            got = resolve_intent(variant, **dict(case.state or {})).intent
            if got in case.forbidden:
                wrong.append((case.utterance, variant, got.value))
    assert wrong == [], wrong


#: The routes a transcriber's noise still loses, by (utterance, variant name). The fold
#: repair pass (``resolve_intent``) and the NFC/combining-dot normalisation closed the six
#: dotted-i losses B51 first pinned and every other variant kind; what is left is only the
#: English-cased ALL-CAPS form of the mail and calendar sentences, because a repair never
#: routes into those two families - the owner deferred them (B45/B46) and their routes stay
#: exactly as they were. A new loss fails this test; so would an entry here that routes
#: again (and ``test_every_known_loss_is_a_deferred_family`` keeps the list honest).
KNOWN_ASR_LOSSES: frozenset[tuple[str, str]] = frozenset(
    {
        ("Maillerime bak.", "upper"),
        ("Mailleri oku.", "upper"),
        ("Ali'ye mail gönder.", "upper"),
        ("Perşembeki toplantıyı iptal et.", "upper"),
        ("Öneriyi oku.", "upper"),
    }
)


def test_every_known_loss_is_a_deferred_family() -> None:
    for utterance, _variant in KNOWN_ASR_LOSSES:
        case = next(c for c in routing.ROUTING_SET if c.utterance == utterance)
        assert case.expected is not None
        assert case.expected.value.startswith(("mail_", "calendar_")), utterance


def test_asr_variants_route_where_their_originals_route() -> None:
    lost: set[tuple[str, str]] = set()
    detail: list[tuple[str, str, str]] = []
    for case in routing.ROUTING_SET:
        if case.expected is None:
            continue
        state = dict(case.state or {})
        if resolve_intent(case.utterance, **state).intent is not case.expected:
            continue
        for name, variant in _asr_variants(case.utterance).items():
            got = resolve_intent(variant, **state).intent
            if got is not case.expected:
                lost.add((case.utterance, name))
                detail.append((case.utterance, variant, got.value))
    assert lost == KNOWN_ASR_LOSSES, detail


def test_the_six_dotted_i_losses_b51_first_pinned_route_again() -> None:
    """Regression: these six lost their route to a dotted i until the fold repair pass."""
    for text, expected in (
        ("Daha hizli.", Intent.FASTER),
        ("Günaydin, bugün ne var?", Intent.MORNING_BRIEFING),
        ("Belgeleri karşilaştir.", Intent.DOCUMENT_COMPARE),
        ("Uygulamayi çaliştir.", Intent.APP_FACTORY_RUN),
        ("Testleri çaliştir.", Intent.APP_FACTORY_TEST),
        ("Görseli dişa aktar.", Intent.CREATIVE_EXPORT),
    ):
        case = next(c for c in routing.ROUTING_SET if c.utterance.replace("ı", "i") == text)
        assert resolve_intent(text, **dict(case.state or {})).intent is expected, text


# ------------------------------------------------------------- 747: normalisation bugs


def test_a_non_turkish_lowercase_i_does_not_split_the_word() -> None:
    """Regression: "İkinci".lower() is "i" + U+0307, and the punctuation strip cut the
    word in two ("i kinci"), so the ordinal - and the route - was lost."""
    text = "İkinci adımı bir daha dene.".lower()
    assert "̇" in text
    assert turkish_casefold(text).split()[0] == "ikinci"
    resolved = resolve_intent(text, executive_run_state="running")
    assert resolved.intent is Intent.EXEC_RETRY
    assert resolved.tokens[0] == "ikinci"


def test_a_decomposed_transcript_is_read_as_its_composed_form() -> None:
    """Regression: an NFD transcript ("s" + U+0327) was cut at every combining mark."""
    text = unicodedata.normalize("NFD", "Görüntüyü kapat.")
    assert text != "Görüntüyü kapat."
    resolved = resolve_intent(text)
    assert resolved.intent is Intent.DISPLAY_OFF
    assert resolved.tokens == ("görüntüyü", "kapat")


def test_all_caps_with_an_english_i_is_not_misread_as_a_bare_stop() -> None:
    """Regression: "SABAH RUTININI DURDUR." casefolded to "rutınını", lost the routine
    noun, and the bare "durdur" made it a STOP - found by the upper-case variant."""
    resolved = resolve_intent("SABAH RUTININI DURDUR.")
    assert resolved.intent is Intent.ROUTINE_PAUSE
    assert resolved.route_repair == "caps_fold"
    # Turkish-cased capitals ("İ" present) are read exactly, as before.
    exact = resolve_intent("SABAH RUTİNİNİ DURDUR.")
    assert exact.intent is Intent.ROUTINE_PAUSE and exact.route_repair is None


def test_a_direct_route_is_never_re_read() -> None:
    """741 stays exactly as it was: a route the words reached directly carries no repair,
    and the fold pass is never consulted for it."""
    resolved = resolve_intent("Ekranları kapat.")
    assert resolved.intent is Intent.DISPLAY_OFF and resolved.route_repair is None


# ------------------------------------------------------------- 746: polite requests


@pytest.mark.parametrize(
    ("text", "state", "expected"),
    [
        ("Kamerayı kapatır mısın?", {}, Intent.EYE_DISABLE),
        ("Kamerayı kapatabilir misin?", {}, Intent.EYE_DISABLE),
        ("Alarmı kapatır mısın?", {"alarm_ringing": True}, Intent.ALARM_STOP),
        ("Alarmı iptal eder misin?", {}, Intent.ALARM_CANCEL),
        ("Bu dosyayı kopyalar mısın?", {}, Intent.DOCUMENT_COPY),
        ("Bu dosyayı taşır mısın Masaüstüne?", {}, Intent.DOCUMENT_MOVE),
        ("gunluk.md adında bir dosya oluşturur musun?", {}, Intent.DOCUMENT_WRITE),
        ("Sunumu da ekler misin?", {"executive_run_state": "running"}, Intent.EXEC_AMEND),
        ("ekranlari kapatir misin", {}, Intent.DISPLAY_OFF),
    ],
)
def test_a_polite_request_routes_as_its_imperative(text, state, expected) -> None:
    resolved = resolve_intent(text, **state)
    assert resolved.intent is expected, text
    # Either the words reached it directly, or the imperative reading did - and then the
    # imperative sentence itself routes there too (its route is the one row 741 pins).
    assert resolved.route_repair in (None, "polite", "polite+ascii_fold"), text
    if resolved.route_repair == "polite":
        assert any(
            resolve_intent(r, **state).intent is expected for r in polite_imperative_readings(text)
        ), text


def test_the_polite_reading_keeps_what_the_owner_said() -> None:
    resolved = resolve_intent("Bu dosyanın sonuna toplantı notu ekler misin?")
    assert resolved.intent is Intent.DOCUMENT_APPEND
    assert resolved.text_to_type == "toplantı notu"
    named = resolve_intent("gunluk.md adında bir dosya oluşturur musun?")
    assert named.new_name == "gunluk.md"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Bunu yapabilir misin?", "a question about what I can do"),
        ("Kahveyi bilir misin?", "knowing is not doing"),
    ],
)
def test_a_question_that_only_looks_polite_is_not_rewritten(text, why) -> None:
    assert polite_imperative_readings(text) == (), why
    resolved = resolve_intent(text)
    assert resolved.route_repair is None, why


def test_a_repair_never_routes_into_the_deferred_mail_and_calendar_families() -> None:
    """B45/B46 are deferred by the owner: a polite "Gönderir misin?" after a read-back is
    not newly turned into a send - the mail family keeps exactly the routes it had."""
    for text, state in (
        ("Gönderir misin?", {"draft_pending": True}),
        ("Onaylar mısın?", {"proposal_pending": True}),
        ("Toplantıyı iptal eder misin?", {"event_focused": True}),
    ):
        # Its imperative IS a mail/calendar route...
        imperative = polite_imperative_readings(text)
        assert any(
            resolve_intent(r, **state).intent.value.startswith(("mail_", "calendar_"))
            for r in imperative
        ), text
        # ...and the polite sentence resolves exactly as the words alone did.
        assert resolve_intent(text, **state) == intents_module._resolve_intent_rules(
            text, **state
        ), text


def test_repaired_readings_that_disagree_route_nowhere(monkeypatch) -> None:
    by_reading = {"a": Intent.DISPLAY_OFF, "b": Intent.EYE_DISABLE}
    monkeypatch.setattr(
        intents_module,
        "_resolve_intent_rules",
        lambda text, **_: ResolvedIntent(by_reading.get(text, Intent.NONE), tokens=(text,)),
    )
    assert intents_module._repair_reading(("a", "b"), {}, folded=False) is None
    agreed = intents_module._repair_reading(("a", "z"), {}, folded=False)
    assert agreed is not None and agreed.intent is Intent.DISPLAY_OFF


def test_the_polite_readings_are_the_imperatives_turkish_allows() -> None:
    assert polite_imperative_readings("Kopyalar mısın?")[:2] == ("kopyal?", "kopyala?")
    assert polite_imperative_readings("Bunu iptal eder misin?")[0] == "Bunu iptal et?"
    assert polite_imperative_readings("Dosyayı taşır mısın?") == ("Dosyayı taşı?",)
    assert polite_imperative_readings("Ekran açık mı?") == ()


def test_a_repaired_route_is_less_confident_than_a_direct_one() -> None:
    direct = resolve_intent("Kamerayı kapat.")
    repaired = resolve_intent("Kamerayı kapatır mısın?")
    assert score_confidence(repaired) == round(score_confidence(direct) - 0.1, 2)


# --------------------------------------------------------------- 746: news source words


@pytest.mark.parametrize(
    "text", ["Haberleri açar mısın?", "Son haber ne zaman yüklendi?", "Bana bir haber aç."]
)
def test_request_words_are_not_read_as_a_news_channel(text) -> None:
    """Regression: "mısın" and "yüklendi" were taken for a channel name, and news.open /
    news.query_latest refused with news_source_not_found instead of using the default."""
    resolved = resolve_intent(text)
    assert resolved.intent in (Intent.NEWS_OPEN, Intent.NEWS_QUERY_LATEST)
    assert resolved.news_source_ref is None


def test_a_named_news_channel_is_still_read() -> None:
    assert resolve_intent("Show'un son haberini açar mısın?").news_source_ref == "show'un"


# ------------------------------------------------------------ 746: matcher paraphrases


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Regression: fell to the bare SUMMARIZE control (a misroute, not a miss).
        ("Bana sabah özetini verir misin?", Intent.MORNING_BRIEFING),
        ("Rutinlerimi söyler misin?", Intent.ROUTINE_LIST),
        ("Rutinlerimi söyle.", Intent.ROUTINE_LIST),
        ("90 saniye sonra bir test alarmı ayarla.", Intent.ALARM_TEST_CREATE),
        ("Yarın 7:30'a alarm ayarla.", Intent.ALARM_CREATE),
    ],
)
def test_the_paraphrases_the_rules_now_know(text, expected) -> None:
    assert resolve_intent(text).intent is expected, text


@pytest.mark.parametrize(
    ("text", "state", "expected"),
    [
        # The registry intents with no corpus template case (B39-B42 families, memory
        # correction): one realistic paraphrase each, routed by the rules alone.
        ("Uygulamayı doğrular mısın?", {"app_project_focused": True}, Intent.APP_FACTORY_VERIFY),
        (
            "Uygulamanın günlüğünü gösterir misin?",
            {"app_project_focused": True},
            Intent.APP_FACTORY_LOG,
        ),
        ("Uygulamayı paketler misin?", {"app_project_focused": True}, Intent.APP_FACTORY_PACKAGE),
        (
            "Paketlenmiş sürümü başlatır mısın?",
            {"app_project_focused": True},
            Intent.APP_FACTORY_LAUNCH,
        ),
        ("Bu uygulamada ne yaptık?", {"app_project_focused": True}, Intent.APP_FACTORY_HISTORY),
        (
            "Kitaplık uygulamasından devam edelim.",
            {"app_project_focused": True},
            Intent.APP_FACTORY_RESUME,
        ),
        (
            "Bu uygulamaya siparişlere teslim tarihi ekler misin?",
            {"app_project_focused": True},
            Intent.APP_FACTORY_MODIFY,
        ),
        ("Bu hatayı düzelt.", {"app_project_focused": True}, Intent.APP_FACTORY_FIX),
        (
            "Bu belgeye Riskler bölümünü ekler misin?",
            {"artifact_focused": True},
            Intent.ARTIFACT_EDIT,
        ),
        ("Bunu kopyalar mısın?", {"artifact_focused": True}, Intent.ARTIFACT_CLONE),
        ("Önceki sürümle karşılaştır.", {"artifact_focused": True}, Intent.ARTIFACT_COMPARE),
        ("Bunu siler misin?", {"artifact_focused": True}, Intent.ARTIFACT_DELETE),
        ("Bunu düzeltsene.", {}, Intent.MEMORY_CORRECT),
        ("Chrome'u aç, sonra YouTube'a gir.", {}, Intent.MISSION_START),
        ("Tamam, başla.", {"mission_state": "awaiting_approval"}, Intent.MISSION_APPROVE),
        ("Bir bekle.", {"mission_state": "running"}, Intent.MISSION_PAUSE),
        ("Kaldığın yerden devam et.", {"mission_state": "paused"}, Intent.MISSION_RESUME),
        ("Kopyaları çöpe gönder.", {}, Intent.DOCUMENT_DEDUP),
    ],
)
def test_every_non_deferred_registry_intent_has_a_routed_paraphrase(text, state, expected):
    assert resolve_intent(text, **state).intent is expected, text


def test_duplicate_files_to_the_bin_is_the_cleanup_not_a_delete_of_the_current_file() -> None:
    """Regression: "Kopya dosyaları çöp kutusuna gönder." proposed document.delete on the
    focused file (the B34 mutation block runs before B32's dedup)."""
    for text in ("Kopya dosyaları çöp kutusuna gönder.", "Kopya dosyaları çöp kutusuna at."):
        assert resolve_intent(text).intent is Intent.DOCUMENT_DEDUP, text
    assert resolve_intent("Bu dosyayı çöp kutusuna gönder.").intent is Intent.DOCUMENT_DELETE


def test_ayarla_beside_the_wake_song_sets_no_alarm() -> None:
    assert resolve_intent("Alarm müziğimi ayarla.").intent not in (
        Intent.ALARM_CREATE,
        Intent.ALARM_TEST_CREATE,
    )
