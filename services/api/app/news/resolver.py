"""The latest-upload resolver (docs/M26_LATEST_NEWS_MODE_SPEC.md §2): what "latest"
means, decided ONLY from real publish/upload timestamps, never search ranking or title
similarity, and content-policy-aware so a Shorts clip or an unrelated promo cannot
silently satisfy "the latest full bulletin".

Pure function over already-fetched candidates: everything network/provider-shaped lives
in ``app.news.provider``. This split is what makes ``tests/unit/test_news_resolver.py``
exact and offline — the fixtures in that file ARE this module's specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.news.classification import (
    VideoCandidate,
    is_promo,
    is_short,
    matches_bulletin_markers,
    near_duplicate_titles,
)
from app.news.models import (
    CONTENT_TYPE_ANY_NEWS,
    CONTENT_TYPE_FULL_BROADCAST,
    CONTENT_TYPE_MAIN_NEWS,
)

#: Selection reasons (``NewsResolutionRow.reason`` / ``ResolverResult.reason``). A
#: closed, greppable vocabulary — never a free-text explanation only a human can parse.
REASON_NEWEST: Final = "newest"
REASON_BULLETIN_MARKER: Final = "bulletin_marker"
REASON_NEWEST_NON_SHORT_FALLBACK: Final = "newest_non_short_fallback"
REASON_NONE_ELIGIBLE: Final = "none_eligible"

#: Per-candidate rejection reasons recorded in ``ResolverResult.rejected`` /
#: ``NewsResolutionRow.candidates_json`` — every refusal is recorded, never silent
#: (task brief: "never guess quietly").
REJECT_SHORTS_EXCLUDED: Final = "shorts_excluded"
REJECT_PROMO_EXCLUDED: Final = "promo_excluded"
REJECT_DUPLICATE: Final = "duplicate"


@dataclass(frozen=True, slots=True)
class ResolverResult:
    selected: VideoCandidate | None
    reason: str
    content_type: str
    answered_by: str
    candidates_considered: int
    #: [(video_id, reason), ...] — every candidate NOT selected, and why.
    rejected: tuple[tuple[str, str], ...] = ()
    #: True when no candidate positively matched the policy's own marker vocabulary and
    #: the safer fallback (newest non-short, non-promo) was used instead — recorded, per
    #: the "never guess quietly" rule, never hidden inside a plain "newest" reason.
    ambiguous: bool = False


def _dedup(candidates: list[VideoCandidate]) -> tuple[list[VideoCandidate], list[tuple[str, str]]]:
    """Newest-first list with near-duplicate titles collapsed onto the first (newest)
    occurrence (M13 spec §2's dedup rule, reused verbatim - see
    ``app.news.classification.near_duplicate_titles``)."""
    ordered = sorted(candidates, key=lambda c: c.published_at, reverse=True)
    kept: list[VideoCandidate] = []
    rejected: list[tuple[str, str]] = []
    for candidate in ordered:
        dup_of = next((k for k in kept if near_duplicate_titles(k.title, candidate.title)), None)
        if dup_of is not None:
            rejected.append((candidate.video_id, f"{REJECT_DUPLICATE}:{dup_of.video_id}"))
            continue
        kept.append(candidate)
    return kept, rejected


def resolve_latest(
    candidates: list[VideoCandidate],
    *,
    content_type: str,
    answered_by: str,
) -> ResolverResult:
    """Decide what "the latest" upload means for ``content_type`` (spec §2).

    - ``latest_any_news``: the newest upload, unconditionally — the owner explicitly
      configured "no filtering", so even a Short or a promo is a legitimate answer.
    - ``latest_full_broadcast`` / ``latest_main_news``: Shorts and promotional content
      are excluded outright; among what remains, a candidate whose title/description
      carries an explicit bulletin marker ("ana haber", "tam bülten", ...) is preferred
      (newest such match); when NONE does, the newest surviving (non-short, non-promo)
      candidate is used as the safer fallback, and the result is marked ``ambiguous``
      so the caller can be honest about it rather than silently presenting a guess as
      a confident match.
    """
    deduped, dedup_rejected = _dedup(candidates)
    rejected: list[tuple[str, str]] = list(dedup_rejected)

    if content_type == CONTENT_TYPE_ANY_NEWS:
        if not deduped:
            return ResolverResult(
                None,
                REASON_NONE_ELIGIBLE,
                content_type,
                answered_by,
                len(candidates),
                tuple(rejected),
            )
        return ResolverResult(
            deduped[0],
            REASON_NEWEST,
            content_type,
            answered_by,
            len(candidates),
            tuple(rejected),
        )

    if content_type not in (CONTENT_TYPE_FULL_BROADCAST, CONTENT_TYPE_MAIN_NEWS):
        raise ValueError(f"unknown content_type: {content_type!r}")

    eligible: list[VideoCandidate] = []
    for candidate in deduped:
        if is_short(candidate):
            rejected.append((candidate.video_id, REJECT_SHORTS_EXCLUDED))
            continue
        if is_promo(candidate):
            rejected.append((candidate.video_id, REJECT_PROMO_EXCLUDED))
            continue
        eligible.append(candidate)

    if not eligible:
        return ResolverResult(
            None,
            REASON_NONE_ELIGIBLE,
            content_type,
            answered_by,
            len(candidates),
            tuple(rejected),
        )

    bulletin_matches = [c for c in eligible if matches_bulletin_markers(c)]
    if bulletin_matches:
        return ResolverResult(
            bulletin_matches[0],
            REASON_BULLETIN_MARKER,
            content_type,
            answered_by,
            len(candidates),
            tuple(rejected),
        )

    # Safer fallback (task brief §2: "take the safest configured rule ... never guess
    # quietly"): the newest candidate that already survived the shorts/promo filter,
    # marked ambiguous so nothing downstream mistakes this for a confident title match.
    return ResolverResult(
        eligible[0],
        REASON_NEWEST_NON_SHORT_FALLBACK,
        content_type,
        answered_by,
        len(candidates),
        tuple(rejected),
        ambiguous=True,
    )


def audit_candidates(
    all_candidates: list[VideoCandidate], result: ResolverResult
) -> list[dict[str, Any]]:
    """The full per-candidate accounting for ``NewsResolutionRow.candidates_json``:
    every candidate the provider returned, newest first, with its verdict — accepted
    (the selection), rejected (with its reason), or simply not chosen (any content_type
    where more than one candidate survives filtering, e.g. ``latest_any_news`` with
    several eligible uploads)."""
    rejected_by_id = dict(result.rejected)
    ordered = sorted(all_candidates, key=lambda c: c.published_at, reverse=True)
    out: list[dict[str, Any]] = []
    for candidate in ordered:
        accepted = result.selected is not None and candidate.video_id == result.selected.video_id
        reason = (
            result.reason if accepted else rejected_by_id.get(candidate.video_id, "not_selected")
        )
        out.append(
            {
                "video_id": candidate.video_id,
                "title": candidate.title,
                "published_at": candidate.published_at.isoformat(),
                "accepted": accepted,
                "reason": reason,
            }
        )
    return out


__all__ = [
    "REASON_BULLETIN_MARKER",
    "REASON_NEWEST",
    "REASON_NEWEST_NON_SHORT_FALLBACK",
    "REASON_NONE_ELIGIBLE",
    "REJECT_DUPLICATE",
    "REJECT_PROMO_EXCLUDED",
    "REJECT_SHORTS_EXCLUDED",
    "ResolverResult",
    "audit_candidates",
    "resolve_latest",
]
