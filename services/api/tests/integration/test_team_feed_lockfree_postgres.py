"""The feeder beside a running cycle, on the REAL routes and the REAL DbStore (feeder-own-lock).

``scripts/team/feed.ps1`` in API mode, while a live cycle of this machine holds the team lock,
takes its own lock, lets the lead work in a throwaway worktree, reads the queue again and writes
ONLY new tasks - each as a conditional create (``PUT /v1/team/queue/tasks/{id}`` with
``expected_updated_at: null``). The PowerShell suite proves the behaviour on the cycle's fake
listener; this test proves the claim "every write is a conditional create against the real
store" on the dev stack's PostgreSQL (TEAM_PROTOCOL 9a): the real application, served on a
loopback port, the cycle's lock held through the real ``POST /v1/team/queue/lock`` with a live
pid, a task ``in_progress`` in the store, and the REAL feed.ps1 with a fake in place of the
model. The fake is written here: ``scripts/tests/lib/fake-claude.ps1`` has no feed scenario
(it writes ``- split_file:`` only).

The test removes its own rows, and puts back a lock row the dev database held before it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.main import create_app
from app.team.models import KIND_LOCK, KIND_REPORT, KIND_TASK, TeamStateRow
from app.team.store import LOCK_KEY, DbStore
from tests.integration.conftest import attach_owner

POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
REPO = Path(__file__).resolve().parents[4]
MACHINE = "LOCKFREE-IT"
FEED_DATE = "2026-10-03"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POWERSHELL.exists(), reason="Windows PowerShell 5.1 is not on this machine"
    ),
]

# The lead's stand-in: logs that it ran (and where), plays ANOTHER WRITER that creates one of the
# cards' ids through the real route while the run works, then writes the feed file the card names.
FAKE_LEAD = r"""
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$card = [Console]::In.ReadToEnd()
$here = (Get-Location).ProviderPath
[System.IO.File]::AppendAllText([string]$env:LOCKFREE_IT_LOG, $here + "`n", $utf8)
$client = New-Object System.Net.WebClient
$client.Encoding = $utf8
$token = [System.IO.File]::ReadAllText([string]$env:LOCKFREE_IT_TOKEN).Trim()
$client.Headers.Add("Authorization", "Bearer " + $token)
$client.Headers.Add("Content-Type", "application/json; charset=utf-8")
$taken = [System.IO.File]::ReadAllText([string]$env:LOCKFREE_IT_TAKEN, $utf8)
[void]$client.UploadString([string]$env:LOCKFREE_IT_TAKEN_URL, "PUT", $taken)
if ($card -match '(?m)^- feed_file: (\S+)') {
    $target = Join-Path $here ($Matches[1] -replace "/", "\")
    $folder = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $folder)) {
        [void](New-Item -ItemType Directory -Force -Path $folder)
    }
    $cards = [System.IO.File]::ReadAllText([string]$env:LOCKFREE_IT_CARDS, $utf8)
    [System.IO.File]::WriteAllText($target, $cards, $utf8)
}
$result = '{"type":"result","subtype":"success","is_error":false,"result":"feed written",'
[Console]::Out.Write($result + '"total_cost_usd":0.1}')
exit 0
"""


def _task(task_id: str, state: str, area: str, title: str, stamp: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "roadmap_row": "His conversations and his people",  # a step of the order (2026-10-05)
        "state": state,
        "area": [area],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 0},
        "created_at": stamp,
        "updated_at": stamp,
    }


def _card(card_id: str) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": f"the lock-free card {card_id}",
        "roadmap_row": "His conversations and his people",  # a step of the order (2026-10-05)
        "area": [f"src/{card_id}"],
        "goal": f"the goal of {card_id}",
        "acceptance": f"the acceptance of {card_id}",
        "evidence_expected": "PROVEN_AUTOMATED",
    }


def _git(root: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr}"
    return done.stdout.strip()


def _sandbox(work: Path) -> Path:
    """A repository of its own, on the lead's branch: the feeder, its libraries, the role file,
    this repository's roadmap."""
    root = work / "repo"
    for name in (
        "NativeProcess.ps1",
        "TeamQueue.ps1",
        "TeamRun.ps1",
        "HttpJson.ps1",
        "TeamFeed.ps1",
    ):
        (root / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / "scripts" / "lib" / name, root / "scripts" / "lib" / name)
    (root / "scripts" / "team").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "scripts" / "team" / "feed.ps1", root / "scripts" / "team" / "feed.ps1")
    (root / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        REPO / ".claude" / "agents" / "lead.md", root / ".claude" / "agents" / "lead.md"
    )
    (root / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "docs" / "ROADMAP.md", root / "docs" / "ROADMAP.md")
    (root / "team").mkdir(parents=True, exist_ok=True)
    (root / "team" / "queue.json").write_bytes(b'{"version":1,"tasks":[]}\n')
    (root / ".gitignore").write_bytes(b".claude/worktrees/\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "team test")
    _git(root, "config", "user.email", "team@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the sandbox")
    _git(root, "checkout", "-q", "-b", "team/nightly/lead")
    return root


class _Served:
    """The real application on a loopback port, in a thread of this process."""

    def __init__(self, app: Any) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off"
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            assert time.monotonic() < deadline, "the application did not start serving"
            time.sleep(0.05)
        return f"http://127.0.0.1:{self.port}"

    def __exit__(self, *_: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=30)


def _row(factory: sessionmaker[Session], kind: str, key: str) -> tuple[Any, str] | None:
    with factory() as session:
        row = session.get(TeamStateRow, (kind, key))
        return None if row is None else (json.loads(json.dumps(row.doc)), row.updated_at)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(Settings().database_url)
    made = build_session_factory(engine)
    with made() as session:
        lock = session.get(TeamStateRow, (KIND_LOCK, LOCK_KEY))
        kept = None if lock is None else (lock.doc, lock.updated_at)
        if lock is not None:
            session.delete(lock)
            session.commit()
    try:
        yield made
    finally:
        with made() as session:
            session.execute(
                delete(TeamStateRow).where(
                    TeamStateRow.kind == KIND_LOCK, TeamStateRow.key == LOCK_KEY
                )
            )
            if kept is not None:
                session.add(
                    TeamStateRow(kind=KIND_LOCK, key=LOCK_KEY, doc=kept[0], updated_at=kept[1])
                )
            session.commit()
        engine.dispose()


def test_the_feeder_beside_a_live_cycle_only_creates_on_the_real_store(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    tag = uuid.uuid4().hex[:8]
    busy_id, mine_id, taken_id = f"lf-{tag}-busy", f"lf-{tag}-mine", f"lf-{tag}-taken"
    ours = (busy_id, mine_id, taken_id)
    settings = Settings()
    app = create_app(settings)
    app.state.team_store = DbStore(factory)
    client = TestClient(app)
    attach_owner(app, client, settings)
    token = client.headers["Authorization"].split(" ", 1)[1]
    work = tmp_path
    (work / "token.txt").write_text(token + "\n", encoding="utf-8")
    stamp = "2026-10-03T00:00:00Z"
    theirs = _task(taken_id, "approved", f"src/{taken_id}-theirs", "the other writer's card", stamp)
    (work / "taken.json").write_text(
        json.dumps({"task": theirs, "expected_updated_at": None}), encoding="utf-8"
    )
    (work / "cards.json").write_text(
        json.dumps([_card(taken_id), _card(mine_id)]), encoding="utf-8"
    )
    (work / "fake-lead.ps1").write_text(FAKE_LEAD, encoding="utf-8")
    root = _sandbox(work)
    try:
        with _Served(app) as url:
            api = httpx.Client(
                base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30
            )
            busy = _task(busy_id, "in_progress", f"src/{busy_id}", "a task in work", stamp)
            busy["branch"] = f"team/d20261003/worker-{busy_id}"
            created = api.put(
                f"/v1/team/queue/tasks/{busy_id}", json={"task": busy, "expected_updated_at": None}
            )
            assert created.status_code == 200, created.text
            # The cycle's lock, through the real route, with a pid that is alive: this process.
            lock = api.post(
                "/v1/team/queue/lock",
                json={
                    "action": "acquire",
                    "machine": MACHINE,
                    "cycle_id": "d20261003",
                    "pid": os.getpid(),
                },
            )
            assert lock.json()["acquired"] is True, lock.text
            busy_before = _row(factory, KIND_TASK, busy_id)
            lock_before = _row(factory, KIND_LOCK, LOCK_KEY)

            feed = root / "scripts" / "team" / "feed.ps1"
            fake = work / "fake-lead.ps1"
            command = (
                f"& '{feed}' -FeedDate '{FEED_DATE}' -Machine '{MACHINE}'"
                f" -ClaudePath '{POWERSHELL}'"
                f" -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','{fake}'"
                f" -QueueUrl '{url}' -QueueToken '{work / 'token.txt'}'"
                f" -FeederLockPath '{work / 'feeder.lock'}'"
                "; exit $LASTEXITCODE"
            )
            env = {
                **os.environ,
                "LOCKFREE_IT_LOG": str(work / "lead.log"),
                "LOCKFREE_IT_TOKEN": str(work / "token.txt"),
                "LOCKFREE_IT_TAKEN": str(work / "taken.json"),
                "LOCKFREE_IT_TAKEN_URL": f"{url}/v1/team/queue/tasks/{taken_id}",
                "LOCKFREE_IT_CARDS": str(work / "cards.json"),
            }
            done = subprocess.run(
                [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=300,
                check=False,
            )
            said = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
            assert done.returncode == 0, said
            ran_in = (work / "lead.log").read_text(encoding="utf-8").split()
            assert len(ran_in) == 1 and "\\.claude\\worktrees\\feed\\" in ran_in[0], ran_in

            # The new card is a row; the card whose id the other writer created is THEIRS.
            mine = _row(factory, KIND_TASK, mine_id)
            assert mine is not None and mine[0]["state"] == "approved", said
            assert _row(factory, KIND_TASK, taken_id) == (theirs, stamp), said
            # The task in work and the cycle's lock are the rows they were.
            assert _row(factory, KIND_TASK, busy_id) == busy_before
            assert _row(factory, KIND_LOCK, LOCK_KEY) == lock_before
            # The report is a file, not a row (the Onay Merkezi keeps the cycle's).
            with factory() as session:
                reports = session.execute(
                    select(TeamStateRow.key).where(TeamStateRow.kind == KIND_REPORT)
                ).scalars()
                assert f"feed-{FEED_DATE}.md" not in list(reports)
            report = (root / "team" / "reports" / f"feed-{FEED_DATE}.md").read_text(
                encoding="utf-8"
            )
            assert taken_id in report and "yazılmadı" in report, report
            assert not (work / "feeder.lock").exists()

            # The create the feeder relies on: the real DbStore answers 409 to a create of an id
            # it has, and the row is not overwritten.
            again = api.put(
                f"/v1/team/queue/tasks/{taken_id}",
                json={
                    "task": _task(taken_id, "approved", f"src/{taken_id}", "overwritten?", stamp),
                    "expected_updated_at": None,
                },
            )
            assert again.status_code == 409, again.text
            assert _row(factory, KIND_TASK, taken_id) == (theirs, stamp)
            api.close()
    finally:
        with factory() as session:
            session.execute(
                delete(TeamStateRow).where(
                    TeamStateRow.kind == KIND_TASK, TeamStateRow.key.in_(ours)
                )
            )
            session.commit()
