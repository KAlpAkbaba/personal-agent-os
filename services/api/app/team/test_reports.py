"""``/v1/team/test-reports``: the test team's round reports, for the owner to read.

The owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı sonuçların girdi çıktı olarak
raporlarını istiyorum incelemek için". ``scripts/testteam/test-round.ps1`` POSTs each round's
Turkish input/output report (per tester and per step Girdi / Beklenen / Çıktı / Sonuç); the
Ofis lists them (``apps/web/app/core/office/OfficeTestReports.tsx``) and opens one.

Under the owner session, as the queue's routes are (the round's token is that session). Text
only, at most 256 KB of UTF-8 a report; the same round again replaces its report; the last 50
rounds are kept, older ones dropped on the write that goes past. Kept where the queue is: the
``team_state`` table (rows of kind ``test_report``, key = the round) when
``app.state.team_store`` is the database, otherwise ``test-reports.json`` beside the queue;
``app.state.team_test_reports`` overrides both. Every refusal is a 4xx with the
``{code, message, problems}`` body of ADR-0214 addendum 4, never a 500.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.identity.dependencies import require_owner_session
from app.team import store as team_store
from app.team.models import TeamStateRow
from app.team.routes import DEFAULT_TEAM_ROOT

# This module's name starts with ``test_``; it holds no tests.
__test__ = False

KIND_TEST_REPORT = "test_report"
#: The rounds kept; the oldest is dropped on the write that goes past.
KEEP = 50
#: One report's text, in UTF-8 bytes (scripts/testteam/TestTeam.ps1: TestTeamReportMaxBytes).
TEXT_MAX_BYTES = 256 * 1024
#: A request body: the text JSON-escaped (``\\u011f`` is six bytes for two) plus the fields.
BODY_MAX_BYTES = 4 * TEXT_MAX_BYTES + 64 * 1024
UNFINISHED_MAX_CHARS = 500
COUNTS = ("passed", "failed", "broke")
#: A round id as test-round.ps1 states it.
_ROUND = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
_SHA = re.compile(r"[0-9A-Za-z._-]{0,80}")
#: What a kept text may not hold: NUL (Postgres JSONB refuses it) and any surrogate (a str
#: from json.loads holds a pair as one character, so a surrogate here is always a lone one).
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")

_WRITE_LOCK = threading.Lock()


class Refused(Exception):
    """A report the store does not take: ``status`` 422 or 413."""

    def __init__(self, status: int, code: str, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.status = status
        self.code = code
        self.problems = problems

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": "; ".join(self.problems), "problems": self.problems}


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    """Microseconds: two reports of one second keep their order (the list is sorted on it)."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(at: str) -> datetime:
    return datetime.strptime(at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def check_report(body: Any) -> dict[str, Any]:
    """The report as it is kept, or :class:`Refused`."""
    if not isinstance(body, dict):
        raise Refused(422, "invalid", ["a report is a JSON object"])
    text = body.get("text")
    if not isinstance(text, str):
        raise Refused(422, "invalid", ["text is the report's Markdown, a string"])
    unfinished = body.get("unfinished", "")
    for name, value in (("text", text), ("unfinished", unfinished)):
        # NUL breaks Postgres JSONB, a lone surrogate breaks UTF-8: both a 422, never a 500.
        if isinstance(value, str) and _UNSTORABLE.search(value) is not None:
            raise Refused(422, "invalid", [f"{name}: no NUL and no lone surrogate"])
    size = len(text.encode("utf-8"))
    if size > TEXT_MAX_BYTES:
        raise Refused(
            413, "too_large", [f"text is {size} bytes; a report is at most {TEXT_MAX_BYTES}"]
        )
    problems: list[str] = []
    round_id = body.get("round")
    if not isinstance(round_id, str) or _ROUND.fullmatch(round_id) is None:
        problems.append("round: a-z, 0-9 and '-' (at most 41)")
    sha = body.get("staging_sha", "")
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        problems.append("staging_sha: a sha, at most 80 characters")
    if not isinstance(unfinished, str) or len(unfinished) > UNFINISHED_MAX_CHARS:
        problems.append(f"unfinished: a string of at most {UNFINISHED_MAX_CHARS} characters")
    counts = body.get("counts", {})
    kept: dict[str, int] = {}
    if not isinstance(counts, dict):
        problems.append("counts: an object of passed, failed, broke")
    else:
        for name in COUNTS:
            value = counts.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(f"counts.{name}: a whole number, 0 or more")
            else:
                kept[name] = value
    if problems:
        raise Refused(422, "invalid", problems)
    return {
        "round": round_id,
        "staging_sha": sha,
        "counts": kept,
        "unfinished": unfinished,
        "text": text,
    }


def summary_of(doc: dict[str, Any]) -> dict[str, Any]:
    """What the Ofis' list shows: everything but the text."""
    return {key: value for key, value in doc.items() if key != "text"}


def _place(docs: list[dict[str, Any]], report: dict[str, Any], at: datetime) -> dict[str, Any]:
    """The report stamped after every one kept (a clock that stands still or steps back keeps
    the order of writing)."""
    when = at
    stamps = [_parse(str(d["at"])) for d in docs if d.get("round") != report["round"]]
    if stamps and max(stamps) >= when:
        when = max(stamps) + timedelta(microseconds=1)
    return {**report, "at": stamp(when)}


def _newest_first(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(docs, key=lambda d: (str(d.get("at", "")), str(d.get("round", ""))), reverse=True)


class RoundReports(Protocol):
    def put(self, report: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]: ...
    def list(self) -> list[dict[str, Any]]: ...
    def get(self, round_id: str) -> dict[str, Any] | None: ...


class DbRoundReports:
    """``team_state`` rows of kind ``test_report``, key = the round, ``updated_at`` = its stamp."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._factory = session_factory

    @staticmethod
    def _stamps(session: Session) -> list[tuple[str, str]]:
        rows = session.execute(
            select(TeamStateRow.key, TeamStateRow.updated_at).where(
                TeamStateRow.kind == KIND_TEST_REPORT
            )
        )
        return [(str(key), str(at)) for key, at in rows]

    def put(self, report: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        with _WRITE_LOCK, self._factory() as session:
            # The round's own old row is not an "other": counted with its new one it pushed a
            # kept round out (50 kept, the newest sent again: 49).
            others = [
                {"round": key, "at": at}
                for key, at in self._stamps(session)
                if key != report["round"]
            ]
            doc = _place(others, report, now or utcnow())
            row = session.get(TeamStateRow, (KIND_TEST_REPORT, doc["round"]))
            if row is None:
                session.add(
                    TeamStateRow(
                        kind=KIND_TEST_REPORT, key=doc["round"], doc=doc, updated_at=doc["at"]
                    )
                )
            else:
                row.doc = doc
                row.updated_at = doc["at"]
            kept = {d["round"] for d in _newest_first([*others, doc])[:KEEP]}
            dropped = [d["round"] for d in others if d["round"] not in kept]
            if dropped:
                session.execute(
                    delete(TeamStateRow).where(
                        TeamStateRow.kind == KIND_TEST_REPORT, TeamStateRow.key.in_(dropped)
                    )
                )
            session.commit()
        return summary_of(doc)

    def list(self) -> list[dict[str, Any]]:
        with self._factory() as session:
            rows = session.execute(
                select(TeamStateRow.doc).where(TeamStateRow.kind == KIND_TEST_REPORT)
            )
            return [summary_of(dict(doc)) for doc in _newest_first(list(rows.scalars()))][:KEEP]

    def get(self, round_id: str) -> dict[str, Any] | None:
        with self._factory() as session:
            row = session.get(TeamStateRow, (KIND_TEST_REPORT, round_id))
            return dict(row.doc) if row is not None else None


class FileRoundReports:
    """``test-reports.json`` under ``team/``: a list of reports."""

    def __init__(self, team_root: Path) -> None:
        self.path = team_root / "test-reports.json"

    def _load(self) -> list[dict[str, Any]]:
        try:
            docs = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [doc for doc in docs if isinstance(doc, dict)] if isinstance(docs, list) else []

    def _save(self, docs: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes((json.dumps(docs, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        os.replace(tmp, self.path)

    def put(self, report: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        with _WRITE_LOCK:
            others = [d for d in self._load() if d.get("round") != report["round"]]
            doc = _place(others, report, now or utcnow())
            self._save(_newest_first([*others, doc])[:KEEP])
        return summary_of(doc)

    def list(self) -> list[dict[str, Any]]:
        return [summary_of(doc) for doc in _newest_first(self._load())][:KEEP]

    def get(self, round_id: str) -> dict[str, Any] | None:
        return next((doc for doc in self._load() if doc.get("round") == round_id), None)


router = APIRouter(dependencies=[Depends(require_owner_session)])


def _reports(request: Request) -> RoundReports:
    state = request.app.state
    wired = getattr(state, "team_test_reports", None)
    if wired is not None:
        return wired
    store = getattr(state, "team_store", None)
    if isinstance(store, team_store.DbStore):
        # The queue's own session factory: the reports live where the queue does.
        return DbRoundReports(store._factory)  # noqa: SLF001
    return FileRoundReports(getattr(state, "team_root", None) or DEFAULT_TEAM_ROOT)


def _refuse(refused: Refused) -> HTTPException:
    return HTTPException(refused.status, refused.detail())


@router.post("/v1/team/test-reports")
async def post_report(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > BODY_MAX_BYTES:
        raise _refuse(Refused(413, "too_large", [f"a request is at most {BODY_MAX_BYTES} bytes"]))
    raw = await request.body()
    if len(raw) > BODY_MAX_BYTES:
        raise _refuse(Refused(413, "too_large", [f"a request is at most {BODY_MAX_BYTES} bytes"]))
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _refuse(Refused(422, "invalid", ["the body is not UTF-8 JSON"])) from error
    try:
        report = check_report(body)
    except Refused as refused:
        raise _refuse(refused) from refused
    target = _reports(request)
    summary = await asyncio.to_thread(lambda: target.put(report))
    return {"report": summary}


@router.get("/v1/team/test-reports")
async def list_reports(request: Request) -> dict[str, Any]:
    target = _reports(request)
    return {"reports": await asyncio.to_thread(target.list)}


@router.get("/v1/team/test-reports/{round_id}")
async def read_report(request: Request, round_id: str) -> dict[str, Any]:
    if _ROUND.fullmatch(round_id) is None:
        raise _refuse(Refused(422, "invalid", ["not a round id"]))
    target = _reports(request)
    doc = await asyncio.to_thread(lambda: target.get(round_id))
    if doc is None:
        raise HTTPException(
            404, {"code": "not_found", "message": "no report of that round", "problems": []}
        )
    return {"report": doc}
