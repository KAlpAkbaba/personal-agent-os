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


# ---------------------------------------------------------------------------
# 2026-09-19 incident: cross-language topic relevance + chrome-diluted scoring
#
# Production run 817c558a, topic "Yapay zeka ile ilgili son haberler" ("news about
# AI", not the AI-agent-specific TOPIC used above): an English article about AI
# coding tools scored topic_relevance 0.0 and a second one 0.2167, both rejected
# off_topic, although both were plainly about the topic. Root causes fixed here:
# (1) the topic's own Turkish words never matched an English page at all — no
# cross-language signal existed; (2) "haberler" (news) had no Turkish stopword
# entry even though its English counterpart did, quietly counting a filler word
# as topic-specific and diluting the real signal; (3) a page's own nav-bar/byline
# chrome was scored right alongside its real content.
# ---------------------------------------------------------------------------

PRODUCTION_TOPIC = "Yapay zeka ile ilgili son haberler"


def test_english_ai_coding_article_clears_the_relevance_floor_for_a_turkish_ai_topic() -> None:
    """The production incident's own example: an English article that is plainly
    about AI (not agents specifically) must clear MIN_TOPIC_RELEVANCE against the
    Turkish topic "Yapay zeka ile ilgili son haberler" -- cross-language token
    overlap (via the same tr->en term map discovery already uses) is what makes
    the owner's own topic words findable in an English-only page.
    """
    score = topic_relevance(
        topic=PRODUCTION_TOPIC,
        title="AI coding tools are changing how indie game studios ship faster",
        excerpt=(
            "AI coding tools are reshaping how indie developers ship games faster "
            "than ever, according to several studio founders interviewed this week. "
            "These artificial intelligence powered assistants let a developer "
            "describe a feature in plain English while the tool writes and iterates "
            "on the implementation, cutting weeks of engineering time down to days. "
            "Several studios said the approach works best for prototyping systems "
            "that would otherwise take months to hand code, though founders "
            "cautioned that AI generated code still needs careful review before "
            "shipping."
        ),
    )
    assert score >= 0.35


def test_unrelated_english_article_still_scores_off_topic_for_the_same_turkish_topic() -> None:
    """The fix above must not turn every English page on-topic: an article with
    no AI content at all stays below the floor."""
    score = topic_relevance(
        topic=PRODUCTION_TOPIC,
        title="Best Budget Gaming Mice of 2026",
        excerpt=(
            "We tested a dozen budget gaming mice this month to find which ones "
            "offer the best value for competitive play. Sensor accuracy, click "
            "latency and build quality were our top priorities during testing. The "
            "winner combines a lightweight shell with a reliable optical sensor at "
            "a price point most players can afford."
        ),
    )
    assert score < 0.35


def test_evaluate_candidate_admits_the_english_ai_article_end_to_end() -> None:
    """The same page through the full gate (page validity + recency + the fix
    above), not just the scoring function in isolation."""
    verdict = evaluate_candidate(
        title="AI coding tools are changing how indie game studios ship faster",
        excerpt=(
            "AI coding tools are reshaping how indie developers ship games faster "
            "than ever, according to several studio founders interviewed this week. "
            "These artificial intelligence powered assistants let a developer "
            "describe a feature in plain English while the tool writes and iterates "
            "on the implementation, cutting weeks of engineering time down to days. "
            "Several studios said the approach works best for prototyping systems "
            "that would otherwise take months to hand code, though founders "
            "cautioned that AI generated code still needs careful review before "
            "shipping."
        ),
        topic=PRODUCTION_TOPIC,
        published_at="2026-09-17T10:00:00Z",
        window_start="2026-09-15T00:00:00Z",
        window_end="2026-09-18T23:59:59Z",
    )
    assert verdict.eligible is True
    assert verdict.reason is None


def test_topic_relevance_scores_page_content_not_its_own_nav_bar() -> None:
    """A page's nav bar/breadcrumb chrome must not itself decide relevance: a nav
    bar that happens to mention "Artificial Intelligence"/"AI" as a site category
    must not make an unrelated article (about gaming mice, not AI) score higher
    than the same article without that chrome — topic_relevance scores
    :func:`app.research.evidence.content_text`, not the raw excerpt."""
    real_content = (
        "We tested a dozen budget gaming mice this month to find which ones offer "
        "the best value for competitive play. Sensor accuracy, click latency and "
        "build quality were our top priorities during testing. The winner combines "
        "a lightweight shell with a reliable optical sensor at a price point most "
        "players can afford."
    )
    plain_score = topic_relevance(
        topic=PRODUCTION_TOPIC, title="Best Budget Gaming Mice of 2026", excerpt=real_content
    )
    nav_polluted_score = topic_relevance(
        topic=PRODUCTION_TOPIC,
        title="Best Budget Gaming Mice of 2026",
        excerpt="Home | Artificial Intelligence | AI | Reviews | Guides | Deals\n" + real_content,
    )
    assert nav_polluted_score == plain_score
