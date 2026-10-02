"""The misheard notebook to the corpus proposals, on the REAL database: script and table agree.

Rows are written through the store's own ``record`` and ``answer`` into the dev stack's
PostgreSQL. The dump is produced by executing the SQL text taken FROM the collector's own
``-ShowQuery`` output - not a copy of it - so a column the script names and the table lacks,
or a renamed column, is red here. Then the collector runs on that dump.

``misheard_utterances`` is emptied around the test: nothing else writes to it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import build_engine, build_session_factory
from app.voice.misheard import service
from app.voice.misheard.models import MisheardUtterance

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
REPO = API_ROOT.parents[1]
COLLECT_SCRIPT = REPO / "scripts" / "voice" / "collect-stt-corpus.ps1"

UNANSWERED = "Ekranları kapatın lütfen."
ANSWERED = "Ofisteki şeyi aç ışığı."
MEANT = "Ofis bilgisayarında ışık uygulamasını aç; ğüşöçİı."


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


def _collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    assert shell is not None
    return subprocess.run(  # noqa: S603 - a fixed interpreter and this repository's own script
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(COLLECT_SCRIPT),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    command.upgrade(_alembic(), "head")
    engine = build_engine(Settings().database_url)
    sessions = build_session_factory(engine)

    def clear() -> None:
        with sessions() as session:
            session.execute(delete(MisheardUtterance))
            session.commit()

    clear()
    try:
        yield sessions
    finally:
        clear()
        engine.dispose()


def _notebook_statement() -> str:
    """The script's own third SELECT, as -ShowQuery prints it."""
    ran = _collect("-ShowQuery", "-Days", "30")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    body = "\n".join(line for line in ran.stdout.splitlines() if not line.lstrip().startswith("--"))
    statements = [s.strip() for s in body.split(";") if s.strip()]
    found = [s for s in statements if "from misheard_utterances" in s]
    assert len(found) == 1, statements
    return found[0]


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_notebook_round_trips_through_the_scripts_own_query(factory, tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    # 23:30 UTC the day before is still that day, whatever offset the database writes it in.
    heard = (now - timedelta(days=1)).replace(hour=23, minute=30, second=0)
    device = uuid.uuid4()
    with factory() as db:
        first = service.record(
            db,
            sentence=UNANSWERED,
            mode="paid",
            reason="no_intent",
            session_id=uuid.uuid4(),
            heard_at=heard,
            now=now,
            engine="gpt-4o-transcribe",
            device_id=device,
            band="low",
            confidence=0.31,
        )
        second = service.record(
            db,
            sentence=ANSWERED,
            mode="local",
            reason="objected",
            session_id=uuid.uuid4(),
            heard_at=heard - timedelta(minutes=5),
            now=now,
            engine="web-speech",
            resolved_intent="app_open",
            band="medium",
            confidence=0.55,
        )
        assert first is not None and second is not None
        assert service.answer(db, second.id, MEANT, now) is not None
        db.commit()

    statement = _notebook_statement()
    with factory() as db:
        raw = db.connection().exec_driver_sql(statement).scalars().all()
    lines = [
        json.dumps(value if isinstance(value, dict) else json.loads(value), ensure_ascii=False)
        for value in raw
    ]
    assert len(lines) == 2
    dump = tmp_path / "stt-dump.jsonl"
    dump.write_text("\n".join([*lines, ""]), encoding="utf-8")
    out = tmp_path / "stt-proposals.json"

    ran = _collect("-DumpPath", str(dump), "-OutPath", str(out))
    assert ran.returncode == 0, ran.stdout + ran.stderr
    report = json.loads(out.read_text(encoding="utf-8-sig"))

    proposals = {p["rendering"]: p for p in report["proposals"]}
    assert set(proposals) == {UNANSWERED, ANSWERED}
    day = heard.strftime("%Y-%m-%d")

    case_1 = proposals[UNANSWERED]
    assert case_1["status"] == "needs_owner_meaning" and case_1["meant"] is None
    assert (case_1["heard_at"], case_1["times_heard"]) == (day, 1)
    assert {
        k: case_1[k]
        for k in ("mode", "engine", "device_id", "reason", "resolved_intent", "band", "confidence")
    } == {
        "mode": "paid",
        "engine": "gpt-4o-transcribe",
        "device_id": str(device),
        "reason": "no_intent",
        "resolved_intent": None,
        "band": "low",
        "confidence": 0.31,
    }

    case_2 = proposals[ANSWERED]
    assert case_2["status"] == "owner_answered"
    assert case_2["meant"] == MEANT  # letter for letter, Turkish letters intact
    assert (case_2["mode"], case_2["reason"], case_2["resolved_intent"]) == (
        "local",
        "objected",
        "app_open",
    )
    for slot in ("intent", "tool", "application", "device"):
        assert case_2[slot] is None, slot

    assert report["misheard"] == {
        "rows": 2,
        "by_reason": {"objected": 1, "no_intent": 1},
        "by_mode": {"local": 1, "paid": 1},
        "answered": 1,
    }
