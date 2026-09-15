"""B20 req 233: when the voice drops, the words still arrive — as text.

Every path in this system that speaks to the owner already knows how to fail politely. The
wake greeting records `greeting_failure="no_tts_key"` rather than buzzing (B13 req 267);
the briefing stops at the clip that would not play and records how far it got, because a
briefing with a hole in it is worse than a short one (B15 req 281). Both are right, and
both stop one step short of the owner: **the text existed the whole time and went
nowhere.** The greeting sentence was composed, normalised and thrown away. The briefing was
built, normalised, split into clips — and on `no_tts_key` the whole of it was dropped on
the floor, with a four-word code left on a database row the owner has no reason to read.
So an owner whose TTS credit ran out overnight woke to silence, and the morning briefing
they had been promised was nowhere they could find it.

The rule this module makes concrete, for every voice-delivery path: **a failure to SPEAK is
never a failure to TELL.** The notification row is written before any transport is tried
(`app.notifications.service.record` commits first, by design), so the worst that can happen
after this point is that the owner reads it in the inbox instead of hearing it — which is
the entire point.

Never raises. These paths run on the wake path, and an alarm that stopped because the
notification store was busy would be a far worse bug than the one this fixes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.logging import get_logger

logger = get_logger("app.voice.text_fallback")

#: The notification kind every voice-to-text fallback is recorded under, so the inbox can
#: show them as one family and a later surface can count them.
KIND_VOICE_TEXT_FALLBACK = "voice.text_fallback"

#: What the owner is told, before the text itself. Turkish, and it says WHY there is no
#: voice - "sesli okuyamadım" without a reason is the sentence that starts a support
#: conversation with oneself at seven in the morning.
TITLE_TR = "Sesle iletemedim, yazıyla iletiyorum"

#: A notification body is read on a phone. Longer than this is not a notification any more;
#: the full text stays in the row's data for anything that wants it.
MAX_BODY_CHARS = 1200

#: Turkish for the failure codes these paths produce. An unknown code is passed through as
#: itself rather than described - a wrong explanation is worse than a raw token.
REASON_TR: dict[str, str] = {
    "no_tts_key": "ses sağlayıcısı yapılandırılmamış",
    "no_tts_provider": "ses sağlayıcısı yok",
    "audio_store_failed": "ses dosyası saklanamadı",
    "clip_not_played": "cihaz sesi çalmadı",
    "synthesis_failed": "ses üretilemedi",
    "audio_too_large": "ses dosyası fazla büyük",
    "no_device": "cihaz bulunamadı",
}


def describe_reason(reason: str) -> str:
    """The Turkish for a failure code, including the `code:detail` shape these paths use."""
    head = (reason or "").split(":", 1)[0].strip()
    return REASON_TR.get(head, head or "bilinmeyen sebep")


def deliver_as_text(
    db: Any,
    *,
    text: str,
    reason: str,
    what: str,
    group_key: str = "",
    priority: str | None = None,
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Any | None:
    """Write the unspoken words to the notification inbox. Returns the row, or None.

    ``what`` names the thing that could not be spoken, in Turkish and in the accusative
    ("sabah brifingini", "selamlamayı"), because "could not be delivered" without a subject
    is not information.

    None (and nothing written) when there is no text: a notification whose body is empty
    tells the owner that something went wrong and not what, which is the failure mode this
    whole module exists to end, arriving by a different door.
    """
    body = (text or "").strip()
    if not body:
        logger.info("text_fallback_skipped_empty", reason=reason)
        return None
    try:
        from app.notifications.models import PRIORITY_NORMAL
        from app.notifications.service import record

        return record(
            db,
            kind=KIND_VOICE_TEXT_FALLBACK,
            title=f"{TITLE_TR} — {what} ({describe_reason(reason)})",
            body=body[:MAX_BODY_CHARS],
            priority=priority or PRIORITY_NORMAL,
            group_key=group_key,
            data={
                "reason": reason,
                "what": what,
                "spoken": False,
                "chars": len(body),
                "truncated": len(body) > MAX_BODY_CHARS,
                **(data or {}),
            },
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        # The session may be poisoned by the failed write; a caller on the wake path is
        # about to keep using it.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - nothing left to try
            pass
        logger.warning("text_fallback_failed", reason=reason, error=f"{type(exc).__name__}: {exc}")
        return None


__all__ = [
    "KIND_VOICE_TEXT_FALLBACK",
    "MAX_BODY_CHARS",
    "REASON_TR",
    "TITLE_TR",
    "deliver_as_text",
    "describe_reason",
]
