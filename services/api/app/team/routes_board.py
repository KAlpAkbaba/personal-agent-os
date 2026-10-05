"""``/v1/team/board/notes``: the team's board (``app.team.board`` holds the rules).

POST one note; GET the newest ones (``since``, ``limit``, ``reply_to``), newest last, with the
card of every danisma's asker (``cards``); GET ``/{id}/context`` one note with its asker's card
(title, goal and acceptance lines, branch, area) and its answers. Under the owner session, as
the queue's routes are - the cycle's team token is that session - and over whichever store the
queue is kept in: the ``team_state`` table when ``app.state.team_store`` is the database,
otherwise ``board.json`` beside the queue. ``app.state.team_board`` overrides both.

A danisma sent to ``auto`` is routed over the queue's lock, live status and areas
(:class:`app.team.board.Routing`); a store that cannot be read routes it to everyone.

Every refusal is a 4xx with the ``{code, message, problems}`` body of ADR-0214 addendum 4: a
note the board cannot take is never a 500.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.identity.dependencies import require_owner_session
from app.team import board
from app.team import store as team_store
from app.team.routes import DEFAULT_TEAM_ROOT

router = APIRouter(dependencies=[Depends(require_owner_session)])

#: How much of a card the answerer is shown: the first lines of the goal and the acceptance.
CARD_LINES = 2
CARD_LINE_CHARS = 240


def _board(request: Request) -> board.Board:
    state = request.app.state
    wired = getattr(state, "team_board", None)
    if wired is not None:
        return wired
    store = getattr(state, "team_store", None)
    if isinstance(store, team_store.DbStore):
        # The queue's own session factory: the board lives where the queue does.
        return board.DbBoard(store._factory)  # noqa: SLF001
    return board.FileBoard(getattr(state, "team_root", None) or DEFAULT_TEAM_ROOT)


def _team(request: Request) -> team_store.TeamStore:
    wired = getattr(request.app.state, "team_store", None)
    if wired is not None:
        return wired
    return team_store.FileStore(getattr(request.app.state, "team_root", None) or DEFAULT_TEAM_ROOT)


def _tasks(request: Request) -> list[dict[str, Any]]:
    """The queue's tasks; none when the queue cannot be read (the board still works)."""
    try:
        tasks = _team(request).read_queue().get("tasks", [])
    except Exception:  # noqa: BLE001 - a missing or broken queue only means no context
        return []
    return [task for task in tasks if isinstance(task, dict)]


def _routing(request: Request) -> board.Routing:
    """The lock (is a cycle live?), the status' runs and the queue's areas; nothing live when
    any of them cannot be read."""
    store = _team(request)
    try:
        live = team_store.lock_is_running(store.read_lock(), team_store.utcnow())
        status = store.read_status() or {}
    except Exception:  # noqa: BLE001
        return board.Routing()
    runs = [run for run in status.get("runs", []) if isinstance(run, dict)]
    areas = {
        str(task.get("id")): [str(path) for path in task.get("area", []) if isinstance(path, str)]
        for task in _tasks(request)
    }
    return board.Routing(live=live, runs=runs, areas=areas)


def _lines(text: Any) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return [
        line if len(line) <= CARD_LINE_CHARS else line[: CARD_LINE_CHARS - 1] + "…"
        for line in lines[:CARD_LINES]
    ]


def card_of(task: dict[str, Any]) -> dict[str, Any]:
    acceptance = task.get("acceptance", "")
    if isinstance(acceptance, list):
        acceptance = "\n".join(str(item) for item in acceptance)
    return {
        "title": str(task.get("title", "")),
        "goal": _lines(task.get("goal")),
        "acceptance": _lines(acceptance),
        "branch": str(task.get("branch", "")),
        "area": [str(path) for path in task.get("area", []) if isinstance(path, str)],
    }


@router.post("/v1/team/board/notes")
async def post_note(request: Request, body: Any = Body(...)) -> dict[str, Any]:  # noqa: B008
    target = _board(request)
    routing = None
    if (
        isinstance(body, dict)
        and body.get("kind") == "danisma"
        and body.get("to", board.AUTO) == board.AUTO
    ):
        routing = await asyncio.to_thread(_routing, request)
    try:
        note = await asyncio.to_thread(lambda: target.post(body, routing=routing))
    except board.Refused as refused:
        raise HTTPException(refused.status, refused.detail()) from refused
    return {"note": note}


@router.get("/v1/team/board/notes")
async def read_notes(
    request: Request,
    since: str | None = None,
    limit: int = Query(default=board.READ_DEFAULT, ge=1, le=board.READ_MAX),
    reply_to: str | None = None,
) -> dict[str, Any]:
    target = _board(request)
    try:
        notes = await asyncio.to_thread(
            lambda: target.read(since=since, limit=limit, reply_to=reply_to)
        )
    except board.Refused as refused:
        raise HTTPException(refused.status, refused.detail()) from refused
    asking = {note.get("task") for note in notes if note.get("kind") == "danisma"}
    cards: dict[str, Any] = {}
    if asking:
        tasks = await asyncio.to_thread(_tasks, request)
        cards = {str(t["id"]): card_of(t) for t in tasks if t.get("id") in asking}
    return {"notes": notes, "cards": cards, "now": board.stamp(board.utcnow())}


@router.get("/v1/team/board/notes/{note_id}/context")
async def note_context(request: Request, note_id: str) -> dict[str, Any]:
    """What an answerer reads before it answers: the note, its asker's card, its answers."""
    if board._NOTE_ID.fullmatch(note_id) is None:  # noqa: SLF001
        raise HTTPException(422, {"code": "invalid", "message": "not a note id", "problems": []})
    target = _board(request)
    notes = await asyncio.to_thread(lambda: target.read(limit=board.READ_MAX))
    note = next((n for n in notes if n.get("id") == note_id), None)
    if note is None:
        raise HTTPException(
            404, {"code": "not_found", "message": "no such note on the board", "problems": []}
        )
    tasks = await asyncio.to_thread(_tasks, request)
    task = next((t for t in tasks if t.get("id") == note.get("task")), None)
    answers = [n for n in notes if n.get("reply_to") == note_id]
    return {"note": note, "card": card_of(task) if task else None, "answers": answers}
