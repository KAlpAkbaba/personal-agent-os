"""compute.run against a real docker engine: no network, read-only, the deadline, a plain run.

Never skipped: without docker these tests FAIL, and the run is reported NOT_RUN. The image must
be on the host already (the runner passes --pull never):
``docker pull python@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.compute import ComputeOutcome, ComputeRequest, run_compute

pytestmark = pytest.mark.integration

_DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")


@pytest.fixture(scope="module")
def docker() -> str:
    found = shutil.which("docker") or (str(_DOCKER_DESKTOP) if _DOCKER_DESKTOP.exists() else None)
    assert found, "no docker CLI on this host: this suite is NOT_RUN here, never skipped"
    return found


def _containers_named(docker: str, name: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv
        [docker, "ps", "-a", "-q", "--filter", f"name=^/{name}$"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()


def test_the_sandbox_cannot_reach_the_network(docker: str) -> None:
    receipt = run_compute(
        ComputeRequest(
            code='import socket\nsocket.create_connection(("1.1.1.1", 53), timeout=3)\n',
            timeout_s=30,
        ),
        docker=docker,
    )
    assert receipt.outcome is ComputeOutcome.OK, receipt
    assert receipt.exit_code == 1, receipt
    assert "Network is unreachable" in receipt.stderr, receipt.stderr


def test_the_root_filesystem_is_read_only(docker: str) -> None:
    receipt = run_compute(ComputeRequest(code='open("/x", "w")\n', timeout_s=30), docker=docker)
    assert receipt.exit_code == 1, receipt
    assert "Read-only file system" in receipt.stderr, receipt.stderr


def test_an_endless_loop_is_killed_at_the_deadline_and_no_container_is_left(docker: str) -> None:
    started = time.monotonic()
    receipt = run_compute(
        ComputeRequest(code="while True:\n    pass\n", timeout_s=4), docker=docker
    )
    elapsed = time.monotonic() - started
    assert receipt.outcome is ComputeOutcome.TIMEOUT, receipt
    assert receipt.killed
    assert elapsed < 60, elapsed  # a hang guard, not the claim
    assert receipt.container
    # --rm removes the killed container; allow the daemon a moment to do it.
    deadline = time.monotonic() + 20
    while _containers_named(docker, receipt.container) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert _containers_named(docker, receipt.container) == ""


def test_a_plain_program_prints_its_answer(docker: str) -> None:
    receipt = run_compute(ComputeRequest(code="print(2+2)\n", timeout_s=30), docker=docker)
    assert receipt.outcome is ComputeOutcome.OK, receipt
    assert receipt.exit_code == 0
    assert receipt.stdout.strip() == "4"


def test_running_as_nobody_without_capabilities(docker: str) -> None:
    code = (
        "import os\n"
        "print(os.getuid())\n"
        "status = open('/proc/self/status').read().splitlines()\n"
        "print([l for l in status if l.startswith(('CapEff', 'NoNewPrivs'))])\n"
    )
    receipt = run_compute(ComputeRequest(code=code, timeout_s=30), docker=docker)
    assert receipt.exit_code == 0, receipt
    assert receipt.stdout.splitlines()[0] == "65534"
    assert "CapEff:\\t0000000000000000" in receipt.stdout
    assert "NoNewPrivs:\\t1" in receipt.stdout


def test_memory_over_the_ceiling_is_oom(docker: str) -> None:
    receipt = run_compute(
        ComputeRequest(code='x = b"x" * (400 * 1024 * 1024)\n', memory_mib=128, timeout_s=30),
        docker=docker,
    )
    assert receipt.outcome is ComputeOutcome.OOM, receipt
    assert receipt.exit_code == 137
