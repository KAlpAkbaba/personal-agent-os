"""The owner's third gate: trials on a real device (TEAM_PROTOCOL 3a, proposal deneme-listesi).

A released task carries ``owner_trials``: what the owner says or does (``sentence``), where
(``machine``), what he must see or hear (``expect``), and - once he has tried - his
``verdict`` ("oldu" / "olmadi"), his own words (``said``) and when (``at``). The old form, a
plain sentence, is still valid in the queue; it has no id to decide on and is not listed.

"Oldu" records the verdict and nothing more: PROVEN_REAL is the lead's to write, quoting the
owner, so the task's state is left as it is and, when no trial is left open and none failed,
its ``reason`` says the owner tried and the lead writes the row. "Olmadı" records the verdict
and opens a fix task (``approved``, the trial and the owner's words quoted, no area - the
lead's split gives it one). The decision is a ledger event recorded before the queue is
written, and it is refused on the file store while a cycle holds the queue, exactly as
``approvals.decide`` is; on the database store it is taken while a cycle runs (every write is
conditional on the version read).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.team import approvals
from app.team import store as team_store

#: The ledger's vocabulary is closed: these two are what the lead adds at merge.
EVENT_TRIAL_PASSED = "team.trial.passed"
EVENT_TRIAL_FAILED = "team.trial.failed"

PASSED = "oldu"
FAILED = "olmadi"
#: The states a trial is decided in: the release has run, the real-device proof has not.
TRIAL_STATES = ("released", "awaiting_real_evidence")
SAID_MAX_CHARS = 500
TASK_ID_MAX = 64  # queue.schema.json: task.id
FIX_STATE = "approved"
FIX_REASON = "alan: lead belirler"
TITLE_MAX_CHARS = 120

Refused = approvals.Refused


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def trial_objects(task: dict[str, Any]) -> list[dict[str, Any]]:
    """The task's trials in the object form; the old plain sentences are skipped."""
    found = task.get("owner_trials")
    if not isinstance(found, list):
        return []
    return [t for t in found if isinstance(t, dict)]


def list_open(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Every undecided trial of a released task, in queue order."""
    out = []
    for task in queue.get("tasks", []):
        if task.get("state") not in TRIAL_STATES:
            continue
        for trial in trial_objects(task):
            if trial.get("verdict") is None:
                out.append(
                    {
                        "task_id": task["id"],
                        "title": task.get("title", ""),
                        "sha": task.get("sha"),
                        "trial": trial,
                    }
                )
    return out


def fix_task_id(task_id: str, queue: dict[str, Any]) -> str:
    """``fix-<task_id>-<n>``, the first ``n`` not in the queue; the task's id is shortened so
    the whole fits the schema's 64 characters."""
    taken = {str(t.get("id", "")) for t in queue.get("tasks", [])}
    n = 1
    while True:
        suffix = f"-{n}"
        stem = f"fix-{task_id}"[: TASK_ID_MAX - len(suffix)].rstrip("-")
        candidate = stem + suffix
        if candidate not in taken:
            return candidate
        n += 1


def _fix_task(
    task: dict[str, Any], trial: dict[str, Any], said: str, fix_id: str, at: str
) -> dict[str, Any]:
    sentence = str(trial.get("sentence", "")).strip()
    title = f"Düzelt: {sentence}"[:TITLE_MAX_CHARS]
    goal = (
        f"Sahibin denemesi olmadı. Görev {task['id']} (yayınlanan sha {task.get('sha') or '-'}), "
        f"deneme {trial.get('id')} - makine: {trial.get('machine', '')}. "
        f'Söylenen/yapılan: "{sentence}". Beklenen: "{trial.get("expect", "")}". '
        f'Sahibin sözü: "{said}". Kök nedeni bul, regresyon testi yaz, düzelt; '
        f"PROVEN_REAL yine sahibin denemesiyle."
    )
    return {
        "id": fix_id,
        "title": title,
        "roadmap_row": str(task.get("roadmap_row", "")),
        "state": FIX_STATE,
        "area": [],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": dict(task.get("budget") or {"max_usd": 5}),
        "created_at": at,
        "updated_at": at,
        "goal": goal,
        "reason": FIX_REASON,
    }


def _refuse_write(error: Exception) -> Refused:
    if isinstance(error, team_store.Invalid):
        return Refused(
            422, "invalid_task", "Görev kuyruk şemasına uymuyor.", {"problems": error.problems}
        )
    return Refused(409, "stale_write", "Görev bu arada değişti; sayfayı yenile.")


def decide(
    store: team_store.TeamStore,
    *,
    task_id: str,
    trial_id: str,
    verdict: str,
    said: str | None,
    record: Callable[[dict[str, Any]], None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record one Oldu / Olmadı, or raise :class:`Refused`. ``record`` writes the ledger event
    (it receives the event's facts) and runs before the queue is written."""
    words = (said or "").strip()
    if verdict not in (PASSED, FAILED):
        raise Refused(422, "invalid_verdict", "Karar 'oldu' ya da 'olmadi' olur.")
    if verdict == FAILED and not words:
        raise Refused(422, "said_required", "Olmadı demek için ne olduğunu söyle.")
    if len(words) > SAID_MAX_CHARS:
        raise Refused(422, "said_too_long", f"Söz en çok {SAID_MAX_CHARS} karakter olur.")
    at = now or team_store.utcnow()
    with approvals.WRITE_LOCK:
        queue = store.read_queue()
        task = next((t for t in queue.get("tasks", []) if t.get("id") == task_id), None)
        if task is None:
            raise Refused(404, "unknown_task", f"{task_id} kuyrukta yok.")
        if task.get("state") not in TRIAL_STATES:
            raise Refused(409, "not_on_trial", f"{task_id} denemede değil ({task.get('state')}).")
        trial = next((t for t in trial_objects(task) if t.get("id") == trial_id), None)
        if trial is None:
            raise Refused(404, "unknown_trial", f"{task_id} görevinde {trial_id} denemesi yok.")
        if trial.get("verdict") is not None:
            raise Refused(
                409,
                "already_decided",
                f"Bu deneme zaten kararlı: {trial['verdict']}.",
                {"verdict": trial["verdict"]},
            )
        lock = store.read_lock()
        if not approvals.decisions_open(store, lock, at):
            raise Refused(409, "cycle_running", "Bir döngü kuyruğu tutuyor; bitince tekrar dene.")
        running = team_store.lock_is_running(lock, at)
        stamp = _stamp(at)
        seen_updated_at = task.get("updated_at")
        fix = (
            _fix_task(task, trial, words, fix_task_id(task_id, queue), stamp)
            if verdict == FAILED
            else None
        )
        record(
            {
                "task_id": task_id,
                "trial_id": trial_id,
                "verdict": verdict,
                "said": words,
                "sentence": trial.get("sentence", ""),
                "machine": trial.get("machine", ""),
                "sha": task.get("sha") or "",
                "state": task.get("state", ""),
                "fix_task_id": fix["id"] if fix else None,
                "updated_at": seen_updated_at or "",
            }
        )
        trial["verdict"] = verdict
        trial["said"] = words or None
        trial["at"] = stamp
        decided = trial_objects(task)
        if all(t.get("verdict") == PASSED for t in decided):
            task["reason"] = f"sahip denedi: oldu ({stamp}) - PROVEN_REAL satırını lead yazar"
        task["updated_at"] = stamp
        try:
            store.put_task(task, seen_updated_at)  # the guard against a second decision
            if fix is not None:
                store.put_task(fix, None)
        except (team_store.Stale, team_store.Invalid) as error:
            raise _refuse_write(error) from error
    return {
        "task_id": task_id,
        "trial_id": trial_id,
        "verdict": verdict,
        "at": stamp,
        "state": task["state"],
        "fix_task_id": fix["id"] if fix else None,
        "cycle_running": running,
        "message": (
            f"Olmadı kaydedildi; {fix['id']} düzeltme işi açıldı (alanını lead belirler)."
            if fix
            else "Oldu kaydedildi; PROVEN_REAL satırını lead senin sözünle yazar."
        ),
    }
