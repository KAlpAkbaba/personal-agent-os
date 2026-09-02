"""M13 evidence extraction + dedup/ranking (no Playwright, no network)."""

from datetime import UTC, datetime

import pytest

from browser_agent.evidence import (
    EXTRACTION_ACCESSIBILITY_SNAPSHOT,
    EXTRACTION_DOM_TEXT,
    PageEvidence,
    dedup_and_rank_evidence,
    extract_page_evidence,
)


class _FixedClock:
    """Stand-in for ``datetime`` with a frozen ``now()``."""

    _moment = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001 - mirrors datetime.now signature
        return cls._moment


class _FakeDriver:
    """Minimal PageDriver: no browser, no network — just canned responses."""

    def __init__(self, *, title: str, body_text: str, snapshot: str = "") -> None:
        self._title = title
        self._body_text = body_text
        self._snapshot = snapshot
        self.navigated_to: list[str] = []

    async def navigate(self, url: str, *, timeout_ms: float = 15_000) -> None:
        self.navigated_to.append(url)

    async def title(self) -> str:
        return self._title

    async def page_text(self, *, timeout_ms: float = 5_000) -> str:
        return self._body_text

    async def accessibility_snapshot(self, *, timeout_ms: float = 5_000) -> str:
        return self._snapshot


@pytest.mark.asyncio
async def test_extract_page_evidence_prefers_dom_text() -> None:
    driver = _FakeDriver(title="Örnek Başlık", body_text="  merhaba   dünya  ")
    item = await extract_page_evidence(
        driver, "https://example.com/a", source_class="news", query="q1", clock=_FixedClock
    )
    assert driver.navigated_to == ["https://example.com/a"]
    assert item.title == "Örnek Başlık"
    assert item.excerpt == "merhaba dünya"
    assert item.extraction_method == EXTRACTION_DOM_TEXT
    assert item.source_class == "news"
    assert item.query == "q1"
    assert item.fetched_at == _FixedClock.now(UTC)


@pytest.mark.asyncio
async def test_extract_page_evidence_falls_back_to_accessibility_snapshot() -> None:
    driver = _FakeDriver(title="T", body_text="   ", snapshot="- text: hello from aria")
    item = await extract_page_evidence(driver, "https://example.com/b", clock=_FixedClock)
    assert item.extraction_method == EXTRACTION_ACCESSIBILITY_SNAPSHOT
    assert "hello from aria" in item.excerpt


@pytest.mark.asyncio
async def test_extract_page_evidence_unknown_source_class_normalizes() -> None:
    driver = _FakeDriver(title="T", body_text="body")
    item = await extract_page_evidence(
        driver, "https://example.com/c", source_class="not-a-real-class", clock=_FixedClock
    )
    assert item.source_class == "unknown"


@pytest.mark.asyncio
async def test_excerpt_is_truncated_and_verbatim() -> None:
    driver = _FakeDriver(title="T", body_text="x" * 2000)
    item = await extract_page_evidence(
        driver, "https://example.com/d", excerpt_chars=50, clock=_FixedClock
    )
    assert len(item.excerpt) == 50
    assert item.excerpt == "x" * 50


def _mk(url: str, *, excerpt: str = "some text", source_class: str = "news") -> PageEvidence:
    return PageEvidence(
        url=url,
        title="T",
        excerpt=excerpt,
        fetched_at=_FixedClock.now(UTC),
        extraction_method=EXTRACTION_DOM_TEXT,
        source_class=source_class,
    )


def test_dedup_keeps_the_richer_duplicate() -> None:
    short = _mk("https://example.com/x", excerpt="short")
    long_ = _mk("https://example.com/x#frag", excerpt="a much longer excerpt of text")
    ranked = dedup_and_rank_evidence([short, long_])
    assert len(ranked) == 1
    assert ranked[0].evidence.excerpt == "a much longer excerpt of text"


def test_dedup_treats_trailing_slash_and_query_as_the_same_url() -> None:
    a = _mk("https://example.com/x/")
    b = _mk("https://example.com/x?utm_source=foo")
    ranked = dedup_and_rank_evidence([a, b])
    assert len(ranked) == 1


def test_ranking_is_deterministic_across_input_order() -> None:
    items = [_mk("https://a.example.com/1"), _mk("https://b.example.com/2")]
    a = dedup_and_rank_evidence(items)
    b = dedup_and_rank_evidence(list(reversed(items)))
    assert [r.evidence.url for r in a] == [r.evidence.url for r in b]


def test_ranking_prefers_higher_source_class_weight() -> None:
    official = _mk("https://gov.example.com/1", source_class="official")
    community = _mk("https://forum.example.com/2", source_class="community")
    ranked = dedup_and_rank_evidence([community, official])
    assert ranked[0].evidence.source_class == "official"
    assert ranked[0].score > ranked[1].score


def test_ranking_rewards_recency_within_window() -> None:
    in_window = PageEvidence(
        url="https://example.com/in",
        title="T",
        excerpt="konu hakkında bilgi",
        fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
        extraction_method=EXTRACTION_DOM_TEXT,
        source_class="news",
    )
    out_of_window = PageEvidence(
        url="https://example.com/out",
        title="T",
        excerpt="konu hakkında bilgi",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        extraction_method=EXTRACTION_DOM_TEXT,
        source_class="news",
    )
    ranked = dedup_and_rank_evidence(
        [out_of_window, in_window],
        topic="konu",
        window_start=datetime(2026, 8, 30, tzinfo=UTC),
        window_end=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert ranked[0].evidence.url == "https://example.com/in"


def test_ranking_rewards_keyword_overlap_with_topic() -> None:
    relevant = _mk("https://example.com/rel", excerpt="yapay zeka ajanlari haberleri")
    irrelevant = _mk("https://example.com/irrel", excerpt="tamamen alakasiz bir konu")
    ranked = dedup_and_rank_evidence(
        [irrelevant, relevant], topic="yapay zeka ajanlari"
    )
    assert ranked[0].evidence.url == "https://example.com/rel"
