"""``/v1/team/board/notes``: the team's board (``app.team.board`` holds the rules).

POST one note; GET the newest ones (``since``, ``limit``), newest last. Under the owner session,
as the queue's routes are - the cycle's team token is that session - and over whichever store
the queue is kept in: the ``team_state`` table when ``app.state.team_store`` is the database,
otherwise ``board.json`` beside the queue. ``app.state.team_board`` overrides both.

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


@router.post("/v1/team/board/notes")
async def post_note(request: Request, body: Any = Body(...)) -> dict[str, Any]:  # noqa: B008
    target = _board(request)
    try:
        note = await asyncio.to_thread(target.post, body)
    except board.Refused as refused:
        raise HTTPException(refused.status, refused.detail()) from refused
    return {"note": note}


@router.get("/v1/team/board/notes")
async def read_notes(
    request: Request,
    since: str | None = None,
    limit: int = Query(default=board.READ_DEFAULT, ge=1, le=board.READ_MAX),
) -> dict[str, Any]:
    target = _board(request)
    try:
        notes = await asyncio.to_thread(lambda: target.read(since=since, limit=limit))
    except board.Refused as refused:
        raise HTTPException(refused.status, refused.detail()) from refused
    return {"notes": notes, "now": board.stamp(board.utcnow())}
