"""compute.run runner: start the policy's docker argv, enforce the deadline, return a receipt.

Never raises for a run that went wrong: a refused request, a missing docker CLI, a killed or
out-of-memory container all come back as a ComputeReceipt. Output is read in chunks on its own
threads; the first ``output_bytes`` of each stream are kept and the rest is drained and dropped,
so a chatty program neither fills memory nor blocks on a full pipe. At the deadline the
CONTAINER is killed by name (killing the docker client leaves it running, measured), then the
client.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import IO, Any

from app.compute.policy import ComputePolicyError, ComputeRequest, build_argv
from app.compute.receipt import ComputeOutcome, ComputeReceipt

_CHUNK = 8192
#: docker run's own failure (daemon down, image absent under --pull never, bad flag).
_DOCKER_ERROR = 125
_SIGKILL = 137
#: How long the client may take to go after its container was killed.
_REAP_S = 15.0

Popen = Callable[..., Any]


def _docker_kill(docker: str) -> Callable[[str], None]:
    def kill(name: str) -> None:
        try:
            subprocess.run(  # noqa: S603 - fixed argv; the name matched the policy pattern
                [docker, "kill", name], capture_output=True, timeout=_REAP_S, check=False
            )
        except (OSError, subprocess.SubprocessError):
            pass  # The client kill below still ends the run; --rm cleans up when it can.

    return kill


class _Reader(threading.Thread):
    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self.kept = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while chunk := self._stream.read(_CHUNK):
                room = self._limit - len(self.kept)
                if room > 0:
                    self.kept += chunk[:room]
                if len(chunk) > max(room, 0):
                    self.truncated = True
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        return bytes(self.kept).decode("utf-8", errors="replace")


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (OSError, ValueError):
        pass  # The container went before reading it all; its exit code says why.
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _limits(request: ComputeRequest) -> dict[str, Any]:
    return {
        "memory_mib": request.memory_mib,
        "cpus": request.cpus,
        "timeout_s": request.timeout_s,
        "output_bytes": request.output_bytes,
        "code_bytes": request.code_bytes,
    }


def run(
    request: ComputeRequest,
    *,
    docker: str = "docker",
    popen: Popen = subprocess.Popen,
    kill_container: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    name: str | None = None,
) -> ComputeReceipt:
    """Run ``request`` once in the sandbox and describe what happened."""
    name = name or f"pagentos-compute-{uuid.uuid4().hex}"
    limits = _limits(request)
    try:
        argv = build_argv(request, name)
    except ComputePolicyError as exc:
        return ComputeReceipt(ComputeOutcome.REFUSED, request.image, limits, detail=str(exc))
    argv[0] = docker
    kill = kill_container or _docker_kill(docker)

    started = clock()
    try:
        proc = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return ComputeReceipt(
            ComputeOutcome.DOCKER_UNAVAILABLE,
            request.image,
            limits,
            container=name,
            detail=f"docker could not be started: {exc}",
        )

    out = _Reader(proc.stdout, request.output_bytes)
    err = _Reader(proc.stderr, request.output_bytes)
    feeder = threading.Thread(
        target=_write_stdin, args=(proc.stdin, request.code.encode("utf-8")), daemon=True
    )
    for thread in (out, err, feeder):
        thread.start()

    killed = False
    try:
        exit_code = proc.wait(timeout=request.timeout_s)
    except subprocess.TimeoutExpired:
        killed = True
        kill(name)
        proc.kill()
        exit_code = proc.wait(timeout=_REAP_S)
    for thread in (out, err, feeder):
        thread.join(timeout=_REAP_S)
    duration_ms = int((clock() - started) * 1000)

    if killed:
        outcome = ComputeOutcome.TIMEOUT
    elif exit_code == _SIGKILL:
        outcome = ComputeOutcome.OOM
    elif exit_code == _DOCKER_ERROR:
        outcome = ComputeOutcome.DOCKER_UNAVAILABLE
    else:
        outcome = ComputeOutcome.OK
    return ComputeReceipt(
        outcome,
        request.image,
        limits,
        exit_code=exit_code,
        stdout=out.text(),
        stderr=err.text(),
        stdout_truncated=out.truncated,
        stderr_truncated=err.truncated,
        duration_ms=duration_ms,
        killed=killed,
        container=name,
    )
