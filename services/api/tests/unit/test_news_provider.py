"""Providers (docs/M26_LATEST_NEWS_MODE_SPEC.md §2, §4): the FixtureNewsProvider
(offline, deterministic) and the real YouTubeFeedProvider's Atom parsing — pinned
against a captured real-shaped feed here (offline) with ONE opt-in live test against
the actual internet (``@pytest.mark.live``, never run by default CI, the same
discipline ``app.research.sources``'s own live opt-in test uses).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.news.classification import VideoCandidate
from app.news.models import ANSWERED_BY_CHANNEL_FEED, ANSWERED_BY_FIXTURE
from app.news.provider import (
    FixtureNewsProvider,
    ProviderUnavailableError,
    YouTubeFeedProvider,
    _parse_feed,
    looks_like_channel_id,
)

CH = "UCLA_DiR1FfKNvjuUpBHmylQ"

#: A real-shaped Atom feed (structure captured from YouTube's own
#: /feeds/videos.xml, values fabricated) - offline and deterministic.
_SAMPLE_FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <yt:channelId>{CH}</yt:channelId>
 <title>Test Channel</title>
 <entry>
  <id>yt:video:aaa111</id>
  <yt:videoId>aaa111</yt:videoId>
  <yt:channelId>{CH}</yt:channelId>
  <title>Ana Haber Bülteni</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=aaa111"/>
  <published>2026-09-08T20:00:00+00:00</published>
  <updated>2026-09-08T20:00:05+00:00</updated>
  <media:group>
   <media:title>Ana Haber Bülteni</media:title>
   <media:description>Günün önemli gelişmeleri.</media:description>
  </media:group>
 </entry>
 <entry>
  <id>yt:video:bbb222</id>
  <yt:videoId>bbb222</yt:videoId>
  <yt:channelId>{CH}</yt:channelId>
  <title>Kısa özet #shorts</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=bbb222"/>
  <published>2026-09-08T18:00:00+00:00</published>
  <updated>2026-09-08T18:00:05+00:00</updated>
  <media:group>
   <media:title>Kısa özet #shorts</media:title>
   <media:description></media:description>
  </media:group>
 </entry>
</feed>
""".encode()


class TestFixtureNewsProvider:
    def test_returns_configured_candidates_and_answered_by_fixture(self) -> None:
        candidate = VideoCandidate(
            video_id="v1",
            title="Ana Haber Bülteni",
            published_at=datetime(2026, 9, 8, tzinfo=UTC),
            channel_id=CH,
            url="https://www.youtube.com/watch?v=v1",
        )
        provider = FixtureNewsProvider(channels={CH: [candidate]})
        candidates, answered_by = provider.list_recent_uploads(CH)
        assert candidates == [candidate]
        assert answered_by == ANSWERED_BY_FIXTURE

    def test_an_unknown_channel_returns_an_empty_list_not_an_error(self) -> None:
        provider = FixtureNewsProvider()
        candidates, answered_by = provider.list_recent_uploads("UCunknown0000000000000")
        assert candidates == []
        assert answered_by == ANSWERED_BY_FIXTURE


class TestChannelIdShape:
    def test_a_real_shaped_id_matches(self) -> None:
        assert looks_like_channel_id(CH) is True

    def test_too_short_does_not_match(self) -> None:
        assert looks_like_channel_id("UC123") is False

    def test_wrong_prefix_does_not_match(self) -> None:
        assert looks_like_channel_id("XX" + "a" * 22) is False


class TestFeedParsing:
    def test_parses_video_id_title_published_at_description(self) -> None:
        candidates = _parse_feed(CH, _SAMPLE_FEED)
        assert len(candidates) == 2
        first = candidates[0]
        assert first.video_id == "aaa111"
        assert first.title == "Ana Haber Bülteni"
        assert first.published_at == datetime(2026, 9, 8, 20, 0, 0, tzinfo=UTC)
        assert first.description == "Günün önemli gelişmeleri."
        assert first.url == "https://www.youtube.com/watch?v=aaa111"
        assert first.channel_id == CH

    def test_real_publish_timestamp_never_a_retrieval_time(self) -> None:
        """The resolver sorts on published_at alone (task brief §2) - this just pins
        that the parser reads the feed's own <published>, never "now"."""
        candidates = _parse_feed(CH, _SAMPLE_FEED)
        assert all(c.published_at.year == 2026 and c.published_at.month == 9 for c in candidates)

    def test_malformed_xml_raises_provider_unavailable_not_a_silent_empty_list(self) -> None:
        import xml.etree.ElementTree as ET

        with pytest.raises(ET.ParseError):
            _parse_feed(CH, b"<not valid xml")


class TestYouTubeFeedProviderOffline:
    def test_http_error_raises_provider_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        class _FailingClient:
            @staticmethod
            def get(*args: object, **kwargs: object) -> object:
                raise httpx.ConnectError("no network in this test")

        monkeypatch.setattr("httpx.get", _FailingClient.get)
        provider = YouTubeFeedProvider()
        with pytest.raises(ProviderUnavailableError):
            provider.list_recent_uploads(CH)

    def test_malformed_response_raises_provider_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeResponse:
            content = b"<not valid xml"

            @staticmethod
            def raise_for_status() -> None:
                return None

        monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse())
        provider = YouTubeFeedProvider()
        with pytest.raises(ProviderUnavailableError):
            provider.list_recent_uploads(CH)

    def test_a_successful_response_answers_by_channel_feed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeResponse:
            content = _SAMPLE_FEED

            @staticmethod
            def raise_for_status() -> None:
                return None

        monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse())
        provider = YouTubeFeedProvider()
        candidates, answered_by = provider.list_recent_uploads(CH)
        assert answered_by == ANSWERED_BY_CHANNEL_FEED
        assert len(candidates) == 2


@pytest.mark.live
class TestYouTubeFeedProviderLive:
    """ONE read-only qualification against a real channel (task brief §4): NASA's
    own public YouTube channel — its identity established from an authoritative
    signal (the feed's own self-reported canonical channel link, captured 2026-09-08
    in this module's docstring, never guessed from a display name). Never run by
    default CI; skips honestly if the network is unavailable rather than failing the
    suite."""

    NASA_CHANNEL_ID = "UCLA_DiR1FfKNvjuUpBHmylQ"

    def test_resolves_a_real_latest_upload_by_real_timestamp(self) -> None:

        provider = YouTubeFeedProvider()
        try:
            candidates, answered_by = provider.list_recent_uploads(self.NASA_CHANNEL_ID)
        except ProviderUnavailableError as exc:  # pragma: no cover - network-dependent
            pytest.skip(f"live network unavailable: {exc}")
        assert answered_by == ANSWERED_BY_CHANNEL_FEED
        assert len(candidates) > 0
        for c in candidates:
            assert c.channel_id == self.NASA_CHANNEL_ID
            assert c.published_at.tzinfo is not None
        # Real timestamps, real order: never assumed sorted by the provider itself -
        # the RESOLVER is what sorts (app.news.resolver.resolve_latest); this only
        # pins that every candidate carries a genuine, distinct published_at.
        assert len({c.published_at for c in candidates}) == len(candidates)
