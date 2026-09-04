"""Regression fixtures taken from the real Research run of 2026-09-04.

That run reached ``ready`` and produced ZERO findings. It was not a crash: every stage
reported success, twelve evidence items were fetched and ranked, and the report was
published with an executive summary and no findings at all. The owner's acceptance check
caught it; the pipeline did not.

The twelve items are reproduced here in the shape they actually had, because each one
represents a different way the pipeline was wrong:

* a genuinely relevant page that was published ten days before the requested window;
* two arXiv abstracts that share no subject with the question at all;
* a Cloudflare interstitial whose title was "Bir dakika lutfen..." and which was ranked
  as if it were an article;
* two pages covering the same announcement, counted as two independent sources;
* and a synthesis response with a fluent executive summary and an empty findings list,
  which was accepted as a finished report.

Each test below asserts that this specific item can no longer reach evidence or a
published report. They are written against the real values, not simplified ones, so a
future refactor that "passes the unit tests" while re-admitting the real page fails here.
"""

from __future__ import annotations

import pytest

from app.research import eligibility
from app.research.contracts import (
    MIN_REPORT_FINDINGS,
    InsufficientValidFindings,
)
from app.research.synthesis import parse_synthesis_response

# The run asked for the last three days ending 2026-09-04.
WINDOW_START = "2026-09-01T00:00:00+00:00"
WINDOW_END = "2026-09-04T12:00:00+00:00"
TOPIC = "son üç gündeki yapay zekâ ajanlarıyla ilgili en önemli gelişmeler"
RETRIEVED_AT = "2026-09-04T11:58:00+00:00"


# --------------------------------------------------------------- old but relevant


def test_granite_model_card_is_on_topic_but_outside_the_window() -> None:
    """IBM's Granite model card: right subject, wrong week.

    This is the item that makes recency a separate check from relevance. It scores well on
    topic - it is genuinely about agentic models - so any gate that only asks "is this
    about AI agents?" admits a ten-day-old page into a three-day report.
    """
    verdict = eligibility.evaluate_candidate(
        title="IBM Granite 4.0 — agentic model ailesi",
        excerpt=(
            "Granite 4.0, araç çağırma ve çok adımlı görev planlaması için eğitilmiş "
            "yapay zeka ajanı modellerinden oluşuyor. Model kartı, ajan iş akışlarında "
            "kullanılmak üzere ince ayar yapılmış sürümleri ve değerlendirme sonuçlarını "
            "listeliyor. Modeller Hugging Face üzerinden indirilebiliyor."
        ),
        url="https://huggingface.co/ibm-granite/granite-4.0",
        topic=TOPIC,
        published_at="2026-08-25T09:00:00+00:00",
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        publisher="huggingface.co",
    )
    assert verdict.topic_relevance >= 0.35, "the page really is about AI agents"
    assert not verdict.eligible
    assert verdict.reason == "outside_recency_window"


def test_retrieval_time_is_never_read_as_a_publication_date() -> None:
    """The 2026-09-04 run treated "fetched today" as "published today".

    Every one of the twelve items had a retrieval timestamp inside the window, which is
    why an August page passed a September window. A page with no discoverable publication
    date must be marked uncertain, not backdated to the moment we fetched it.
    """
    confidence, resolved = eligibility.publication_date_confidence(
        published_at=None,
        published_hint=None,
        body_dates=(),
        retrieved_at=RETRIEVED_AT,
    )
    assert confidence == "none"
    assert resolved is None

    verdict = eligibility.evaluate_candidate(
        title="Yapay zeka ajanları için yeni araç protokolü yayınlandı",
        excerpt=(
            "Yeni protokol, yapay zeka ajanlarının harici araçları çağırma biçimini "
            "standartlaştırıyor. Geliştiriciler, ajanların farklı sağlayıcılar arasında "
            "taşınabilir hale geldiğini söylüyor ve ilk uygulamalar bu hafta yayınlandı."
        ),
        url="https://example.com/agent-tool-protocol",
        topic=TOPIC,
        published_at=None,
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert not verdict.eligible
    assert verdict.reason == "date_uncertain"


# --------------------------------------------------------------- unrelated arXiv


@pytest.mark.parametrize(
    ("title", "excerpt", "url"),
    [
        (
            "A new series representation for Catalan's constant",
            "We derive a rapidly convergent series representation for Catalan's constant "
            "and establish error bounds for its partial sums. The construction relies on "
            "a hypergeometric transformation and yields improved numerical estimates.",
            "https://arxiv.org/abs/2609.01123",
        ),
        (
            "Halo density profiles in self-interacting dark matter simulations",
            "We present cosmological simulations of self-interacting dark matter and "
            "measure the resulting halo density profiles across a range of cross "
            "sections. The inner slopes differ systematically from cold dark matter.",
            "https://arxiv.org/abs/2609.01455",
        ),
    ],
    ids=["catalans-constant", "dark-matter-halo"],
)
def test_unrelated_arxiv_abstracts_are_rejected_as_off_topic(
    title: str, excerpt: str, url: str
) -> None:
    """Two arXiv abstracts became ranked evidence in the real run.

    They arrived because the search provider returns recent arXiv listings for almost any
    technical query. Nothing about them is about AI agents, and no amount of downstream
    synthesis can turn them into an answer, so they must not survive the gate.
    """
    verdict = eligibility.evaluate_candidate(
        title=title,
        excerpt=excerpt,
        url=url,
        topic=TOPIC,
        published_at="2026-09-02T00:00:00+00:00",
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        publisher="arxiv.org",
    )
    assert not verdict.eligible
    assert verdict.reason == "off_topic"
    assert verdict.topic_relevance < 0.35


# --------------------------------------------------------------- interstitial


def test_cloudflare_interstitial_cannot_become_evidence() -> None:
    """The literal page that was ranked as source [e7] on 2026-09-04.

    Its title was a Turkish "just a moment" interstitial and its body was the challenge
    text. It carried a 200 status, a fresh retrieval time and enough characters to look
    like a page, so only content classification catches it.
    """
    title = "Bir dakika lütfen..."
    excerpt = (
        "Bir dakika lütfen... Devam etmeden önce bağlantınızın "
        "güvenliğini doğrulamamız gerekiyor. Bu işlem birkaç "
        "saniye sürebilir. Lütfen tarayıcınızda JavaScript'in "
        "etkin olduğundan emin olun ve sayfayı yenilemeyin."
    )
    assert eligibility.classify_page_validity(title=title, excerpt=excerpt) != "normal_content"

    verdict = eligibility.evaluate_candidate(
        title=title,
        excerpt=excerpt,
        url="https://news.example.com/ai-agents-weekly",
        topic=TOPIC,
        http_status=200,
        published_at="2026-09-03T08:00:00+00:00",
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert not verdict.eligible
    assert verdict.reason == "interstitial"


def test_interstitial_is_rejected_before_topic_or_date_are_judged() -> None:
    """A block page must report WHY it was blocked, not a downstream symptom.

    Reporting "off_topic" for a challenge page would send the next person debugging the
    search query instead of the fetch strategy.
    """
    verdict = eligibility.evaluate_candidate(
        title="Bir dakika lütfen...",
        excerpt="Güvenlik doğrulaması yapılıyor, lütfen bekleyin.",
        url="https://news.example.com/x",
        topic="tamamen alakasiz bir konu",
        published_at="2019-01-01T00:00:00+00:00",
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert verdict.reason == "interstitial"


# --------------------------------------------------------------- duplicate coverage


def test_two_reports_of_the_same_announcement_count_once() -> None:
    """The run counted the same launch twice and called it two sources.

    Duplicate coverage inflates the evidence count without adding anything a finding can
    rest on, which is part of how twelve items produced zero defensible findings.
    """
    first = eligibility.duplicate_event_key(
        title="OpenAI yeni yapay zeka ajanı platformunu duyurdu",
        url="https://a.example.com/openai-agent-platform",
        publisher="a.example.com",
    )
    second = eligibility.duplicate_event_key(
        title="OpenAI, yapay zeka ajanı platformunu duyurdu",
        url="https://b.example.com/openai-duyurdu",
        publisher="b.example.com",
    )
    assert eligibility.is_near_duplicate(first, second)

    verdict = eligibility.evaluate_candidate(
        title="OpenAI, yapay zeka ajanı platformunu duyurdu",
        excerpt=(
            "OpenAI, geliştiricilerin kendi yapay zeka ajanlarını kurmasına olanak tanıyan "
            "platformunu duyurdu. Ajanlar araç kullanımı, hafıza ve çok adımlı görev "
            "planlaması yapabiliyor; kurumsal erişim bu hafta açılıyor."
        ),
        url="https://b.example.com/openai-duyurdu",
        topic=TOPIC,
        published_at="2026-09-03T10:00:00+00:00",
        retrieved_at=RETRIEVED_AT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        publisher="b.example.com",
        existing_event_keys=(first,),
    )
    assert not verdict.eligible
    assert verdict.reason == "duplicate_event"


# --------------------------------------------------------------- zero findings


def _valid_finding(index: int) -> dict[str, object]:
    return {
        "id": f"f{index}",
        "title": f"Bulgu {index}",
        "summary": "Ajan platformu duyuruldu ve kurumsal erişim açıldı.",
        "why_it_matters": "Ajanların üretim ortamına girmesini hızlandırıyor.",
        "importance": 4,
        "label": "source_fact",
        "confidence": 0.7,
        "evidence_ids": ["e1"],
    }


def test_a_fluent_summary_with_no_findings_is_not_a_report() -> None:
    """The exact shape the provider returned on 2026-09-04.

    The executive summary was well-formed Turkish prose, so every check that looked at the
    summary passed. An answer with nothing in it is not an answer, and this response must
    be refused rather than published.
    """
    payload = {
        "executive_summary": (
            "Son üç günde yapay zeka ajanları alanında çeşitli gelişmeler yaşandı ve "
            "sektör hızla ilerlemeye devam ediyor."
        ),
        "findings": [],
        "why_it_matters": [],
        "watch_next": [],
        "details": [],
    }
    with pytest.raises(InsufficientValidFindings) as excinfo:
        parse_synthesis_response(payload)
    assert excinfo.value.produced == 0
    assert excinfo.value.required == MIN_REPORT_FINDINGS
    assert excinfo.value.as_dict()["error_class"] == "insufficient_valid_findings"


def test_two_findings_are_still_not_enough() -> None:
    """The floor is three, and it is a contract rather than a preference.

    A report with one or two findings reads as an answer while resting on almost nothing,
    which is the failure mode that is hardest for the owner to catch by eye.
    """
    payload = {
        "executive_summary": "Özet.",
        "findings": [_valid_finding(1), _valid_finding(2)],
        "why_it_matters": [],
        "watch_next": [],
        "details": [],
    }
    with pytest.raises(InsufficientValidFindings) as excinfo:
        parse_synthesis_response(payload)
    assert excinfo.value.produced == 2


def test_findings_that_cite_nothing_do_not_count_towards_the_floor() -> None:
    """Three findings, but only one of them attributable.

    Unattributed findings are quarantined individually, and the remainder is then measured
    against the floor - so padding a response with uncited claims cannot buy a pass.
    """
    uncited = _valid_finding(2) | {"evidence_ids": []}
    payload = {
        "executive_summary": "Özet.",
        "findings": [_valid_finding(1), uncited, _valid_finding(3) | {"evidence_ids": []}],
        "why_it_matters": [],
        "watch_next": [],
        "details": [],
    }
    with pytest.raises(InsufficientValidFindings) as excinfo:
        parse_synthesis_response(payload)
    assert excinfo.value.produced == 1


def test_three_attributable_findings_are_accepted() -> None:
    """The floor rejects thin reports; it must not reject real ones."""
    payload = {
        "executive_summary": "Özet.",
        "findings": [_valid_finding(1), _valid_finding(2), _valid_finding(3)],
        "why_it_matters": [],
        "watch_next": [],
        "details": [],
    }
    result = parse_synthesis_response(payload)
    assert len(result.findings) == 3
    assert all(f.evidence_ids for f in result.findings)
