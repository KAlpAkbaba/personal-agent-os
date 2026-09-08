"""Latest News Mode's intents, pinned through ``resolve_intent`` — the ONE router
(docs/M27_LATEST_NEWS_MODE_SPEC.md §6). The class (query/action) and the capability are
pinned too, the same discipline ``tests/unit/test_alarms_voice.py`` established for
its own M18.3 family.
"""

from __future__ import annotations

import pytest

from app.voice.intents import (
    CAPABILITY_BY_INTENT,
    KLASS_ACTION,
    KLASS_QUERY,
    QUERY_TOOL_BY_INTENT,
    Intent,
    klass_for,
    resolve_intent,
)
from app.voice.realtime_sessions.tools import default_registry


@pytest.mark.parametrize(
    ("utterance", "intent"),
    [
        # Task brief §6, verbatim.
        ("Haberleri aç.", Intent.NEWS_OPEN),
        ("Son haberleri aç.", Intent.NEWS_OPEN),
        ("Show Haber'i aç.", Intent.NEWS_OPEN),
        ("Show'un son haberini aç.", Intent.NEWS_OPEN),
        ("Bugünün Show Ana Haber videosunu aç.", Intent.NEWS_OPEN),
        ("En son yüklenen ana haberi aç.", Intent.NEWS_OPEN),
        ("Haberleri YouTube'dan aç.", Intent.NEWS_OPEN),
        ("Haberleri özetle.", Intent.NEWS_SUMMARIZE),
        ("Haberleri anlat.", Intent.NEWS_SUMMARIZE),
        ("Bugünkü haberleri özetle.", Intent.NEWS_SUMMARIZE),
        ("Son haber ne zaman yüklenmiş?", Intent.NEWS_QUERY_LATEST),
        ("Şu an hangi haber videosunu açacaksın?", Intent.NEWS_QUERY_LATEST),
    ],
)
def test_every_owner_phrase_resolves_to_its_intent(utterance: str, intent: Intent) -> None:
    assert resolve_intent(utterance).intent is intent


@pytest.mark.parametrize(
    ("utterance", "expected_ref"),
    [
        ("Haberleri aç.", None),
        ("Son haberleri aç.", None),
        ("En son yüklenen ana haberi aç.", None),
        ("Show Haber'i aç.", "show"),
        ("Show'un son haberini aç.", "show'un"),
        ("Bugünün Show Ana Haber videosunu aç.", "show"),
    ],
)
def test_the_channel_name_hint_is_extracted_only_when_actually_named(
    utterance: str, expected_ref: str | None
) -> None:
    assert resolve_intent(utterance).news_source_ref == expected_ref


def test_asr_noise_variants_still_resolve() -> None:
    for utterance in ("haberlerı aç", "haberleri ac", "haberleri aç"):
        assert resolve_intent(utterance).intent is Intent.NEWS_OPEN


class TestNegativeAssertions:
    """Task brief §6: "Haberleri aç" must not start a generic research run only, must
    not play the alarm's wake music, and must not open a random search result — which,
    at the router level, means it must resolve to NEWS_OPEN and nothing else at all."""

    def test_haberleri_ac_is_never_display_wake_or_eye_enable(self) -> None:
        resolved = resolve_intent("Haberleri aç.")
        assert resolved.intent not in (Intent.DISPLAY_WAKE, Intent.EYE_ENABLE, Intent.APP_OPEN)
        assert resolved.intent is Intent.NEWS_OPEN

    def test_haberleri_ozetle_is_never_the_generic_summarize_control(self) -> None:
        resolved = resolve_intent("Haberleri özetle.")
        assert resolved.intent is not Intent.SUMMARIZE
        assert resolved.intent is Intent.NEWS_SUMMARIZE

    def test_haberleri_ozetle_is_never_document_summarize(self) -> None:
        resolved = resolve_intent("Haberleri özetle.")
        assert resolved.intent is not Intent.DOCUMENT_SUMMARIZE

    def test_a_bare_ac_with_no_haber_noun_is_untouched(self) -> None:
        """The news branch requires the "haber" noun - a bare display/eye/app "aç"
        must still resolve to ITS OWN family, never NEWS_OPEN."""
        assert resolve_intent("Ekranları aç.").intent is Intent.DISPLAY_WAKE
        assert resolve_intent("Gözünü aç.").intent is Intent.EYE_ENABLE


@pytest.mark.parametrize("intent", [Intent.NEWS_OPEN, Intent.NEWS_SUMMARIZE])
def test_every_mutating_news_intent_is_an_action_with_a_registered_capability(
    intent: Intent,
) -> None:
    """docs/M18_ACTION_CONTRACT.md §2: an ACTION targets a canonical capability and
    ends in a receipt — a real mutation (a browser opens/plays; a research task is
    created)."""
    assert klass_for(intent) == KLASS_ACTION
    assert intent in CAPABILITY_BY_INTENT
    assert CAPABILITY_BY_INTENT[intent] in set(default_registry().names())


def test_news_query_latest_is_a_query_and_mutates_nothing() -> None:
    assert klass_for(Intent.NEWS_QUERY_LATEST) == KLASS_QUERY
    assert Intent.NEWS_QUERY_LATEST not in CAPABILITY_BY_INTENT
    assert Intent.NEWS_QUERY_LATEST in QUERY_TOOL_BY_INTENT
    assert QUERY_TOOL_BY_INTENT[Intent.NEWS_QUERY_LATEST] in set(default_registry().names())


def test_the_resolved_intent_carries_the_capability_for_the_client() -> None:
    resolved = resolve_intent("Haberleri aç.")
    as_dict = resolved.to_dict()
    assert as_dict["capability"] == "news.open"
    assert as_dict["klass"] == KLASS_ACTION
    assert as_dict["news_source_ref"] is None
