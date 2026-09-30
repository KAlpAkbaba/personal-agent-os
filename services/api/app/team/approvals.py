"""The owner's two decidable gates, read from and written to ``team/queue.json``.

A task at ``awaiting_owner`` (the idea) or ``awaiting_release`` (the release) waits for the
owner. Onayla on the idea writes ``approved``; Onayla on the release leaves the state at
``awaiting_release`` and raises ``release_approved`` (the cycle reads ``approved`` as "assign a
worker", so a release approval must not use it); Reddet writes ``stopped`` with the owner's
reason at either gate. All of it is
written where the NEXT cycle reads it: a decision is refused while a cycle holds the queue
(its own read-modify-write would overwrite ours), and it starts nothing - approving a release
changes one file and does not run a release.

Every decision is a ledger event, recorded BEFORE the queue is written: if the ledger refuses,
the queue is untouched and the owner is told, so no decision exists that the ledger does not
know. The ledger's vocabulary is closed; the constants below are what the lead adds to it.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: awaiting_* state -> the gate's name as the owner says it (ASCII, as TeamQueue.ps1).
GATES: dict[str, str] = {"awaiting_owner": "fikir", "awaiting_release": "yayin"}

APPROVED_STATE = "approved"
RELEASE_GATE_STATE = "awaiting_release"
STOPPED_STATE = "stopped"

SUBSYSTEM_TEAM = "team"
EVENT_TASK_APPROVED = "team.task.approved"
EVENT_TASK_REJECTED = "team.task.rejected"

LOCK_STALE_HOURS = 6  # TeamQueue.ps1: $script:TeamLockStaleHours
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
    try:
        lock = json.loads((team_root / "lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(lock, dict) or lock.get("held") is not True:
        return False
    try:
        acquired = datetime.strptime(str(lock["acquired_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except (KeyError, ValueError):
        return True  # held and unreadable: assume it is held rather than clobber a run
    return (now or _now()) - acquired < timedelta(hours=LOCK_STALE_HOURS)


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


def _proposal_text(team_root: Path, proposal: str) -> str | None:
    if not proposal:
        return None
    normalized = proposal.replace("\\", "/")
    if normalized.startswith("team/"):
        return _read_inside(team_root, normalized)  # a path: its file, or nothing
    return proposal  # prose


def newest_cycle_report(team_root: Path) -> dict[str, str] | None:
    reports = sorted(
        (team_root / "reports").glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name)
    )
    if not reports:
        return None
    latest = reports[-1]
    try:
        return {"file": latest.name, "text": latest.read_text(encoding="utf-8")[:TEXT_MAX_CHARS]}
    except OSError:
        return None


def list_pending(queue: dict[str, Any], team_root: Path) -> list[dict[str, Any]]:
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
                "proposal_text": _proposal_text(team_root, proposal),
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
    :class:`Refused` itself; it runs before the queue is written.
    """
    reason = (reason or "").strip()
    if decision == "reject" and not reason:
        raise Refused(422, "reason_required", "Reddetmek için bir gerekçe gerekli.")
    at = now or _now()
    path = team_root / "queue.json"
    with _WRITE_LOCK:
        queue = load_queue(path)
        task, actual_gate = _resolve(queue, task_id=task_id, gate=gate, channel=channel)
        if cycle_running(team_root, now=at):
            raise Refused(409, "cycle_running", "Bir döngü kuyruğu tutuyor; bitince tekrar dene.")
        from_state = task["state"]
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
        _write_atomic(path, queue)
    return {
        "task_id": task["id"],
        "gate": actual_gate,
        "decision": decision,
        "state": to_state,
        "applied": "next_cycle",
    }


def _write_atomic(path: Path, queue: dict[str, Any]) -> None:
    text = json.dumps(queue, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)
