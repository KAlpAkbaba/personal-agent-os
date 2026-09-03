"""Unit tests for browser_agent.extraction's pure metadata/date/link logic.

No browser: ``build_metadata``/``build_links``/``parse_json_ld``/
``normalize_date_to_iso_utc`` are pure functions of a :class:`RawPageData`
(the shape ``read_raw_page_data`` produces from a live page's DOM). Here that
shape is built directly from saved HTML fixture files with BeautifulSoup
(already a project dependency for SERP parsing) — the same information a
live-page JS read would produce, without needing a browser process.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from browser_agent.extraction import (
    RawPageData,
    build_links,
    build_metadata,
    normalize_date_to_iso_utc,
    parse_json_ld,
)

FIXTURE_SITE = Path(__file__).parent.parent / "fixtures" / "site"


def _meta_content(soup: BeautifulSoup, selector: str) -> str | None:
    el = soup.select_one(selector)
    return el.get("content") if el else None


def _raw_from_html(html: str) -> RawPageData:
    """Mirror ``extraction._EXTRACT_JS``'s field selection, in Python, for tests."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1, h2")
    canonical = soup.select_one('link[rel="canonical"]')
    time_el = soup.select_one("time[datetime]")
    links = [
        {"text": a.get_text(strip=True), "href": a.get("href", "")} for a in soup.select("a[href]")
    ]
    json_ld_raw = [
        s.get_text()
        for s in soup.select('script[type="application/ld+json"]')
        if s.get_text().strip()
    ]
    return RawPageData(
        title=(soup.title.get_text() if soup.title else "") or "",
        heading_text=heading.get_text(strip=True) if heading else "",
        has_password_field=soup.select_one('input[type="password"]') is not None,
        html_lang=(soup.html.get("lang") if soup.html else None),
        canonical_url=canonical.get("href") if canonical else None,
        description=_meta_content(soup, 'meta[name="description"]'),
        og_site_name=_meta_content(soup, 'meta[property="og:site_name"]'),
        article_published_time=_meta_content(soup, 'meta[property="article:published_time"]'),
        article_modified_time=_meta_content(soup, 'meta[property="article:modified_time"]'),
        meta_date=_meta_content(soup, 'meta[name="date"]'),
        meta_author=_meta_content(soup, 'meta[name="author"]'),
        time_datetime=time_el.get("datetime") if time_el else None,
        links=links,
        json_ld_raw=json_ld_raw,
    )


def test_metadata_from_article_fixture() -> None:
    html = (FIXTURE_SITE / "article.html").read_text(encoding="utf-8")
    raw = _raw_from_html(html)
    metadata = build_metadata(raw)

    assert metadata.canonical_url == "https://example.com/agents-ship-faster-now"
    assert metadata.published_at == "2026-09-01T10:00:00Z"
    assert metadata.modified_at == "2026-09-02T08:30:00Z"
    assert metadata.publisher == "Fixture Times"
    assert metadata.author == "Ada Researcher"
    assert metadata.language == "en"
    assert metadata.description == "A fixture article about agent shipping velocity."


def test_absent_description_is_null_never_guessed() -> None:
    raw = RawPageData(
        title="No description here",
        heading_text="",
        has_password_field=False,
        html_lang=None,
        canonical_url=None,
        description=None,
        og_site_name=None,
        article_published_time=None,
        article_modified_time=None,
        meta_date=None,
        meta_author=None,
        time_datetime=None,
        links=[],
        json_ld_raw=[],
    )
    metadata = build_metadata(raw)
    assert metadata.description is None
    assert metadata.published_at is None
    assert metadata.publisher is None
    assert metadata.author is None


def test_published_at_source_priority_article_time_wins() -> None:
    raw = RawPageData(
        title="t",
        heading_text="",
        has_password_field=False,
        html_lang=None,
        canonical_url=None,
        description=None,
        og_site_name=None,
        article_published_time="2026-01-01T00:00:00Z",
        article_modified_time=None,
        meta_date="2020-01-01",
        meta_author=None,
        time_datetime="2019-01-01",
        links=[],
        json_ld_raw=[],
    )
    assert build_metadata(raw).published_at == "2026-01-01T00:00:00Z"


def test_published_at_falls_back_to_meta_date_then_time_tag() -> None:
    raw_meta_date = RawPageData(
        title="t",
        heading_text="",
        has_password_field=False,
        html_lang=None,
        canonical_url=None,
        description=None,
        og_site_name=None,
        article_published_time=None,
        article_modified_time=None,
        meta_date="2024-06-15",
        meta_author=None,
        time_datetime="2019-01-01",
        links=[],
        json_ld_raw=[],
    )
    assert build_metadata(raw_meta_date).published_at == "2024-06-15T00:00:00Z"

    raw_time_tag = RawPageData(
        title="t",
        heading_text="",
        has_password_field=False,
        html_lang=None,
        canonical_url=None,
        description=None,
        og_site_name=None,
        article_published_time=None,
        article_modified_time=None,
        meta_date=None,
        meta_author=None,
        time_datetime="2023-03-04T12:00:00+00:00",
        links=[],
        json_ld_raw=[],
    )
    assert build_metadata(raw_time_tag).published_at == "2023-03-04T12:00:00Z"


class TestNormalizeDateToIsoUtc:
    def test_none_stays_none(self) -> None:
        assert normalize_date_to_iso_utc(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert normalize_date_to_iso_utc("  ") is None

    def test_iso_with_z_normalizes(self) -> None:
        assert normalize_date_to_iso_utc("2026-09-01T10:00:00Z") == "2026-09-01T10:00:00Z"

    def test_iso_with_offset_converts_to_utc(self) -> None:
        assert normalize_date_to_iso_utc("2026-09-01T13:00:00+03:00") == "2026-09-01T10:00:00Z"

    def test_bare_date_normalizes_to_midnight_utc(self) -> None:
        assert normalize_date_to_iso_utc("2026-09-01") == "2026-09-01T00:00:00Z"

    def test_unparseable_string_is_returned_unchanged_never_dropped(self) -> None:
        assert normalize_date_to_iso_utc("sometime last week") == "sometime last week"


class TestBuildLinks:
    def test_filters_to_http_https_only(self) -> None:
        raw_links = [
            {"text": "ok", "href": "https://example.com/a"},
            {"text": "mail", "href": "mailto:tips@example.com"},
            {"text": "js", "href": "javascript:alert(1)"},
            {"text": "anchor", "href": "#section"},
        ]
        links = build_links(raw_links, base_url="https://example.com/page")
        assert [link["href"] for link in links] == ["https://example.com/a"]

    def test_resolves_relative_hrefs_against_base_url(self) -> None:
        raw_links = [{"text": "second", "href": "second.html"}]
        links = build_links(raw_links, base_url="https://example.com/dir/index.html")
        assert links[0]["href"] == "https://example.com/dir/second.html"

    def test_dedupes_and_caps_at_limit(self) -> None:
        raw_links = [{"text": f"l{i}", "href": "https://example.com/same"} for i in range(5)]
        links = build_links(raw_links, base_url="https://example.com/", limit=200)
        assert len(links) == 1

    def test_respects_custom_limit(self) -> None:
        raw_links = [{"text": str(i), "href": f"https://example.com/{i}"} for i in range(10)]
        links = build_links(raw_links, base_url="https://example.com/", limit=3)
        assert len(links) == 3


def test_parse_json_ld_from_article_fixture() -> None:
    html = (FIXTURE_SITE / "article.html").read_text(encoding="utf-8")
    raw = _raw_from_html(html)
    parsed = parse_json_ld(raw.json_ld_raw)
    assert len(parsed) == 1
    assert parsed[0]["@type"] == "NewsArticle"
    assert parsed[0]["datePublished"] == "2026-09-01T10:00:00Z"


def test_parse_json_ld_skips_malformed_block_without_raising() -> None:
    parsed = parse_json_ld(["{not valid json", '{"@type": "Thing", "name": "ok"}'])
    assert len(parsed) == 1
    assert parsed[0]["name"] == "ok"
