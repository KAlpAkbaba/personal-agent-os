"""Unit tests for the M13 candidate-evidence eligibility gate.

Every scenario here reproduces a REAL shape from the 2026-09-04 owner
research run that fetched and ranked 12 evidence items and produced ZERO
findings: a Turkish Cloudflare-style interstitial title, an English "Just a
moment..." page, a Hugging Face Granite 4.2 page published outside the
requested window, a Turkish AI article, an off-topic "Catalan's constant is
irrational" arXiv paper, an off-topic cosmology halo-profile abstract, and a
cookie-consent-only page. Plus a genuinely on-topic in-window item, a
near-duplicate Turkish/English pair covering the same launch, and an item
with no date at all (which must never be silently treated as in-window).
"""

from __future__ import annotations

from app.research.eligibility import (
    REJECTION_REASONS,
    EligibilityVerdict,
    classify_page_validity,
    duplicate_event_key,
    evaluate_candidate,
    is_near_duplicate,
    publication_date_confidence,
    recency_verdict,
    topic_relevance,
)

TOPIC = "yapay zeka ajanlarıyla ilgili son gelişmeler"
WINDOW_START = "2026-09-01T00:00:00Z"
WINDOW_END = "2026-09-04T23:59:59Z"


# ---------------------------------------------------------------------------
# classify_page_validity
# ---------------------------------------------------------------------------


def test_turkish_cloudflare_interstitial_is_classified_as_interstitial() -> None:
    verdict = classify_page_validity(
        title="Bir dakika lütfen...",
        excerpt=(
            "Bir dakika lütfen... www.example.com sitesinin tarayıcınızın güvenliğini doğruluyoruz."
        ),
    )
    assert verdict == "interstitial"


def test_english_just_a_moment_interstitial_is_classified_as_interstitial() -> None:
    verdict = classify_page_validity(
        title="Just a moment...",
        excerpt="Just a moment...Enable JavaScript and cookies to continue",
    )
    assert verdict == "interstitial"


def test_interstitial_detection_is_case_and_diacritic_insensitive() -> None:
    """Same phrase, all-caps and without lowercase diacritics -- must still match."""
    verdict = classify_page_validity(
        title="BİR DAKİKA LÜTFEN",
        excerpt="DOĞRULANIYOR, sitenin güvenliğini kontrol ediyoruz.",
    )
    assert verdict == "interstitial"


def test_long_article_merely_mentioning_the_phrase_is_not_misclassified() -> None:
    """A real, long article that happens to open with the words "just a moment"
    must be scored on its substance, not flagged as a challenge page -- this is
    exactly the false-positive the short-page + title-weighting rule guards
    against.
    """
    long_excerpt = (
        "This in-depth feature explores how the new agent framework works. " * 10
    ) + " Just a moment before we dive deeper into the architecture, let's review the basics."
    verdict = classify_page_validity(
        title="Deep Dive: New Agent Framework",
        excerpt=long_excerpt,
    )
    assert verdict == "normal_content"


def test_cookie_consent_only_page_is_classified_as_consent() -> None:
    verdict = classify_page_validity(
        title="Çerez Politikası",
        excerpt=(
            "Bu web sitesini kullanarak çerez politikamızı kabul etmiş "
            "olursunuz. Devam etmeden önce çerez tercihlerinizi "
            "yönetebilirsiniz."
        ),
    )
    assert verdict == "consent"


def test_captcha_page_is_classified_as_captcha() -> None:
    verdict = classify_page_validity(
        title="Are you a robot?",
        excerpt="Our systems have detected unusual traffic from your computer network.",
    )
    assert verdict == "captcha"


def test_login_wall_is_classified_as_login_required() -> None:
    verdict = classify_page_validity(
        title="Sign in to continue",
        excerpt="Please sign in to continue reading this article.",
    )
    assert verdict == "login_required"


def test_403_status_is_classified_as_access_denied() -> None:
    verdict = classify_page_validity(
        title="Forbidden",
        excerpt="You do not have permission to access this resource on this server.",
        http_status=403,
    )
    assert verdict == "access_denied"


def test_subscribers_only_paywall_text_is_access_denied_without_a_status_code() -> None:
    verdict = classify_page_validity(
        title="Premium Article",
        excerpt=(
            "This article is for subscribers only. Please subscribe to "
            "continue reading this in-depth piece about the industry."
        ),
    )
    assert verdict == "access_denied"


def test_short_low_content_page_is_classified_as_empty() -> None:
    verdict = classify_page_validity(title="Page", excerpt="Loading...")
    assert verdict == "empty"


def test_non_string_fields_are_malformed() -> None:
    assert classify_page_validity(title=None, excerpt="hello") == "malformed"  # type: ignore[arg-type]


def test_markup_only_excerpt_is_malformed() -> None:
    verdict = classify_page_validity(title="Untitled", excerpt="<html><body></body></html>")
    assert verdict == "malformed"


# ---------------------------------------------------------------------------
# topic_relevance
# ---------------------------------------------------------------------------


def test_ai_agent_news_item_scores_high_relevance() -> None:
    score = topic_relevance(
        topic=TOPIC,
        title="OpenAI Launches New AI Agent Builder Tool",
        excerpt=(
            "The new tool lets autonomous agents call external tools "
            "automatically, an agentic workflow that pairs an AI agent with "
            "a copilot-style assistant. Developers can use tool use and MCP "
            "(Model Context Protocol) to connect LLM agents to real-world "
            "tools such as calendars and browsers."
        ),
    )
    assert score >= 0.5


def test_turkish_ai_agent_article_scores_high_relevance() -> None:
    score = topic_relevance(
        topic=TOPIC,
        title="Yapay Zeka Ajanları Dünyasında Yeni Bir Adım",
        excerpt=(
            "Şirket, yapay zeka ajanlarının görev tamamlama oranını artıran "
            "yeni bir çerçeve duyurdu. Ajanlar artık araç kullanımı ve MCP "
            "entegrasyonları sayesinde daha karmaşık işleri otonom şekilde "
            "yürütebiliyor."
        ),
    )
    assert score >= 0.5


def test_catalans_constant_arxiv_paper_scores_near_zero() -> None:
    score = topic_relevance(
        topic=TOPIC,
        title="Catalan's constant is irrational",
        excerpt=(
            "We prove that Catalan's constant, defined as an alternating "
            "sum of reciprocals of odd squares, is irrational, extending "
            "classical techniques from Diophantine approximation theory "
            "and hypergeometric series."
        ),
    )
    assert score < 0.2


def test_cosmology_halo_profile_paper_scores_near_zero() -> None:
    score = topic_relevance(
        topic=TOPIC,
        title="A New Analytic Halo Density Profile for Cold Dark Matter",
        excerpt=(
            "We present a new analytic fitting formula for the density "
            "profile of dark matter halos calibrated against N-body "
            "simulations across a wide range of halo masses and "
            "cosmological parameters."
        ),
    )
    assert score < 0.2


def test_relevance_score_is_bounded_and_deterministic() -> None:
    kwargs = dict(
        topic=TOPIC,
        title="Granite 4.2 Release Notes: Agentic AI Model Family",
        excerpt=(
            "Granite 4.2 is IBM's latest language model release, tuned for "
            "agentic workflows, tool use, and AI agent orchestration."
        ),
    )
    first = topic_relevance(**kwargs)
    second = topic_relevance(**kwargs)
    assert first == second
    assert 0.0 <= first <= 1.0


# ---------------------------------------------------------------------------
# publication_date_confidence
# ---------------------------------------------------------------------------


def test_explicit_iso_datetime_is_high_confidence() -> None:
    confidence, date = publication_date_confidence(
        published_at="2026-08-25T10:00:00Z",
        published_hint=None,
        retrieved_at="2026-09-04T09:00:00Z",
    )
    assert confidence == "high"
    assert date == "2026-08-25"


def test_turkish_relative_hint_is_medium_confidence() -> None:
    confidence, date = publication_date_confidence(
        published_at=None,
        published_hint="3 gün önce",
        retrieved_at="2026-09-04T09:00:00Z",
    )
    assert confidence == "medium"
    # A relative hint alone doesn't carry an absolute date.
    assert date is None


def test_english_relative_hint_is_medium_confidence() -> None:
    confidence, date = publication_date_confidence(
        published_at=None,
        published_hint="3 days ago",
        retrieved_at="2026-09-04T09:00:00Z",
    )
    assert confidence == "medium"


def test_year_only_date_is_low_confidence() -> None:
    confidence, date = publication_date_confidence(
        published_at="2026",
        published_hint=None,
        retrieved_at=None,
    )
    assert confidence == "low"
    assert date == "2026"


def test_agreeing_body_dates_are_medium_confidence() -> None:
    confidence, date = publication_date_confidence(
        published_at=None,
        published_hint=None,
        retrieved_at=None,
        body_dates=("2026-09-01", "2026-09-01"),
    )
    assert confidence == "medium"
    assert date == "2026-09-01"


def test_disagreeing_body_dates_are_low_confidence_with_no_date() -> None:
    confidence, date = publication_date_confidence(
        published_at=None,
        published_hint=None,
        retrieved_at=None,
        body_dates=("2026-09-01", "2026-08-20"),
    )
    assert confidence == "low"
    assert date is None


def test_nothing_but_retrieval_time_is_no_confidence() -> None:
    """The core incident bug: a page fetched today with no real publication
    signal must NEVER report a date or non-"none" confidence just because it
    was retrieved recently.
    """
    confidence, date = publication_date_confidence(
        published_at=None,
        published_hint=None,
        retrieved_at="2026-09-04T09:00:00Z",
    )
    assert confidence == "none"
    assert date is None


def test_retrieved_at_is_never_returned_as_the_publication_date() -> None:
    _, date = publication_date_confidence(
        published_at=None,
        published_hint=None,
        retrieved_at="2026-09-04T09:00:00Z",
        body_dates=(),
    )
    assert date != "2026-09-04"
    assert date is None


# ---------------------------------------------------------------------------
# recency_verdict
# ---------------------------------------------------------------------------


def test_granite_page_five_days_before_window_is_outside_recency_window() -> None:
    verdict = recency_verdict(
        published_at="2026-08-25",
        event_date=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="high",
    )
    assert verdict == "outside_recency_window"


def test_turkish_article_two_days_before_window_is_outside_recency_window() -> None:
    verdict = recency_verdict(
        published_at="2026-08-30",
        event_date=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="high",
    )
    assert verdict == "outside_recency_window"


def test_date_inside_window_is_in_window() -> None:
    verdict = recency_verdict(
        published_at="2026-09-03T10:00:00Z",
        event_date=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="high",
    )
    assert verdict == "in_window"


def test_no_confidence_is_always_date_uncertain_never_in_window() -> None:
    verdict = recency_verdict(
        published_at=None,
        event_date=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="none",
    )
    assert verdict == "date_uncertain"
    assert verdict != "in_window"


def test_missing_date_with_high_confidence_label_is_still_date_uncertain() -> None:
    """Confidence is graded independently of whether a date is even present;
    a caller mismatch (confidence says "high" but no date was actually
    supplied) must not fall through to "in_window" by accident.
    """
    verdict = recency_verdict(
        published_at=None,
        event_date=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="high",
    )
    assert verdict == "date_uncertain"


def test_unparseable_window_bounds_yield_date_uncertain() -> None:
    verdict = recency_verdict(
        published_at="2026-09-03",
        event_date=None,
        window_start="not-a-date",
        window_end="2026-09-04",
        confidence="high",
    )
    assert verdict == "date_uncertain"


def test_event_date_inside_window_counts_even_if_published_at_does_not() -> None:
    verdict = recency_verdict(
        published_at="2026-08-01",
        event_date="2026-09-02",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        confidence="high",
    )
    assert verdict == "in_window"


# ---------------------------------------------------------------------------
# duplicate_event_key / is_near_duplicate
# ---------------------------------------------------------------------------


def test_turkish_and_english_coverage_of_same_launch_are_near_duplicates() -> None:
    key_en = duplicate_event_key(
        title="OpenAI Launches Agent Builder",
        url="https://techcrunch.com/2026/09/03/openai-agent-builder",
        publisher="TechCrunch",
    )
    key_tr = duplicate_event_key(
        title="OpenAI, Agent Builder'ı Duyurdu",
        url="https://milliyet.com.tr/teknoloji/openai-agent-builder",
        publisher="Milliyet Teknoloji",
    )
    assert is_near_duplicate(key_en, key_tr)


def test_genuinely_different_stories_are_not_near_duplicates() -> None:
    key_agent = duplicate_event_key(
        title="OpenAI Launches Agent Builder",
        url="https://techcrunch.com/a",
        publisher="TechCrunch",
    )
    key_catalan = duplicate_event_key(
        title="Catalan's constant is irrational",
        url="https://arxiv.org/abs/x",
        publisher="arXiv",
    )
    assert not is_near_duplicate(key_agent, key_catalan)


def test_duplicate_event_key_is_deterministic() -> None:
    kwargs = dict(
        title="OpenAI Launches Agent Builder",
        url="https://techcrunch.com/a",
        publisher="TechCrunch",
    )
    assert duplicate_event_key(**kwargs) == duplicate_event_key(**kwargs)


def test_identical_key_is_always_a_near_duplicate() -> None:
    key = duplicate_event_key(title="Same Title Here", url="https://example.com/a")
    assert is_near_duplicate(key, key)


# ---------------------------------------------------------------------------
# REJECTION_REASONS / EligibilityVerdict
# ---------------------------------------------------------------------------


def test_rejection_reasons_vocabulary_is_exact() -> None:
    assert REJECTION_REASONS == frozenset(
        {
            "off_topic",
            "outside_recency_window",
            "date_uncertain",
            "interstitial",
            "duplicate_event",
            "insufficient_content",
        }
    )


def test_eligibility_verdict_as_dict_round_trips_fields() -> None:
    verdict = EligibilityVerdict(
        eligible=False,
        reason="off_topic",
        topic_relevance=0.05,
        recency="date_uncertain",
        page_validity="normal_content",
        publication_date=None,
        date_confidence="none",
    )
    assert verdict.as_dict() == {
        "eligible": False,
        "reason": "off_topic",
        "topic_relevance": 0.05,
        "recency": "date_uncertain",
        "page_validity": "normal_content",
        "publication_date": None,
        "date_confidence": "none",
    }


# ---------------------------------------------------------------------------
# evaluate_candidate (full pipeline, precedence)
# ---------------------------------------------------------------------------


def test_turkish_interstitial_is_rejected_as_interstitial_not_off_topic() -> None:
    """Precedence check: an interstitial's boilerplate text shares almost no
    words with the topic, but the reason reported must be "interstitial",
    not a misleading "off_topic".
    """
    verdict = evaluate_candidate(
        title="Bir dakika lütfen...",
        excerpt=(
            "Bir dakika lütfen... www.example.com sitesinin tarayıcınızın güvenliğini doğruluyoruz."
        ),
        topic=TOPIC,
    )
    assert verdict.eligible is False
    assert verdict.reason == "interstitial"
    assert verdict.page_validity == "interstitial"


def test_cookie_consent_page_is_rejected_as_interstitial() -> None:
    verdict = evaluate_candidate(
        title="Çerez Politikası",
        excerpt=(
            "Bu web sitesini kullanarak çerez politikamızı kabul etmiş "
            "olursunuz. Devam etmeden önce çerez tercihlerinizi "
            "yönetebilirsiniz."
        ),
        topic=TOPIC,
    )
    assert verdict.eligible is False
    assert verdict.reason == "interstitial"
    assert verdict.page_validity == "consent"


def test_granite_page_outside_window_is_rejected_for_recency_not_topic() -> None:
    """The Hugging Face Granite 4.2 page from the real incident: genuinely
    on-topic content, published five days before the requested window.
    """
    verdict = evaluate_candidate(
        title="Granite 4.2 Release Notes: Agentic AI Model Family",
        excerpt=(
            "Granite 4.2 is IBM's latest language model release, tuned for "
            "agentic workflows, tool use, and AI agent orchestration. The "
            "model supports MCP integrations and can act as an autonomous "
            "agent in enterprise copilot deployments."
        ),
        topic=TOPIC,
        published_at="2026-08-25",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert verdict.eligible is False
    assert verdict.reason == "outside_recency_window"
    assert verdict.topic_relevance >= 0.35
    assert verdict.page_validity == "normal_content"


def test_catalans_constant_is_rejected_as_off_topic_even_when_in_window() -> None:
    verdict = evaluate_candidate(
        title="Catalan's constant is irrational",
        excerpt=(
            "We prove that Catalan's constant, defined as an alternating "
            "sum of reciprocals of odd squares, is irrational, extending "
            "classical techniques from Diophantine approximation theory "
            "and hypergeometric series."
        ),
        topic=TOPIC,
        published_at="2026-09-02",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert verdict.eligible is False
    assert verdict.reason == "off_topic"


def test_on_topic_in_window_item_is_eligible() -> None:
    verdict = evaluate_candidate(
        title="OpenAI Launches New AI Agent Builder Tool",
        excerpt=(
            "The new tool lets autonomous agents call external tools "
            "automatically, an agentic workflow that pairs an AI agent "
            "with a copilot-style assistant. Developers can use tool use "
            "and MCP (Model Context Protocol) to connect LLM agents to "
            "real-world tools such as calendars and browsers."
        ),
        topic=TOPIC,
        published_at="2026-09-03T10:00:00Z",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert verdict.eligible is True
    assert verdict.reason is None
    assert verdict.recency == "in_window"
    assert verdict.date_confidence == "high"


def test_item_with_no_date_at_all_is_date_uncertain_never_in_window() -> None:
    """The exact defect from the incident: a candidate that is otherwise a
    perfectly good, on-topic match but carries no trustworthy date must be
    rejected as "date_uncertain", never silently accepted.
    """
    verdict = evaluate_candidate(
        title="OpenAI Launches New AI Agent Builder Tool",
        excerpt=(
            "The new tool lets autonomous agents call external tools "
            "automatically, an agentic workflow that pairs an AI agent "
            "with a copilot-style assistant. Developers can use tool use "
            "and MCP (Model Context Protocol) to connect LLM agents to "
            "real-world tools such as calendars and browsers."
        ),
        topic=TOPIC,
        published_at=None,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert verdict.eligible is False
    assert verdict.reason == "date_uncertain"
    assert verdict.recency == "date_uncertain"
    assert verdict.recency != "in_window"


def test_near_duplicate_coverage_is_rejected_as_duplicate_event() -> None:
    primary_key = duplicate_event_key(
        title="OpenAI Launches Agent Builder",
        url="https://techcrunch.com/a",
        publisher="TechCrunch",
    )
    verdict = evaluate_candidate(
        title="OpenAI, Agent Builder'ı Duyurdu",
        excerpt=(
            "The new tool lets autonomous agents call external tools "
            "automatically, an agentic workflow that pairs an AI agent "
            "with a copilot-style assistant. Developers can use tool use "
            "and MCP (Model Context Protocol) to connect LLM agents to "
            "real-world tools such as calendars and browsers."
        ),
        topic=TOPIC,
        publisher="Milliyet Teknoloji",
        published_at="2026-09-03T10:00:00Z",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        existing_event_keys=(primary_key,),
    )
    assert verdict.eligible is False
    assert verdict.reason == "duplicate_event"


def test_evaluate_candidate_reason_is_always_from_rejection_vocabulary() -> None:
    verdict = evaluate_candidate(
        title="Bir dakika lütfen...",
        excerpt="Bir dakika lütfen... doğrulanıyor.",
        topic=TOPIC,
    )
    assert verdict.reason in REJECTION_REASONS
