"""The ONE place owner-requested playback reaches the device (ADR-0112).

Both surfaces that will ever start a song -- the ``media.play`` voice tool and,
later, a REST route -- come through here, the same discipline
``app.news.playback_service`` and ``app.artifacts.open_service`` state: two
surfaces must never drift on what "playing" means.

Three device calls, in this order, on ONE ``media`` session:

1. ``browser.session_open``  -- the OWNER's own Chrome (profile ``owner``,
   contract v1.4), attached through the enrollment they authorised, so the song
   plays in the browser they are signed into. When no browser is enrolled the
   worker answers ``capability_missing`` and this falls back to ``isolated`` -- a
   blank context that works and is signed into nothing -- and SAYS so, because a
   consent wall instead of a song is not a surprise the owner should have to
   diagnose. Never ``research`` (a live research run may hold it, and the worker
   refuses media there), never ``alarm`` (an ad-hoc song is not the wake song),
   never ``news``.
2. ``browser.search``        -- the device's own web search. A media session
   carries the same risk classes as a research one (READ + NAVIGATE, and
   ``browser.search`` is NAVIGATE), so this needs no second session.
3. ``browser.media_play``    -- and its ``verified`` field is the only evidence
   this module will call "playing".

**A weaker result is never reported as a stronger one.** ``session_open``
succeeding means a browser exists. A search returning results means the engine
answered. Only ``media_play``'s own ``verified`` means the page really moved,
and when it is false the row says ``unverified`` and the owner is told exactly
that -- the failure mode this repository has now hit twice, most recently on
2026-09-09 when a typing run reported "I could not verify the text" for a plan
that never reached the keyboard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.media.models import (
    PLAYBACK_LIVE_STATUSES,
    PLAYBACK_STATUS_CLOSED,
    PLAYBACK_STATUS_FAILED,
    PLAYBACK_STATUS_PLAYING,
    PLAYBACK_STATUS_UNVERIFIED,
    OwnerMediaPlaybackRow,
)
from app.media.resolve import Candidate, pick_video, search_query
from app.routines.dispatch import DeviceActionPort

logger = get_logger("app.media.playback_service")

CAPABILITY_SESSION_OPEN: Final = "browser.session_open"
CAPABILITY_SESSION_CLOSE: Final = "browser.session_close"
CAPABILITY_SEARCH: Final = "browser.search"
CAPABILITY_TAB_NEW: Final = "browser.tab_new"
CAPABILITY_MEDIA_PLAY: Final = "browser.media_play"
CAPABILITY_MEDIA_STOP: Final = "browser.media_stop"

#: The owner's OWN Chrome (contract v1.4, ADR-0113): their tabs, their logins, their
#: extensions. Asked for twice on 2026-09-10 after being told in concrete terms what it
#: means, and gated on the device by an enrollment record only the owner can create.
OWNER_ATTACHED_PROFILE: Final = "owner"
#: The fallback when no browser is enrolled: a fresh non-persistent context. It works,
#: and it is signed into nothing -- so a request that lands here is ANSWERED DIFFERENTLY
#: rather than silently, because "I played it in a blank browser you are not signed into"
#: and "I played it in yours" are not the same thing and the owner can hear the
#: difference the moment a consent wall appears instead of the song.
OWNER_MEDIA_PROFILE: Final = "isolated"
OWNER_MEDIA_SESSION_KIND: Final = "media"
#: ``session_id`` convention, mirroring ``news-<id>`` and ``alarm-<id>``.
SESSION_PREFIX: Final = "owner-media-"

DEFAULT_VOLUME: Final = 0.5
DEFAULT_VERIFY_SECONDS: Final = 3
SEARCH_MAX_RESULTS: Final = 10
SEARCH_LOCALE: Final = "tr-TR"

TIMEOUT_SESSION_OPEN_S: Final = 30.0
TIMEOUT_SEARCH_S: Final = 45.0
TIMEOUT_MEDIA_PLAY_S: Final = 30.0
TIMEOUT_MEDIA_STOP_S: Final = 15.0

ERROR_NO_DEVICE: Final = "no_capable_device"
ERROR_CAPABILITY_MISSING: Final = "capability_missing"
ERROR_NOTHING_REQUESTED: Final = "nothing_requested"
ERROR_SEARCH_FAILED: Final = "search_failed"
ERROR_NO_VIDEO_FOUND: Final = "no_video_found"
ERROR_PLAYBACK_FAILED: Final = "playback_failed"
ERROR_PLAYBACK_UNVERIFIED: Final = "playback_unverified"
ERROR_NOTHING_PLAYING: Final = "nothing_playing"
#: The owner DID enrol a browser and that browser is gone -- which is what happens every
#: time Chrome restarts without the debugging port. Its own class, because the answer is
#: a specific one-command fix and not "something went wrong": telling them it was never
#: set up would send them looking for something they already did.
ERROR_OWNER_BROWSER_GONE: Final = "owner_browser_unreachable"

#: One Turkish sentence per outcome. They live here, next to the code that
#: decides which one is true, rather than in the tool -- the tool reads a
#: sentence out, it does not get to choose a kinder one.
SPEECH: Final[dict[str, str]] = {
    ERROR_NO_DEVICE: "Bilgisayarınıza şu anda ulaşamıyorum efendim; açamadım.",
    ERROR_CAPABILITY_MISSING: (
        "Bilgisayarınızdaki tarayıcı bu işi henüz üstlenmiyor efendim; açamadım."
    ),
    ERROR_NOTHING_REQUESTED: "Neyi açayım efendim?",
    ERROR_SEARCH_FAILED: "Aramayı yapamadım efendim; bir daha deneyeyim mi?",
    ERROR_NO_VIDEO_FOUND: "Bunu bulamadım efendim; başka bir isimle deneyelim mi?",
    ERROR_PLAYBACK_FAILED: "Videoyu açamadım efendim.",
    ERROR_PLAYBACK_UNVERIFIED: (
        "Açtım efendim ama çaldığını doğrulayamadım; ekranda görüyor musunuz?"
    ),
    ERROR_NOTHING_PLAYING: "Şu anda açtığım bir şey yok efendim.",
}

#: The device's vocabulary, translated once, here.
_DEVICE_ERROR_TRANSLATION: Final[dict[str, str]] = {
    "no_capable_device": ERROR_NO_DEVICE,
    "capability_missing": ERROR_CAPABILITY_MISSING,
    "dependency_unavailable": ERROR_CAPABILITY_MISSING,
}


@dataclass(frozen=True, slots=True)
class PlaybackOutcome:
    ok: bool
    playback_id: str | None
    status: str
    speech: str
    error_class: str | None = None
    video_id: str | None = None
    title: str = ""
    url: str | None = None
    #: WHICH browser this happened in -- the owner's own, or the blank fallback.
    profile: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "playback_id": self.playback_id,
            "status": self.status,
            "error_class": self.error_class,
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "profile": self.profile,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _translate(error_class: str, fallback: str) -> str:
    return _DEVICE_ERROR_TRANSLATION.get(error_class, fallback)


def _fail(
    db: Session,
    row: OwnerMediaPlaybackRow | None,
    error_class: str,
    message: str | None = None,
) -> PlaybackOutcome:
    if row is not None:
        row.status = PLAYBACK_STATUS_FAILED
        row.error_class = error_class
        row.error_message = message[:1000] if message else None
        row.updated_at = _now()
        db.commit()
    return PlaybackOutcome(
        ok=False,
        playback_id=str(row.id) if row is not None else None,
        status=PLAYBACK_STATUS_FAILED,
        speech=SPEECH[error_class],
        error_class=error_class,
        video_id=row.video_id if row is not None else None,
        title=row.video_title if row is not None else "",
        url=row.url if row is not None else None,
    )


def live_playback(db: Session) -> OwnerMediaPlaybackRow | None:
    """The most recent playback that may still be on screen, or ``None``."""
    stmt = (
        select(OwnerMediaPlaybackRow)
        .where(OwnerMediaPlaybackRow.status.in_(PLAYBACK_LIVE_STATUSES))
        .order_by(OwnerMediaPlaybackRow.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def play_request(
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    request_text: str,
    volume: float = DEFAULT_VOLUME,
    verify_seconds: int = DEFAULT_VERIFY_SECONDS,
) -> PlaybackOutcome:
    """Search for what the owner named and play the first real video.

    A previous playback that is still live is stopped FIRST. Two songs at once
    is not a thing anyone asked for, and leaving an orphan isolated Chrome
    context behind on every request is how a machine ends up with nine of them.
    """
    spoken = " ".join(str(request_text or "").split())
    if not spoken:
        return PlaybackOutcome(
            ok=False,
            playback_id=None,
            status=PLAYBACK_STATUS_FAILED,
            speech=SPEECH[ERROR_NOTHING_REQUESTED],
            error_class=ERROR_NOTHING_REQUESTED,
        )
    if device_action is None:
        return PlaybackOutcome(
            ok=False,
            playback_id=None,
            status=PLAYBACK_STATUS_FAILED,
            speech=SPEECH[ERROR_NO_DEVICE],
            error_class=ERROR_NO_DEVICE,
        )

    previous = live_playback(db)
    if previous is not None:
        stop_playback(db, device_action, row=previous)

    query = search_query(spoken)
    row = OwnerMediaPlaybackRow(
        id=uuid.uuid4(),
        request_text=spoken[:4000],
        query=query,
        session_id="",
        status=PLAYBACK_LIVE_STATUSES[0],
        created_at=_now(),
        updated_at=_now(),
    )
    row.session_id = f"{SESSION_PREFIX}{row.id}"
    db.add(row)
    db.commit()

    prefix = f"owner-media:{row.id}"
    profile, open_result, fallback_reason = _open_session(device_action, row.session_id, prefix)
    if not open_result.ok:
        translated = _translate(open_result.error_class, ERROR_PLAYBACK_FAILED)
        return _fail(db, row, translated, open_result.message)

    if profile == OWNER_ATTACHED_PROFILE:
        # A NEW TAB, before anything navigates. Attaching hands this session the
        # browser's FIRST EXISTING tab (``ExistingSessionBackend`` takes
        # ``context.pages[0]``), so searching would drive whatever the owner
        # happens to have open there to Google and then to YouTube -- their work,
        # gone, to play a song. ``tab_new`` opens one and selects it, so every
        # step after this happens somewhere that did not exist a moment ago.
        #
        # Not needed for ``isolated``: that context is ours and starts blank.
        tab = device_action.run(
            capability=CAPABILITY_TAB_NEW,
            payload={"session_id": row.session_id},
            idempotency_key=f"{prefix}:tab_new",
            timeout_s=TIMEOUT_SESSION_OPEN_S,
        )
        if not tab.ok:
            # Refuse rather than fall back onto the owner's tab.
            _close_session(device_action, row.session_id, prefix)
            translated = _translate(tab.error_class, ERROR_PLAYBACK_FAILED)
            return _fail(db, row, translated, tab.message)

    search_result = device_action.run(
        capability=CAPABILITY_SEARCH,
        payload={
            "session_id": row.session_id,
            "query": query,
            "engine": "auto",
            "max_results": SEARCH_MAX_RESULTS,
            "locale": SEARCH_LOCALE,
        },
        idempotency_key=f"{prefix}:search",
        timeout_s=TIMEOUT_SEARCH_S,
    )
    if not search_result.ok:
        _close_session(device_action, row.session_id, prefix)
        translated = _translate(search_result.error_class, ERROR_SEARCH_FAILED)
        return _fail(db, row, translated, search_result.message)

    candidate: Candidate | None = pick_video((search_result.result or {}).get("results"))
    if candidate is None:
        _close_session(device_action, row.session_id, prefix)
        return _fail(db, row, ERROR_NO_VIDEO_FOUND)

    row.video_id = candidate.video_id
    row.video_title = candidate.title[:500]
    row.url = candidate.url
    row.updated_at = _now()
    db.commit()

    play_result = device_action.run(
        capability=CAPABILITY_MEDIA_PLAY,
        payload={
            "session_id": row.session_id,
            "url": candidate.url,
            "volume": float(volume),
            "verify_seconds": int(verify_seconds),
        },
        idempotency_key=f"{prefix}:media_play",
        timeout_s=TIMEOUT_MEDIA_PLAY_S,
    )
    if not play_result.ok:
        _close_session(device_action, row.session_id, prefix)
        return _fail(
            db, row, _translate(play_result.error_class, ERROR_PLAYBACK_FAILED), play_result.message
        )

    result = play_result.result or {}
    row.receipt_json = dict(result)
    verified = bool(result.get("verified"))
    row.status = PLAYBACK_STATUS_PLAYING if verified else PLAYBACK_STATUS_UNVERIFIED
    row.error_class = None if verified else ERROR_PLAYBACK_UNVERIFIED
    # The worker's own page title beats the SERP's, which is the engine's
    # rendering of it; keep the SERP title only when the page did not say.
    page_title = result.get("title")
    if isinstance(page_title, str) and page_title.strip():
        row.video_title = page_title.strip()[:500]
    row.updated_at = _now()
    db.commit()

    logger.info(
        "owner_media_played",
        playback_id=str(row.id),
        verified=verified,
        video_id=candidate.video_id,
    )
    if not verified:
        return PlaybackOutcome(
            ok=False,
            playback_id=str(row.id),
            status=PLAYBACK_STATUS_UNVERIFIED,
            speech=SPEECH[ERROR_PLAYBACK_UNVERIFIED],
            error_class=ERROR_PLAYBACK_UNVERIFIED,
            video_id=candidate.video_id,
            title=row.video_title,
            url=candidate.url,
            profile=profile,
        )
    return PlaybackOutcome(
        ok=True,
        playback_id=str(row.id),
        status=PLAYBACK_STATUS_PLAYING,
        speech=_playing_speech(row.video_title, profile, fallback_reason),
        video_id=candidate.video_id,
        title=row.video_title,
        url=candidate.url,
        profile=profile,
    )


def _open_session(device_action: DeviceActionPort, session_id: str, prefix: str) -> tuple[str, Any]:
    """The owner's own Chrome first; the blank one only if nothing is enrolled.

    ``capability_missing`` is the worker's answer when no browser is enrolled for
    attach -- a configuration fact, not a failure of this request -- so it is the
    ONE error that earns a second attempt. Every other refusal is real and is
    returned as it came: a Chrome that has died, a port that stopped answering
    and an endpoint that was revoked must not be quietly worked around, or the
    owner would be told a song is playing in a browser they never opened.

    The risk classes stay READ + NAVIGATE even in the owner's own browser, which
    is narrower than that profile's default. Playing a video needs nothing more,
    and a session may always be narrowed; the wider grant the owner authorised
    belongs to the tools that actually need to click and type.
    """
    payload = {
        "session_id": session_id,
        "session_kind": OWNER_MEDIA_SESSION_KIND,
        "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
        "channel": "chrome",
    }
    attached = device_action.run(
        capability=CAPABILITY_SESSION_OPEN,
        payload={**payload, "profile": OWNER_ATTACHED_PROFILE},
        idempotency_key=f"{prefix}:session_open",
        timeout_s=TIMEOUT_SESSION_OPEN_S,
    )
    if attached.ok:
        return OWNER_ATTACHED_PROFILE, attached, ""
    # Two ways the owner's browser is not there, and both are configuration facts they
    # can fix in one command -- so both fall back rather than leaving them with silence:
    #
    #   capability_missing      nothing was ever enrolled
    #   dependency_unavailable  something WAS enrolled and that browser is gone, which is
    #                           what happens every time Chrome restarts without the port
    #
    # The first version refused outright on the second, reasoning that a dead endpoint
    # must not be silently worked around. The reasoning was right and the conclusion was
    # wrong: on 2026-09-10 the owner asked for a song, heard "tarayıcıyı açamadı", and had
    # no way to know that re-authorising takes one command. Falling back is fine. Falling
    # back QUIETLY is what must not happen, and the sentence the owner hears is where that
    # difference lives.
    if attached.error_class not in ("capability_missing", "dependency_unavailable"):
        return OWNER_ATTACHED_PROFILE, attached, ""
    logger.info(
        "owner_media_attach_unavailable",
        error_class=attached.error_class,
        detail=attached.message[:200] if attached.message else "",
    )
    fallback = device_action.run(
        capability=CAPABILITY_SESSION_OPEN,
        payload={**payload, "profile": OWNER_MEDIA_PROFILE},
        idempotency_key=f"{prefix}:session_open_fallback",
        timeout_s=TIMEOUT_SESSION_OPEN_S,
    )
    reason = (
        ERROR_OWNER_BROWSER_GONE
        if attached.error_class == "dependency_unavailable"
        else ERROR_CAPABILITY_MISSING
    )
    return OWNER_MEDIA_PROFILE, fallback, reason


def _playing_speech(title: str, profile: str, reason: str = "") -> str:
    """Say WHAT is playing, not just that something is.

    The owner named a song from memory; hearing the title back is how they learn
    the machine heard the same one, and it is what makes "hayır, diğeri" a
    sentence they can say.

    And WHERE, when it is not where they expect. A blank browser is signed into
    nothing, so what they will actually see is a consent wall rather than their
    own YouTube; saying so costs one clause and saves them diagnosing it.
    """
    clean = " ".join((title or "").split())
    where = ""
    if profile != OWNER_ATTACHED_PROFILE:
        if reason == ERROR_OWNER_BROWSER_GONE:
            # They DID authorise one; that browser is simply gone. Telling them "we
            # never set it up" would send them looking for something they already did.
            where = (
                " Kendi tarayıcınıza ulaşamadım efendim, ayrı bir pencerede açtım; "
                "Chrome yeniden başlamış olmalı, yetkilendirmeyi tazelememiz yeter."
            )
        else:
            where = (
                " Kendi tarayıcınıza bağlı değilim, ayrı bir pencerede açtım; "
                "bağlanmamı isterseniz Chrome yetkilendirmesini bir kez yapmamız gerekiyor."
            )
    if not clean:
        return f"Açtım efendim, çalıyor.{where}"
    return f"Açtım efendim, çalıyor: {clean[:120]}.{where}"


def _close_session(device_action: DeviceActionPort, session_id: str, prefix: str) -> None:
    """Best effort. A session left open is a stray Chrome, not a lost answer, so
    a failure here is logged and never turned into the owner's answer."""
    try:
        device_action.run(
            capability=CAPABILITY_SESSION_CLOSE,
            payload={"session_id": session_id},
            idempotency_key=f"{prefix}:session_close",
            timeout_s=TIMEOUT_MEDIA_STOP_S,
        )
    except Exception:  # noqa: BLE001 - cleanup must never replace the real outcome
        logger.warning("owner_media_session_close_failed", session_id=session_id)


def stop_playback(
    db: Session,
    device_action: DeviceActionPort | None,
    *,
    row: OwnerMediaPlaybackRow | None = None,
) -> PlaybackOutcome:
    """Stop the playback this service started -- that session and no other."""
    target = row if row is not None else live_playback(db)
    if target is None:
        return PlaybackOutcome(
            ok=False,
            playback_id=None,
            status=PLAYBACK_STATUS_CLOSED,
            speech=SPEECH[ERROR_NOTHING_PLAYING],
            error_class=ERROR_NOTHING_PLAYING,
        )
    if device_action is None:
        return _fail(db, target, ERROR_NO_DEVICE)

    result = device_action.run(
        capability=CAPABILITY_MEDIA_STOP,
        payload={"session_id": target.session_id},
        idempotency_key=f"owner-media:{target.id}:media_stop",
        timeout_s=TIMEOUT_MEDIA_STOP_S,
    )
    # ``media_stop`` pauses AND closes the session (contract §3b), so a failure
    # still gets a session_close attempt rather than leaving Chrome running.
    if not result.ok:
        _close_session(device_action, target.session_id, f"owner-media:{target.id}")
    target.status = PLAYBACK_STATUS_CLOSED
    target.receipt_json = dict(result.result or {})
    target.updated_at = _now()
    db.commit()
    return PlaybackOutcome(
        ok=True,
        playback_id=str(target.id),
        status=PLAYBACK_STATUS_CLOSED,
        speech="Durdurdum efendim.",
        video_id=target.video_id,
        title=target.video_title,
        url=target.url,
    )


__all__ = [
    "CAPABILITY_MEDIA_PLAY",
    "CAPABILITY_MEDIA_STOP",
    "CAPABILITY_SEARCH",
    "CAPABILITY_TAB_NEW",
    "CAPABILITY_SESSION_OPEN",
    "DEFAULT_VOLUME",
    "ERROR_NOTHING_PLAYING",
    "ERROR_NOTHING_REQUESTED",
    "ERROR_NO_DEVICE",
    "ERROR_NO_VIDEO_FOUND",
    "ERROR_OWNER_BROWSER_GONE",
    "ERROR_PLAYBACK_FAILED",
    "ERROR_PLAYBACK_UNVERIFIED",
    "ERROR_SEARCH_FAILED",
    "OWNER_ATTACHED_PROFILE",
    "OWNER_MEDIA_PROFILE",
    "OWNER_MEDIA_SESSION_KIND",
    "SESSION_PREFIX",
    "SPEECH",
    "PlaybackOutcome",
    "live_playback",
    "play_request",
    "stop_playback",
]
