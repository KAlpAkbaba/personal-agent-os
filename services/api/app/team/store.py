"""Where the team's queue, lock and reports live: the database, or the files (pilot-02).

Two stores, one contract (:class:`TeamStore`). :class:`DbStore` is the Cloud Core's
``team_state`` table - one row per task, one lock row, the reports and the proposals as text -
so an approval can be given with the home PC off and the office PC reads the same queue.
:class:`FileStore` is ``team/queue.json`` + ``team/lock.json`` as before: the home PC without
the API keeps working, and the Onay Merkezi reads whichever ``app.state.team_store`` says.

The rules here are the ones ``scripts/lib/TeamQueue.ps1`` states, and each is held to its
other half by a test: a task is valid when ``team/queue.schema.json`` says so (the copy
beside this file is compared to it byte for byte), and the lock is stale after
``LOCK_STALE_HOURS`` (the test reads the PowerShell constant).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.team import models_setting
from app.team.models import KIND_LOCK, KIND_PROPOSAL, KIND_REPORT, KIND_TASK, TeamStateRow

LOCK_STALE_HOURS = 6  # TeamQueue.ps1: $script:TeamLockStaleHours
TEXT_MAX_CHARS = 20000
LOCK_KEY = "lock"
KIND_STATUS = "status"  # a team_state row of its own kind (String(16)): no new table
STATUS_KEY = "status"
KIND_MODELS = "models"  # the model setting (ADR-0214 addendum 7): one row, as the status is
MODELS_KEY = "models"
SCHEMA_PATH = Path(__file__).with_name("queue.schema.json")
_REPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,78}\.md$")
#: A proposal's file name under ``team/proposals/`` (``scripts/team/cycle.ps1`` posts it).
PROPOSAL_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,120}\.md$"
PROPOSAL_MAX_CHARS = 200_000
#: ``team_state.key`` is VARCHAR(80) (migration 0063): the name is the row's key, kept whole.
KEY_WIDTH: int = TeamStateRow.__table__.c.key.type.length
_PROPOSAL_NAME = re.compile(PROPOSAL_NAME_PATTERN)
#: Windows keeps these stems for devices whatever follows the first dot: ``nul.md`` is the null
#: device there, not a file (``os.replace`` onto it fails; ``com1.md`` opens a serial port).
_DEVICE_STEM = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])", re.IGNORECASE)
_WRITE_LOCK = threading.RLock()


class Stale(Exception):
    """The write did not see the current version (or the lock is not the writer's)."""


class Invalid(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


#: ``team_state.updated_at`` is VARCHAR(32) (migration 0063).
VERSION_WIDTH = 32


def lock_version(at: datetime, machine: str, cycle_id: str, pid: int) -> str:
    """The lock row's write precondition: the second it was taken and WHO took it, in a
    form that always fits the column.

    It used to be ``<stamp>|<machine>|<cycle>|<pid>`` - 42 characters for ``MAIL`` and
    ``adr0224-02`` - and PostgreSQL refused it (``value too long for type character
    varying(32)``): the first cycle in database mode died on its first call (production,
    2026-10-01), while every test, on SQLite, which does not enforce a VARCHAR's length,
    was green. The taker is now a digest: two takers inside one second still differ, and
    the lock document itself (``doc``) is what names the machine and the cycle."""
    taker = hashlib.sha256(f"{machine}|{cycle_id}|{pid}".encode()).hexdigest()
    version = f"{stamp(at)}|{taker[: VERSION_WIDTH - len(stamp(at)) - 1]}"
    assert len(version) <= VERSION_WIDTH
    return version


def _copy(document: Any) -> Any:
    return json.loads(json.dumps(document))


# ------------------------------------------------------------------ the schema

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}


def _problems(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str) -> list[str]:
    if "$ref" in schema:
        return _problems(value, root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, where)
    found: list[str] = []
    if "const" in schema and value != schema["const"]:
        found.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where}: {value!r} is not one of the allowed values")
    kind = schema.get("type")
    if kind is not None:
        wrong = not isinstance(value, _TYPES[kind]) or (
            kind in ("integer", "number") and isinstance(value, bool)
        )
        if wrong:
            return [*found, f"{where}: must be {kind}"]
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            found.append(f"{where}: does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            found.append(f"{where}: is too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{where}: is below {schema['minimum']}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            found.append(f"{where}: has more than {schema['maxItems']} items")
        for index, item in enumerate(value):
            if "items" in schema:
                found += _problems(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        found += [
            f"{where}: '{n}' is missing" for n in schema.get("required", []) if n not in value
        ]
        known = schema.get("properties", {})
        for name, item in value.items():
            if name in known:
                found += _problems(item, known[name], root, f"{where}.{name}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{where}: '{name}' is not a field of the protocol")
    return found


def task_problems(task: Any) -> list[str]:
    """Every way ``task`` breaks ``queue.schema.json``'s task definition. Empty when none."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _problems(task, schema["$defs"]["task"], schema, "task")


# ------------------------------------------------------------------ the lock rules


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def lock_is_running(lock: dict[str, Any] | None, at: datetime) -> bool:
    """A held, non-stale lock. One that does not say when it was taken cannot be shown stale."""
    if not isinstance(lock, dict) or lock.get("held") is not True:
        return False
    acquired = _parse(lock.get("acquired_at"))
    if acquired is None:
        return True
    return at - acquired < timedelta(hours=LOCK_STALE_HOURS)


def lock_decision(
    lock: dict[str, Any] | None, machine: str, at: datetime, *, takeover_dead: bool
) -> dict[str, Any]:
    """TeamQueue.ps1 ``Get-TeamLockDecision``: free / stale / ours / held (+ ``dead``).

    ``takeover_dead`` is the client saying that the process that took OUR lock is gone (only
    the client can look at its own process table); it is never believed for another machine.
    """
    if not isinstance(lock, dict) or lock.get("held") is not True:
        return {"acquired": True, "kind": "free", "holder": "", "since": "", "pid": 0}
    holder = str(lock.get("machine", ""))
    base = {
        "holder": holder,
        "since": str(lock.get("acquired_at", "")),
        "pid": int(lock.get("pid") or 0),
    }
    if not lock_is_running(lock, at):
        return {"acquired": True, "kind": "stale", **base}
    if holder and holder.upper() == machine.upper():
        return {"acquired": takeover_dead, "kind": "dead" if takeover_dead else "ours", **base}
    return {"acquired": False, "kind": "held", **base}


def _new_lock(machine: str, cycle_id: str, pid: int, at: datetime) -> dict[str, Any]:
    return {
        "held": True,
        "machine": machine,
        "cycle_id": cycle_id,
        "pid": pid,
        "acquired_at": stamp(at),
    }


def _may_release(lock: dict[str, Any] | None, machine: str) -> None:
    if isinstance(lock, dict) and lock.get("held") is True:
        if str(lock.get("machine", "")).upper() != machine.upper():
            raise Stale(f"the lock is held by {lock.get('machine')}, not {machine}")


def _is_device_name(name: str) -> bool:
    return _DEVICE_STEM.fullmatch(name.split(".", 1)[0]) is not None


def _text_problems(what: str, text: str) -> list[str]:
    """Why neither store may keep ``text``: one rule, so SQLite and a file do not accept what
    PostgreSQL refuses (JSONB has no U+0000 - a 500 on the Cloud Core until 2026-10-01)."""
    if "\x00" in text:
        return [f"{what} text has no U+0000 character"]
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return [f"{what} text is valid Unicode (it holds half of a surrogate pair)"]
    return []


def _check_report(name: Any, text: Any) -> None:
    # ``fullmatch``: ``$`` alone also matches before a final \n
    if not isinstance(name, str) or not _REPORT_NAME.fullmatch(name) or _is_device_name(name):
        raise Invalid([f"a report name is a file name ending in .md: {name!r}"])
    if not isinstance(text, str):
        raise Invalid(["a report's text is a string"])
    problems = _text_problems("a report's", text)
    if problems:
        raise Invalid(problems)


def proposal_name_problems(name: Any) -> list[str]:
    """Why ``name`` cannot name a proposal in either store. Empty when it can.

    ``fullmatch``: ``$`` also matches before a final newline. A name the key column cannot
    hold is refused, never cut - a cut name would be another proposal's key. A name the file
    store's machine reads as a device is refused on both stores: one rule for the name."""
    if not isinstance(name, str) or _PROPOSAL_NAME.fullmatch(name) is None:
        return [f"a proposal name matches {PROPOSAL_NAME_PATTERN}: {name!r}"]
    if len(name) > KEY_WIDTH:
        return [f"a proposal name is at most {KEY_WIDTH} characters: this one is {len(name)}"]
    if _is_device_name(name):
        return [f"a proposal name is not a Windows device name: {name!r}"]
    return []


def _check_proposal(name: Any, text: Any) -> None:
    problems = proposal_name_problems(name)
    if not isinstance(text, str):
        problems.append("a proposal's text is a string")
    elif len(text) > PROPOSAL_MAX_CHARS:
        problems.append(f"a proposal's text is at most {PROPOSAL_MAX_CHARS} characters")
    else:
        problems.extend(_text_problems("a proposal's", text))
    if problems:
        raise Invalid(problems)


def _check_models(document: Any) -> None:
    """What a store keeps is the whole setting, stamped: all five roles, ``fallback`` and the
    ``updated_at`` its writer gave it (``models_setting`` holds the rules)."""
    problems = [text for _, text in models_setting.problems(document, strict=True)]
    if not problems and _parse(document.get("updated_at")) is None:
        problems.append("the setting's updated_at is UTC, YYYY-MM-DDTHH:MM:SSZ")
    if problems:
        raise Invalid(problems)


# ------------------------------------------------------------------ the contract


class TeamStore(Protocol):
    kind: str

    def read_queue(self) -> dict[str, Any]: ...
    def put_task(self, task: dict[str, Any], expected_updated_at: str | None) -> dict[str, Any]: ...
    def read_lock(self) -> dict[str, Any] | None: ...
    def acquire_lock(
        self,
        *,
        machine: str,
        cycle_id: str,
        pid: int,
        takeover_dead: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]: ...
    def release_lock(self, *, machine: str, cycle_id: str, now: datetime | None = None) -> None: ...
    def put_report(self, name: str, text: str, *, now: datetime | None = None) -> None: ...
    def newest_report(self) -> dict[str, str] | None: ...
    def put_proposal(self, name: str, text: str, *, now: datetime | None = None) -> None: ...
    def read_proposal(self, name: str) -> str | None: ...
    def read_status(self) -> dict[str, Any] | None: ...
    def put_status(self, document: dict[str, Any]) -> None: ...
    def read_models(self) -> dict[str, Any] | None: ...
    def put_models(self, document: dict[str, Any]) -> None: ...


def _check_put(
    task: Any, expected: str | None, current_updated_at: str | None, exists: bool
) -> None:
    problems = task_problems(task)
    if problems:
        raise Invalid(problems)
    if not exists and expected is not None:
        raise Stale(f"{task['id']} is not in the queue (expected version {expected})")
    if exists and current_updated_at != expected:
        raise Stale(f"{task['id']} changed since it was read")


class FileStore:
    """``queue.json``, ``lock.json``, ``reports/*.md`` and ``proposals/*.md`` under ``team/``."""

    kind = "file"

    def __init__(self, team_root: Path) -> None:
        self.root = team_root

    def read_queue(self) -> dict[str, Any]:
        return json.loads((self.root / "queue.json").read_text(encoding="utf-8"))

    def _write(self, path: Path, document: dict[str, Any]) -> None:
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, path)

    def put_task(self, task: dict[str, Any], expected_updated_at: str | None) -> dict[str, Any]:
        with _WRITE_LOCK:
            queue = self.read_queue()
            tasks = queue.setdefault("tasks", [])
            index = next((i for i, t in enumerate(tasks) if t.get("id") == task.get("id")), None)
            current = None if index is None else tasks[index].get("updated_at")
            _check_put(task, expected_updated_at, current, index is not None)
            stored = _copy(task)
            if index is None:
                tasks.append(stored)
            else:
                tasks[index] = stored
            self._write(self.root / "queue.json", queue)
        return _copy(stored)

    def read_lock(self) -> dict[str, Any] | None:
        try:
            lock = json.loads((self.root / "lock.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return lock if isinstance(lock, dict) else None

    def acquire_lock(
        self,
        *,
        machine: str,
        cycle_id: str,
        pid: int,
        takeover_dead: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        at = now or utcnow()
        with _WRITE_LOCK:
            result = lock_decision(self.read_lock(), machine, at, takeover_dead=takeover_dead)
            if result["acquired"]:
                self._write(self.root / "lock.json", _new_lock(machine, cycle_id, pid, at))
        return result

    def release_lock(self, *, machine: str, cycle_id: str, now: datetime | None = None) -> None:
        with _WRITE_LOCK:
            _may_release(self.read_lock(), machine)
            self._write(self.root / "lock.json", {"held": False})

    def read_status(self) -> dict[str, Any] | None:
        try:
            status = json.loads((self.root / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return status if isinstance(status, dict) else None

    def put_status(self, document: dict[str, Any]) -> None:
        with _WRITE_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            self._write(self.root / "status.json", _copy(document))

    def read_models(self) -> dict[str, Any] | None:
        """``models.json`` as it is on disk (a person may have written it): the reader fills
        and judges it (``models_setting.effective``)."""
        try:
            setting = json.loads((self.root / "models.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return setting if isinstance(setting, dict) else None

    def put_models(self, document: dict[str, Any]) -> None:
        _check_models(document)
        with _WRITE_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            self._write(self.root / "models.json", _copy(document))

    def put_report(self, name: str, text: str, *, now: datetime | None = None) -> None:
        _check_report(name, text)
        folder = self.root / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(text.encode("utf-8"))

    def newest_report(self) -> dict[str, str] | None:
        reports = sorted(
            (self.root / "reports").glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name)
        )
        if not reports:
            return None
        try:
            text = reports[-1].read_text(encoding="utf-8")[:TEXT_MAX_CHARS]
        except OSError:
            return None
        return {"file": reports[-1].name, "text": text}

    def put_proposal(self, name: str, text: str, *, now: datetime | None = None) -> None:
        _check_proposal(name, text)
        folder = self.root / "proposals"
        with _WRITE_LOCK:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / (name + ".tmp")
            tmp.write_bytes(text.encode("utf-8"))
            os.replace(tmp, folder / name)

    def read_proposal(self, name: str) -> str | None:
        if proposal_name_problems(name):
            return None
        try:
            return (self.root / "proposals" / name).read_bytes().decode("utf-8")
        except (OSError, ValueError):
            return None


class DbStore:
    """The Cloud Core's ``team_state`` table.

    ``session_factory`` returns a context manager yielding a Session (a ``sessionmaker`` does,
    and so does ``ArtifactRuntime.session``). Every write is conditional on the version the
    writer saw (``UPDATE ... WHERE updated_at = :expected``), so two writers cannot both win.
    """

    kind = "db"

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._factory = session_factory

    def read_queue(self) -> dict[str, Any]:
        with self._factory() as session:
            rows = session.execute(
                select(TeamStateRow.doc).where(TeamStateRow.kind == KIND_TASK)
            ).scalars()
            tasks = [_copy(doc) for doc in rows]
        tasks.sort(key=lambda t: (str(t.get("created_at", "")), str(t.get("id", ""))))
        return {"version": 1, "tasks": tasks}

    def put_task(self, task: dict[str, Any], expected_updated_at: str | None) -> dict[str, Any]:
        stored = _copy(task)
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_TASK, str(task.get("id", ""))))
            _check_put(
                task, expected_updated_at, None if row is None else row.updated_at, row is not None
            )
            if row is None:
                session.add(
                    TeamStateRow(
                        kind=KIND_TASK, key=task["id"], doc=stored, updated_at=task["updated_at"]
                    )
                )
                try:
                    session.commit()
                except IntegrityError as error:
                    session.rollback()
                    raise Stale(f"{task['id']} was created by someone else") from error
                return _copy(stored)
            changed = session.execute(
                update(TeamStateRow)
                .where(
                    TeamStateRow.kind == KIND_TASK,
                    TeamStateRow.key == task["id"],
                    TeamStateRow.updated_at == expected_updated_at,
                )
                .values(doc=stored, updated_at=task["updated_at"])
            )
            if changed.rowcount != 1:
                session.rollback()
                raise Stale(f"{task['id']} changed since it was read")
            session.commit()
        return _copy(stored)

    def read_lock(self) -> dict[str, Any] | None:
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_LOCK, LOCK_KEY))
            return None if row is None else _copy(row.doc)

    def _write_lock(
        self, session: Session, expected: str | None, doc: dict[str, Any], version: str
    ) -> None:
        """Conditional on the version read; ``version`` differs from it even inside one second."""
        if expected is None:
            session.add(TeamStateRow(kind=KIND_LOCK, key=LOCK_KEY, doc=doc, updated_at=version))
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise Stale("the lock was taken by someone else") from error
            return
        changed = session.execute(
            update(TeamStateRow)
            .where(
                TeamStateRow.kind == KIND_LOCK,
                TeamStateRow.key == LOCK_KEY,
                TeamStateRow.updated_at == expected,
            )
            .values(doc=doc, updated_at=version)
        )
        if changed.rowcount != 1:
            session.rollback()
            raise Stale("the lock changed while it was being written")
        session.commit()

    def acquire_lock(
        self,
        *,
        machine: str,
        cycle_id: str,
        pid: int,
        takeover_dead: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        at = now or utcnow()
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_LOCK, LOCK_KEY))
            current = None if row is None else _copy(row.doc)
            result = lock_decision(current, machine, at, takeover_dead=takeover_dead)
            if not result["acquired"]:
                return result
            version = lock_version(at, machine, cycle_id, pid)
            try:
                self._write_lock(
                    session,
                    None if row is None else row.updated_at,
                    _new_lock(machine, cycle_id, pid, at),
                    version,
                )
            except Stale:
                # Two machines raced for it and the other won: we hold nothing.
                return {"acquired": False, "kind": "held", "holder": "", "since": "", "pid": 0}
        return result

    def release_lock(self, *, machine: str, cycle_id: str, now: datetime | None = None) -> None:
        at = now or utcnow()
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_LOCK, LOCK_KEY))
            if row is None:
                return
            _may_release(row.doc, machine)
            self._write_lock(session, row.updated_at, {"held": False}, f"{stamp(at)}|released")

    def read_status(self) -> dict[str, Any] | None:
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_STATUS, STATUS_KEY))
            return None if row is None else _copy(row.doc)

    def put_status(self, document: dict[str, Any]) -> None:
        """The newest status wins: it is a heartbeat, not a versioned document."""
        doc = _copy(document)
        version = str(doc.get("updated_at", ""))
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_STATUS, STATUS_KEY))
            if row is None:
                session.add(
                    TeamStateRow(kind=KIND_STATUS, key=STATUS_KEY, doc=doc, updated_at=version)
                )
            else:
                row.doc = doc
                row.updated_at = version
            try:
                session.commit()
            except IntegrityError:
                session.rollback()  # two first writes raced: the other one's heartbeat stands

    def read_models(self) -> dict[str, Any] | None:
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_MODELS, MODELS_KEY))
            return None if row is None else _copy(row.doc)

    def put_models(self, document: dict[str, Any]) -> None:
        """The owner's last choice wins: one row, replaced whole."""
        _check_models(document)
        doc = _copy(document)
        version = str(doc["updated_at"])
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_MODELS, MODELS_KEY))
            if row is None:
                session.add(
                    TeamStateRow(kind=KIND_MODELS, key=MODELS_KEY, doc=doc, updated_at=version)
                )
            else:
                row.doc = doc
                row.updated_at = version
            try:
                session.commit()
            except IntegrityError:
                # Two first puts raced and the other's row is there: ours replaces it.
                session.rollback()
                session.execute(
                    update(TeamStateRow)
                    .where(TeamStateRow.kind == KIND_MODELS, TeamStateRow.key == MODELS_KEY)
                    .values(doc=doc, updated_at=version)
                )
                session.commit()

    def put_report(self, name: str, text: str, *, now: datetime | None = None) -> None:
        _check_report(name, text)
        at = stamp(now or utcnow())
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_REPORT, name))
            if row is None:
                session.add(
                    TeamStateRow(kind=KIND_REPORT, key=name, doc={"text": text}, updated_at=at)
                )
            else:
                row.doc = {"text": text}
                row.updated_at = at
            session.commit()

    def newest_report(self) -> dict[str, str] | None:
        with self._factory() as session:
            row = session.execute(
                select(TeamStateRow)
                .where(TeamStateRow.kind == KIND_REPORT)
                .order_by(TeamStateRow.updated_at.desc(), TeamStateRow.key.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {"file": row.key, "text": str(row.doc.get("text", ""))[:TEXT_MAX_CHARS]}

    def put_proposal(self, name: str, text: str, *, now: datetime | None = None) -> None:
        """One row per proposal, ``key`` = its file name; a second put replaces the text."""
        _check_proposal(name, text)
        at = stamp(now or utcnow())
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_PROPOSAL, name))
            if row is None:
                session.add(
                    TeamStateRow(kind=KIND_PROPOSAL, key=name, doc={"text": text}, updated_at=at)
                )
            else:
                row.doc = {"text": text}
                row.updated_at = at
            try:
                session.commit()
            except IntegrityError:
                # Two first puts of one name raced and the other's row is there: ours replaces.
                session.rollback()
                session.execute(
                    update(TeamStateRow)
                    .where(TeamStateRow.kind == KIND_PROPOSAL, TeamStateRow.key == name)
                    .values(doc={"text": text}, updated_at=at)
                )
                session.commit()

    def read_proposal(self, name: str) -> str | None:
        if proposal_name_problems(name):
            return None
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_PROPOSAL, name))
            if row is None:
                return None
            text = row.doc.get("text")
            return text if isinstance(text, str) else None
