"""The row is the truth (ADR-0207 b).

Everything the owner, the workflow or a route may do to a browser task goes through
here, against the database: start it, run ONE round, hear the owner's word, cancel it.
The loop itself (``app.webtask.loop``) is pure and knows no database; this module loads
the state from the row, hands it to the loop, and writes what comes back.

Three things are decided here and nowhere else:

* **One task at a time.** The owner has one Chrome and the loop works in it; a second
  task while one is in flight is refused with the first one's id.
* **A confirmation is a claim, and it is judged.** ``confirm_db`` runs the claim through
  ``app.actions.confirmation_gate.check_gate`` - the gate mail send uses: the same
  session that heard the read-back, a LATER turn, resolved by the ONE router. A model
  cannot confirm for the owner, and neither can a page.
* **The ledger.** One row when a task starts, one each time it stops for the owner, one
  when it ends. The rounds are on the task's own row; a typed value is in neither.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.confirmation_gate import Confirmation, check_gate
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_WEB_TASK_ASKED_OWNER,
    EVENT_TYPE_WEB_TASK_FINISHED,
    EVENT_TYPE_WEB_TASK_STARTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    SUBSYSTEM_BROWSER,
)
from app.logging import get_logger
from app.webtask import loop
from app.webtask.loop import OwnerWordError, Ports, TaskState
from app.webtask.models import SOURCE_VOICE, WebTaskRow
from app.webtask.types import (
    ASK_CONFIRM,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
    TERMINAL,
)
from app.webtask.types import (
    STATUS_FAILED as TASK_FAILED,
)

logger = get_logger("app.webtask.service")

CAPABILITY_WEB_TASK: Final = "web.task"
MAX_GOAL_CHARS: Final = 600
#: A row that says "running" and has not been written for this long has nothing driving
#: it (a worker that died between two rounds). It is closed, never left to refuse every
#: later task - the operator mission's own lesson (production, 2026-09-18).
ORPHAN_AFTER: Final = timedelta(minutes=20)
#: A task parked for the owner is not an orphan: it waits as long as the workflow does.
PARKED_AFTER: Final = timedelta(hours=3)

#: ``check_gate`` speaks in the states of the thing being confirmed.
_STATE_PREPARED: Final = "prepared"
_STATE_READ_BACK: Final = "read_back"
_STATE_GONE: Final = "gone"


class WebTaskError(Exception):
    """A request the task's state does not allow. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str = "", **detail: Any) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.detail = detail


def workflow_id_for(task_id: uuid.UUID) -> str:
    return f"web-task-{task_id}"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def get_task(db: Session, task_id: uuid.UUID) -> WebTaskRow:
    row = db.get(WebTaskRow, task_id)
    if row is None:
        raise WebTaskError("not_found", f"no browser task {task_id}")
    return row


def load(row: WebTaskRow) -> TaskState:
    # A fresh dict every time: the state is replaced on the row, never edited in place
    # (an in-place change to a JSON column is not seen as a change and is never written).
    return TaskState.from_dict(dict(row.state_json or {}))


def _retained(document: dict[str, Any]) -> dict[str, Any]:
    """What of the page a stored state keeps (ADR-0207 PR-C).

    * Ended (done / failed / cancelled - an abandoned task is failed): nothing. There is
      no next round to act on it, and the page may be the owner's mail.
    * Waiting for the owner, and not fresh: the address, the title and the elements (what
      was asked about), never the text. The loop observes anew after the owner's word -
      ``observation_fresh`` is false in every wait - so no reader needs it.
    * Running: all of it. The next round acts on it instead of observing twice.

    A NEW document is returned; the one passed in is never edited in place.
    """
    status = document.get("status")
    observation = document.get("observation")
    if status in TERMINAL:
        return {**document, "observation": None, "observation_fresh": False}
    if (
        status == STATUS_WAITING_OWNER
        and isinstance(observation, dict)
        and not document.get("observation_fresh")
        and observation.get("text")
    ):
        return {**document, "observation": {**observation, "text": ""}}
    return document


def scrub_observations(db: Session, now: datetime | None = None) -> int:
    """Clear the observation of every ended task that still holds one (rows written before
    the retention rule). Idempotent: a second call finds nothing. Returns the count."""
    rows = db.execute(select(WebTaskRow).where(WebTaskRow.status.in_(tuple(TERMINAL)))).scalars()
    cleared = 0
    for row in rows:
        document = dict(row.state_json or {})
        if document.get("observation") is None and not document.get("observation_fresh"):
            continue
        # The status column is the truth for a row; the document may predate it.
        row.state_json = {**document, "observation": None, "observation_fresh": False}
        cleared += 1
    if cleared:
        db.commit()
        logger.info("web_task_observations_scrubbed", rows=cleared, at=(now or _now()).isoformat())
    return cleared


def _write(row: WebTaskRow, state: TaskState, *, now: datetime | None = None) -> None:
    moment = now or _now()
    row.state_json = _retained(state.as_dict())
    row.status = state.status
    row.round_index = state.round_index
    row.failure = state.failure
    row.message = (state.message or "")[:1200]
    waiting = state.pending.kind if (state.pending and state.status == STATUS_WAITING_OWNER) else ""
    row.waiting_for = waiting
    if waiting == ASK_CONFIRM:
        if row.read_back_at is None:
            row.read_back_at = moment
    else:
        row.read_back_at = None
        row.read_back_session_id = None
        row.read_back_turn = None
    row.updated_at = moment
    if state.status in TERMINAL and row.completed_at is None:
        row.completed_at = moment


def task_dict(row: WebTaskRow) -> dict[str, Any]:
    """What a surface may show. The trail, never the observation: page text stays out of
    anything that is read aloud or drawn without the untrusted-content treatment."""
    state = load(row)
    return {
        "task_id": str(row.id),
        "goal": row.goal,
        "status": row.status,
        "waiting_for": row.waiting_for,
        "message": row.message,
        "failure": row.failure,
        "round_index": row.round_index,
        "rounds": [r.as_dict() for r in state.rounds],
        "attended": row.attended,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


# ------------------------------------------------------------------ one task at a time


def _orphaned(row: WebTaskRow, now: datetime) -> bool:
    written = _aware(row.updated_at) or _aware(row.created_at) or now
    limit = PARKED_AFTER if row.status == STATUS_WAITING_OWNER else ORPHAN_AFTER
    return now - written > limit


def active_task(db: Session, *, now: datetime | None = None) -> WebTaskRow | None:
    moment = now or _now()
    rows = (
        db.execute(
            select(WebTaskRow)
            .where(WebTaskRow.status.in_((STATUS_RUNNING, STATUS_WAITING_OWNER)))
            .order_by(WebTaskRow.created_at.desc())
        )
        .scalars()
        .all()
    )
    live: WebTaskRow | None = None
    for row in rows:
        if _orphaned(row, moment):
            state = load(row)
            state.status = TASK_FAILED
            state.failure = "abandoned"
            state.message = "Görev yarıda kaldı; onu yürüten süreç durmuş."
            state.pending = None
            state.grant = None
            _write(row, state, now=moment)
            db.commit()
            _ledger(db, row, EVENT_TYPE_WEB_TASK_FINISHED, "yarıda kaldı", status=STATUS_FAILED)
            continue
        live = live or row
    return live


def start_task_db(
    db: Session,
    *,
    goal: str,
    device_id: uuid.UUID | None = None,
    source: str = SOURCE_VOICE,
    session_id: str | None = None,
    allowed_hosts: tuple[str, ...] = (),
    now: datetime | None = None,
) -> WebTaskRow:
    goal = " ".join((goal or "").split())
    if not goal:
        raise WebTaskError("empty_goal", "a browser task needs a goal")
    if len(goal) > MAX_GOAL_CHARS:
        raise WebTaskError("goal_too_long", f"a goal is at most {MAX_GOAL_CHARS} characters")
    moment = now or _now()
    running = active_task(db, now=moment)
    # Rows that ended before the retention rule still hold a page; the next task clears
    # them. (A timed sweep from the app's lifespan is the named follow-up.)
    scrub_observations(db, moment)
    if running is not None:
        raise WebTaskError(
            "task_in_flight", "another browser task is in flight", task_id=str(running.id)
        )
    task_id = uuid.uuid4()
    state = TaskState(task_id=str(task_id), goal=goal, allowed_hosts=list(allowed_hosts))
    row = WebTaskRow(
        id=task_id,
        goal=goal,
        status=state.status,
        device_id=device_id,
        source=source,
        session_id=session_id,
        attended=True,
        state_json=state.as_dict(),
        created_at=moment,
        updated_at=moment,
    )
    db.add(row)
    db.commit()
    _ledger(db, row, EVENT_TYPE_WEB_TASK_STARTED, f"başladı: {goal[:80]}")
    return row


# ------------------------------------------------------------------ one round


def outcome(row: WebTaskRow) -> dict[str, Any]:
    return {
        "status": row.status,
        "waiting_for": row.waiting_for,
        "round_index": row.round_index,
        "failure": row.failure,
    }


def run_round_db(db: Session, task_id: uuid.UUID, ports: Ports) -> dict[str, Any]:
    """ONE round of the task, written to the row. Safe to call on a task that is not
    running: it answers with the row as it is."""
    row = get_task(db, task_id)
    if row.cancel_requested and row.status not in TERMINAL:
        return outcome(cancel_db(db, task_id))
    if row.status != STATUS_RUNNING:
        return outcome(row)
    before = row.status, row.waiting_for
    state = loop.run_round(load(row), ports)
    # The owner may have cancelled while the round ran: his word is on the row, and the
    # round's result does not overwrite it.
    db.refresh(row)
    if row.cancel_requested and state.status not in TERMINAL:
        state = loop.cancel(state)
    _write(row, state)
    db.commit()
    if state.status == STATUS_WAITING_OWNER and (row.status, row.waiting_for) != before:
        _ledger(
            db,
            row,
            EVENT_TYPE_WEB_TASK_ASKED_OWNER,
            f"sahibe soruldu ({row.waiting_for})",
            detail={
                "waiting_for": row.waiting_for,
                "risk": state.pending.risk if state.pending else "",
            },
        )
    if state.status in TERMINAL:
        _finished(db, row, state)
    return outcome(row)


# ------------------------------------------------------------------ the owner's words


def note_read_back_db(
    db: Session, task_id: uuid.UUID, *, session_id: str, turn: int | None
) -> WebTaskRow:
    """The surface that READ the pending question to the owner says so: which session,
    which turn. A confirmation is judged against exactly this."""
    row = get_task(db, task_id)
    if row.status != STATUS_WAITING_OWNER or row.waiting_for != ASK_CONFIRM:
        raise WebTaskError("nothing_to_confirm", "the task is not waiting for a confirmation")
    row.read_back_at = _now()
    row.read_back_session_id = session_id
    row.read_back_turn = turn
    row.updated_at = _now()
    db.commit()
    return row


def confirm_db(db: Session, task_id: uuid.UUID, confirmation: Confirmation | None) -> WebTaskRow:
    """The owner's word for the step that was read back - judged, then applied."""
    row = get_task(db, task_id)
    if row.status in TERMINAL:
        state_name = _STATE_GONE
    elif row.status == STATUS_WAITING_OWNER and row.waiting_for == ASK_CONFIRM:
        state_name = _STATE_READ_BACK if row.read_back_session_id else _STATE_PREPARED
    elif row.status == STATUS_WAITING_OWNER:
        # A payment, a login, a denied site: nothing here is opened by a word.
        raise WebTaskError("nothing_to_confirm", "this wait is not answered by a confirmation")
    else:
        state_name = _STATE_GONE
    verdict = check_gate(
        state=state_name,
        prepared_state=_STATE_PREPARED,
        read_back_state=_STATE_READ_BACK,
        read_back_at=_aware(row.read_back_at) if row.read_back_session_id else None,
        read_back_session_id=row.read_back_session_id,
        read_back_turn=row.read_back_turn,
        confirmation=confirmation,
        host_flag_enabled=True,
        provider_available=True,
    )
    if not verdict.ok:
        raise WebTaskError(str(verdict.reason), "the confirmation was not accepted")
    assert confirmation is not None
    state = load(row)
    try:
        state = loop.confirm(state, source=confirmation.source)
    except OwnerWordError as exc:
        raise WebTaskError(exc.reason) from exc
    _write(row, state)
    db.commit()
    return row


def continue_db(db: Session, task_id: uuid.UUID, *, answer: str = "") -> WebTaskRow:
    """ "devam": the same round, observed again."""
    row = get_task(db, task_id)
    state = load(row)
    try:
        state = loop.continue_(state, answer)
    except OwnerWordError as exc:
        raise WebTaskError(exc.reason) from exc
    _write(row, state)
    db.commit()
    return row


def decline_db(db: Session, task_id: uuid.UUID) -> WebTaskRow:
    row = get_task(db, task_id)
    state = load(row)
    try:
        state = loop.decline(state)
    except OwnerWordError as exc:
        raise WebTaskError(exc.reason) from exc
    _write(row, state)
    db.commit()
    return row


def request_cancel_db(db: Session, task_id: uuid.UUID) -> WebTaskRow:
    """The owner's cancel, written where the running round will read it."""
    row = get_task(db, task_id)
    if row.status in TERMINAL:
        return row
    row.cancel_requested = True
    row.updated_at = _now()
    db.commit()
    if row.status == STATUS_WAITING_OWNER:
        # Nothing is running that would read the flag: the cancel is applied now.
        return cancel_db(db, task_id)
    return row


def cancel_db(db: Session, task_id: uuid.UUID) -> WebTaskRow:
    row = get_task(db, task_id)
    if row.status in TERMINAL:
        return row
    state = loop.cancel(load(row))
    _write(row, state)
    row.cancel_requested = True
    db.commit()
    _finished(db, row, state)
    return row


def fail_db(db: Session, task_id: uuid.UUID, *, reason: str, detail: str) -> WebTaskRow | None:
    """The workflow's last word when a round could not run at all: the row says FAILED,
    with the reason - never a row left "running" with nothing running it."""
    row = db.get(WebTaskRow, task_id)
    if row is None or row.status in TERMINAL:
        return row
    state = load(row)
    state.status = TASK_FAILED
    state.failure = reason
    state.message = f"Görev yürütülemedi: {detail[:300]}"
    state.pending = None
    state.grant = None
    _write(row, state)
    db.commit()
    _finished(db, row, state)
    return row


# ------------------------------------------------------------------ the ledger


def _finished(db: Session, row: WebTaskRow, state: TaskState) -> None:
    words = {STATUS_DONE: "bitti", TASK_FAILED: "başarısız", STATUS_CANCELLED: "iptal edildi"}
    _ledger(
        db,
        row,
        EVENT_TYPE_WEB_TASK_FINISHED,
        f"{words.get(state.status, state.status)}"
        + (f" ({state.failure})" if state.failure else ""),
        status=STATUS_COMPLETED if state.status == STATUS_DONE else STATUS_FAILED,
        detail={
            "failure": state.failure,
            "rounds": len(state.rounds),
            "sites": sorted({r.site for r in state.rounds if r.site}),
            "confirmed_steps": sum(1 for r in state.rounds if r.confirmed_by),
        },
    )


def _ledger(
    db: Session,
    row: WebTaskRow,
    event_type: str,
    summary: str,
    *,
    status: str = STATUS_COMPLETED,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_BROWSER,
                action=CAPABILITY_WEB_TASK,
                factual_summary=f"{CAPABILITY_WEB_TASK} -> {summary}",
                status=status,
                occurred_at=_now(),
                detail_json={"task_id": str(row.id), **(detail or {})},
                source="live",
                source_ref=(
                    f"web_task:{row.id}:{event_type}:{row.round_index}:"
                    f"{row.status}:{row.waiting_for}"
                ),
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
        logger.warning("web_task_ledger_failed", task_id=str(row.id), event_type=event_type)


__all__ = [
    "CAPABILITY_WEB_TASK",
    "WebTaskError",
    "active_task",
    "cancel_db",
    "confirm_db",
    "continue_db",
    "decline_db",
    "fail_db",
    "get_task",
    "load",
    "note_read_back_db",
    "outcome",
    "request_cancel_db",
    "run_round_db",
    "scrub_observations",
    "start_task_db",
    "task_dict",
    "workflow_id_for",
]
