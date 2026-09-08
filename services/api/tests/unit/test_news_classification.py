"""Pure classification: Shorts, promo, bulletin markers, near-duplicate titles
(docs/M27_LATEST_NEWS_MODE_SPEC.md §2, §3).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.news.classification import (
    VideoCandidate,
    is_promo,
    is_short,
    matches_bulletin_markers,
    near_duplicate_titles,
)

CH = "UCtestchannel0000000000"


def _candidate(**kwargs: object) -> VideoCandidate:
    defaults = dict(
        video_id="v1",
        title="Ana Haber Bülteni",
        published_at=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
        channel_id=CH,
        url="https://www.youtube.com/watch?v=v1",
        description="",
    )
    defaults.update(kwargs)
    return VideoCandidate(**defaults)  # type: ignore[arg-type]


class TestIsShort:
    def test_explicit_signal_wins_over_everything(self) -> None:
        c = _candidate(is_short=True, duration_s=600.0, title="Ana Haber Bülteni")
        assert is_short(c) is True

    def test_duration_threshold(self) -> None:
        assert is_short(_candidate(duration_s=60.0)) is True
        assert is_short(_candidate(duration_s=60.1)) is False
        assert is_short(_candidate(duration_s=10.0)) is True

    def test_shorts_url_marker(self) -> None:
        c = _candidate(url="https://www.youtube.com/shorts/abc123", duration_s=None)
        assert is_short(c) is True

    def test_shorts_hashtag_marker(self) -> None:
        c = _candidate(title="Günün özeti #shorts", duration_s=None)
        assert is_short(c) is True

    def test_unknown_duration_and_no_marker_is_not_a_short(self) -> None:
        c = _candidate(duration_s=None, title="Ana Haber Bülteni", url="https://x/watch?v=v1")
        assert is_short(c) is False


class TestIsPromo:
    def test_teaser_marker(self) -> None:
        assert is_promo(_candidate(title="Yeni sezon fragmanı")) is True

    def test_ordinary_bulletin_is_not_promo(self) -> None:
        assert is_promo(_candidate(title="Ana Haber Bülteni")) is False

    def test_trailer_in_description_counts(self) -> None:
        assert is_promo(_candidate(title="Habere Bakış", description="Official trailer")) is True


class TestBulletinMarkers:
    def test_matches_turkish_marker(self) -> None:
        assert matches_bulletin_markers(_candidate(title="Ana Haber Bülteni - 8 Eylül")) is True

    def test_matches_english_marker(self) -> None:
        assert matches_bulletin_markers(_candidate(title="Tonight's Main News")) is True

    def test_unrelated_title_does_not_match(self) -> None:
        assert matches_bulletin_markers(_candidate(title="Kısa haber özeti")) is False

    def test_diacritic_folding(self) -> None:
        # "bülten" without the dotted ü/ş should still be recognised.
        assert matches_bulletin_markers(_candidate(title="Tam bulten yayinda")) is True


class TestNearDuplicateTitles:
    def test_identical_titles(self) -> None:
        assert near_duplicate_titles("Ana Haber Bülteni", "Ana Haber Bülteni") is True

    def test_near_duplicate_with_punctuation_difference(self) -> None:
        assert (
            near_duplicate_titles("Ana Haber Bülteni 8 Eylül", "Ana Haber Bülteni - 8 Eylül!")
            is True
        )

    def test_below_threshold_extra_content_word_is_not_a_duplicate(self) -> None:
        """One genuinely new content word (a year) drops the Jaccard below 0.9 -
        this is a DIFFERENT (if related) upload, not a republish, and must not be
        silently collapsed onto the other one."""
        assert (
            near_duplicate_titles("Ana Haber Bülteni 8 Eylül", "Ana Haber Bülteni 8 Eylül 2026")
            is False
        )

    def test_unrelated_titles(self) -> None:
        assert near_duplicate_titles("Ana Haber Bülteni", "Ekonomi Gündemi") is False

    def test_empty_title_never_matches(self) -> None:
        assert near_duplicate_titles("", "Ana Haber Bülteni") is False
