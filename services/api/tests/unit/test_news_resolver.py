"""The latest-upload resolver's own fixtures (docs/M26_LATEST_NEWS_MODE_SPEC.md §3):
"Video A 08:00, B 10:00, C yesterday -> expect B; the newest item is a Short; the
newest is an unrelated promo and the newest full bulletin is second; near-duplicate
titles." Each scenario is checked against EVERY content-policy value, because "each
preference value gets its own expectation" (task brief).

Pure and offline: no network, no browser, no database. This file IS the resolver's
specification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.news.classification import VideoCandidate
from app.news.models import (
    CONTENT_TYPE_ANY_NEWS,
    CONTENT_TYPE_FULL_BROADCAST,
    CONTENT_TYPE_MAIN_NEWS,
)
from app.news.provider import FixtureNewsProvider
from app.news.resolver import (
    REASON_BULLETIN_MARKER,
    REASON_NEWEST,
    REASON_NEWEST_NON_SHORT_FALLBACK,
    REASON_NONE_ELIGIBLE,
    REJECT_DUPLICATE,
    REJECT_PROMO_EXCLUDED,
    REJECT_SHORTS_EXCLUDED,
    audit_candidates,
    resolve_latest,
)

CH = "UCfixturechannel00000000"
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _c(video_id: str, title: str, published_at: datetime, **kwargs: object) -> VideoCandidate:
    defaults: dict[str, object] = dict(
        video_id=video_id,
        title=title,
        published_at=published_at,
        channel_id=CH,
        url=f"https://www.youtube.com/watch?v={video_id}",
        description="",
    )
    defaults.update(kwargs)
    return VideoCandidate(**defaults)  # type: ignore[arg-type]


ALL_CONTENT_TYPES = (CONTENT_TYPE_ANY_NEWS, CONTENT_TYPE_FULL_BROADCAST, CONTENT_TYPE_MAIN_NEWS)


# --------------------------------------------------------------------------- #
# Scenario 1: plain "latest" by real timestamp, never search rank
# --------------------------------------------------------------------------- #


class TestScenarioPlainLatest:
    """A 08:00, B 10:00, C yesterday -> expect B, for every content policy (none of
    these three is a Short or a promo, so every policy agrees)."""

    CANDIDATES = [
        _c("A", "Ana Haber Bülteni Sabah", NOW.replace(hour=8, minute=0)),
        _c("B", "Ana Haber Bülteni Öğle", NOW.replace(hour=10, minute=0)),
        _c("C", "Ana Haber Bülteni Dün", NOW - timedelta(days=1)),
    ]

    def test_expect_b_for_every_content_type(self) -> None:
        for content_type in ALL_CONTENT_TYPES:
            result = resolve_latest(
                list(self.CANDIDATES), content_type=content_type, answered_by="fixture"
            )
            assert result.selected is not None, content_type
            assert result.selected.video_id == "B", content_type

    def test_any_news_reason_is_newest(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        assert result.reason == REASON_NEWEST
        assert result.ambiguous is False

    def test_main_news_matches_bulletin_marker(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_MAIN_NEWS, answered_by="fixture"
        )
        assert result.reason == REASON_BULLETIN_MARKER
        assert result.ambiguous is False

    def test_fixture_provider_returns_the_same_candidates(self) -> None:
        provider = FixtureNewsProvider(channels={CH: list(self.CANDIDATES)})
        candidates, answered_by = provider.list_recent_uploads(CH)
        assert answered_by == "fixture"
        assert {c.video_id for c in candidates} == {"A", "B", "C"}


# --------------------------------------------------------------------------- #
# Scenario 2: the newest item is a Short
# --------------------------------------------------------------------------- #


class TestScenarioNewestIsShort:
    """The newest upload is a Short. latest_any_news accepts it (no filtering at all
    - the owner explicitly configured that); latest_full_broadcast/latest_main_news
    must skip it (task brief: "A Shorts clip must not silently satisfy 'the latest
    full bulletin'") and fall through to the next eligible bulletin."""

    CANDIDATES = [
        _c(
            "OLDER_BULLETIN",
            "Ana Haber Bülteni",
            NOW - timedelta(hours=3),
        ),
        _c(
            "NEWEST_SHORT",
            "Günün en çarpıcı anı #shorts",
            NOW,
            duration_s=25.0,
        ),
    ]

    def test_any_news_accepts_the_short(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "NEWEST_SHORT"
        assert result.reason == REASON_NEWEST

    def test_full_broadcast_skips_the_short(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_FULL_BROADCAST, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "OLDER_BULLETIN"
        assert ("NEWEST_SHORT", REJECT_SHORTS_EXCLUDED) in result.rejected

    def test_main_news_skips_the_short_too(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_MAIN_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "OLDER_BULLETIN"

    def test_explicit_is_short_signal_is_honoured_over_duration(self) -> None:
        c = _c("X", "Ana Haber Bülteni", NOW, duration_s=600.0, is_short=True)
        result = resolve_latest(
            [c], content_type=CONTENT_TYPE_FULL_BROADCAST, answered_by="fixture"
        )
        assert result.selected is None
        assert result.reason == REASON_NONE_ELIGIBLE


# --------------------------------------------------------------------------- #
# Scenario 3: newest is an unrelated promo, newest full bulletin is second
# --------------------------------------------------------------------------- #


class TestScenarioNewestIsPromo:
    CANDIDATES = [
        _c("BULLETIN", "Ana Haber Bülteni", NOW - timedelta(hours=1)),
        _c("PROMO", "Yeni dizi için fragman", NOW),
    ]

    def test_any_news_still_takes_the_newest_including_the_promo(self) -> None:
        """latest_any_news is "no filtering at all": the owner chose that policy
        knowing it accepts anything, including a promo — this is the honest, non-
        surprising behaviour of that specific configuration value."""
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "PROMO"

    def test_full_broadcast_skips_the_promo(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_FULL_BROADCAST, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "BULLETIN"
        assert ("PROMO", REJECT_PROMO_EXCLUDED) in result.rejected

    def test_main_news_skips_the_promo_too(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_MAIN_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "BULLETIN"


# --------------------------------------------------------------------------- #
# Scenario 4: near-duplicate titles
# --------------------------------------------------------------------------- #


class TestScenarioNearDuplicateTitles:
    CANDIDATES = [
        _c("OLD_COPY", "Ana Haber Bülteni - 8 Eylül 2026", NOW - timedelta(hours=2)),
        _c("NEW_COPY", "Ana Haber Bülteni 8 Eylül 2026", NOW),
    ]

    def test_the_newer_copy_is_selected(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "NEW_COPY"

    def test_the_older_copy_is_recorded_as_a_duplicate_not_silently_dropped(self) -> None:
        result = resolve_latest(
            list(self.CANDIDATES), content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        rejected_by_id = dict(result.rejected)
        assert rejected_by_id["OLD_COPY"] == f"{REJECT_DUPLICATE}:NEW_COPY"

    def test_distinct_stories_are_never_deduped(self) -> None:
        candidates = [
            _c("A", "Ana Haber Bülteni", NOW - timedelta(hours=1)),
            _c("B", "Ekonomi Gündemi", NOW),
        ]
        result = resolve_latest(
            candidates, content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture"
        )
        assert result.rejected == ()


# --------------------------------------------------------------------------- #
# Answered_by / audit trail
# --------------------------------------------------------------------------- #


class TestAnsweredByAndAudit:
    def test_answered_by_is_recorded_verbatim(self) -> None:
        result = resolve_latest(
            [_c("A", "Ana Haber Bülteni", NOW)],
            content_type=CONTENT_TYPE_ANY_NEWS,
            answered_by="channel_feed",
        )
        assert result.answered_by == "channel_feed"

    def test_no_candidates_at_all_is_none_eligible_never_a_guess(self) -> None:
        result = resolve_latest([], content_type=CONTENT_TYPE_ANY_NEWS, answered_by="fixture")
        assert result.selected is None
        assert result.reason == REASON_NONE_ELIGIBLE

    def test_audit_candidates_names_every_candidate_and_its_verdict(self) -> None:
        candidates = [
            _c("BULLETIN", "Ana Haber Bülteni", NOW - timedelta(hours=1)),
            _c("PROMO", "Yeni dizi için fragman", NOW),
        ]
        result = resolve_latest(
            candidates, content_type=CONTENT_TYPE_FULL_BROADCAST, answered_by="fixture"
        )
        audit = audit_candidates(candidates, result)
        by_id = {a["video_id"]: a for a in audit}
        assert by_id["BULLETIN"]["accepted"] is True
        assert by_id["PROMO"]["accepted"] is False
        assert by_id["PROMO"]["reason"] == REJECT_PROMO_EXCLUDED

    def test_unknown_content_type_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="unknown content_type"):
            resolve_latest([], content_type="not_a_real_policy", answered_by="fixture")


# --------------------------------------------------------------------------- #
# Ambiguous fallback: no bulletin marker matches any eligible candidate
# --------------------------------------------------------------------------- #


class TestAmbiguousFallback:
    def test_main_news_with_no_marker_match_falls_back_and_flags_ambiguous(self) -> None:
        """Neither candidate carries an explicit bulletin marker; the safer fallback
        (newest non-short, non-promo) is used, and the result says so honestly
        (task brief: "never guess quietly")."""
        candidates = [
            _c("OLDER", "Gündem Özeti", NOW - timedelta(hours=1)),
            _c("NEWER", "Gece Programı", NOW),
        ]
        result = resolve_latest(
            candidates, content_type=CONTENT_TYPE_MAIN_NEWS, answered_by="fixture"
        )
        assert result.selected is not None
        assert result.selected.video_id == "NEWER"
        assert result.reason == REASON_NEWEST_NON_SHORT_FALLBACK
        assert result.ambiguous is True
