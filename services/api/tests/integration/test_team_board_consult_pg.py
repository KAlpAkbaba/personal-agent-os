"""The board's consultation (danisma) on the REAL database, and ``board.ps1`` against it.

The owner's refinement of 2026-10-03: "bir ajan diğerine desin ki: ben de şu anda şu iş var,
ama şöyle mi ilerlesem sence yoksa şöyle mi daha doğru olur". The notes are ``team_state`` rows
on the dev stack's PostgreSQL; the queue, the lock and the live status the router reads are a
file store in a temporary folder, so the dev database's real queue is never touched. The dev
database may hold real notes: what was there is put back when a test ends.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
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
from app.team.store import FileStore
from tests.integration.conftest import attach_owner, shared_identity

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[4]
NOTES = "/v1/team/board/notes"
POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)
ASKER_AREA = ["services/api/app/team/board.py", "scripts/lib/TeamBoard.ps1"]
SHARING_AREA = ["scripts/lib/TeamBoard.ps1", "scripts/team/feed.ps1"]
OTHER_AREA = ["apps/web/app/core/office/"]
FILES = "services/api/app/team/board.py,scripts/lib/TeamBoard.ps1,scripts/team/board.ps1"


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


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    """A live cycle: the lock held now, three worker runs, the three tasks' cards and areas."""
    root = tmp_path / "team"
    root.mkdir()
    branch = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        encoding="utf-8",
        check=False,
    ).stdout.strip()
    tasks = [
        {
            "id": "consult-asker",
            "title": "Danışma notu",
            "state": "in_progress",
            "area": ASKER_AREA,
            "branch": branch or "main",
            "goal": "Ajanlar birbirine danışır.\nCevaplayan işi bilerek cevap verir.\nÜçüncü.",
            "acceptance": "Kabul 1: -To auto paylaşan koltuğa gider.\nKabul 2: wait sınırlı.",
        },
        {"id": "consult-sharer", "title": "Paylaşan", "state": "in_progress", "area": SHARING_AREA},
        {"id": "consult-other", "title": "Öteki", "state": "in_progress", "area": OTHER_AREA},
    ]
    now = board.stamp(board.utcnow())
    (root / "queue.json").write_text(json.dumps({"version": 1, "tasks": tasks}), encoding="utf-8")
    (root / "lock.json").write_text(
        json.dumps({"held": True, "machine": "PC", "cycle_id": "c1", "pid": 1, "acquired_at": now}),
        encoding="utf-8",
    )
    runs = [
        {"task": task, "role": "worker", "started_at": now}
        for task in ("consult-asker", "consult-other", "consult-sharer")
    ]
    (root / "status.json").write_text(
        json.dumps({"cycle_id": "c1", "runs": runs, "updated_at": now}), encoding="utf-8"
    )
    return root


def _app(factory: sessionmaker[Session], team_root: Path, settings: Settings):
    app = create_app(settings)
    app.state.team_store = FileStore(team_root)  # queue, lock, status: a temporary folder
    app.state.team_board = board.DbBoard(factory)  # the notes: the dev PostgreSQL
    return app


def _consult(**fields: object) -> dict[str, object]:
    return {
        "seat": "worker-1",
        "task": "consult-asker",
        "kind": "danisma",
        "situation": "Danışma notunu ekliyorum; seçenekleri nerede doğrulayayım?",
        "options": ["A: board.py içinde", "B: route'ta pydantic"],
        "my_lean": "A: iki depo aynı kuralı kullanır",
        "files": FILES.split(","),
        **fields,
    }


def _hello(client: TestClient) -> None:
    for seat, task in (
        ("worker-1", "consult-asker"),
        ("worker-2", "consult-other"),
        ("worker-3", "consult-sharer"),
        ("worker-4", "consult-gone"),  # said hello, then its run ended: never routed to
    ):
        body = {"seat": seat, "task": task, "kind": "bilgi", "text": "başlıyorum"}
        assert client.post(NOTES, json=body).status_code == 200


def test_auto_routing_options_and_the_cevap_on_postgres(factory, team_root: Path) -> None:
    settings = Settings()
    app = _app(factory, team_root, settings)
    client = TestClient(app)
    attach_owner(app, client, settings)
    _hello(client)
    asked = client.post(NOTES, json=_consult(to="auto"))
    assert asked.status_code == 200, asked.text
    note = asked.json()["note"]
    assert note["to"] == "worker-3" and "scripts/lib/TeamBoard.ps1" in note["route"]
    for options in (["A: bir"], ["A: bir", "B: iki", "C: üç", "D: dört"]):
        refused = client.post(NOTES, json=_consult(options=options))
        assert refused.status_code == 422, refused.text
    assert client.post(NOTES, json=_consult(to="worker-1")).status_code == 422  # itself
    rule = client.post(NOTES, json=_consult(to="auto", topic="kural")).json()["note"]
    assert rule["to"] == "lead"
    answer = {
        "seat": "worker-3",
        "task": "consult-sharer",
        "kind": "cevap",
        "to": "worker-1",
        "reply_to": note["id"],
        "choice": "B",
        "text": "B: route'taki model 422'yi kendisi verir.",
    }
    assert client.post(NOTES, json={**answer, "choice": "D"}).status_code == 422
    assert client.post(NOTES, json=answer).status_code == 200
    read = client.get(NOTES, params={"limit": 50}).json()
    assert read["cards"]["consult-asker"]["goal"][0] == "Ajanlar birbirine danışır."
    answers = client.get(NOTES, params={"reply_to": note["id"]}).json()["notes"]
    assert [a["choice"] for a in answers] == ["B"]
    with factory() as session:
        row = session.get(TeamStateRow, (board.KIND_NOTE, note["id"]))
        assert row is not None and row.doc["options"] == _consult()["options"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def served(factory, team_root: Path, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    settings = Settings()
    app = _app(factory, team_root, settings)
    runtime = shared_identity(settings)
    app.state.identity = runtime
    token_file = tmp_path / "team.token"
    token_file.write_bytes(
        runtime.service.issue_session(client_kind="cli", label="team-board-talk").token.encode()
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
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO / "scripts" / "team" / "board.ps1"),
            *arguments,
            "-Url",
            url,
            "-TokenFile",
            str(token_file),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=180,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="board.ps1 is Windows PowerShell 5.1")
def test_board_ps1_asks_reads_the_context_and_waits_on_postgres(served, factory) -> None:
    url, token_file = served
    for seat, task in (("worker-2", "consult-other"), ("worker-3", "consult-sharer")):
        hello = _board(
            "post", "-Seat", seat, "-Task", task, "-Kind", "bilgi", "-Text", "başlıyorum",
            url=url, token_file=token_file,
        )  # fmt: skip
        assert hello.returncode == 0, hello.stdout + hello.stderr
    asked = _board(
        "post", "-Seat", "worker-1", "-Task", "consult-asker", "-Kind", "danisma",
        "-Situation", "Seçenekleri nerede doğrulayayım?", "-OptionA", "board.py içinde",
        "-OptionB", "route'ta pydantic", "-Lean", "A: tek kural", "-Files", FILES,
        url=url, token_file=token_file,
    )  # fmt: skip
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert "kime: worker-3 (ortak dosya: " in asked.stdout, asked.stdout
    note_id = asked.stdout.split("no ", 1)[1].split(",", 1)[0]

    read = _board("read", "-For", "worker-3", url=url, token_file=token_file)
    assert ">> SANA " in read.stdout and "kart: Danışma notu" in read.stdout, read.stdout
    assert "seçenekler: A: board.py içinde | B: route'ta pydantic" in read.stdout, read.stdout

    context = _board("context", "-Note", note_id, url=url, token_file=token_file)
    assert context.returncode == 0, context.stdout + context.stderr
    assert "Kart: Danışma notu" in context.stdout, context.stdout
    assert "Hedef: Ajanlar birbirine danışır." in context.stdout, context.stdout
    assert "Kabul: Kabul 1: -To auto paylaşan koltuğa gider." in context.stdout, context.stdout
    assert "Dal farkı (main..." in context.stdout, context.stdout
    assert "changed" in context.stdout or "fark yok" in context.stdout, context.stdout

    started = time.monotonic()
    none = _board(
        "wait", "-Note", note_id, "-Minutes", "0.05", "-PollSeconds", "1",
        url=url, token_file=token_file,
    )  # fmt: skip
    assert none.returncode == 0 and none.stdout.startswith("cevap gelmedi"), none.stdout
    assert time.monotonic() - started < 60

    answered = _board(
        "post", "-Seat", "worker-3", "-Task", "consult-sharer", "-Kind", "cevap",
        "-To", "worker-1", "-ReplyTo", note_id, "-Choice", "B",
        "-Text", "B: route'taki model 422'yi kendisi verir.",
        url=url, token_file=token_file,
    )  # fmt: skip
    assert answered.returncode == 0, answered.stdout + answered.stderr
    got = _board(
        "wait", "-Note", note_id, "-Minutes", "1", "-PollSeconds", "30",
        url=url, token_file=token_file,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout.startswith("CEVAP worker-3: seçim B - "), got.stdout
    assert token_file.read_text().strip() not in read.stdout + context.stdout + got.stdout
