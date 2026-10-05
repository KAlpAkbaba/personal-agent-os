"""JARVIS phones the owner and tells him, in Turkish, what happened.

One number only. ``OwnerCaller`` has exactly one destination - the owner's, from settings -
and a call to anything else is refused before the provider is asked (``CallRefused``). The
Twilio trial can only reach verified numbers anyway; this is the rule that still holds the
day the account is upgraded.

What a call says is synthesised by OUR TTS (the alarm greeting's provider, so the same voice
the owner hears every morning) into a short WAV that the Cloud Core serves once, from an
unguessable url that dies after one fetch or ten minutes (``AUDIO_TTL``). When our TTS cannot
speak - no key, the offline fake (a 110 Hz tone is never played to a person as speech), an
error, or no public https origin for Twilio to fetch from - the TwiML falls back to Twilio's
own ``<Say language="tr-TR">``.

Every call is a ledger row (subsystem ``telephony``) with its reason, Twilio's call SID and,
once known, the status Twilio reports. Not answered (busy / no-answer / failed / canceled)
means ONE retry five minutes after the first call, and when that one is not answered either,
a notification - never a third call. The hourly cap is counted from the ledger, so a restart
does not reset it. Pending retries are process state: a restart between a call and its
five-minute check loses that one retry, and the ledger still says the call was placed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alarms.audio_store import AudioStore
from app.ledger import service as ledger
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.logging import get_logger
from app.notifications import service as notifications
from app.notifications.models import PRIORITY_NORMAL, PRIORITY_URGENT, NotificationRow
from app.telephony import policy
from app.telephony.provider import (
    ANSWERED,
    IN_FLIGHT,
    NOT_ANSWERED,
    TelephonyError,
    TelephonyProvider,
)
from app.telephony.twiml import build_twiml

logger = get_logger("app.telephony.service")

AUDIO_TTL: Final[timedelta] = timedelta(minutes=10)
RETRY_AFTER: Final[timedelta] = timedelta(minutes=5)
#: A call Twilio still reports as in flight this long after it was placed is closed as
#: "unknown" - the phone rings for 30 s, so this only happens when Twilio stops answering.
GIVE_UP_AFTER: Final[timedelta] = timedelta(minutes=30)
#: How far back the event scan looks for important notifications nobody has called about.
NOTIFICATION_LOOKBACK: Final[timedelta] = timedelta(minutes=15)
AUDIO_PATH: Final[str] = "/v1/telephony/audio/"
SOURCE: Final[str] = "telephony"
GREETING: Final[str] = "Merhaba, ben JARVIS."
TEST_MESSAGE: Final[str] = (
    "Bu bir deneme araması. Önemli bir şey olduğunda seni böyle arayıp anlatacağım."
)

SessionScope = Callable[[], contextlib.AbstractContextManager[Session]]
Speak = Callable[[str], tuple[bytes, str]]

_NUMBER_NOISE = re.compile(r"[\s\-().]")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class CallRefused(ValueError):
    """A call to a number that is not the configured owner number."""


class TelephonyNotConfigured(RuntimeError):
    """No provider credentials, no owner number or no Twilio number."""


def normalize_number(value: str) -> str:
    """E.164 without the spaces, dashes and brackets people write; ``00`` becomes ``+``.
    Empty for anything that is not a phone number after that."""
    cleaned = _NUMBER_NOISE.sub("", value or "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    return cleaned if _E164.match(cleaned) else ""


def mask_number(number: str) -> str:
    if len(number) < 6:
        return "***"
    return number[:3] + "*" * (len(number) - 5) + number[-2:]


@dataclasses.dataclass(frozen=True, slots=True)
class CallOutcome:
    #: placed | skipped
    status: str
    reason: str
    call_sid: str | None = None
    #: audio (our TTS) | say (Twilio's tr-TR voice)
    spoken: str | None = None
    attempt: int = 1

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(slots=True)
class _Pending:
    sid: str
    kind: str
    message: str
    attempt: int
    placed_at: datetime


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class OwnerCaller:
    def __init__(
        self,
        *,
        provider: TelephonyProvider | None,
        owner_number: str,
        from_number: str,
        public_base_url: str,
        audio_store: AudioStore,
        speak: Speak | None,
        session_scope: SessionScope,
        max_per_hour: int = policy.DEFAULT_MAX_PER_HOUR,
        retry_after: timedelta = RETRY_AFTER,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self.owner_number = normalize_number(owner_number)
        self.from_number = normalize_number(from_number)
        self._base_url = public_base_url.rstrip("/")
        self._audio_store = audio_store
        self._speak = speak
        self._session_scope = session_scope
        self.max_per_hour = max_per_hour
        self._retry_after = retry_after
        self._clock = clock
        self._pending: dict[str, _Pending] = {}
        #: The last sentence a call spoke (the settings page and the tests read it).
        self.last_message: str = ""

    # ------------------------------------------------------------------ state

    @property
    def configured(self) -> bool:
        return bool(
            self._provider is not None
            and self._provider.configured()
            and self.owner_number
            and self.from_number
        )

    @property
    def speaks_with(self) -> str:
        return "audio" if self._speak is not None and self._can_serve_audio() else "say"

    def _can_serve_audio(self) -> bool:
        return self._base_url.startswith("https://")

    @property
    def pending(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------------ calling

    def call(
        self,
        kind: str,
        message: str,
        *,
        to: str | None = None,
        origin: str | None = None,
        now: datetime | None = None,
    ) -> CallOutcome:
        """Decide and, when the policy says so, ring the owner. Raises ``CallRefused`` for any
        other number, ``TelephonyNotConfigured`` without credentials or numbers, and
        ``TelephonyError`` when the provider refuses the call."""
        if not self.configured:
            raise TelephonyNotConfigured("telephony is not configured")
        moment = _aware(now or self._clock())
        if to is not None and normalize_number(to) != self.owner_number:
            self._ledger(
                event_type=v.EVENT_TYPE_TELEPHONY_CALL_REFUSED,
                status=v.STATUS_FAILED,
                severity=v.SEVERITY_WARNING,
                summary="Sahibin numarası olmayan bir numaraya arama reddedildi.",
                source_ref=f"refused:{uuid.uuid4()}",
                moment=moment,
                detail={"reason": kind, "to": mask_number(normalize_number(to) or to)},
            )
            logger.warning("telephony_call_refused", reason=kind)
            raise CallRefused("only the owner's number can be called")
        decision = policy.decide(
            kind,
            now=moment,
            calls_in_last_hour=self._calls_in_last_hour(moment),
            max_per_hour=self.max_per_hour,
        )
        if not decision.call:
            self._ledger(
                event_type=v.EVENT_TYPE_TELEPHONY_CALL_SKIPPED,
                status=v.STATUS_SKIPPED,
                summary=f"Arama yapılmadı ({decision.reason}).",
                source_ref=origin or f"skipped:{uuid.uuid4()}",
                moment=moment,
                detail={"reason": kind, "decision": decision.reason},
            )
            logger.info("telephony_call_skipped", reason=kind, decision=decision.reason)
            return CallOutcome(status="skipped", reason=decision.reason)
        return self._place(kind, message, attempt=1, moment=moment, origin=origin)

    def _compose(self, kind: str, message: str) -> str:
        rule = policy.RULES.get(kind)
        opening = rule.opening if rule is not None else ""
        return " ".join(part for part in (GREETING, opening, message.strip()) if part)

    def _audio_url(self, text: str, moment: datetime) -> str | None:
        if self._speak is None or not self._can_serve_audio():
            return None
        try:
            audio, content_type = self._speak(text)
        except Exception as exc:  # noqa: BLE001 - Twilio's own Turkish voice is the fallback
            logger.warning("telephony_tts_failed", error=type(exc).__name__)
            return None
        handle = self._audio_store.put(audio, content_type=content_type, now=moment)
        return f"{self._base_url}{AUDIO_PATH}{handle.token}"

    def _place(
        self, kind: str, message: str, *, attempt: int, moment: datetime, origin: str | None
    ) -> CallOutcome:
        assert self._provider is not None
        text = self._compose(kind, message)
        audio_url = self._audio_url(text, moment)
        spoken = "audio" if audio_url else "say"
        twiml = build_twiml(text=text, audio_url=audio_url)
        try:
            placed = self._provider.place_call(
                to=self.owner_number, from_=self.from_number, twiml=twiml
            )
        except TelephonyError as exc:
            self._ledger(
                event_type=v.EVENT_TYPE_TELEPHONY_CALL_FAILED,
                status=v.STATUS_FAILED,
                severity=v.SEVERITY_WARNING,
                summary="Arama sağlayıcısı aramayı kabul etmedi.",
                source_ref=f"failed:{uuid.uuid4()}",
                moment=moment,
                detail={"reason": kind, "attempt": attempt, "error": str(exc)},
            )
            logger.warning("telephony_call_failed", reason=kind, error=str(exc))
            raise
        self.last_message = text
        self._pending[placed.sid] = _Pending(
            sid=placed.sid, kind=kind, message=message, attempt=attempt, placed_at=moment
        )
        self._ledger(
            event_type=v.EVENT_TYPE_TELEPHONY_CALL_PLACED,
            status=v.STATUS_STARTED,
            severity=v.SEVERITY_CRITICAL if policy.is_critical(kind) else v.SEVERITY_NOTICE,
            summary=f"Sahip telefonla arandı: {text}",
            source_ref=origin if (origin and attempt == 1) else f"call:{placed.sid}:placed",
            moment=moment,
            detail={
                "reason": kind,
                "call_sid": placed.sid,
                "call_status": placed.status,
                "attempt": attempt,
                "spoken": spoken,
                "to": mask_number(self.owner_number),
            },
        )
        logger.info(
            "telephony_call_placed",
            reason=kind,
            call_sid=placed.sid,
            attempt=attempt,
            spoken=spoken,
        )
        return CallOutcome(
            status="placed", reason=kind, call_sid=placed.sid, spoken=spoken, attempt=attempt
        )

    # ------------------------------------------------------------------ after the call

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        """Five minutes after a call: what happened to it, and the one retry it may get."""
        moment = _aware(now or self._clock())
        counts = {"answered": 0, "retried": 0, "notified": 0, "unknown": 0}
        for pending in list(self._pending.values()):
            if moment - pending.placed_at < self._retry_after:
                continue
            try:
                status = self._provider.call_status(pending.sid) if self._provider else ""
            except TelephonyError as exc:
                logger.warning("telephony_status_failed", call_sid=pending.sid, error=str(exc))
                status = ""
            if status in IN_FLIGHT or not status:
                if moment - pending.placed_at < GIVE_UP_AFTER:
                    continue
                status = status or "unknown"
            del self._pending[pending.sid]
            answered = status in ANSWERED
            self._ledger(
                event_type=v.EVENT_TYPE_TELEPHONY_CALL_ENDED,
                status=v.STATUS_COMPLETED if answered else v.STATUS_FAILED,
                summary=f"Arama sonucu: {status}.",
                source_ref=f"call:{pending.sid}:ended",
                moment=moment,
                detail={
                    "reason": pending.kind,
                    "call_sid": pending.sid,
                    "call_status": status,
                    "attempt": pending.attempt,
                },
            )
            if answered:
                counts["answered"] += 1
                continue
            if status not in NOT_ANSWERED:
                counts["unknown"] += 1
            if pending.attempt == 1:
                try:
                    self._place(
                        pending.kind, pending.message, attempt=2, moment=moment, origin=None
                    )
                    counts["retried"] += 1
                    continue
                except TelephonyError:
                    pass
            self._notify_unanswered(pending, moment)
            counts["notified"] += 1
        return counts

    def _notify_unanswered(self, pending: _Pending, moment: datetime) -> None:
        with self._session_scope() as db:
            notifications.record(
                db,
                kind=policy.UNANSWERED_NOTIFICATION_KIND,
                title="Seni aradım, ulaşamadım",
                body=pending.message,
                priority=PRIORITY_URGENT if policy.is_critical(pending.kind) else PRIORITY_NORMAL,
                group_key=f"telephony:{pending.kind}",
                data={"call_sid": pending.sid, "reason": pending.kind},
                now=moment,
            )

    # ------------------------------------------------------------------ the events that ring

    def call_for_notifications(self, now: datetime | None = None) -> int:
        """Ring once for each recent important notification nobody has called about yet.

        The ledger row is the memory: its ``source_ref`` is ``notification:<id>``, written by
        the call or by the decision not to call (quiet hours, the cap), and ledger writes are
        idempotent on it - so one notification is one decision, across passes and restarts.
        """
        if not self.configured:
            return 0
        moment = _aware(now or self._clock())
        with self._session_scope() as db:
            rows = (
                db.execute(
                    select(NotificationRow)
                    .where(NotificationRow.created_at >= moment - NOTIFICATION_LOOKBACK)
                    .order_by(NotificationRow.created_at)
                )
                .scalars()
                .all()
            )
            todo: list[tuple[str, str, str]] = []
            for row in rows:
                kind = policy.call_kind_for_notification(row.kind)
                if kind is None:
                    continue
                ref = f"notification:{row.id}"
                seen = db.execute(
                    select(ActivityEventRow.event_id).where(
                        ActivityEventRow.source == SOURCE, ActivityEventRow.source_ref == ref
                    )
                ).first()
                if seen is None:
                    message = ". ".join(p for p in (row.title.strip(), row.body.strip()) if p)
                    todo.append((kind, message, ref))
        placed = 0
        for kind, message, ref in todo:
            try:
                if self.call(kind, message, origin=ref, now=moment).status == "placed":
                    placed += 1
            except TelephonyError:
                continue
        return placed

    # ------------------------------------------------------------------ the ledger

    def _calls_in_last_hour(self, moment: datetime) -> int:
        with self._session_scope() as db:
            return int(
                db.execute(
                    select(func.count())
                    .select_from(ActivityEventRow)
                    .where(
                        ActivityEventRow.event_type == v.EVENT_TYPE_TELEPHONY_CALL_PLACED,
                        ActivityEventRow.occurred_at >= moment - timedelta(hours=1),
                    )
                ).scalar_one()
            )

    def _ledger(
        self,
        *,
        event_type: str,
        status: str,
        summary: str,
        source_ref: str,
        moment: datetime,
        detail: dict[str, Any],
        severity: str = v.SEVERITY_INFO,
    ) -> None:
        try:
            with self._session_scope() as db:
                ledger.record(
                    db,
                    ledger.ActivityEvent(
                        event_type=event_type,
                        subsystem=v.SUBSYSTEM_TELEPHONY,
                        action=event_type,
                        factual_summary=summary,
                        source=SOURCE,
                        source_ref=source_ref,
                        status=status,
                        severity=severity,
                        occurred_at=moment,
                        module="app.telephony",
                        detail_json=detail,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - a broken ledger never stops a call
            logger.warning("telephony_ledger_failed", error=type(exc).__name__)


# ---------------------------------------------------------------------- construction


def build_speak(settings: Any) -> Speak | None:
    """Our TTS - the alarm greeting's provider - or ``None`` when it cannot really speak."""
    from app.alarms.greeting_audio import (
        build_greeting_tts,
        is_fallback_provider,
        normalize_greeting,
        synthesize_greeting,
    )

    provider = build_greeting_tts(settings)
    if is_fallback_provider(provider):
        return None

    def speak(text: str) -> tuple[bytes, str]:
        audio = synthesize_greeting(normalize_greeting(text), provider=provider)
        return audio.audio, "audio/wav"

    return speak


_FROM_SETTINGS: Any = object()


def build_owner_caller(
    settings: Any,
    *,
    session_scope: SessionScope,
    audio_store: AudioStore,
    provider: TelephonyProvider | None = None,
    speak: Speak | None | Any = _FROM_SETTINGS,
) -> OwnerCaller:
    from app.telephony.twilio import TwilioTelephony

    if provider is None:
        provider = TwilioTelephony(
            settings.telephony_twilio_account_sid.get_secret_value(),
            settings.telephony_twilio_auth_token.get_secret_value(),
        )
    return OwnerCaller(
        provider=provider,
        owner_number=settings.telephony_owner_number,
        from_number=settings.telephony_from_number,
        public_base_url=settings.telephony_public_base_url,
        audio_store=audio_store,
        speak=build_speak(settings) if speak is _FROM_SETTINGS else speak,
        session_scope=session_scope,
        max_per_hour=settings.telephony_max_calls_per_hour,
    )


__all__ = [
    "AUDIO_TTL",
    "GIVE_UP_AFTER",
    "RETRY_AFTER",
    "TEST_MESSAGE",
    "CallOutcome",
    "CallRefused",
    "OwnerCaller",
    "TelephonyNotConfigured",
    "build_owner_caller",
    "build_speak",
    "mask_number",
    "normalize_number",
]
