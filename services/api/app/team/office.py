"""The Ofis page's data: who sits where, what they are doing, what the cycle is up to.

:func:`office_view` is a pure function over what the stores already hold (the queue, the lock,
the cycle's live status, the pending approvals) and a clock. It adds nothing secret: every
string in the answer is one the queue or the status already carries.

Seat rules (the contract the page is built against):

* the seats are ``lead``, ``researcher``, ``integrator``, ``worker-1..N``, ``inspector``,
  ``owner`` in that order, where N is four or the number of live worker runs, whichever is
  larger: a run is never left without a seat;
* a live run whose role is ``worker`` takes a worker seat of its own, in start order; EVERY live
  run of another role sits on that role's one seat. A seat with a live run is ``working``; its
  ``runs`` lists them in start order (``task_id``, ``task_title``, ``since``) and the seat's own
  ``task_id`` / ``task_title`` / ``since`` are the first run's;
* a seat with no live run has no ``runs`` and is ``returned`` when the newest task of that role
  is ``returned`` or ``stopped`` (the task is the seat's), else ``waiting``. "Newest task of a
  role" is the task whose latest report by that role is the most recent; the worker seats with
  no live run take the newest such worker tasks that are returned/stopped, newest first;
* the owner seat is always ``waiting`` with no task (the page shows the approvals on it).

``running_agents`` counts the live RUNS on the seats, not the seats that work (three inspector
runs are three); ``capacity`` is six, or the number of runs when more than six are in flight.

``updated_at`` must be UTC in the ``YYYY-MM-DDTHH:MM:SSZ`` form (the one ``team_store.stamp``
writes); a value without the ``Z`` (no zone, or an offset) cannot be read and is not live, and one
more than two minutes ahead of the clock (skew) is not live either.

A status is live only while its ``updated_at`` is under ten minutes old AND the lock is held
(not stale) by the cycle that wrote it; otherwise ``running`` is false and no seat works. The
lock's age counts from the holder's own last status (``team_store.lock_alive_since``): a cycle
seven hours old that wrote its status two minutes ago is running.

The model policy (ADR-0214 addendum 7): every seat carries ``model``, the model its role is set
to (the owner seat: none), and ``running_model`` only while a live run of the seat is on another
one (lowered by the chain, or raised to the inspector's floor); a run entry carries ``model``
when the status named one. ``cycle.limits`` is the status' ``limits`` - ``ok`` with null
percentages when no status gave any: nobody computes a percentage here - and ``models`` is the
setting document itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.team import models_setting
from app.team import store as team_store

CAPACITY = 6
LOWERED_MAX = 20  # limits.lowered: this cycle's downgrades, newest last
STATUS_STALE_MINUTES = 10
#: A clock a little ahead is fine; further ahead is not "live". The lock's heartbeat holds the
#: same bound (``team_store.lock_alive_since``).
STATUS_FUTURE_SKEW_MINUTES = team_store.STATUS_FUTURE_SKEW_MINUTES
SUMMARY_MAX_LINES = 40  # queue.schema.json: report.summary maxItems
MIN_WORKER_SEATS = 4
_ROLE_SEATS = ("lead", "researcher", "integrator", "inspector")
_FINISHED = ("released", "done")
_RETURNED = ("returned", "stopped")


def _usage_limit(status: dict[str, Any] | None) -> dict[str, Any]:
    raw = (status or {}).get("usage_limit")
    if isinstance(raw, dict) and raw.get("state") in ("ok", "waiting", "stopped"):
        return {"state": raw["state"], "resets_at": raw.get("resets_at")}
    return {"state": "ok", "resets_at": None}


def _limit_window(raw: Any, now: datetime) -> dict[str, Any]:
    clear = {"state": "ok", "resets_at": None, "used_pct": None}
    if not isinstance(raw, dict) or raw.get("state") not in ("ok", "limited"):
        return clear
    resets_at = raw.get("resets_at") if isinstance(raw.get("resets_at"), str) else None
    lifted = team_store._parse(resets_at) if resets_at else None
    if lifted is not None and lifted <= now:
        # The cycle that wrote it may be gone: a limit whose reset has passed is not a limit,
        # and the number measured before the reset is not this window's.
        return clear
    used = raw.get("used_pct")
    known = isinstance(used, (int, float)) and not isinstance(used, bool)
    return {"state": raw["state"], "resets_at": resets_at, "used_pct": used if known else None}


def _limits(status: dict[str, Any] | None, setting: dict[str, Any], now: datetime) -> dict:
    raw = (status or {}).get("limits")
    raw = raw if isinstance(raw, dict) else {}
    lowered = raw.get("lowered") if isinstance(raw.get("lowered"), list) else []
    fallback = raw.get("fallback")
    return {
        "fable": _limit_window(raw.get("fable"), now),
        "all": _limit_window(raw.get("all"), now),
        "fallback": fallback if isinstance(fallback, bool) else setting["fallback"],
        "lowered": [entry for entry in lowered if isinstance(entry, dict)][-LOWERED_MAX:],
    }


def _is_live(lock: dict[str, Any] | None, status: dict[str, Any] | None, now: datetime) -> bool:
    if not isinstance(status, dict):
        return False
    written = team_store._parse(status.get("updated_at"))
    if written is None or now - written > timedelta(minutes=STATUS_STALE_MINUTES):
        return False
    if written - now > timedelta(minutes=STATUS_FUTURE_SKEW_MINUTES):
        return False
    if not team_store.lock_is_running(lock, now, status):
        return False
    holder = (lock or {}).get("cycle_id")
    return not holder or holder == status.get("cycle_id")


def _live_runs(status: dict[str, Any]) -> list[dict[str, Any]]:
    runs = [r for r in status.get("runs") or [] if isinstance(r, dict)]
    return sorted(runs, key=lambda r: str(r.get("started_at", "")))


def _latest_report(task: dict[str, Any], role: str) -> dict[str, Any] | None:
    mine = [r for r in task.get("reports") or [] if isinstance(r, dict) and r.get("role") == role]
    return max(mine, key=lambda r: str(r.get("at", "")), default=None)


def _newest_by_role(tasks: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """The tasks a role reported on, newest report first."""
    stamped = [(str(_latest_report(t, role)["at"]), t) for t in tasks if _latest_report(t, role)]
    return [
        t
        for _, t in sorted(stamped, key=lambda pair: (pair[0], pair[1].get("id", "")), reverse=True)
    ]


def _task_report(task: dict[str, Any]) -> dict[str, Any] | None:
    reports = [r for r in task.get("reports") or [] if isinstance(r, dict)]
    if not reports:
        return None
    newest = max(reports, key=lambda r: str(r.get("at", "")))
    summary = [str(line) for line in newest.get("summary") or []][:SUMMARY_MAX_LINES]
    return {
        "role": newest.get("role", ""),
        "at": newest.get("at", ""),
        "outcome": newest.get("outcome", ""),
        "summary": summary,
    }


def _worker_seats(live_worker_runs: int) -> list[str]:
    count = max(MIN_WORKER_SEATS, live_worker_runs)
    return [f"worker-{n}" for n in range(1, count + 1)]


def _seat(seat: str, role: str, state: str, task: dict[str, Any] | None, since: Any) -> dict:
    return {
        "seat": seat,
        "role": role,
        "state": state,
        "task_id": None if task is None else task.get("id"),
        "task_title": None if task is None else task.get("title"),
        "since": since if task is not None else None,
        "runs": [],
    }


def _working_seat(seat: str, role: str, runs: list[dict], by_id: dict[str, dict]) -> dict:
    listed = []
    for run in runs:
        task = by_id.get(str(run.get("task")), {})
        entry = {
            "task_id": task.get("id", run.get("task")),
            "task_title": task.get("title"),
            "since": run.get("started_at"),
        }
        if isinstance(run.get("model"), str) and run["model"]:
            entry["model"] = run["model"]  # a cycle older than the policy names none
        listed.append(entry)
    return {"seat": seat, "role": role, "state": "working", **listed[0], "runs": listed}


def _with_models(agent: dict[str, Any], roles: dict[str, str]) -> dict[str, Any]:
    """The seat with the model its role is set to, and the other model a live run is on."""
    configured = roles.get(agent["role"])
    agent["model"] = configured
    other = next(
        (r["model"] for r in agent["runs"] if r.get("model", configured) != configured), None
    )
    if other is not None:
        agent["running_model"] = other
    return agent


def office_view(
    queue: dict[str, Any],
    lock: dict[str, Any] | None,
    status: dict[str, Any] | None,
    approvals: list[dict[str, Any]],
    now: datetime,
    *,
    models: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``models`` is the setting in force (``models_setting.effective``); the defaults without."""
    setting = models if models is not None else models_setting.defaults(team_store.stamp(now))
    tasks = [t for t in queue.get("tasks", []) if isinstance(t, dict)]
    by_id = {str(t.get("id")): t for t in tasks}
    live = _is_live(lock, status, now)
    runs = _live_runs(status) if live and status else []

    worker_seats = _worker_seats(sum(1 for r in runs if r.get("role") == "worker"))
    placed: dict[str, list[dict[str, Any]]] = {}
    workers = 0
    for run in runs:
        role = str(run.get("role", ""))
        if role == "worker":
            placed[worker_seats[workers]] = [run]
            workers += 1
        elif role in _ROLE_SEATS:
            placed.setdefault(role, []).append(run)

    running_agents = sum(len(seated) for seated in placed.values())
    running_ids = {str(r.get("task")) for r in runs}
    returned_workers = [
        t
        for t in _newest_by_role(tasks, "worker")
        if t.get("state") in _RETURNED and str(t.get("id")) not in running_ids
    ]
    free_worker_seats = [s for s in worker_seats if s not in placed]
    agents: list[dict[str, Any]] = []
    for seat in ("lead", "researcher", "integrator", *worker_seats, "inspector", "owner"):
        role = "worker" if seat in worker_seats else seat
        if seat == "owner":
            agents.append(_seat(seat, "owner", "waiting", None, None))
        elif seat in placed:
            agents.append(_working_seat(seat, role, placed[seat], by_id))
        elif seat in worker_seats:
            index = free_worker_seats.index(seat)  # position among the seats nobody is working
            task = returned_workers[index] if index < len(returned_workers) else None
            state = "returned" if task is not None else "waiting"
            agents.append(_seat(seat, role, state, task, task and task.get("updated_at")))
        else:
            newest = next(iter(_newest_by_role(tasks, role)), None)
            if newest is not None and newest.get("state") in _RETURNED:
                agents.append(_seat(seat, role, "returned", newest, newest.get("updated_at")))
            else:
                agents.append(_seat(seat, role, "waiting", None, None))

    document = status if isinstance(status, dict) else {}
    return {
        "cycle": {
            "cycle_id": document.get("cycle_id"),
            "machine": document.get("machine"),
            "started_at": document.get("started_at"),
            "running": live,
            "running_agents": running_agents,
            "capacity": max(CAPACITY, running_agents),
            "estimated_usd": document.get("estimated_usd") or 0,
            "usage_limit": _usage_limit(status),
            "limits": _limits(status, setting, now),
            "updated_at": document.get("updated_at"),
        },
        "agents": [_with_models(agent, setting["roles"]) for agent in agents],
        "models": setting,
        "tasks": {
            str(t["id"]): {
                "title": t.get("title", ""),
                "state": t.get("state", ""),
                "goal": t.get("goal", ""),
                "acceptance": t.get("acceptance", ""),
                "branch": t.get("branch", ""),
                "sha": t.get("sha"),
                "reason": t.get("reason"),
                "report": _task_report(t),
            }
            for t in tasks
            if t.get("state") not in _FINISHED and "id" in t
        },
        "approvals": approvals,
    }
