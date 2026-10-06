"""The conversations, read for spends on a short clock (money-ledger).

Every ``interval_s`` (20 s) the conversations of the last two days that have new lines - or
that ended and were not finished - are run through ``app.conversations.spend.decide``:

* a ``BOOK`` is written at once as a TENTATIVE spend (unique per conversation + line, so a
  second pass books nothing) and said in one short notification with "geri al";
* an ``ASK`` becomes a ``money_questions`` row; it is ASKED (one notification, "X liralık bir
  harcama yaptınız mı?") only once the conversation has ENDED and ``ASK_DELAY`` has passed -
  never while the talk goes on, never twice.

A conversation is finished (never read again) once it ended and its questions were asked.
A failed pass is counted by its error's type and tried again at the next one. Nothing here
contacts a bank or moves money.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.conversations import spend
from app.conversations.models import ConversationRow, SegmentRow
from app.logging import get_logger
from app.loops import LoopHeartbeat
from app.money import pending, service
from app.money.models import MoneyEntry, MoneyQuestion, MoneyScan
from app.notifications import service as notifications

logger = get_logger("app.money.loop")

#: The question waits this long after the conversation ends (the card: 1-2 minutes).
ASK_DELAY: Final = timedelta(seconds=90)
SCAN_INTERVAL_SECONDS: Final = 20.0
LOOK_BACK: Final = timedelta(days=2)
KIND_BOOKED: Final = "money.booked"
KIND_QUESTION: Final = "money.question"
TITLE: Final = "Para defteri"

SessionScope = Callable[[], AbstractContextManager[Session]]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _book(db: Session, cid: uuid.UUID, decision: spend.Decision, *, now: datetime) -> bool:
    exists = db.scalars(
        select(MoneyEntry.id).where(
            MoneyEntry.conversation_id == cid, MoneyEntry.segment_seq == decision.anchor_seq
        )
    ).first()
    if exists is not None or not decision.amount_kurus:
        return False
    try:
        row = service.book_spend(
            db,
            decision.amount_kurus,
            status=service.STATUS_TENTATIVE,
            source=service.SOURCE_CONVERSATION,
            method=decision.method,
            occurred_at=decision.occurred_at,
            now=now,
            description="Konuşmadan",
            category=decision.category,
            conversation_id=cid,
            segment_seq=decision.anchor_seq,
        )
    except service.MoneyRefused:
        db.rollback()
        return False
    notifications.record(
        db,
        kind=KIND_BOOKED,
        title=TITLE,
        body=service.booked_line(row),
        group_key=f"money:{row.id}",
        data={"entry_id": str(row.id), "amount_kurus": row.amount_kurus},
        now=now,
    )
    pending.note_booking(now)
    return True


def _question(db: Session, cid: uuid.UUID, decision: spend.Decision, *, now: datetime) -> None:
    exists = db.scalars(
        select(MoneyQuestion.id).where(
            MoneyQuestion.conversation_id == cid, MoneyQuestion.anchor_seq == decision.anchor_seq
        )
    ).first()
    if exists is not None:
        return
    db.add(
        MoneyQuestion(
            id=uuid.uuid4(),
            conversation_id=cid,
            anchor_seq=decision.anchor_seq,
            amount_kurus=decision.amount_kurus,
            method=decision.method,
            category=decision.category,
            reason=decision.reason or spend.REASON_NO_ACCEPTANCE,
            occurred_at=decision.occurred_at,
            created_at=now,
        )
    )
    db.commit()


def _ask_due(db: Session, cid: uuid.UUID, *, now: datetime) -> int:
    asked = 0
    rows = db.scalars(
        select(MoneyQuestion)
        .where(MoneyQuestion.conversation_id == cid, MoneyQuestion.asked_at.is_(None))
        .order_by(MoneyQuestion.anchor_seq)
    ).all()
    for row in rows:
        row.asked_at = now
        db.flush()
        notifications.record(
            db,
            kind=KIND_QUESTION,
            title=TITLE,
            body=service.question_line(row),
            group_key=f"money-question:{row.id}",
            data={"question_id": str(row.id), "amount_kurus": row.amount_kurus},
            now=now,
        )
        asked += 1
    if asked:
        pending.note_question(now)
    db.commit()
    return asked


def scan_conversations(db: Session, *, now: datetime) -> dict[str, int]:
    """One pass. Returns how many spends were booked and questions asked."""
    counts = {"booked": 0, "asked": 0}
    since = _aware(now) - LOOK_BACK
    finished = select(MoneyScan.conversation_id).where(MoneyScan.finished_at.is_not(None))
    conversations = db.scalars(
        select(ConversationRow).where(
            or_(ConversationRow.started_at >= since, ConversationRow.ended_at.is_(None)),
            ConversationRow.id.not_in(finished),
        )
    ).all()
    for conv in conversations:
        segments = db.scalars(
            select(SegmentRow).where(SegmentRow.conversation_id == conv.id).order_by(SegmentRow.seq)
        ).all()
        scan = db.get(MoneyScan, conv.id)
        last_seq = segments[-1].seq if segments else 0
        ended = conv.ended_at is not None
        if scan is not None and scan.last_seq == last_seq and not ended:
            continue
        lines = [
            spend.Line(seq=s.seq, is_owner=s.is_owner, said=s.text, at=s.spoken_at)
            for s in segments
        ]
        owner_known = any(s.is_owner for s in segments)
        for decision in spend.decide(lines, ended=ended, owner_known=owner_known):
            if decision.kind == spend.BOOK:
                counts["booked"] += int(_book(db, conv.id, decision, now=now))
            else:
                _question(db, conv.id, decision, now=now)
        if scan is None:
            scan = MoneyScan(conversation_id=conv.id, last_seq=0, updated_at=now)
            db.add(scan)
        scan.last_seq = last_seq
        scan.updated_at = now
        if ended and conv.ended_at is not None and _aware(now) >= _aware(conv.ended_at) + ASK_DELAY:
            counts["asked"] += _ask_due(db, conv.id, now=now)
            scan.finished_at = now
        db.commit()
    return counts


class SpendLoop:
    def __init__(
        self,
        session_scope: SessionScope,
        *,
        interval_s: float = SCAN_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_scope = session_scope
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._beat = LoopHeartbeat(name="money_spend_loop", interval_s=interval_s)
        self.last_result: dict[str, int] | None = None

    def scan_once(self, moment: datetime | None = None) -> dict[str, int] | None:
        moment = moment or self._clock()
        try:
            with self._session_scope() as db:
                result = scan_conversations(db, now=moment)
        except Exception as exc:  # noqa: BLE001 - a scan never takes the process down
            logger.warning("money_spend_scan_failed", error=type(exc).__name__)
            self._beat.record_failure(exc, now=moment)
            self.last_result = None
            return None
        self.last_result = result
        self._beat.record_pass(now=moment)
        return result

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.scan_once, self._clock())
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        self._beat.bind(self._task, now=self._clock())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
            self._beat.bind(None)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def health_check(self) -> dict[str, Any]:
        health = self._beat.health_check(now=self._clock())
        health["last_result"] = self.last_result
        return health


__all__ = ["ASK_DELAY", "KIND_BOOKED", "KIND_QUESTION", "SpendLoop", "scan_conversations"]
