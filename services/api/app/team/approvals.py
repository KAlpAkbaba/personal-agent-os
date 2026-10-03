"""The owner's two decidable gates, read from and written to ``team/queue.json``.

A task at ``awaiting_owner`` (the idea) or ``awaiting_release`` (the release) waits for the
owner. Onayla on the idea writes ``approved``; Onayla on the release leaves the state at
``awaiting_release`` and raises ``release_approved`` (the cycle reads ``approved`` as "assign a
worker", so a release approval must not use it); Reddet writes ``stopped`` with the owner's
reason at either gate. All of it is
written where the NEXT cycle reads it (the :class:`app.team.store.TeamStore` the app is wired
with: the Cloud Core's database, or ``team/queue.json``), and it starts nothing - approving a
release changes one task and does not run a release.

A running cycle closes the gates only on the file store (:func:`decisions_open`): there the
cycle rewrites the whole queue file at its end and would overwrite ours. On the database store
the cycle writes back only the tasks it changed, each conditional on the ``updated_at`` it
read, and it does not touch a task waiting at a gate - so the owner decides while it runs, and
a task that did change meanwhile is a ``stale_write``, never an overwrite.

An idea's text is read through the same store (``read_proposal``): on the Cloud Core there is
no ``team/`` folder. The file under ``team/`` is the fallback of the file store only.

Every decision is a ledger event, recorded BEFORE the queue is written: if the ledger refuses,
the queue is untouched and the owner is told, so no decision exists that the ledger does not
know. The ledger's vocabulary is closed; the constants below are what the lead adds to it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.team import store as team_store

#: awaiting_* state -> the gate's name as the owner says it (ASCII, as TeamQueue.ps1).
GATES: dict[str, str] = {"awaiting_owner": "fikir", "awaiting_release": "yayin"}

APPROVED_STATE = "approved"
RELEASE_GATE_STATE = "awaiting_release"
STOPPED_STATE = "stopped"

SUBSYSTEM_TEAM = "team"
EVENT_TASK_APPROVED = "team.task.approved"
EVENT_TASK_REJECTED = "team.task.rejected"

LOCK_STALE_HOURS = team_store.LOCK_STALE_HOURS  # TeamQueue.ps1: $script:TeamLockStaleHours
TEXT_MAX_CHARS = 20000
_WRITE_LOCK = threading.Lock()


@dataclass
class Refused(Exception):
    """A decision the queue or the rules will not take. ``status`` is the HTTP answer."""

    status: int
    code: str
    message: str
    extra: dict[str, Any] = field(default_factory=dict)

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_queue(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def gate_of(task: dict[str, Any]) -> str | None:
    return GATES.get(str(task.get("state", "")))


def normalize_gate(word: str | None) -> str | None:
    """A spoken or typed gate name -> ``fikir`` / ``yayin``, or None. Turkish dotted/dotless i."""
    if not word:
        return None
    text = word.strip().replace("I", "ı").replace("İ", "i").lower().replace("ı", "i")
    return text if text in GATES.values() else None


def cycle_running(team_root: Path, *, now: datetime | None = None) -> bool:
    """A held, non-stale ``team/lock.json``. A missing or unreadable lock is not a running cycle."""
    files = team_store.FileStore(team_root)
    return team_store.lock_is_running(files.read_lock(), now or _now(), files.read_status())


def _read_inside(team_root: Path, relative: str) -> str | None:
    """The text of ``relative`` (repo-relative, under ``team/``), or None. Never leaves team/."""
    try:
        target = (team_root.parent / relative).resolve()
        target.relative_to(team_root.resolve())
        if not target.is_file():
            return None
        return target.read_text(encoding="utf-8")[:TEXT_MAX_CHARS]
    except (OSError, ValueError):
        return None


def decisions_open(store: team_store.TeamStore, lock: dict[str, Any] | None, at: datetime) -> bool:
    """Whether the owner can decide now. The listing says it and :func:`decide` enforces it.

    Always on the database store: its writes are per task and conditional (module docstring).
    On the file store only while no cycle holds the queue (its status is its heartbeat)."""
    if store.kind == "db":
        return True
    return not team_store.lock_is_running(lock, at, store.read_status())


PROPOSALS_PREFIX = "team/proposals/"


def _proposal_text(store: team_store.TeamStore, team_root: Path, proposal: str) -> str | None:
    if not proposal:
        return None
    normalized = proposal.replace("\\", "/")
    if not normalized.startswith("team/"):
        return proposal  # prose
    # A path: its text from the store (the file name is the store's key), or nothing.
    text = None
    if normalized.startswith(PROPOSALS_PREFIX):
        text = store.read_proposal(normalized[len(PROPOSALS_PREFIX) :])
    if text is None and store.kind == "file":
        return _read_inside(team_root, normalized)  # written by hand under team/
    return None if text is None else text[:TEXT_MAX_CHARS]


def newest_cycle_report(team_root: Path) -> dict[str, str] | None:
    return team_store.FileStore(team_root).newest_report()


def list_pending(
    queue: dict[str, Any], team_root: Path, store: team_store.TeamStore | None = None
) -> list[dict[str, Any]]:
    """What waits at the owner's two gates. ``store`` is where a proposal's text is read
    (default: the files under ``team_root``)."""
    store = store or team_store.FileStore(team_root)
    out = []
    for task in queue.get("tasks", []):
        gate = gate_of(task)
        if gate is None:
            continue
        proposal = str(task.get("proposal", ""))
        out.append(
            {
                "task_id": task["id"],
                "title": task.get("title", ""),
                "gate": gate,
                "state": task["state"],
                "goal": task.get("goal", ""),
                "acceptance": task.get("acceptance", ""),
                "proposal": proposal or None,
                "proposal_text": _proposal_text(store, team_root, proposal),
                "sha": task.get("sha"),
                "reports": task.get("reports", []),
                "updated_at": task.get("updated_at"),
            }
        )
    return out


def _resolve(
    queue: dict[str, Any],
    *,
    task_id: str | None,
    gate: str | None,
    channel: str,
) -> tuple[dict[str, Any], str]:
    tasks = queue.get("tasks", [])
    named_gate = normalize_gate(gate)
    if channel == "voice":
        if named_gate is None:
            raise Refused(
                422, "gate_required", "Hangi onay: fikir mi, yayın mı? Söylenmedi, yapılmadı."
            )
        waiting = [t for t in tasks if gate_of(t) == named_gate]
        if task_id is not None:
            waiting = [t for t in waiting if t["id"] == task_id]
            if not waiting:
                raise Refused(
                    409, "gate_mismatch", f"{task_id} bu kapıda ({named_gate}) beklemiyor."
                )
        if not waiting:
            raise Refused(409, "nothing_waiting", f"{named_gate} kapısında bekleyen görev yok.")
        if len(waiting) > 1:
            raise Refused(
                409,
                "ambiguous",
                f"{named_gate} kapısında {len(waiting)} görev bekliyor; hangisi belli değil.",
                {"candidates": [t["id"] for t in waiting]},
            )
        return waiting[0], named_gate
    if not task_id:
        raise Refused(422, "task_required", "Görev kimliği gerekli.")
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        raise Refused(404, "unknown_task", f"{task_id} kuyrukta yok.")
    actual = gate_of(task)
    if actual is None:
        raise Refused(
            409, "not_at_a_gate", f"{task_id} bir onay kapısında değil ({task['state']})."
        )
    if gate and named_gate != actual:
        raise Refused(409, "gate_mismatch", f"{task_id} artık '{actual}' kapısında; sayfa eski.")
    return task, actual


def decide(
    team_root: Path,
    *,
    store: team_store.TeamStore | None = None,
    task_id: str | None,
    decision: str,
    reason: str | None,
    gate: str | None,
    channel: str,
    record: Callable[[dict[str, Any]], None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one owner decision to the queue file, or raise :class:`Refused`.

    ``record`` writes the ledger event (it receives the event's facts) and may raise
    :class:`Refused` itself; it runs before the queue is written. ``store`` is where the queue
    and the lock live (default: the files under ``team_root``).
    """
    store = store or team_store.FileStore(team_root)
    reason = (reason or "").strip()
    if decision == "reject" and not reason:
        raise Refused(422, "reason_required", "Reddetmek için bir gerekçe gerekli.")
    at = now or _now()
    with _WRITE_LOCK:
        queue = store.read_queue()
        task, actual_gate = _resolve(queue, task_id=task_id, gate=gate, channel=channel)
        lock = store.read_lock()
        if not decisions_open(store, lock, at):
            raise Refused(409, "cycle_running", "Bir döngü kuyruğu tutuyor; bitince tekrar dene.")
        running = team_store.lock_is_running(lock, at, store.read_status())
        from_state = task["state"]
        seen_updated_at = task.get("updated_at")
        release_approval = decision == "approve" and from_state == RELEASE_GATE_STATE
        if decision == "reject":
            to_state = STOPPED_STATE
        elif release_approval:
            to_state = RELEASE_GATE_STATE  # never "approved": that means "assign a worker"
        else:
            to_state = APPROVED_STATE
        record(
            {
                "task_id": task["id"],
                "title": task.get("title", ""),
                "gate": actual_gate,
                "decision": decision,
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
                "channel": channel,
                "updated_at": task.get("updated_at", ""),
            }
        )
        task["state"] = to_state
        if decision == "reject":
            task["reason"] = reason
        if release_approval:
            task["release_approved"] = True
            task["release_approved_at"] = _stamp(at)
            task["release_approved_by"] = channel  # shell | voice
        task["updated_at"] = _stamp(at)
        try:
            store.put_task(task, seen_updated_at)
        except team_store.Stale as error:
            raise Refused(409, "stale_write", "Görev bu arada değişti; sayfayı yenile.") from error
        except team_store.Invalid as error:
            raise Refused(
                422, "invalid_task", "Görev kuyruk şemasına uymuyor.", {"problems": error.problems}
            ) from error
    return {
        "task_id": task["id"],
        "gate": actual_gate,
        "decision": decision,
        "state": to_state,
        "applied": "next_cycle",
        "cycle_running": running,
        "message": (
            "Karar kaydedildi; çalışan döngü bunu görmez, bir sonraki döngüde uygulanır."
            if running
            else "Karar kaydedildi; bir sonraki döngüde uygulanır."
        ),
    }
