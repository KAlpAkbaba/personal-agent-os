"""The team's board: short Turkish notes the seats of a cycle write to each other while they work.

The owner's idea of 2026-10-03 ("sanki gerçek bir ofis çalışanları gibi"). A note is
``{id, at, seat, task, kind, to, reply_to, text}``; ``at`` is this server's time, never the
writer's. A note is INFORMATION: whatever it says, the assignment, the protocol and the owner's
rules win over it (``team/plans/team-board-adr.md``).

The bounds are the server's and both stores keep them through the same functions: a text of
at most ``TEXT_MAX_CHARS``, a seat the team knows, one of ``KINDS``, ``RATE_PER_TASK_HOUR``
notes per task per hour (429 beyond), no token-shaped text (``app.memory.policy``'s patterns:
refused, not stored), and the board keeps the newest ``KEEP_NOTES`` of the last
``KEEP_DAYS`` days - pruned on the write that goes past them, never by a sweep elsewhere.

Two stores: :class:`DbBoard` keeps a note as a ``team_state`` row of kind ``note`` (key = the
note's id, ``updated_at`` = its ``at``: no new table, no migration), :class:`FileBoard` as
``board.json`` beside ``team/queue.json``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.identity.tokens import OWNER_CREDENTIAL_PREFIX, SESSION_TOKEN_PREFIX
from app.memory.policy import find_secret
from app.team.models import TeamStateRow

KIND_NOTE = "note"  # a team_state row of its own kind (String(16))
KINDS: tuple[str, ...] = ("bilgi", "soru", "fikir", "cevap")
EVERYONE = "herkes"
#: The seats of a cycle: the lead, the researcher, the integrator, the inspectors, worker-1..9.
#: scripts/lib/TeamBoard.ps1 holds the same pattern (a unit test compares the two).
SEAT_PATTERN = r"^(?:lead|researcher|integrator|inspector(?:-[1-9])?|worker-[1-9])$"
#: A task id as ``team/queue.schema.json`` states it.
TASK_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"
TEXT_MAX_CHARS = 280
KEEP_NOTES = 500
KEEP_DAYS = 7
RATE_PER_TASK_HOUR = 20
READ_DEFAULT = 30
READ_MAX = KEEP_NOTES
FIELDS = ("seat", "task", "kind", "to", "reply_to", "text")
_REQUIRED = ("seat", "task", "kind", "text")
_SEAT = re.compile(SEAT_PATTERN)
_TASK = re.compile(TASK_PATTERN)
_NOTE_ID = re.compile(r"^n-\d{8}T\d{12}Z-[0-9a-f]{8}$")
#: Rate check, write and prune are one step: two posts at once may not both take the last slot.
_WRITE_LOCK = threading.Lock()
#: This system's own credentials, which the memory scanner does not know: the team token is a
#: session token, and every seat reads every note. The prefix alone, named in prose, passes.
OWN_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pagentos_session_token", re.compile(rf"{SESSION_TOKEN_PREFIX}[A-Za-z0-9_-]{{16,}}")),
    ("pagentos_owner_credential", re.compile(rf"{OWNER_CREDENTIAL_PREFIX}[A-Za-z0-9_-]{{16,}}")),
)


def find_note_secret(text: str) -> str | None:
    """The NAME of the first credential pattern the text matches (never the match), or None."""
    for name, pattern in OWN_TOKEN_PATTERNS:
        if pattern.search(text):
            return name
    return find_secret(text)


class Refused(Exception):
    """A note or a read the board does not take: ``status`` is the HTTP answer (422 / 429)."""

    def __init__(self, status: int, code: str, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.status = status
        self.code = code
        self.problems = problems

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "problems": self.problems}


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def note_id(at: datetime, suffix: str | None = None) -> str:
    """``n-<UTC to the microsecond>-<8 hex>``: ids sort in the order the notes were written."""
    tail = suffix if suffix is not None else uuid.uuid4().hex[:8]
    return f"n-{at.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{tail}"


def parse_since(since: str | None) -> str | None:
    """A ``since`` the reader gave, as a stamp the notes' ``at`` compares with; 422 otherwise."""
    if since is None or since == "":
        return None
    try:
        at = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as error:
        raise Refused(422, "invalid", [f"since is an ISO time: {since!r}"]) from error
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return stamp(at)


def _shape_problems(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["a note is a JSON object"]
    problems = [f"'{name}' is missing" for name in _REQUIRED if name not in body]
    problems += [f"'{name}' is not a field of a note" for name in body if name not in FIELDS]
    for name in FIELDS:
        if name in body and not isinstance(body[name], str):
            problems.append(f"'{name}' is a string")
    if problems:
        return problems
    text = body["text"]
    if not text.strip():
        problems.append("text is not empty")
    if len(text) > TEXT_MAX_CHARS:
        problems.append(f"text is at most {TEXT_MAX_CHARS} characters: this one is {len(text)}")
    if "\x00" in text:
        problems.append("text has no U+0000 character")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        problems.append("text is valid Unicode")
    if _SEAT.fullmatch(body["seat"]) is None:
        problems.append(f"seat is a seat of the team: {body['seat']!r}")
    to = body.get("to", EVERYONE)
    if to != EVERYONE and _SEAT.fullmatch(to) is None:
        problems.append(f"to is '{EVERYONE}' or a seat of the team: {to!r}")
    if body["kind"] not in KINDS:
        problems.append(f"kind is one of {', '.join(KINDS)}: {body['kind']!r}")
    if _TASK.fullmatch(body["task"]) is None:
        problems.append(f"task is a task id ({TASK_PATTERN}): {body['task']!r}")
    reply_to = body.get("reply_to", "")
    if reply_to and _NOTE_ID.fullmatch(reply_to) is None:
        problems.append(f"reply_to is a note id: {reply_to!r}")
    return problems


def check_note(body: Any) -> None:
    """Every rule a note's own fields must keep; a :class:`Refused` 422 for the first that breaks.
    A secret-like text is named by the pattern that matched, never echoed."""
    problems = _shape_problems(body)
    if problems:
        raise Refused(422, "invalid", problems)
    secret = find_note_secret(body["text"])
    if secret is not None:
        raise Refused(
            422,
            "secret_like",
            [f"text looks like a credential ({secret}); a note never carries one"],
        )


def build_note(body: dict[str, Any], at: datetime) -> dict[str, Any]:
    return {
        "id": note_id(at),
        "at": stamp(at),
        "seat": body["seat"],
        "task": body["task"],
        "kind": body["kind"],
        "to": body.get("to") or EVERYONE,
        "reply_to": body.get("reply_to", ""),
        "text": body["text"],
    }


def check_rate(notes: list[dict[str, Any]], task: str, at: datetime) -> None:
    """429 when ``task`` already has ``RATE_PER_TASK_HOUR`` notes in the hour before ``at``."""
    since = stamp(at - timedelta(hours=1))
    recent = sum(1 for note in notes if note.get("task") == task and str(note.get("at")) > since)
    if recent >= RATE_PER_TASK_HOUR:
        raise Refused(
            429,
            "rate_limited",
            [f"{task} has {recent} notes in the last hour; at most {RATE_PER_TASK_HOUR}"],
        )


def prune_notes(
    notes: list[dict[str, Any]], at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(kept, dropped)``: the newest ``KEEP_NOTES`` of the last ``KEEP_DAYS`` days, oldest
    first. Pure, and a fixed point: pruning what it kept drops nothing."""
    cutoff = stamp(at - timedelta(days=KEEP_DAYS))
    ordered = sorted(notes, key=lambda note: str(note.get("id", "")))
    fresh = [note for note in ordered if str(note.get("at", "")) >= cutoff]
    kept = fresh[-KEEP_NOTES:]
    kept_ids = {id(note) for note in kept}
    return kept, [note for note in ordered if id(note) not in kept_ids]


def select_notes(
    notes: list[dict[str, Any]], since: str | None, limit: int
) -> list[dict[str, Any]]:
    """At most ``limit`` notes at or after ``since``: the newest ones, newest last."""
    if not 1 <= limit <= READ_MAX:
        raise Refused(422, "invalid", [f"limit is between 1 and {READ_MAX}"])
    after = parse_since(since)
    ordered = sorted(notes, key=lambda note: str(note.get("id", "")))
    if after is not None:
        ordered = [note for note in ordered if str(note.get("at", "")) >= after]
    return ordered[-limit:]


def _post(
    notes: list[dict[str, Any]], body: Any, at: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The one post rule both stores apply: ``(the note, the notes the write prunes)``."""
    check_note(body)
    reply_to = body.get("reply_to", "")
    if reply_to and not any(note.get("id") == reply_to for note in notes):
        raise Refused(422, "invalid", [f"reply_to names no note on the board: {reply_to!r}"])
    check_rate(notes, body["task"], at)
    note = build_note(body, at)
    _, dropped = prune_notes([*notes, note], at)
    return note, dropped


# ------------------------------------------------------------------ the stores


class Board(Protocol):
    def post(self, body: Any, *, now: datetime | None = None) -> dict[str, Any]: ...
    def read(
        self, *, since: str | None = None, limit: int = READ_DEFAULT, now: datetime | None = None
    ) -> list[dict[str, Any]]: ...
    def prune(self, *, now: datetime | None = None) -> int: ...


class DbBoard:
    """``team_state`` rows of kind ``note``. The board is at most a few hundred rows: every
    rule runs on the whole of it, in Python, so SQLite and PostgreSQL cannot disagree."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._factory = session_factory

    @staticmethod
    def _notes(session: Session) -> list[dict[str, Any]]:
        rows = session.execute(select(TeamStateRow.doc).where(TeamStateRow.kind == KIND_NOTE))
        return [dict(doc) for doc in rows.scalars()]

    @staticmethod
    def _drop(session: Session, dropped: list[dict[str, Any]]) -> None:
        keys = [str(note["id"]) for note in dropped]
        if keys:
            session.execute(
                delete(TeamStateRow).where(
                    TeamStateRow.kind == KIND_NOTE, TeamStateRow.key.in_(keys)
                )
            )

    def post(self, body: Any, *, now: datetime | None = None) -> dict[str, Any]:
        at = now or utcnow()
        with _WRITE_LOCK, self._factory() as session:
            note, dropped = _post(self._notes(session), body, at)
            session.add(
                TeamStateRow(kind=KIND_NOTE, key=note["id"], doc=note, updated_at=note["at"])
            )
            self._drop(session, [n for n in dropped if n["id"] != note["id"]])
            session.commit()
        return dict(note)

    def read(
        self, *, since: str | None = None, limit: int = READ_DEFAULT, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        with self._factory() as session:
            return select_notes(self._notes(session), since, limit)

    def prune(self, *, now: datetime | None = None) -> int:
        at = now or utcnow()
        with _WRITE_LOCK, self._factory() as session:
            _, dropped = prune_notes(self._notes(session), at)
            self._drop(session, dropped)
            session.commit()
        return len(dropped)


class FileBoard:
    """``board.json`` under ``team/``: a list of notes, oldest first."""

    def __init__(self, team_root: Path) -> None:
        self.path = team_root / "board.json"

    def _load(self) -> list[dict[str, Any]]:
        try:
            notes = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [note for note in notes if isinstance(note, dict)] if isinstance(notes, list) else []

    def _save(self, notes: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes((json.dumps(notes, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        os.replace(tmp, self.path)

    def post(self, body: Any, *, now: datetime | None = None) -> dict[str, Any]:
        at = now or utcnow()
        with _WRITE_LOCK:
            notes = self._load()
            note, _ = _post(notes, body, at)
            kept, _ = prune_notes([*notes, note], at)
            self._save(kept)
        return dict(note)

    def read(
        self, *, since: str | None = None, limit: int = READ_DEFAULT, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        return select_notes(self._load(), since, limit)

    def prune(self, *, now: datetime | None = None) -> int:
        at = now or utcnow()
        with _WRITE_LOCK:
            notes = self._load()
            kept, dropped = prune_notes(notes, at)
            if dropped:
                self._save(kept)
        return len(dropped)
