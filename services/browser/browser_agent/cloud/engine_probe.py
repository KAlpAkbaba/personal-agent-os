"""Which search engine answers the cloud worker without a verification wall (ADR-0248).

A measuring instrument, nothing more. It asks each engine the worker has a few fixed public
questions through the UNCHANGED worker, one attempt each, and writes a table of what came
back. It never solves or bypasses a wall: no captcha is answered, no consent button is
pressed, no stealth plugin, proxy or changed user agent, no second try after a wall. When an
engine shows one, that engine's remaining questions are not asked.

Two halves. The pure half (``plan``, ``row_from_ack``, ``render_table``, ``to_json``) does no
I/O. The driver (``run_probe`` over a worker link, ``main`` for ``python -m``) starts the
worker child on a temporary data dir and talks to it over stdio; it dials no broker and
writes no file. The run on the Cloud Core host is the lead's, through
``infra/docker/cloud-browser/measure-search-engines.sh``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from ..search_engines import ENGINES
from . import policy as cloud_policy

#: Public questions, none of them the owner's: two Turkish, one English.
QUERIES: tuple[str, ...] = (
    "İstanbul hava durumu",
    "Türkiye'nin en uzun nehri",
    "python asyncio tutorial",
)
#: 4 engines x 3 questions. A cap, not a product: a fifth engine makes the default plan
#: refuse until somebody decides the new number.
MAX_REQUESTS = 12
#: Between two requests to the SAME engine.
MIN_GAP_S = 20
REQUEST_TIMEOUT_S = 45
RUN_DEADLINE_S = 15 * 60
#: The worker enforces ``timeout_ms`` itself; this is the hang guard around it.
_EXEC_GRACE_S = 15

WALL_OUTCOMES = frozenset({"captcha", "consent", "blocked"})
#: After the first of these an engine is asked nothing more in this run.
STOP_OUTCOMES = WALL_OUTCOMES | {"transport_error", "error", "verification_pending"}

ADDRESS_NOTE = "the host's own outbound address (not printed)"
PROBE_PROFILE = "research"


class WorkerLink(Protocol):
    async def start(self) -> dict[str, Any]: ...

    async def exec(
        self, request_id: str, capability: str, payload: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]: ...

    async def stop(self) -> None: ...


# ------------------------------------------------------------------ pure half


def plan(
    engines: Sequence[str] = ENGINES, queries: Sequence[str] = QUERIES
) -> list[tuple[str, int]]:
    """``(engine, query_index)`` pairs, question by question, so one engine is never asked
    twice in a row while another is left. Refuses a matrix above ``MAX_REQUESTS``, an engine
    the worker does not have and an engine named twice."""
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        raise ValueError(f"unknown engine(s) {unknown}; the worker has {list(ENGINES)}")
    if len(set(engines)) != len(engines):
        raise ValueError(f"an engine is named twice: {list(engines)}")
    total = len(engines) * len(queries)
    if total > MAX_REQUESTS:
        raise ValueError(f"{total} requests planned; the probe makes at most {MAX_REQUESTS}")
    return [(engine, index) for index in range(len(queries)) for engine in engines]


def _row(
    engine: str,
    query_index: int,
    outcome: str,
    *,
    results: int = 0,
    error_class: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "query_index": query_index,
        "outcome": outcome,
        "results": results,
        "wall": outcome if outcome in WALL_OUTCOMES else None,
        "error_class": error_class,
        "elapsed_ms": elapsed_ms,
    }


def skipped_row(engine: str, query_index: int, reason: str) -> dict[str, Any]:
    return _row(engine, query_index, reason)


def _attempt_for(engine: str, attempts: Any) -> dict[str, Any] | None:
    if not isinstance(attempts, list):
        return None
    mine = [a for a in attempts if isinstance(a, dict) and a.get("provider") == engine]
    if mine:
        return mine[-1]
    return attempts[-1] if attempts and isinstance(attempts[-1], dict) else None


def row_from_ack(
    engine: str, query_index: int, ack: dict[str, Any], elapsed_ms: int | None
) -> dict[str, Any]:
    """One row from the worker's own answer: the outcome is the one ``run_search`` recorded
    in ``attempts`` (on a result, or in the error's ``evidence`` for PROVIDER_RATE_LIMITED);
    nothing here classifies a page a second time. Titles and URLs are never copied."""
    error_class: str | None = None
    if ack.get("ok"):
        body = ack.get("result") or {}
        attempts = body.get("attempts")
        count = body.get("result_count")
        if not isinstance(count, int) or isinstance(count, bool):
            count = len(body.get("results") or [])
    else:
        error = ack.get("error") or {}
        error_class = str(error.get("class") or "unknown")
        evidence = error.get("evidence")
        attempts = evidence.get("attempts") if isinstance(evidence, dict) else None
        count = 0
    attempt = _attempt_for(engine, attempts)
    if attempt is not None:
        outcome = str(attempt.get("outcome") or "unknown")
    elif error_class is not None:
        outcome = "error"
    else:
        outcome = "ok" if count else "empty"
    return _row(
        engine,
        query_index,
        outcome,
        results=count if outcome == "ok" else 0,
        error_class=error_class,
        elapsed_ms=elapsed_ms,
    )


def _summary(engine: str, rows: list[dict[str, Any]]) -> str:
    mine = [r for r in rows if r["engine"] == engine]
    for row in mine:
        number = row["query_index"] + 1
        if row["wall"]:
            return f"{engine}: wall: {row['wall']} at query {number}"
        if row["outcome"] in STOP_OUTCOMES:
            return f"{engine}: stopped: {row['outcome']} ({row['error_class']}) at query {number}"
    answered = sum(1 for r in mine if r["outcome"] == "ok")
    line = f"{engine}: answered {answered} of {len(mine)}"
    empty = sum(1 for r in mine if r["outcome"] == "empty")
    not_run = sum(1 for r in mine if r["outcome"].startswith("not_run"))
    if empty:
        line += f" (empty {empty}: a wall the worker has no marker for also reads as empty)"
    if not_run:
        line += f" (not run {not_run})"
    return line


def render_table(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    """Markdown: the meta, one line per planned request (counts only), one summary per
    engine. No address, no result title, no URL."""
    lines = ["## Search-engine probe (ADR-0248)", ""]
    lines += [f"- {key}: {value}" for key, value in meta.items()]
    lines += [
        "",
        "| engine | query | outcome | results | wall | error_class | elapsed_ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cells = [
            row["engine"],
            str(row["query_index"] + 1),
            row["outcome"],
            str(row["results"]),
            row["wall"] or "-",
            row["error_class"] or "-",
            "-" if row["elapsed_ms"] is None else str(row["elapsed_ms"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    engines = list(dict.fromkeys(row["engine"] for row in rows))
    lines += [f"- {_summary(engine, rows)}" for engine in engines]
    return "\n".join(lines) + "\n"


def to_json(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    return json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ driver


def _session_open_payload(session_id: str) -> dict[str, Any]:
    # Sent already clamped: what the production bridge would hand the worker.
    return cloud_policy.clamp_command(
        "browser.session_open", {"session_id": session_id, "profile": PROBE_PROFILE}
    )


def _meta(hello: dict[str, Any], image_digest: str, now_utc: datetime) -> dict[str, Any]:
    browser = hello.get("browser") if isinstance(hello.get("browser"), dict) else {}
    return {
        "date_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "image_digest": image_digest,
        "worker_version": str(hello.get("worker_version", "unknown")),
        "chromium_version": str(browser.get("version") or "unknown"),
        "address": ADDRESS_NOTE,
    }


async def run_probe(
    link: WorkerLink,
    *,
    engines: Sequence[str] = ENGINES,
    queries: Sequence[str] = QUERIES,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    image_digest: str = "unknown",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One run: start, one ``session_open``, at most ``MAX_REQUESTS`` searches, one
    ``session_close`` and the child's stop in a ``finally``."""
    planned = plan(engines, queries)
    session_id = f"engine-probe-{uuid.uuid4().hex}"
    rows: list[dict[str, Any]] = []
    opened = False
    try:
        hello = await link.start()
        meta = _meta(hello, image_digest, datetime.now(UTC))
        opened = True
        ack = await asyncio.wait_for(
            link.exec(
                uuid.uuid4().hex,
                "browser.session_open",
                _session_open_payload(session_id),
                REQUEST_TIMEOUT_S * 1000,
            ),
            REQUEST_TIMEOUT_S + _EXEC_GRACE_S,
        )
        if not ack.get("ok"):
            error_class = str((ack.get("error") or {}).get("class") or "unknown")
            rows = [_row(e, i, "not_run_session_open", error_class=error_class) for e, i in planned]
            return rows, meta
        started = clock()
        stopped: set[str] = set()
        last_sent: dict[str, float] = {}
        for engine, index in planned:
            if engine in stopped:
                rows.append(skipped_row(engine, index, "skipped_after_wall"))
                continue
            wait = 0.0
            if engine in last_sent:
                wait = max(0.0, MIN_GAP_S - (clock() - last_sent[engine]))
            if clock() + wait - started >= RUN_DEADLINE_S:
                rows.append(skipped_row(engine, index, "not_run_deadline"))
                continue
            if wait > 0:
                await sleep(wait)
            sent_at = last_sent[engine] = clock()
            payload = {
                "session_id": session_id,
                "query": queries[index],
                "engine": engine,
                "max_results": 10,
                # Never "handoff": the window is headless and nothing may wait for a person.
                "interstitial": "fallback",
            }
            request_id = uuid.uuid4().hex
            try:
                ack = await asyncio.wait_for(
                    link.exec(request_id, "browser.search", payload, REQUEST_TIMEOUT_S * 1000),
                    REQUEST_TIMEOUT_S + _EXEC_GRACE_S,
                )
            except TimeoutError:
                cancel = getattr(link, "cancel", None)
                if callable(cancel):
                    cancel(request_id)
                ack = {"ok": False, "error": {"class": "timeout", "message": "no answer"}}
            row = row_from_ack(engine, index, ack, round((clock() - sent_at) * 1000))
            rows.append(row)
            if row["outcome"] in STOP_OUTCOMES:
                stopped.add(engine)
        sent = sum(1 for r in rows if r["elapsed_ms"] is not None)
        meta["requests_sent"] = f"{sent} of {len(planned)} planned (cap {MAX_REQUESTS})"
        return rows, meta
    finally:
        if opened:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    link.exec(
                        uuid.uuid4().hex,
                        "browser.session_close",
                        {"session_id": session_id},
                        REQUEST_TIMEOUT_S * 1000,
                    ),
                    REQUEST_TIMEOUT_S + _EXEC_GRACE_S,
                )
        await link.stop()


async def _main_async(engines: Sequence[str], want_json: bool) -> int:
    import shutil
    import tempfile
    from pathlib import Path

    # Imported here, not at module import: the worker module is heavy, and nothing in the
    # cloud package dials anything on import (the broker is only dialled by __main__._run).
    from . import broker, config
    from .__main__ import build_worker_args

    root = Path(tempfile.mkdtemp(prefix="engine-probe-"))
    try:
        cfg = config.CloudConfig(
            broker_http_url="",
            state_dir=root / "state",
            data_dir=root / "data",
            token_file=root / "state" / "absent",
        )
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        link = broker.SubprocessWorker(build_worker_args(cfg))
        rows, meta = await run_probe(
            link,
            engines=engines,
            image_digest=os.environ.get("PAGENTOS_PROBE_IMAGE_DIGEST", "unknown"),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    sys.stdout.write(render_table(rows, meta))
    if want_json:
        sys.stdout.write("\n```json\n" + to_json(rows, meta) + "\n```\n")
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m browser_agent.cloud.engine_probe")
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    parser.add_argument("--json", action="store_true", help="also print the rows as JSON")
    args = parser.parse_args(argv)
    try:
        plan(args.engines)
    except ValueError as exc:
        sys.stderr.write(f"engine probe refused: {exc}\n")
        return 2
    try:
        return asyncio.run(_main_async(args.engines, args.json))
    except Exception as exc:  # noqa: BLE001 - one line for the operator, never a half table
        sys.stderr.write(f"engine probe failed: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
