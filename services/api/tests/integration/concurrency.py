"""``fire_together``: the owner's phone and two PCs writing the same thing at the same moment.

Three races reached production on 2026-10-06 with every single-request test green (a lost
conversation line behind a 409, a 500 on the same new household item, a 21st watch past a cap
of 20); only the test team found them. This helper makes the same shape a test the worker and
the inspector run: N requests through the REAL application object (``create_app``, the owner
session from ``attach_owner``) to the REAL PostgreSQL of the integration suite, released
together.

How they overlap: an ``httpx.AsyncClient`` over ``ASGITransport`` runs every request on one
event loop; each waits on one ``asyncio.Barrier(n)`` and they are released together. The write
routes do their database work in ``asyncio.to_thread`` with a session of their own, so N
released requests are N threads on N pooled connections - the database sees them at once
(``test_concurrency_helper_pg.py`` counts it: N different backend pids open together).

Released together is not yet read-then-write interleaved: on 2026-10-06 the bare race lost a
line in 4 of 5 runs, a watch past the cap in 2 of 5. A strict xfail needs every run, so
``meet_after`` names the read (a regular expression on the SQL) after which the requests wait
for each other before they go on to write - the window a check-then-insert leaves open,
held open. A fixed route passes through it too: a lock or an upsert makes the wait end by
``meet_wait_s`` and the rows come out right.

The claim is the caller's: the status codes returned here and the rows it counts afterwards.
``timeout_s`` and ``meet_wait_s`` are hang guards, never assertions.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

Body = Mapping[str, Any] | Callable[[int], Mapping[str, Any]] | None


def _app_and_headers(
    target: TestClient | FastAPI, headers: Mapping[str, str] | None
) -> tuple[FastAPI, dict[str, str]]:
    if isinstance(target, TestClient):
        app = target.app
        merged = {
            key: value for key, value in target.headers.items() if key.lower() == "authorization"
        }
    else:
        app = target
        merged = {}
    merged.update(headers or {})
    return app, merged  # type: ignore[return-value]


class Meeting:
    """The first ``n`` statements matching ``pattern`` wait until all ``n`` have run, or until
    ``wait_s``; later matches pass at once. ``arrived`` is how many met; ``released_seeing``
    is, per released statement, how many had arrived when it was let go - ``[n] * n`` when the
    window was really held, fewer for one that timed out (a fixed route's lock)."""

    def __init__(self, pattern: str, n: int, wait_s: float) -> None:
        self.pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self.n = n
        self.wait_s = wait_s
        self.arrived = 0
        self.released_seeing: list[int] = []
        self.cond = threading.Condition()

    def after_cursor_execute(self, conn, cursor, statement, parameters, context, many) -> None:  # noqa: ANN001, PLR0913
        if not self.pattern.search(statement):
            return
        with self.cond:
            if self.arrived >= self.n:
                return
            self.arrived += 1
            self.cond.notify_all()
            self.cond.wait_for(lambda: self.arrived >= self.n, timeout=self.wait_s)
            self.released_seeing.append(self.arrived)


@contextmanager
def _meeting(pattern: str | None, n: int, wait_s: float) -> Iterator[Meeting | None]:
    if pattern is None:
        yield None
        return
    meeting = Meeting(pattern, n, wait_s)
    event.listen(Engine, "after_cursor_execute", meeting.after_cursor_execute)
    try:
        yield meeting
    finally:
        event.remove(Engine, "after_cursor_execute", meeting.after_cursor_execute)


def fire_together(
    target: TestClient | FastAPI,
    method: str,
    path: str,
    *,
    json: Body = None,
    n: int = 2,
    headers: Mapping[str, str] | None = None,
    meet_after: str | None = None,
    meet_wait_s: float = 3.0,
    timeout_s: float = 60.0,
    on_meeting: Callable[[Meeting], None] | None = None,
) -> list[httpx.Response]:
    """Send ``n`` requests to ``path`` at the same moment; their responses, in request order.

    ``target`` is ``owner_client(settings)`` (its app and its owner ``Authorization`` are
    used) or a bare app with ``headers``. ``json`` is one body for every request, or
    ``json(i)`` a body per request (25 different watch labels). ``meet_after`` (optional): a
    regular expression on the SQL of the route's read; see the module text. ``on_meeting``
    (optional) is given the ``Meeting`` afterwards, to count how its window was released.
    Call it from a synchronous test; it runs its own event loop.
    """
    if n < 2:
        raise ValueError("fire_together needs at least two requests to make a race")
    app, sent_headers = _app_and_headers(target, headers)

    async def run() -> list[httpx.Response]:
        barrier = asyncio.Barrier(n)
        # An unhandled error is the 500 a device sees, not an exception in the test.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=sent_headers
        ) as client:

            async def one(i: int) -> httpx.Response:
                body = json(i) if callable(json) else json
                await barrier.wait()
                return await client.request(method, path, json=body)

            return await asyncio.wait_for(
                asyncio.gather(*(one(i) for i in range(n))), timeout=timeout_s
            )

    with _meeting(meet_after, n, meet_wait_s) as meeting:
        responses = asyncio.run(run())
    if meeting is not None and meeting.arrived == 0:
        # A stale pattern would quietly turn the held window back into a timing race.
        raise AssertionError(f"meet_after matched no statement of {method} {path}: {meet_after!r}")
    if meeting is not None and on_meeting is not None:
        on_meeting(meeting)
    return responses
