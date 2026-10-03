"""The team's board on the REAL database, and ``board.ps1`` against it (owner's idea 2026-10-03).

A note is a ``team_state`` row of kind ``note``; SQLite enforces no VARCHAR length and has no
JSONB, so the rules, the pruning and the routes are taken to the dev stack's PostgreSQL, every
row is held to the widths the model states, and the PowerShell client a run calls is pointed at
a real server (uvicorn, in this process) over that store: one seat posts, another reads.

The dev database may hold real notes: what was there is put back when a test ends.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team import board
from app.team.models import TeamStateRow
from app.team.routes_board import router as board_router
from app.team.store import DbStore
from tests.integration.conftest import attach_owner, shared_identity

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[4]
NOTES = "/v1/team/board/notes"
NOW = datetime(2026, 10, 3, 9, 0, 0, tzinfo=UTC)
POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)
TOKEN_SHAPED = "AKIA" + "ABCDEFGHIJKLMNOP"


def _note(**fields: object) -> dict[str, object]:
    return {
        "seat": "worker-1",
        "task": "team-board",
        "kind": "bilgi",
        "text": "çalışıyorum",
        **fields,
    }


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)
    with made() as session:
        found = session.execute(select(TeamStateRow).where(TeamStateRow.kind == board.KIND_NOTE))
        kept = [(row.kind, row.key, row.doc, row.updated_at) for row in found.scalars()]

    def clear() -> None:
        with made() as session:
            session.execute(delete(TeamStateRow).where(TeamStateRow.kind == board.KIND_NOTE))
            session.commit()

    clear()
    try:
        yield made
    finally:
        clear()
        with made() as session:
            for kind, key, doc, updated_at in kept:
                session.add(TeamStateRow(kind=kind, key=key, doc=doc, updated_at=updated_at))
            session.commit()
        engine.dispose()


def _rows(factory: sessionmaker[Session]) -> list[TeamStateRow]:
    widths = {
        c.name: c.type.length
        for c in TeamStateRow.__table__.columns
        if getattr(c.type, "length", None)
    }
    with factory() as session:
        rows = list(
            session.execute(
                select(TeamStateRow).where(TeamStateRow.kind == board.KIND_NOTE)
            ).scalars()
        )
        for row in rows:
            for name, width in widths.items():
                assert len(getattr(row, name)) <= width, f"{row.key}: {name} > {width}"
            assert row.doc["id"] == row.key and row.doc["at"] == row.updated_at
        return rows


def test_the_rules_and_the_pruning_hold_on_postgres(factory) -> None:
    store = board.DbBoard(factory)
    posted = store.post(_note(text="ş" * 280), now=NOW)
    assert board.DbBoard(factory).read(now=NOW) == [posted]  # the row, not this object's memory
    for fields, status in (
        ({"text": "ş" * 281}, 422),
        ({"seat": "stranger"}, 422),
        ({"kind": "emir"}, 422),
        ({"text": f"anahtar {TOKEN_SHAPED}"}, 422),
    ):
        with pytest.raises(board.Refused) as caught:
            store.post(_note(**fields), now=NOW)
        assert caught.value.status == status
    for minute in range(19):
        store.post(_note(text=f"{minute}"), now=NOW + timedelta(minutes=minute + 1))
    with pytest.raises(board.Refused) as caught:
        store.post(_note(text="21"), now=NOW + timedelta(minutes=30))
    assert caught.value.status == 429
    assert len(_rows(factory)) == 20

    for index in range(520):
        store.post(
            _note(task=f"task-{index % 26:02d}", text=f"not {index}"),
            now=NOW + timedelta(hours=2, seconds=index * 10),
        )
    after = NOW + timedelta(hours=2, seconds=5300)
    notes = store.read(now=after, limit=500)
    assert len(notes) == 500 and len(_rows(factory)) == 500
    assert notes[0]["text"] == "not 20" and notes[-1]["text"] == "not 519"
    assert store.prune(now=after) == 0
    assert board.DbBoard(factory).read(limit=500) == notes
    week = NOW + timedelta(hours=2, seconds=2000, days=7)
    dropped = store.prune(now=week)
    assert dropped > 0 and store.prune(now=week) == 0
    assert len(_rows(factory)) == 500 - dropped


def test_the_routes_refuse_with_their_4xx_on_postgres(factory) -> None:
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    app.include_router(board_router)
    client = TestClient(app)
    attach_owner(app, client, settings)
    for body in (
        _note(text="x" * 281),
        _note(seat="stranger"),
        _note(kind="emir"),
        _note(text=f"key {TOKEN_SHAPED}"),
    ):
        answer = client.post(NOTES, json=body)
        assert answer.status_code == 422, answer.text
    for index in range(20):
        assert client.post(NOTES, json=_note(text=f"{index}")).status_code == 200
    answer = client.post(NOTES, json=_note(text="21"))
    assert answer.status_code == 429, answer.text
    assert len(client.get(NOTES, params={"limit": 500}).json()["notes"]) == 20
    assert len(_rows(factory)) == 20


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def served(factory, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A real server over the real store; the owner token in a file, as the cycle has it."""
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    app.include_router(board_router)
    runtime = shared_identity(settings)
    app.state.identity = runtime
    token_file = tmp_path / "team.token"
    token_file.write_bytes(
        runtime.service.issue_session(client_kind="cli", label="team-board").token.encode()
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", token_file
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _board(*arguments: str, url: str, token_file: Path) -> subprocess.CompletedProcess[str]:
    script = REPO / "scripts" / "team" / "board.ps1"
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
            "-Url",
            url,
            "-TokenFile",
            str(token_file),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="board.ps1 is Windows PowerShell 5.1")
def test_board_ps1_one_seat_posts_another_reads_on_postgres(served, factory) -> None:
    url, token_file = served
    posted = _board(
        "post",
        "-Seat",
        "inspector",
        "-Task",
        "team-board",
        "-Kind",
        "soru",
        "-To",
        "worker-2",
        "-Text",
        "Dev stack açık mı? Ölçümü birlikte yapalım.",
        url=url,
        token_file=token_file,
    )
    assert posted.returncode == 0, posted.stdout + posted.stderr
    assert posted.stdout.startswith("Not panoya yazıldı: no n-"), posted.stdout
    asked = _rows(factory)[0].doc
    answered = _board(
        "post",
        "-Seat",
        "worker-2",
        "-Task",
        "team-board",
        "-Kind",
        "cevap",
        "-To",
        "inspector",
        "-ReplyTo",
        asked["id"],
        "-Text",
        "Açık, 5432 hazır.",
        url=url,
        token_file=token_file,
    )
    assert answered.returncode == 0, answered.stdout
    refused = _board(
        "post",
        "-Seat",
        "worker-2",
        "-Task",
        "team-board",
        "-Kind",
        "fikir",
        "-Text",
        "y" * 281,
        url=url,
        token_file=token_file,
    )
    assert refused.returncode == 2 and "HTTP 422" in refused.stdout, refused.stdout

    read = _board("read", "-For", "worker-2", url=url, token_file=token_file)
    assert read.returncode == 0, read.stdout + read.stderr
    lines = read.stdout.strip().splitlines()
    assert lines[0] == "Ekip panosu: 2 not, 1 tanesi worker-2 koltuğuna."
    assert (
        lines[1].startswith(">> SANA ")
        and "Dev stack açık mı? Ölçümü birlikte yapalım." in lines[1]
    )
    assert not lines[2].startswith(">>") and f"yanıtladığı {asked['id']}" in lines[2]
    assert token_file.read_text().strip() not in read.stdout + posted.stdout
    assert [row.doc["seat"] for row in sorted(_rows(factory), key=lambda r: r.key)] == [
        "inspector",
        "worker-2",
    ]
