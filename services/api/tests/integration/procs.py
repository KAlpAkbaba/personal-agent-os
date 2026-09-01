"""Spawning and stopping the helper processes the E2E tests need.

Several integration tests run real subprocesses — a Temporal worker, a uvicorn
server, a probe — and a few of them kill one on purpose, because surviving a
hard kill is the property under test. The cleanup path is a different matter:
a hard kill there leaves the child's PostgreSQL backends behind. Docker
Desktop's port forwarder keeps the TCP connection open after the client
process is gone, so the server does not see the peer disappear and the backend
sits in `idle` until keepalives reap it. Enough of those and the next test to
ask for a connection fails with "too many clients already" — a failure that
reads like a product defect and is not one.

So: `stop()` asks the child to exit, which lets psycopg send its terminate
message and the backends go away at once. `kill()` stays available and stays
correct where the abrupt death IS the test.

On Windows there is no SIGTERM: `Popen.terminate()` is `TerminateProcess`, as
abrupt as a kill. The graceful equivalent is a console control event, which
requires the child to have been created in its own process group — hence
`spawn()` rather than a bare `subprocess.Popen` at each call site.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

#: Windows: put the child in its own group so CTRL_BREAK reaches it alone.
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

STOP_TIMEOUT_S = 15.0


def spawn(args: list[str], **kwargs) -> subprocess.Popen:
    """Start a helper process that `stop()` can later shut down gracefully."""
    creationflags = kwargs.pop("creationflags", 0)
    if sys.platform == "win32":
        creationflags |= _CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(args, creationflags=creationflags, **kwargs)


def stop(proc: subprocess.Popen | None, *, timeout: float = STOP_TIMEOUT_S) -> None:
    """Ask `proc` to exit, then insist. Safe to call on an already-dead process.

    Graceful first so the child closes its database connections itself; the
    kill is the fallback for a child that ignores or cannot service the signal.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except (OSError, ValueError):  # already gone, or no console to signal
        pass
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - nothing left to try
        pass


__all__ = ["STOP_TIMEOUT_S", "spawn", "stop"]
