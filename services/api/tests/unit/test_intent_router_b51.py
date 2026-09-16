"""B51 - routing that knows how sure it is (docs/DECISIONS.md ADR-0158).

The deterministic router is not weakened (row 741); these tests pin what grows around it:
confidence, the clarification question, the flag-off model and its refusals, the candidate
list's safety, the deictic reference, and robustness to the ASR variants a real
transcriber produces.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_DOCUMENT, ObjectFocusRow
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
from app.voice.intents import Intent, ResolvedIntent, resolve_intent, turkish_casefold
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


def _asr_variants(text: str) -> list[str]:
    """What a transcriber really does to an owner's sentence: no apostrophes, a leading
    filler, and a dotted i where the dotless one belongs."""
    base = text.strip()
    variants = {
        base.replace("'", "").replace("’", ""),
        "yani " + turkish_casefold(base[:1]) + base[1:] if base else base,
        base.replace("ı", "i"),
    }
    variants.discard(base)
    return sorted(v for v in variants if v)


def test_asr_variants_of_the_routing_set_never_misroute() -> None:
    wrong: list[tuple[str, str, str]] = []
    for case in routing.ROUTING_SET:
        for variant in _asr_variants(case.utterance):
            got = resolve_intent(variant, **dict(case.state or {})).intent
            if got in case.forbidden:
                wrong.append((case.utterance, variant, got.value))
    assert wrong == [], wrong


#: The routes a transcriber's dotted i still loses (747 PARTIAL). ``turkish_casefold`` keeps
#: "ı" and "i" apart on purpose ("ISI" is not "isi"), and folding them would change the
#: deterministic router row 741 protects - so the gap is named here instead. A new loss fails
#: this test; a fixed one fails it too, until it is taken off the list.
KNOWN_DOTTED_I_LOSSES = frozenset(
    {
        "Daha hızlı.",
        "Günaydın, bugün ne var?",
        "Belgeleri karşılaştır.",
        "Uygulamayı çalıştır.",
        "Testleri çalıştır.",
        "Görseli dışa aktar.",
    }
)


def test_asr_variants_route_where_their_originals_route() -> None:
    lost: list[tuple[str, str, str]] = []
    dotted_i_lost: set[str] = set()
    for case in routing.ROUTING_SET:
        if case.expected is None:
            continue
        state = dict(case.state or {})
        if resolve_intent(case.utterance, **state).intent is not case.expected:
            continue
        for variant in _asr_variants(case.utterance):
            got = resolve_intent(variant, **state).intent
            if got is case.expected:
                continue
            if variant == case.utterance.replace("ı", "i"):
                dotted_i_lost.add(case.utterance)
            else:
                lost.append((case.utterance, variant, got.value))
    assert lost == [], lost
    assert dotted_i_lost == KNOWN_DOTTED_I_LOSSES, sorted(dotted_i_lost ^ KNOWN_DOTTED_I_LOSSES)
