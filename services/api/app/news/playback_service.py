"""Governed playback (docs/M27_LATEST_NEWS_MODE_SPEC.md §5): resolves the latest
eligible video for a source and opens it on the browser worker's OWN dedicated ``news``
profile/context (packages/protocol/BROWSER_CAPABILITIES.md §1/§2, v1.3) — never the
research browser, never the alarm browser, never an existing owner tab.

The ONE place both the voice tool (``news.open``) and the REST route
(``POST /v1/news/open``) reach the device, mirroring exactly how
``app.artifacts.open_service`` is the one place ``artifact.open`` and its REST route
meet (same discipline, same reason: two surfaces must never drift on what "opened"
means). ``browser.session_open`` succeeding is NOT proof a video is playing — this
module classifies the outcome from ``browser.media_play``'s own ``verified``/``playing``
fields, the strongest evidence the worker actually offers, and never upgrades a weaker
result to a stronger claim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.news.models import (
    PLAYBACK_STATUS_CLOSED,
    PLAYBACK_STATUS_FAILED,
    PLAYBACK_STATUS_OPENING,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
    NewsPlaybackContextRow,
)
from app.news.resolve_service import NewsResolveError, resolve_for_source
from app.news.sources_service import NewsSourceView
from app.routines.dispatch import DeviceActionPort

CAPABILITY_SESSION_OPEN = "browser.session_open"
CAPABILITY_MEDIA_PLAY = "browser.media_play"
CAPABILITY_MEDIA_STOP = "browser.media_stop"

#: The dedicated persistent profile Latest News Mode owns (packages/protocol/
#: BROWSER_CAPABILITIES.md §2 v1.3) — never "research" (profile contention with a live
#: research run), never "alarm" (a news video must never interrupt or replace the
#: owner's wake song).
NEWS_BROWSER_PROFILE = "news"
NEWS_BROWSER_SESSION_KIND = "media"

DEFAULT_VOLUME = 0.5
DEFAULT_VERIFY_SECONDS = 3
TIMEOUT_SESSION_OPEN_S = 30.0
TIMEOUT_MEDIA_PLAY_S = 30.0
TIMEOUT_MEDIA_STOP_S = 15.0

ERROR_NO_ELIGIBLE_VIDEO = "no_eligible_video"
ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_PLAYBACK_UNVERIFIED = "playback_unverified"
ERROR_PLAYBACK_FAILED = "playback_failed"
ERROR_NOT_FOUND = "not_found"

_SPEECH: dict[str, str] = {
    ERROR_NO_ELIGIBLE_VIDEO: "Şu anda açabileceğim uygun bir haber videosu bulamadım efendim.",
    ERROR_CAPABILITY_MISSING: "Bu bilgisayardaki ajan tarayıcı açamıyor efendim.",
    ERROR_PLAYBACK_UNVERIFIED: "Videoyu açtım ama gerçekten oynadığını doğrulayamadım efendim.",
    ERROR_PLAYBACK_FAILED: "Haberi açamadım efendim.",
    ERROR_NOT_FOUND: "Böyle bir haber oturumu yok efendim.",
}

#: Device error classes translated to this module's own vocabulary (mirrors
#: app.artifacts.open_service._DEVICE_ERROR_TRANSLATION's own table).
_DEVICE_ERROR_TRANSLATION: dict[str, str] = {
    "no_capable_device": ERROR_CAPABILITY_MISSING,
    "capability_missing": ERROR_CAPABILITY_MISSING,
    "timeout": ERROR_PLAYBACK_FAILED,
    "dependency_unavailable": ERROR_PLAYBACK_FAILED,
    "cancelled": ERROR_PLAYBACK_FAILED,
}


@dataclass(frozen=True, slots=True)
class PlaybackOutcome:
    ok: bool
    context_id: str | None
    status: str
    speech: str
    error_class: str | None = None
    video_id: str | None = None
    title: str | None = None
    published_at: str | None = None
    receipt: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "context_id": self.context_id,
            "status": self.status,
            "speech": self.speech,
            "error_class": self.error_class,
            "video_id": self.video_id,
            "title": self.title,
            "published_at": self.published_at,
            "receipt": self.receipt,
        }


def open_latest_news(
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    source: NewsSourceView,
    content_type: str | None = None,
    volume: float = DEFAULT_VOLUME,
    idempotency_prefix: str | None = None,
    timeout_s: float = TIMEOUT_MEDIA_PLAY_S,
    provider: Any | None = None,
) -> PlaybackOutcome:
    """Resolve the latest eligible video for ``source`` and play it in a fresh, own
    ``news_media_context_id`` context. Never reuses another context's session.
    ``provider`` overrides the source's own default provider (tests only; production
    always resolves through ``source.provider``)."""
    try:
        outcome = resolve_for_source(db, source, content_type=content_type, provider=provider)
    except NewsResolveError as exc:
        return PlaybackOutcome(
            False, None, PLAYBACK_STATUS_FAILED, _SPEECH[ERROR_NO_ELIGIBLE_VIDEO], exc.error_class
        )
    result = outcome.result
    if result.selected is None:
        return PlaybackOutcome(
            False,
            None,
            PLAYBACK_STATUS_FAILED,
            _SPEECH[ERROR_NO_ELIGIBLE_VIDEO],
            ERROR_NO_ELIGIBLE_VIDEO,
        )
    candidate = result.selected

    context_id = uuid.uuid4()
    session_id = f"news-{context_id}"
    now = datetime.now(UTC)
    row = NewsPlaybackContextRow(
        id=context_id,
        news_source_id=source.news_source_id,
        video_id=candidate.video_id,
        channel_id=candidate.channel_id,
        video_title=candidate.title,
        published_at=candidate.published_at,
        content_type=result.content_type,
        session_id=session_id,
        status=PLAYBACK_STATUS_OPENING,
        receipt_json={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()

    if device_action is None:
        row.status = PLAYBACK_STATUS_FAILED
        row.error_class = ERROR_CAPABILITY_MISSING
        db.commit()
        return PlaybackOutcome(
            False,
            str(context_id),
            PLAYBACK_STATUS_FAILED,
            _SPEECH[ERROR_CAPABILITY_MISSING],
            ERROR_CAPABILITY_MISSING,
            candidate.video_id,
            candidate.title,
            candidate.published_at.isoformat(),
        )

    prefix = idempotency_prefix or f"news-open:{context_id}"
    open_result = device_action.run(
        capability=CAPABILITY_SESSION_OPEN,
        payload={
            "session_id": session_id,
            "profile": NEWS_BROWSER_PROFILE,
            "session_kind": NEWS_BROWSER_SESSION_KIND,
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
            "channel": "chrome",
        },
        idempotency_key=f"{prefix}:session_open",
        timeout_s=TIMEOUT_SESSION_OPEN_S,
    )
    # browser.session_open succeeding is NOT proof a video is playing (task brief §5) —
    # it only means a browser exists; the honest evidence is media_play's own verified
    # field, checked below. A session_open failure IS decisive though: nothing else can
    # run without a session.
    if not open_result.ok:
        translated = _DEVICE_ERROR_TRANSLATION.get(open_result.error_class, ERROR_PLAYBACK_FAILED)
        row.status = PLAYBACK_STATUS_FAILED
        row.error_class = translated
        row.error_message = open_result.message[:1000] if open_result.message else None
        db.commit()
        return PlaybackOutcome(
            False,
            str(context_id),
            PLAYBACK_STATUS_FAILED,
            _SPEECH[translated],
            translated,
            candidate.video_id,
            candidate.title,
            candidate.published_at.isoformat(),
        )

    play_result = device_action.run(
        capability=CAPABILITY_MEDIA_PLAY,
        payload={
            "session_id": session_id,
            "url": candidate.url,
            "volume": float(volume),
            "verify_seconds": DEFAULT_VERIFY_SECONDS,
        },
        idempotency_key=f"{prefix}:media_play",
        timeout_s=timeout_s,
    )
    if not play_result.ok:
        translated = _DEVICE_ERROR_TRANSLATION.get(play_result.error_class, ERROR_PLAYBACK_FAILED)
        row.status = PLAYBACK_STATUS_FAILED
        row.error_class = translated
        row.error_message = play_result.message[:1000] if play_result.message else None
        db.commit()
        return PlaybackOutcome(
            False,
            str(context_id),
            PLAYBACK_STATUS_FAILED,
            _SPEECH[translated],
            translated,
            candidate.video_id,
            candidate.title,
            candidate.published_at.isoformat(),
        )

    receipt = play_result.result or {}
    row.receipt_json = receipt
    row.opened_at = datetime.now(UTC)
    # Honest classification (task brief §5: "classify honestly rather than upgrading
    # it"): `verified` is the browser's own read-back that currentTime actually
    # advanced — NOT that play() merely returned without raising.
    if receipt.get("verified") is True:
        row.status = PLAYBACK_STATUS_PLAYING
        title = str(receipt.get("title") or candidate.title)
        speech = f"{title} açıldı efendim."
        db.commit()
        return PlaybackOutcome(
            True,
            str(context_id),
            PLAYBACK_STATUS_PLAYING,
            speech,
            None,
            candidate.video_id,
            title,
            candidate.published_at.isoformat(),
            receipt,
        )
    row.status = PLAYBACK_STATUS_UNVERIFIED
    row.error_class = ERROR_PLAYBACK_UNVERIFIED
    db.commit()
    return PlaybackOutcome(
        False,
        str(context_id),
        PLAYBACK_STATUS_UNVERIFIED,
        _SPEECH[ERROR_PLAYBACK_UNVERIFIED],
        ERROR_PLAYBACK_UNVERIFIED,
        candidate.video_id,
        candidate.title,
        candidate.published_at.isoformat(),
        receipt,
    )


def close_playback(
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    context_id: uuid.UUID,
    idempotency_key: str | None = None,
    timeout_s: float = TIMEOUT_MEDIA_STOP_S,
) -> PlaybackOutcome:
    """Cleanup closes ONLY this context's session (task brief §5) — never another
    context's, never the research or alarm browser (a structurally different profile
    entirely, not merely a different session id)."""
    row = db.get(NewsPlaybackContextRow, context_id)
    if row is None:
        return PlaybackOutcome(
            False, None, PLAYBACK_STATUS_FAILED, _SPEECH[ERROR_NOT_FOUND], ERROR_NOT_FOUND
        )
    if row.status == PLAYBACK_STATUS_CLOSED:
        return PlaybackOutcome(
            True,
            str(row.id),
            PLAYBACK_STATUS_CLOSED,
            "Zaten kapalı efendim.",
            None,
            row.video_id,
            row.video_title,
        )
    if device_action is None:
        return PlaybackOutcome(
            False,
            str(row.id),
            row.status,
            _SPEECH[ERROR_CAPABILITY_MISSING],
            ERROR_CAPABILITY_MISSING,
            row.video_id,
            row.video_title,
        )
    key = idempotency_key or f"news-close:{row.id}"
    stop_result = device_action.run(
        capability=CAPABILITY_MEDIA_STOP,
        payload={"session_id": row.session_id},
        idempotency_key=key,
        timeout_s=timeout_s,
    )
    row.closed_at = datetime.now(UTC)
    row.status = PLAYBACK_STATUS_CLOSED
    db.commit()
    speech = "Haberi kapattım efendim." if stop_result.ok else "Kapatmayı denedim efendim."
    return PlaybackOutcome(
        stop_result.ok,
        str(row.id),
        PLAYBACK_STATUS_CLOSED,
        speech,
        None if stop_result.ok else (stop_result.error_class or ERROR_PLAYBACK_FAILED),
        row.video_id,
        row.video_title,
    )


__all__ = [
    "DEFAULT_VERIFY_SECONDS",
    "DEFAULT_VOLUME",
    "ERROR_CAPABILITY_MISSING",
    "ERROR_NOT_FOUND",
    "ERROR_NO_ELIGIBLE_VIDEO",
    "ERROR_PLAYBACK_FAILED",
    "ERROR_PLAYBACK_UNVERIFIED",
    "NEWS_BROWSER_PROFILE",
    "NEWS_BROWSER_SESSION_KIND",
    "PlaybackOutcome",
    "close_playback",
    "open_latest_news",
]
