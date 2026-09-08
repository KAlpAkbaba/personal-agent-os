"""The research the owner is pointing at, durably (docs/DECISIONS.md ADR-0076).

The owner's record, 2026-09-06 evening. Two completed research runs existed with the
SAME title, twenty-five minutes apart, and a third on a near-identical topic. On a fresh
voice session "Teknik anlat." produced one clarification — "Efendim, iki tamamlanmış
araştırmam var: «...» ve «...». Hangisini anlatayım?" — six times in two and a half
minutes, because nothing in the server could accept the owner's spoken answer; and a page
reload started a new WebRTC session that had no context at all. The only thing that had
ever linked a follow-up to a run was the voice session's own ``context_json``, which dies
with the session.

This module is the durable replacement for that memory, and it is owner-level, not
session-level:

* ``set_focus`` appends one row saying which research is now the one being talked about
  and WHY (a completion, a spoken result, a click in the UI, a spoken selection, or a
  resolved reference like "bir öncekini anlat");
* ``current_focus`` / ``previous_focus`` / ``focus_stack`` read that history back as
  distinct jobs, most recent first;
* ``set_pending_clarification`` / ``take_pending_clarification`` hold the ONE question the
  server asked and the candidates it offered, for ten minutes, so "ikincisi" or
  "20:19'daki" has something to land on.

Identity is the task id, always. Two runs may share a title — the owner's record has
exactly that pair — so a title is a label here and never a key.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.research.models import (
    FOCUS_FOLLOWUP_REFERENCE,
    FOCUS_OWNER_SELECTED_BY_VOICE,
    FOCUS_OWNER_SELECTED_IN_UI,
    FOCUS_RESEARCH_JUST_COMPLETED,
    FOCUS_RESULT_JUST_SPOKEN,
    FOCUS_SOURCES,
    OWNER_ID,
    STAGE_READY,
    ResearchFocusRow,
    ResearchOwnerStateRow,
    ResearchReportRow,
    ResearchRunRow,
)

logger = get_logger("app.research.focus")

#: How many DISTINCT researches the owner can still point back at. Eight is a
#: conversation's worth of history, not an archive: "üçüncü araştırma" is a plausible
#: thing to say, "sekizinci" already is not, and an unbounded stack would turn an ordinal
#: into a lottery.
FOCUS_STACK_LIMIT = 8

#: How long an unanswered clarification stays answerable. Ten minutes: long enough for the
#: owner to think, short enough that "ikincisi" said in a different conversation an hour
#: later can never silently select something. Applied on READ, so nothing has to sweep.
PENDING_CLARIFICATION_TTL = timedelta(minutes=10)

#: How many focus rows to read to build the distinct stack. The table appends, so the same
#: job may hold several consecutive rows; reading a multiple of the limit means a run of
#: repeats cannot hide the older jobs behind it.
_SCAN_ROWS = FOCUS_STACK_LIMIT * 6


@dataclass(frozen=True, slots=True)
class FocusEntry:
    """One research, as the focus names it — and exactly the shape the web track reads.

    ``research_job_id`` is the identity; everything else is description. ``completed_at``
    is the task's own ``ready_at`` (the moment the pipeline called it done), which is what
    the owner hears back as "bugün 20:19'daki".
    """

    research_job_id: str
    artifact_id: str | None = None
    topic: str = ""
    completed_at: datetime | None = None
    mode: str | None = None
    source_count: int = 0
    status: str = ""
    source_of_focus: str = ""
    selected_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_job_id": self.research_job_id,
            "artifact_id": self.artifact_id,
            "topic": self.topic,
            "completed_at": _iso(self.completed_at),
            "mode": self.mode,
            "source_count": self.source_count,
            "status": self.status,
            "source_of_focus": self.source_of_focus,
            "selected_at": _iso(self.selected_at),
        }

    def candidate_dict(self) -> dict[str, Any]:
        """The bounded shape a pending clarification stores per candidate."""
        return {
            "research_job_id": self.research_job_id,
            "artifact_id": self.artifact_id,
            "topic": self.topic,
            "completed_at": _iso(self.completed_at),
            "mode": self.mode,
            "source_count": self.source_count,
        }

    @classmethod
    def from_candidate(cls, raw: dict[str, Any]) -> FocusEntry:
        # Every read is a .get(): this row was written by an earlier release of this
        # very module and must be readable by a later one (tests/unit/
        # test_research_contracts.py audits the whole package for bare-key reads).
        artifact_id = raw.get("artifact_id")
        mode = raw.get("mode")
        return cls(
            research_job_id=str(raw.get("research_job_id") or ""),
            artifact_id=str(artifact_id) if artifact_id else None,
            topic=str(raw.get("topic") or ""),
            completed_at=_parse(raw.get("completed_at")),
            mode=str(mode) if mode else None,
            source_count=int(raw.get("source_count") or 0),
        )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else _parse(value)


# --------------------------------------------------------------------- describe


def describe(
    db: Session,
    research_job_id: str | uuid.UUID,
    *,
    source_of_focus: str = "",
    selected_at: datetime | None = None,
) -> FocusEntry | None:
    """The durable description of one research run, or None when it is not one.

    Reads the run row, the report row and the task row — never a ledger event, never a
    voice session. A job with no report row is still described (an owner may focus a run
    the UI lists); the READY check belongs to the callers that require completion.
    """
    try:
        task_id = uuid.UUID(str(research_job_id))
    except ValueError:
        return None
    run = db.get(ResearchRunRow, task_id)
    if run is None:
        return None
    report = db.get(ResearchReportRow, task_id)
    report_json = dict(getattr(report, "report_json", None) or {})
    plan_json = dict(getattr(run, "plan_json", None) or {})
    stats = report_json.get("stats") if isinstance(report_json.get("stats"), dict) else {}

    topic = str(report_json.get("topic") or plan_json.get("topic") or plan_json.get("input") or "")
    status = run.stage
    completed_at: datetime | None = None
    task = _task(db, task_id)
    if task is not None:
        topic = topic or str(getattr(task, "intent", "") or "")
        status = str(getattr(task, "status", "") or run.stage)
        completed_at = _aware(getattr(task, "ready_at", None))
    completed_at = completed_at or _aware(getattr(report, "updated_at", None))

    mode = str((stats or {}).get("mode") or plan_json.get("mode") or "") or None
    sources = report_json.get("sources") or ()
    return FocusEntry(
        research_job_id=str(task_id),
        artifact_id=(
            str(report.artifact_id) if report is not None and report.artifact_id else None
        ),
        topic=topic,
        completed_at=completed_at,
        mode=mode,
        source_count=len(sources) if isinstance(sources, (list, tuple)) else 0,
        status=status,
        source_of_focus=source_of_focus,
        selected_at=_aware(selected_at),
    )


def _task(db: Session, task_id: uuid.UUID) -> Any:
    from app.artifacts.models import Task

    try:
        return db.get(Task, task_id)
    except Exception:  # noqa: BLE001 - a deployment/test schema without the tasks table
        return None


def is_completed(db: Session, research_job_id: str | uuid.UUID) -> bool:
    """ "Completed" as the pipeline means it: stage ready AND a report body exists."""
    try:
        task_id = uuid.UUID(str(research_job_id))
    except ValueError:
        return False
    run = db.get(ResearchRunRow, task_id)
    if run is None or run.stage != STAGE_READY:
        return False
    report = db.get(ResearchReportRow, task_id)
    return report is not None and bool(report.report_json or {})


# ------------------------------------------------------------------- set / read


#: "Most recent" must never fall back to a tiebreak on a random UUID. Two focus writes
#: from the SAME process (a research completing and the announcer speaking it; two calls
#: in one test) can land on the identical ``datetime.now(UTC)`` reading on a coarse wall
#: clock (Windows' default resolution is far coarser than a microsecond) — measured as a
#: flaky ``test_voice_research_followup`` binding on 2026-09-08. Three guards, in order:
#: the default clock below never hands out one instant twice in a process (the same rule
#: as ``app.operator.focus._next_default_selected_at``; a caller with a real moment passes
#: ``now=`` and skips only this one); ``_after_the_latest_row`` pushes a new row's instant
#: past the newest row's in the table, whichever clock the caller used; and the row id is
#: itself time-ordered (``ResearchFocusRow.id`` is a counter-backed UUIDv7,
#: ``app.ids.focus_row_id``), so a tie that is nevertheless in the table —
#: rows from before these rules, two writers in concurrent transactions — reads as
#: insertion order rather than as a coin toss.
_focus_clock_lock = threading.Lock()
_focus_last_selected_at: datetime | None = None


def _next_default_selected_at() -> datetime:
    global _focus_last_selected_at
    with _focus_clock_lock:
        now = datetime.now(UTC)
        if _focus_last_selected_at is not None and now <= _focus_last_selected_at:
            now = _focus_last_selected_at + timedelta(microseconds=1)
        _focus_last_selected_at = now
        return now


def _after_the_latest_row(db: Session, now: datetime) -> datetime:
    """The stack orders focus ACTS, so a new row's instant is never at or before the
    newest row's — even when the caller's own moment ties it. Both writers of one run
    (the completion, then the announcer speaking the result) pass their own ``now`` a
    few microseconds apart on a coarse clock and tied on the runner: the spoken focus
    then lost to the earlier row on a row-id tiebreak (measured 2026-09-08)."""
    try:
        latest = db.execute(
            select(func.max(ResearchFocusRow.selected_at)).where(
                ResearchFocusRow.owner_id == OWNER_ID
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - a schema without the table: the caller's moment stands
        return now
    if latest is None:
        return now
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now if now > latest else latest + timedelta(microseconds=1)


def set_focus(
    db: Session,
    research_job_id: str | uuid.UUID,
    *,
    source: str,
    session_id: uuid.UUID | str | None = None,
    now: datetime | None = None,
) -> FocusEntry | None:
    """Append one focus entry. Returns the entry, or None when nothing was written.

    Never raises at a caller's expense: the focus is a convenience over durable research
    rows, and a deployment (or a unit-test schema) without the table must not turn a
    completed research into a failed one. The insert runs in its own SAVEPOINT so a
    failure cannot poison the caller's transaction.
    """
    if source not in FOCUS_SOURCES:
        raise ValueError(f"unknown source_of_focus: {source!r}")
    try:
        task_id = uuid.UUID(str(research_job_id))
    except ValueError:
        return None
    now = _after_the_latest_row(db, now or _next_default_selected_at())
    entry = describe(db, task_id, source_of_focus=source, selected_at=now)
    if entry is None:
        return None
    try:
        sid = uuid.UUID(str(session_id)) if session_id else None
    except ValueError:
        sid = None
    row = ResearchFocusRow(
        owner_id=OWNER_ID,
        research_job_id=task_id,
        artifact_id=(uuid.UUID(entry.artifact_id) if entry.artifact_id else None),
        source_of_focus=source,
        session_id=sid,
        selected_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("research_focus_write_failed", research_job_id=str(task_id))
        return None
    return entry


def _rows(db: Session, *, limit: int = _SCAN_ROWS) -> list[ResearchFocusRow]:
    try:
        return list(
            db.execute(
                select(ResearchFocusRow)
                .where(ResearchFocusRow.owner_id == OWNER_ID)
                # selected_at is strictly increasing per table (set_focus) and the id is
                # a time-ordered UUIDv7: a tie, should one exist, still reads as the
                # order the rows were written.
                .order_by(ResearchFocusRow.selected_at.desc(), ResearchFocusRow.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001 - a schema without the table has no focus, not an error
        return []


def focus_stack(db: Session, limit: int = FOCUS_STACK_LIMIT) -> tuple[FocusEntry, ...]:
    """The owner's focus history as DISTINCT researches, most recent first.

    The table appends, so a job set twice in a row holds two rows; the stack collapses
    them onto the newer one. That is what makes an ordinal mean what the owner means:
    "ikinci araştırma" is the second research they were talking about, never the second
    row someone happened to write.
    """
    seen: set[str] = set()
    out: list[FocusEntry] = []
    for row in _rows(db):
        job_id = str(row.research_job_id)
        if job_id in seen:
            continue
        seen.add(job_id)
        entry = describe(
            db, job_id, source_of_focus=row.source_of_focus, selected_at=row.selected_at
        )
        if entry is None:
            continue  # the run row is gone; the focus row is history, not a claim
        out.append(entry)
        if len(out) >= max(limit, 1):
            break
    return tuple(out)


def current_focus(db: Session) -> FocusEntry | None:
    stack = focus_stack(db, limit=1)
    return stack[0] if stack else None


def previous_focus(db: Session) -> FocusEntry | None:
    """The most recent DISTINCT job before the current one — "bir önceki araştırma"."""
    stack = focus_stack(db, limit=2)
    return stack[1] if len(stack) >= 2 else None


def nth_focus(db: Session, index: int) -> FocusEntry | None:
    """1 = the current focus, 2 = the one before it, ... (an owner-facing ordinal)."""
    if index < 1:
        return None
    stack = focus_stack(db, limit=max(index, 1))
    return stack[index - 1] if len(stack) >= index else None


# ---------------------------------------------------------- pending clarification


def _state_row(db: Session, *, create: bool = False) -> ResearchOwnerStateRow | None:
    try:
        row = db.get(ResearchOwnerStateRow, OWNER_ID)
    except Exception:  # noqa: BLE001 - schema without the table
        return None
    if row is None and create:
        row = ResearchOwnerStateRow(owner_id=OWNER_ID, updated_at=datetime.now(UTC))
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except Exception:  # noqa: BLE001
            logger.warning("research_owner_state_write_failed")
            return None
    return row


def set_pending_clarification(
    db: Session,
    *,
    question: str,
    candidates: tuple[FocusEntry, ...] | list[FocusEntry] = (),
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Remember the ONE question just asked and what it offered.

    Stored owner-level on purpose: the six repeated clarifications in the owner's record
    happened because the question lived nowhere at all, so the next turn — and certainly
    the next session — could not know it had been asked.
    """
    now = now or datetime.now(UTC)
    row = _state_row(db, create=True)
    if row is None:
        return None
    payload = {
        "asked_at": _iso(now),
        "question": question,
        "candidates": [c.candidate_dict() for c in candidates],
    }
    try:
        with db.begin_nested():
            row.pending_clarification_json = payload
            row.updated_at = now
            db.flush()
    except Exception:  # noqa: BLE001
        logger.warning("research_pending_clarification_write_failed")
        return None
    return payload


def peek_pending_clarification(
    db: Session, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """The live pending clarification, or None. Expiry is applied HERE, on read."""
    now = now or datetime.now(UTC)
    row = _state_row(db)
    payload = dict(getattr(row, "pending_clarification_json", None) or {}) if row else {}
    if not payload:
        return None
    asked_at = _parse(payload.get("asked_at"))
    if asked_at is not None and (now - asked_at) > PENDING_CLARIFICATION_TTL:
        return None
    return payload


def take_pending_clarification(
    db: Session, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """:func:`peek_pending_clarification`, and clear it. The answer is used once."""
    payload = peek_pending_clarification(db, now=now)
    clear_pending_clarification(db)
    return payload


def clear_pending_clarification(db: Session) -> None:
    row = _state_row(db)
    if row is None or row.pending_clarification_json is None:
        return
    try:
        with db.begin_nested():
            row.pending_clarification_json = None
            row.updated_at = datetime.now(UTC)
            db.flush()
    except Exception:  # noqa: BLE001
        logger.warning("research_pending_clarification_clear_failed")


def pending_candidates(payload: dict[str, Any] | None) -> tuple[FocusEntry, ...]:
    raw = (payload or {}).get("candidates") or ()
    return tuple(FocusEntry.from_candidate(c) for c in raw if isinstance(c, dict))


# --------------------------------------------------------------- completion hook


def note_research_ready(
    db: Session, research_job_id: str | uuid.UUID, *, now: datetime | None = None
) -> FocusEntry | None:
    """Focus the research that just completed (``research_just_completed``).

    Called from ``app.research.runs_service.update_run`` — the ONE choke point every path
    that marks a run READY goes through, REST-started and voice-started alike. Skipped
    when this job is already the current focus, so a replayed activity does not fill the
    table with identical rows.
    """
    if not is_completed(db, research_job_id):
        return None
    current = current_focus(db)
    if current is not None and current.research_job_id == str(research_job_id):
        return None
    return set_focus(db, research_job_id, source=FOCUS_RESEARCH_JUST_COMPLETED, now=now)


__all__ = [
    "FOCUS_FOLLOWUP_REFERENCE",
    "FOCUS_OWNER_SELECTED_BY_VOICE",
    "FOCUS_OWNER_SELECTED_IN_UI",
    "FOCUS_RESEARCH_JUST_COMPLETED",
    "FOCUS_RESULT_JUST_SPOKEN",
    "FOCUS_STACK_LIMIT",
    "PENDING_CLARIFICATION_TTL",
    "FocusEntry",
    "clear_pending_clarification",
    "current_focus",
    "describe",
    "focus_stack",
    "is_completed",
    "note_research_ready",
    "nth_focus",
    "peek_pending_clarification",
    "pending_candidates",
    "previous_focus",
    "set_focus",
    "set_pending_clarification",
    "take_pending_clarification",
]
