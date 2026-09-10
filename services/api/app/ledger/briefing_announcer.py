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
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, Protocol

from sqlalchemy.orm import Session

from app.ledger.briefing import POLICY_DIGEST, VIA_VOICE, mark_delivered, pending
from app.ledger.models import PendingBriefingRow
from app.logging import get_logger
from app.narration.numbers import cardinal

logger = get_logger("app.ledger.briefing_announcer")

#: Often enough that a research run finishing feels answered, rare enough that an idle
#: machine is not asking the database for work every second.
DEFAULT_INTERVAL_S: Final[float] = 20.0
#: More digest rows than this and the sentence stops naming a number the owner can hold
#: in their head; it says "several" instead. Deliberately small.
MAX_DIGEST_COUNTED: Final[int] = 20


class BriefingSpeaker(Protocol):
    """Says one sentence to the owner. ``True`` iff it reached them THERE AND THEN.

    Deliberately narrower than ``BriefingPort``: that one carries a routine and a
    firing because a routine is what asked. Nothing asked for these.

    ``briefing_ids`` rides along so the sentence carries its own receipt. A device push
    is heard immediately and returns ``True``, and this class stamps the rows. A web
    session is pull-only: the frame waits in the session buffer until the owner next
    speaks, so ``say`` returns ``False`` and the rows are stamped by the drain instead --
    by the thing that actually delivered them. ``False`` here therefore means "not yet",
    not "lost": the row stays pending and the next pass sees it is already queued.
    """

    def say(self, text: str, briefing_ids: Sequence[uuid.UUID]) -> bool: ...


SessionFactory = Callable[[], AbstractContextManager[Session]]


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
    ) -> None:
        self._session_factory = session_factory
        self._speaker = speaker
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ sweep

    def sweep_once(self) -> int:
        """One pass. Returns how many rows were stamped delivered (0 or more).

        More than one only ever happens for a digest, where several rows became one
        spoken sentence and were therefore all delivered by it.
        """
        with self._session_factory() as session:
            rows = pending(session)
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
                if not self._speak(target.speech, [target.briefing_id]):
                    return 0
                mark_delivered(session, target.briefing_id, VIA_VOICE)
                logger.info("briefing_delivered", policy=target.policy, count=1)
                return 1

            digests = [row for row in rows if row.policy == POLICY_DIGEST]
            ids = [row.briefing_id for row in digests]
            if not self._speak(digest_sentence(digests), ids):
                return 0
            for briefing_id in ids:
                mark_delivered(session, briefing_id, VIA_VOICE)
            logger.info("briefing_delivered", policy=POLICY_DIGEST, count=len(digests))
            return len(digests)

    def _speak(self, text: str, briefing_ids: Sequence[uuid.UUID]) -> bool:
        """Never lets a speaker's failure look like a delivery, or stop the loop."""
        try:
            return bool(self._speaker.say(text, briefing_ids))
        except Exception:  # noqa: BLE001 - a transport fault is not a delivery
            logger.warning("briefing_say_failed")
            return False

    # --------------------------------------------------------------- background

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sweep_once)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the sweep loop must never die silently
                logger.exception("briefing_sweep_failed")
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

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

    def say(self, text: str, briefing_ids: Sequence[uuid.UUID] = ()) -> bool:
        delivery = self._port.narrate(
            text=text,
            routine_id=self.SYSTEM_ORIGIN,
            firing_id=uuid.uuid4(),
            briefing_ids=list(briefing_ids),
        )
        if not delivery.delivered:
            logger.info("briefing_not_delivered", reason=delivery.reason)
        return bool(delivery.delivered)


__all__ = [
    "DEFAULT_INTERVAL_S",
    "MAX_DIGEST_COUNTED",
    "VIA_VOICE",
    "BriefingSpeaker",
    "PendingBriefingAnnouncer",
    "RealtimeSayBriefingSpeaker",
    "digest_sentence",
]
