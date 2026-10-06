"""The half ADR-0110 named and did not build: a queued briefing reaches the owner.

`app.ledger.briefing` decides what the owner needs to hear and writes the Turkish
sentence into ``pending_briefings``. Nothing read it. On 2026-09-10 that table held
fourteen undelivered rows, every one of them expired -- among them the research runs
that finished while the owner was asleep. The queue had a filler and no deliverer,
which is the shape this repository has now recorded four times: built, tested, never
wired.

**What "delivered" means here.** Exactly what ``SidebandPusher.push`` returned, and
nothing else -- the same rule ``RealtimeSayBriefing`` states for the alarm. A row is
stamped by the thing that actually delivered it. No live session, no bound device, a
push that failed at the transport: all of them leave the row alone, so it is spoken on
the next pass instead of being marked as heard by nobody.

**One at a time, briefly.** The constitution is explicit: "A completed task must not
automatically force a long result onto the owner. Default: notify briefly and wait." So
a pass speaks ONE thing. The urgent policies (``immediate``/``completion``/``once``)
are spoken as themselves, newest urgency first, because each is about one event the
owner asked about. ``digest`` rows are the opposite: they are ordinary autonomous
activity, and reading nine of them aloud would be the flood the constitution forbids --
so they become one sentence that says how many there are and offers the detail. That is
what the spec means by "accumulated and summarized at delivery time".

**No live session at all: the notification ladder.** A voice session is not the only way
to reach the owner. When the speaker says ``no_live_session`` -- and only then -- each
non-digest row gets ONE notification (``briefing:<id>`` is its duplicate lock), and the
ladder (toast -> sound -> push -> inbox) carries it. The row is stamped later, from what
the ladder wrote on that notification (``notification:push``), never when it is queued.
A notification that only reached the inbox has not been heard: the row waits, and the
next live session says it aloud.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.briefing import (
    POLICY_DIGEST,
    VIA_NOTIFICATION_PREFIX,
    VIA_VOICE,
    mark_delivered,
    pending,
    record_delivery_failure,
)
from app.ledger.models import PendingBriefingRow
from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.narration.numbers import cardinal
from app.notifications import service as notifications
from app.notifications.models import (
    CHANNEL_PUSH,
    CHANNEL_SOUND,
    CHANNEL_TOAST,
    PRIORITY_NORMAL,
    NotificationRow,
)

logger = get_logger("app.ledger.briefing_announcer")

#: Often enough that a research run finishing feels answered, rare enough that an idle
#: machine is not asking the database for work every second.
DEFAULT_INTERVAL_S: Final[float] = 20.0
#: More digest rows than this and the sentence stops naming a number the owner can hold
#: in their head; it says "several" instead. Deliberately small.
MAX_DIGEST_COUNTED: Final[int] = 20
#: The one speaker answer that sends a briefing to the notification ladder. A web session
#: that queued the frame (``queued_to_session`` / ``already_queued``) will say it on the
#: owner's next word, and a notification on top of that would be the same news twice.
REASON_NO_LIVE_SESSION: Final[str] = "no_live_session"
#: How many notifications one pass may write. A night of finished research must not wake
#: up as a wall of identical "Brifing" rows in one sweep; the rest follow 20 s later.
MAX_FALLBACK_PER_SWEEP: Final[int] = 5
#: Rungs that put the notification in front of the owner. ``inbox`` is not one of them.
_HEARD_RUNGS: Final[frozenset[str]] = frozenset({CHANNEL_TOAST, CHANNEL_SOUND, CHANNEL_PUSH})


@dataclass(frozen=True)
class SayOutcome:
    """What a speaker did, and why: ``BriefingDelivery.reason`` passed through as it is."""

    delivered: bool
    reason: str


class BriefingSpeaker(Protocol):
    """Says one sentence to the owner. ``delivered`` iff it reached them THERE AND THEN.

    Deliberately narrower than ``BriefingPort``: that one carries a routine and a
    firing because a routine is what asked. Nothing asked for these.

    ``briefing_ids`` rides along so the sentence carries its own receipt. A device push
    is heard immediately and is ``delivered``, and this class stamps the rows. A web
    session is pull-only: the frame waits in the session buffer until the owner next
    speaks, so ``say`` is not delivered and the rows are stamped by the drain instead --
    by the thing that actually delivered them. Not delivered therefore means "not yet",
    not "lost": the row stays pending and the next pass sees it is already queued. The
    ``reason`` tells "not yet" (``queued_to_session``) from "nobody to say it to"
    (``no_live_session``), and only the second goes to the notification ladder.
    """

    def say(self, text: str, briefing_ids: Sequence[uuid.UUID]) -> SayOutcome: ...


SessionFactory = Callable[[], AbstractContextManager[Session]]


class BriefingFallback(Protocol):
    """The way to the owner when there is no live session: one notification per row."""

    def open_for(self, briefing_id: uuid.UUID) -> NotificationRow | None: ...

    def notify(self, row: PendingBriefingRow, now: datetime | None = None) -> NotificationRow: ...


def _group_key(briefing_id: uuid.UUID) -> str:
    return f"briefing:{briefing_id}"


class NotificationBriefingFallback:
    """``notifications.record`` behind ``BriefingFallback``; the ladder does the rest.

    ``group_key`` ``briefing:<id>`` is the duplicate lock: ``open_for`` finds a row in ANY
    state (read, superseded, quarantined), so a briefing is never notified twice. The
    priority is always normal -- a briefing is not an alarm, quiet hours defer it
    (``record``'s own rule) and the phone does not ring for it.
    """

    TITLE: Final[str] = "Brifing"

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def open_for(self, briefing_id: uuid.UUID) -> NotificationRow | None:
        with self._session_factory() as db:
            note = (
                db.execute(
                    select(NotificationRow)
                    .where(NotificationRow.group_key == _group_key(briefing_id))
                    .order_by(NotificationRow.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if note is not None:
                db.expunge(note)
            return note

    def notify(self, row: PendingBriefingRow, now: datetime | None = None) -> NotificationRow:
        with self._session_factory() as db:
            note = notifications.record(
                db,
                kind=f"briefing.{row.policy}",
                title=self.TITLE,
                body=row.speech,
                priority=PRIORITY_NORMAL,
                group_key=_group_key(row.briefing_id),
                data={"briefing_ids": [str(row.briefing_id)], "policy": row.policy},
                now=now,
            )
            db.refresh(note)
            db.expunge(note)
            return note


def _ladder_gave_up(note: NotificationRow) -> bool:
    return note.delivered_at is None and (
        note.quarantined_at is not None or bool(note.ladder_exhausted)
    )


def digest_sentence(rows: list[PendingBriefingRow]) -> str:
    """One sentence for a pile of ordinary activity, and an offer -- never the pile.

    The numbers are Turkish words rather than digits because this is spoken
    (``app.narration.numbers``), the same rule ``app.ledger.briefing.speech_for``
    follows for a research count.
    """
    count = len(rows)
    if count == 1:
        return f"Efendim, bilginize; {rows[0].speech.rstrip('.')}."
    if count <= MAX_DIGEST_COUNTED:
        words = cardinal(count)
        return (
            f"Efendim, bilginize; kendi üzerimde {words} kayıt biriktirdim. "
            "İsterseniz tek tek anlatayım."
        )
    return "Efendim, bilginize; kendi üzerimde epeyce kayıt biriktirdi. İsterseniz anlatayım."


class PendingBriefingAnnouncer:
    """Drains ``pending_briefings`` into the owner's live session, one pass at a time."""

    def __init__(
        self,
        session_factory: SessionFactory,
        speaker: BriefingSpeaker,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        fallback: BriefingFallback | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._speaker = speaker
        self._fallback = fallback
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        #: B07 req 18: this loop's own health. Four of the nine background loops
        #: could not be seen on the health surface at all, and they were the four
        #: that carry a notification to the owner - so the failure they can have is
        #: the one nobody would notice.
        self.heartbeat = LoopHeartbeat(name="briefing_announcer", interval_s=self._interval_s)

    # ------------------------------------------------------------------ sweep

    def sweep_once(self, now: datetime | None = None) -> int:
        """One pass. Returns how many rows were stamped delivered (0 or more).

        More than one only ever happens for a digest, where several rows became one
        spoken sentence and were therefore all delivered by it.

        ``now`` exists so ONE clock decides a pass. ``pending`` drops rows whose expiry has
        passed, and a caller that fixes the clock for the rows but not for the sweep is
        making two decisions with two clocks: the announcer tests queued rows dated
        2026-09-11 09:00 and swept with the real one, so every case passed until real time
        crossed the 24-hour expiry and then all six failed at once - nine minutes after a
        green CI run. Production still passes nothing and gets the real clock.
        """
        with self._session_factory() as session:
            rows = pending(session, now)
            if not rows:
                return 0
            urgent = [row for row in rows if row.policy != POLICY_DIGEST]
            if urgent:
                # ``pending`` orders by priority then age, so the first is the one that
                # matters most and has waited longest at that level. It also BLOCKS the
                # ones behind it while it waits in a session buffer, and that is right:
                # stacking a second sentence on one the owner has not heard yet is the
                # flood the constitution forbids.
                target = urgent[0]
                outcome = self._speak(target.speech, [target.briefing_id])
                if not outcome.delivered:
                    if outcome.reason == REASON_NO_LIVE_SESSION and self._fallback is not None:
                        # Nobody to say it to: the ladder carries it, and waiting on the
                        # ladder is not a failed attempt.
                        return self._fall_back(session, urgent, now)
                    # B07 req 16: count it. Returning without recording the failure is what
                    # made one unspeakable row block the queue for ever - it stayed the most
                    # urgent pending row and was chosen again on every pass.
                    record_delivery_failure(session, target.briefing_id, reason="say_failed")
                    return 0
                mark_delivered(session, target.briefing_id, VIA_VOICE)
                logger.info("briefing_delivered", policy=target.policy, count=1)
                return 1

            digests = [row for row in rows if row.policy == POLICY_DIGEST]
            ids = [row.briefing_id for row in digests]
            # Digests never go to the ladder: ordinary activity, and a notification each
            # would be the flood. They wait for a live session.
            if not self._speak(digest_sentence(digests), ids).delivered:
                for briefing_id in ids:
                    record_delivery_failure(session, briefing_id, reason="digest_say_failed")
                return 0
            for briefing_id in ids:
                mark_delivered(session, briefing_id, VIA_VOICE)
            logger.info("briefing_delivered", policy=POLICY_DIGEST, count=len(digests))
            return len(digests)

    def _fall_back(
        self, session: Session, rows: list[PendingBriefingRow], now: datetime | None
    ) -> int:
        """No live session: notify each row once, stamp the ones the ladder delivered.

        The stamp is read off the notification row the ladder wrote (``delivered_via``),
        never taken from having queued it -- a queue is not a delivery. ``inbox`` (or a
        notification still open) leaves the row pending without counting a failure: the
        next live session speaks it. A ladder that gave up counts one.
        """
        if self._fallback is None:
            return 0
        stamped = written = 0
        for row in sorted(rows, key=lambda r: r.created_at):
            note = self._fallback.open_for(row.briefing_id)
            if note is None:
                if written < MAX_FALLBACK_PER_SWEEP:
                    self._fallback.notify(row, now)
                    written += 1
                continue
            if note.delivered_at is not None and note.delivered_via in _HEARD_RUNGS:
                mark_delivered(
                    session, row.briefing_id, VIA_NOTIFICATION_PREFIX + note.delivered_via, now
                )
                stamped += 1
            elif _ladder_gave_up(note):
                record_delivery_failure(session, row.briefing_id, reason="ladder_failed")
        if written or stamped:
            logger.info("briefing_fallback", notified=written, delivered=stamped)
        return stamped

    def _speak(self, text: str, briefing_ids: Sequence[uuid.UUID]) -> SayOutcome:
        """Never lets a speaker's failure look like a delivery, or stop the loop."""
        try:
            outcome = self._speaker.say(text, briefing_ids)
        except Exception:  # noqa: BLE001 - a transport fault is not a delivery
            logger.warning("briefing_say_failed")
            return SayOutcome(delivered=False, reason="say_raised")
        if isinstance(outcome, SayOutcome):
            return outcome
        # A speaker that only answers yes/no gives no reason, so never the ladder.
        return SayOutcome(delivered=bool(outcome), reason="unknown")

    # --------------------------------------------------------------- background

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.heartbeat.record_failure(exc)  # noqa: BLE001 - the sweep loop must never die silently
                logger.exception("briefing_sweep_failed")
            else:
                self.heartbeat.record_pass()
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            self.heartbeat.bind(self._task)

    def health_check(self) -> dict[str, Any]:
        return self.heartbeat.health_check()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()


class RealtimeSayBriefingSpeaker:
    """Speaks through the SAME path the alarm's own briefing uses.

    A thin adapter rather than a second implementation: ``RealtimeSayBriefing`` already
    finds the owner's one live session (including a session that never expires -- see its
    ``_live_session``), normalizes the text through the narration pipeline and pushes
    ``SB_SAY``. Two ways to speak to the owner would be two things to keep true.
    """

    #: A briefing was not asked for by a routine, and the frame's ids are not optional.
    #: A fixed, obviously-not-a-routine id says "the system spoke on its own" in the
    #: ledger and the device trail rather than borrowing some routine's identity.
    SYSTEM_ORIGIN: Final[uuid.UUID] = uuid.UUID("00000000-0000-0000-0000-00000000b71e")

    def __init__(self, briefing_port: Any) -> None:
        self._port = briefing_port

    def say(self, text: str, briefing_ids: Sequence[uuid.UUID] = ()) -> SayOutcome:
        delivery = self._port.narrate(
            text=text,
            routine_id=self.SYSTEM_ORIGIN,
            firing_id=uuid.uuid4(),
            briefing_ids=list(briefing_ids),
        )
        if not delivery.delivered:
            logger.info("briefing_not_delivered", reason=delivery.reason)
        return SayOutcome(delivered=bool(delivery.delivered), reason=str(delivery.reason))


__all__ = [
    "DEFAULT_INTERVAL_S",
    "MAX_DIGEST_COUNTED",
    "MAX_FALLBACK_PER_SWEEP",
    "REASON_NO_LIVE_SESSION",
    "VIA_VOICE",
    "BriefingFallback",
    "BriefingSpeaker",
    "NotificationBriefingFallback",
    "PendingBriefingAnnouncer",
    "RealtimeSayBriefingSpeaker",
    "SayOutcome",
    "digest_sentence",
]
