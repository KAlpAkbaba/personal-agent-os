"""Channel identity resolution (docs/M27_LATEST_NEWS_MODE_SPEC.md §1) — the single
most important rule: never guess a channel identity from a display name. Every case
here either resolves from an authoritative signal or resolves to nothing at all;
there is no third outcome.
"""

from __future__ import annotations

from app.news.identity import (
    RESOLVED_BY_CHANNEL_URL,
    RESOLVED_BY_DIRECT_ID,
    RESOLVED_BY_PROVIDER_LOOKUP,
    resolve_channel_identity,
)

REAL_ID = (
    "UCLA_DiR1FfKNvjuUpBHmylQ"  # NASA's real, public channel id (used as a fixture value only)
)


class TestDirectId:
    def test_bare_channel_id_resolves_directly(self) -> None:
        identity = resolve_channel_identity(REAL_ID)
        assert identity is not None
        assert identity.channel_id == REAL_ID
        assert identity.resolved_by == RESOLVED_BY_DIRECT_ID

    def test_a_short_string_is_not_a_channel_id(self) -> None:
        assert resolve_channel_identity("UC123") is None

    def test_empty_input_resolves_to_nothing(self) -> None:
        assert resolve_channel_identity("") is None
        assert resolve_channel_identity("   ") is None


class TestChannelUrl:
    def test_channel_url_extracts_the_id_from_the_path(self) -> None:
        identity = resolve_channel_identity(f"https://www.youtube.com/channel/{REAL_ID}")
        assert identity is not None
        assert identity.channel_id == REAL_ID
        assert identity.resolved_by == RESOLVED_BY_CHANNEL_URL

    def test_channel_url_without_scheme(self) -> None:
        identity = resolve_channel_identity(f"www.youtube.com/channel/{REAL_ID}/videos")
        assert identity is not None
        assert identity.channel_id == REAL_ID

    def test_a_non_youtube_url_never_resolves(self) -> None:
        assert resolve_channel_identity(f"https://example.com/channel/{REAL_ID}") is None


class TestHandleUrlNeedsLiveLookup:
    def test_a_handle_url_with_no_fetcher_stays_unresolved(self) -> None:
        """The central rule in miniature: without an authoritative lookup, a handle
        URL is NOT enough on its own — the handle text itself names nothing
        canonical."""
        assert resolve_channel_identity("https://www.youtube.com/@SomeNewsChannel") is None

    def test_a_handle_url_resolves_through_an_injected_fetcher(self) -> None:
        def fake_fetch(url: str) -> str:
            assert "@SomeNewsChannel" in url
            return f'<link rel="canonical" href="https://www.youtube.com/channel/{REAL_ID}">'

        identity = resolve_channel_identity(
            "https://www.youtube.com/@SomeNewsChannel", fetch_page=fake_fetch
        )
        assert identity is not None
        assert identity.channel_id == REAL_ID
        assert identity.resolved_by == RESOLVED_BY_PROVIDER_LOOKUP

    def test_a_fetcher_that_finds_nothing_canonical_stays_unresolved(self) -> None:
        def fake_fetch(url: str) -> str:
            return "<html><body>no canonical link here</body></html>"

        assert (
            resolve_channel_identity(
                "https://www.youtube.com/@SomeNewsChannel", fetch_page=fake_fetch
            )
            is None
        )

    def test_a_fetcher_that_raises_never_propagates(self) -> None:
        def broken_fetch(url: str) -> str:
            raise RuntimeError("network is down")

        assert (
            resolve_channel_identity(
                "https://www.youtube.com/@SomeNewsChannel", fetch_page=broken_fetch
            )
            is None
        )

    def test_channelid_meta_pattern_also_resolves(self) -> None:
        def fake_fetch(url: str) -> str:
            return f'window.ytInitialData = {{"channelId":"{REAL_ID}"}};'

        identity = resolve_channel_identity(
            "https://www.youtube.com/@SomeNewsChannel", fetch_page=fake_fetch
        )
        assert identity is not None
        assert identity.channel_id == REAL_ID


class TestNeverGuessedFromDisplayNameAlone:
    def test_a_bare_display_name_never_resolves(self) -> None:
        """This is THE rule (task brief): "Show Ana Haber" as a plain name, with no
        URL at all, must never produce a channel id — not even a plausible one."""
        assert resolve_channel_identity("Show Ana Haber") is None
        assert resolve_channel_identity("Show Ana Haber", fetch_page=lambda u: "<html/>") is None
