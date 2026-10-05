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

The consultation (the owner's refinement of 2026-10-03, ``team/plans/team-board-talk-adr.md``):
a ``danisma`` carries the asker's ``situation`` (it is also the note's ``text``), two or three
named ``options`` ('A: ...'), ``my_lean``, at most ``FILES_MAX`` repository ``files`` and a
``topic``; ``to`` is a seat or ``auto``, which :func:`route_auto` resolves against the RUNNING
seats (:class:`Routing`). A ``cevap`` to a danisma names its ``choice``. A ``bilgi`` of the test
queue carries a ``slot`` snapshot of the line - the queue decides, the board only reports it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.identity.tokens import OWNER_CREDENTIAL_PREFIX, SESSION_TOKEN_PREFIX
from app.memory.policy import find_secret
from app.team.models import TeamStateRow

KIND_NOTE = "note"  # a team_state row of its own kind (String(16))
KINDS: tuple[str, ...] = ("bilgi", "soru", "fikir", "cevap", "danisma")
EVERYONE = "herkes"
AUTO = "auto"
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
# -- the consultation (danisma) and its answer; scripts/lib/TeamBoard.ps1 holds the same bounds
SITUATION_MAX_CHARS = 400
OPTION_MAX_CHARS = 200  # the named option, 'A: ' included
LEAN_MAX_CHARS = 200
FILES_MAX = 5
FILE_MAX_CHARS = 200
CHOICE_MAX_CHARS = 200
WAIT_MINUTES_MAX = 15
TOPICS: tuple[str, ...] = ("kod", "test", "kural")
OPTION_LETTERS = "ABC"
OTHER_CHOICE = "başka:"
CONSULT_FIELDS = ("situation", "options", "my_lean", "files", "topic")
_CONSULT_REQUIRED = ("seat", "task", "kind", "situation", "options", "my_lean")
_LIST_FIELDS = ("options", "files")
# -- the test queue's snapshot on a bilgi (scripts/lib/TeamTestSlots.ps1 writes it)
SLOT_STATES: tuple[str, ...] = ("take", "wait", "free")
SLOT_KINDS: tuple[str, ...] = ("database", "desktop", "heavy")
SLOT_FIELDS = ("state", "kinds", "holders", "waiting", "estimate_min")
SLOT_NAMES_MAX = 20
SLOT_ESTIMATE_MAX = 600
_SLOT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_OPTION_NAMED = re.compile(r"^([A-Z])\s*[:)]\s*(.*)$", re.DOTALL)
_DRIVE = re.compile(r"^[A-Za-z]:")
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
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        # a time at the edge of the calendar with an offset (9999-12-31T23:59:59-23:59) is a
        # valid ISO string that overflows when turned to UTC: refused like any bad time
        return stamp(at)
    except (ValueError, OverflowError) as error:
        raise Refused(422, "invalid", [f"since is an ISO time: {since!r}"]) from error


def _fields_of(kind: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(allowed, required)`` for a note of ``kind``: the extra fields belong to one kind each."""
    if kind == "danisma":
        return ("seat", "task", "kind", "to", "reply_to", *CONSULT_FIELDS), _CONSULT_REQUIRED
    if kind == "cevap":
        return (*FIELDS, "choice"), _REQUIRED
    if kind == "bilgi":
        return (*FIELDS, "slot"), _REQUIRED
    return FIELDS, _REQUIRED


def _text_problems(name: str, text: str, limit: int) -> list[str]:
    problems = []
    if not text.strip():
        problems.append(f"{name} is not empty")
    if len(text) > limit:
        problems.append(f"{name} is at most {limit} characters: this one is {len(text)}")
    if "\x00" in text:
        problems.append(f"{name} has no U+0000 character")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        problems.append(f"{name} is valid Unicode")
    return problems


def name_options(options: list[str]) -> tuple[list[str], list[str]]:
    """``(named, problems)``: option *i* is 'A: ...', 'B: ...', 'C: ...' by its place; an option
    that names itself must name its own place. The bound holds for the named text."""
    named: list[str] = []
    problems: list[str] = []
    if not 2 <= len(options) <= len(OPTION_LETTERS):
        problems.append(f"a danisma has 2 or 3 options: this one has {len(options)}")
    for index, option in enumerate(options[: len(OPTION_LETTERS)]):
        letter = OPTION_LETTERS[index]
        body = option.strip()
        found = _OPTION_NAMED.match(body)
        if found is not None:
            if found.group(1) != letter:
                problems.append(f"option {index + 1} is option {letter}: {option[:20]!r}")
            body = found.group(2).strip()
        text = f"{letter}: {body}"
        problems += _text_problems(f"option {letter}", body, OPTION_MAX_CHARS)
        if body and len(text) > OPTION_MAX_CHARS:
            problems.append(f"option {letter} is at most {OPTION_MAX_CHARS} characters, named")
        named.append(text)
    return named, problems


def file_problems(path: str) -> list[str]:
    """A repository path: relative, forward slashes, no '..', no drive, at most 200 chars."""
    parts = path.split("/")
    if (
        not path.strip()
        or len(path) > FILE_MAX_CHARS
        or "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or _DRIVE.match(path)
        or ".." in parts
    ):
        return [f"a file is a repository path (relative, '/', no '..'): {path[:80]!r}"]
    return []


def _consult_problems(body: dict[str, Any]) -> list[str]:
    problems = _text_problems("situation", body["situation"], SITUATION_MAX_CHARS)
    problems += _text_problems("my_lean", body["my_lean"], LEAN_MAX_CHARS)
    problems += name_options(body["options"])[1]
    files = body.get("files", [])
    if len(files) > FILES_MAX:
        problems.append(f"a danisma names at most {FILES_MAX} files: this one {len(files)}")
    for path in files:
        problems += file_problems(path)
    topic = body.get("topic", TOPICS[0])
    if topic not in TOPICS:
        problems.append(f"topic is one of {', '.join(TOPICS)}: {topic!r}")
    to = body.get("to", AUTO)
    if to == body["seat"]:
        problems.append("a danisma is never addressed to its own asker")
    elif to != AUTO and _SEAT.fullmatch(to) is None:
        problems.append(f"to is '{AUTO}' or a seat of the team: {to!r}")
    return problems


def slot_problems(slot: Any) -> list[str]:
    """The test queue's snapshot: ``{state, kinds, holders, waiting, estimate_min?}``."""
    if not isinstance(slot, dict):
        return ["slot is a JSON object"]
    problems = [
        f"'slot.{name}' is not a field of a slot" for name in slot if name not in SLOT_FIELDS
    ]
    if slot.get("state") not in SLOT_STATES:
        problems.append(f"slot.state is one of {', '.join(SLOT_STATES)}")
    kinds = slot.get("kinds")
    if not isinstance(kinds, list) or not kinds or any(k not in SLOT_KINDS for k in kinds):
        problems.append(f"slot.kinds is a list of {', '.join(SLOT_KINDS)}")
    for name in ("holders", "waiting"):
        names = slot.get(name, [])
        if (
            not isinstance(names, list)
            or len(names) > SLOT_NAMES_MAX
            or any(not isinstance(n, str) or _SLOT_NAME.fullmatch(n) is None for n in names)
        ):
            problems.append(f"slot.{name} is at most {SLOT_NAMES_MAX} seat or role names")
    estimate = slot.get("estimate_min", 0)
    if (
        isinstance(estimate, bool)
        or not isinstance(estimate, int)
        or not 0 <= estimate <= SLOT_ESTIMATE_MAX
    ):
        problems.append(f"slot.estimate_min is whole minutes, 0..{SLOT_ESTIMATE_MAX}")
    return problems


def _shape_problems(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["a note is a JSON object"]
    allowed, required = _fields_of(body.get("kind"))
    problems = [f"'{name}' is missing" for name in required if name not in body]
    problems += [f"'{name}' is not a field of a note" for name in body if name not in allowed]
    for name in allowed:
        if name not in body or name == "slot":
            continue
        if name in _LIST_FIELDS:
            value = body[name]
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                problems.append(f"'{name}' is a list of strings")
        elif not isinstance(body[name], str):
            problems.append(f"'{name}' is a string")
    if problems:
        return problems
    if "text" in body:
        problems += _text_problems("text", body["text"], TEXT_MAX_CHARS)
    if _SEAT.fullmatch(body["seat"]) is None:
        problems.append(f"seat is a seat of the team: {body['seat']!r}")
    if body["kind"] == "danisma":
        problems += _consult_problems(body)
    else:
        to = body.get("to", EVERYONE)
        if to != EVERYONE and _SEAT.fullmatch(to) is None:
            problems.append(f"to is '{EVERYONE}' or a seat of the team: {to!r}")
    if "choice" in body and len(body["choice"]) > CHOICE_MAX_CHARS:
        problems.append(f"choice is at most {CHOICE_MAX_CHARS} characters")
    if "slot" in body:
        problems += slot_problems(body["slot"])
    if body["kind"] not in KINDS:
        problems.append(f"kind is one of {', '.join(KINDS)}: {body['kind']!r}")
    if _TASK.fullmatch(body["task"]) is None:
        problems.append(f"task is a task id ({TASK_PATTERN}): {body['task']!r}")
    reply_to = body.get("reply_to", "")
    if reply_to and _NOTE_ID.fullmatch(reply_to) is None:
        problems.append(f"reply_to is a note id: {reply_to!r}")
    return problems


def _scanned_text(body: dict[str, Any]) -> str:
    """Every free text of a note, for the secret scan."""
    parts = [body.get(name, "") for name in ("text", "situation", "my_lean", "choice")]
    parts += [*body.get("options", []), *body.get("files", [])]
    return "\n".join(part for part in parts if isinstance(part, str))


def check_note(body: Any) -> None:
    """Every rule a note's own fields must keep; a :class:`Refused` 422 for the first that breaks.
    A secret-like text is named by the pattern that matched, never echoed."""
    problems = _shape_problems(body)
    if problems:
        raise Refused(422, "invalid", problems)
    secret = find_note_secret(_scanned_text(body))
    if secret is not None:
        raise Refused(
            422,
            "secret_like",
            [f"text looks like a credential ({secret}); a note never carries one"],
        )


def choice_problems(choice: str | None, asked: dict[str, Any]) -> list[str]:
    """A cevap to a danisma names one of ITS options' letters, or 'başka: <what>'."""
    letters = [str(option)[:1] for option in asked.get("options", [])]
    if choice is None:
        return [
            f"a cevap to a danisma names its choice: {', '.join(letters)} or '{OTHER_CHOICE} ...'"
        ]
    if choice in letters:
        return []
    if choice.startswith(OTHER_CHOICE) and choice[len(OTHER_CHOICE) :].strip():
        return []
    return [f"choice is one of {', '.join(letters)} or '{OTHER_CHOICE} ...': {choice[:40]!r}"]


def build_note(body: dict[str, Any], at: datetime) -> dict[str, Any]:
    if body["kind"] == "danisma":
        return {
            "id": note_id(at),
            "at": stamp(at),
            "seat": body["seat"],
            "task": body["task"],
            "kind": body["kind"],
            "to": body.get("to") or AUTO,
            "reply_to": body.get("reply_to", ""),
            "text": body["situation"],
            "situation": body["situation"],
            "options": name_options(body["options"])[0],
            "my_lean": body["my_lean"],
            "files": list(body.get("files", [])),
            "topic": body.get("topic", TOPICS[0]),
            "route": "soran seçti",
        }
    note = {
        "id": note_id(at),
        "at": stamp(at),
        "seat": body["seat"],
        "task": body["task"],
        "kind": body["kind"],
        "to": body.get("to") or EVERYONE,
        "reply_to": body.get("reply_to", ""),
        "text": body["text"],
    }
    for name in ("choice", "slot"):
        if name in body:
            note[name] = json.loads(json.dumps(body[name]))
    return note


# ------------------------------------------------------------------ routing ``to: auto``


@dataclass(frozen=True)
class RunningSeat:
    seat: str
    task: str
    area: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Routing:
    """What ``auto`` is routed over: whether a cycle runs (its lock), the status' runs
    (``{role, task}``) and every task's area from the queue."""

    live: bool = False
    runs: list[dict[str, Any]] = field(default_factory=list)
    areas: dict[str, list[str]] = field(default_factory=dict)


def _role_of(seat: str) -> str:
    return seat.split("-", 1)[0] if seat.startswith(("worker-", "inspector-")) else seat


def running_seats(notes: list[dict[str, Any]], routing: Routing) -> list[RunningSeat]:
    """The seats running now, in the status' order, the lead first. A run's seat is the seat
    whose newest note is on the run's task with the run's role (a worker says hello first); a
    run of another role that wrote nothing yet sits in its role's own seat. No live cycle:
    nobody runs, the lead neither."""
    if not routing.live:
        return []
    newest: dict[str, dict[str, Any]] = {}
    for note in sorted(notes, key=lambda n: str(n.get("id", ""))):
        newest[str(note.get("seat", ""))] = note
    seats = [RunningSeat("lead", "", [])]
    for run in routing.runs:
        role, task = str(run.get("role", "")), str(run.get("task", ""))
        if role == "lead" or not task:
            continue
        found = [
            seat
            for seat, note in newest.items()
            if note.get("task") == task and _role_of(seat) == role
        ]
        if not found and role != "worker" and _SEAT.fullmatch(role):
            found = [role]
        for seat in found:
            if all(s.seat != seat for s in seats):
                seats.append(RunningSeat(seat, task, list(routing.areas.get(task, []))))
    return seats


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().casefold()


def _covers(area: str, path: str) -> bool:
    """Whether an area entry (a file, a directory, a glob) holds ``path``."""
    a, p = _norm(area), _norm(path)
    if not a or not p:
        return False
    if "*" in a:
        return fnmatch.fnmatchcase(p, a) or p.startswith(a.split("*", 1)[0].rstrip("/") + "/")
    return p == a.rstrip("/") or p.startswith(a.rstrip("/") + "/")


def _overlap(one: str, other: str) -> bool:
    return _covers(one, other) or _covers(other, one)


def route_auto(
    asker: str, asker_area: list[str], files: list[str], topic: str, seats: list[RunningSeat]
) -> tuple[str, str]:
    """``(seat, why)`` for a danisma sent to ``auto``: a question on the owner's rules or on
    files outside every running task's area goes to the lead; else the running seat whose
    area shares the most of the asker's area and files; else the inspector for a test
    question; else the lead. Never the asker, never a seat that is not running: with nobody
    to send it to, the note goes to everyone."""
    others = [seat for seat in seats if seat.seat != asker]
    lead = any(seat.seat == "lead" for seat in others)

    def to_lead(why: str) -> tuple[str, str]:
        if lead:
            return "lead", why
        return EVERYONE, f"{why}; çalışan bir Proje Yöneticisi yok, not herkese"

    if not others:
        return EVERYONE, "şu an çalışan başka koltuk yok; not herkese"
    if topic == "kural":
        return to_lead("sahibin kuralı sorusu: Proje Yöneticisi")
    areas = [*asker_area, *(a for seat in others for a in seat.area)]
    if files and not any(_overlap(path, a) for path in files for a in areas):
        return to_lead("dosyalar iki işin de alanı dışında: Proje Yöneticisi")
    mine = [*asker_area, *files]
    best: tuple[int, RunningSeat, list[str]] | None = None
    for seat in others:
        shared = [p for p in mine if any(_overlap(p, a) for a in seat.area)]
        if shared and (best is None or len(shared) > best[0]):
            best = (len(shared), seat, shared)
    if best is not None:
        return best[1].seat, "ortak dosya: " + ", ".join(dict.fromkeys(best[2]))[:200]
    if topic == "test":
        for seat in others:
            if _role_of(seat.seat) == "inspector":
                return seat.seat, "test sorusu: Denetleyici"
    return to_lead("ortak dosyalı çalışan koltuk yok: Proje Yöneticisi")


def _route(note: dict[str, Any], notes: list[dict[str, Any]], routing: Routing | None) -> None:
    if note["kind"] != "danisma" or note["to"] != AUTO:
        return
    seats = running_seats(notes, routing or Routing())
    asker_area = routing.areas.get(note["task"], []) if routing else []
    seat, why = route_auto(note["seat"], list(asker_area), note["files"], note["topic"], seats)
    note["to"], note["route"] = seat, why


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
    notes: list[dict[str, Any]], since: str | None, limit: int, reply_to: str | None = None
) -> list[dict[str, Any]]:
    """At most ``limit`` notes at or after ``since`` (only the answers to ``reply_to`` when it
    is given): the newest ones, newest last."""
    if not 1 <= limit <= READ_MAX:
        raise Refused(422, "invalid", [f"limit is between 1 and {READ_MAX}"])
    if reply_to and _NOTE_ID.fullmatch(reply_to) is None:
        raise Refused(422, "invalid", [f"reply_to is a note id: {reply_to[:40]!r}"])
    after = parse_since(since)
    ordered = sorted(notes, key=lambda note: str(note.get("id", "")))
    if after is not None:
        ordered = [note for note in ordered if str(note.get("at", "")) >= after]
    if reply_to:
        ordered = [note for note in ordered if note.get("reply_to") == reply_to]
    return ordered[-limit:]


def _post(
    notes: list[dict[str, Any]], body: Any, at: datetime, routing: Routing | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The one post rule both stores apply: ``(the note, the notes the write prunes)``."""
    check_note(body)
    reply_to = body.get("reply_to", "")
    asked = next((note for note in notes if note.get("id") == reply_to), None) if reply_to else None
    if reply_to and asked is None:
        raise Refused(422, "invalid", [f"reply_to names no note on the board: {reply_to!r}"])
    if body["kind"] == "cevap" and asked is not None and asked.get("kind") == "danisma":
        problems = choice_problems(body.get("choice"), asked)
        if problems:
            raise Refused(422, "invalid", problems)
    elif "choice" in body:
        raise Refused(422, "invalid", ["choice answers a danisma only"])
    check_rate(notes, body["task"], at)
    note = build_note(body, at)
    _route(note, notes, routing)
    _, dropped = prune_notes([*notes, note], at)
    return note, dropped


# ------------------------------------------------------------------ the stores


class Board(Protocol):
    def post(
        self, body: Any, *, now: datetime | None = None, routing: Routing | None = None
    ) -> dict[str, Any]: ...
    def read(
        self,
        *,
        since: str | None = None,
        limit: int = READ_DEFAULT,
        now: datetime | None = None,
        reply_to: str | None = None,
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

    def post(
        self, body: Any, *, now: datetime | None = None, routing: Routing | None = None
    ) -> dict[str, Any]:
        at = now or utcnow()
        with _WRITE_LOCK, self._factory() as session:
            note, dropped = _post(self._notes(session), body, at, routing)
            session.add(
                TeamStateRow(kind=KIND_NOTE, key=note["id"], doc=note, updated_at=note["at"])
            )
            self._drop(session, [n for n in dropped if n["id"] != note["id"]])
            session.commit()
        return dict(note)

    def read(
        self,
        *,
        since: str | None = None,
        limit: int = READ_DEFAULT,
        now: datetime | None = None,
        reply_to: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._factory() as session:
            return select_notes(self._notes(session), since, limit, reply_to)

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

    def post(
        self, body: Any, *, now: datetime | None = None, routing: Routing | None = None
    ) -> dict[str, Any]:
        at = now or utcnow()
        with _WRITE_LOCK:
            notes = self._load()
            note, _ = _post(notes, body, at, routing)
            kept, _ = prune_notes([*notes, note], at)
            self._save(kept)
        return dict(note)

    def read(
        self,
        *,
        since: str | None = None,
        limit: int = READ_DEFAULT,
        now: datetime | None = None,
        reply_to: str | None = None,
    ) -> list[dict[str, Any]]:
        return select_notes(self._load(), since, limit, reply_to)

    def prune(self, *, now: datetime | None = None) -> int:
        at = now or utcnow()
        with _WRITE_LOCK:
            notes = self._load()
            kept, dropped = prune_notes(notes, at)
            if dropped:
                self._save(kept)
        return len(dropped)
