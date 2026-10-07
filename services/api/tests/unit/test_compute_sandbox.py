"""compute.run sandbox: the docker argv rule and the runner, with a fake subprocess.

No docker, no network: the real-docker proof is tests/integration/test_compute_sandbox_docker.py.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

import pytest

from app.compute import policy
from app.compute.policy import (
    DEFAULT_IMAGE,
    ComputePolicyError,
    ComputeRequest,
    build_argv,
)
from app.compute.receipt import ComputeOutcome, ComputeReceipt
from app.compute.runner import run
from app.execution.rule import Availability, ExecutionRequest, JobKind, Target, decide

NAME = "pagentos-compute-" + "0" * 32
DIGEST = "ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c"
REPO_ROOT = Path(__file__).resolve().parents[4]


# --------------------------------------------------------------------- argv rule


def test_argv_is_exactly_the_hardened_one_shot_run() -> None:
    req = ComputeRequest(code="print(1)", memory_mib=256, cpus=1.0, timeout_s=30)
    assert build_argv(req, NAME) == [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        NAME,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--ulimit",
        "nofile=64:64",
        "--init",
        "--user",
        "65534:65534",
        "--log-driver",
        "none",
        "-i",
        f"python@sha256:{DIGEST}",
        "python3",
        "-",
    ]


def test_argv_has_no_network() -> None:
    argv = build_argv(ComputeRequest(code="print(1)"), NAME)
    i = argv.index("--network")
    assert argv[i + 1] == "none"
    assert not any(a.startswith("--net=") or a == "--net" for a in argv)


def test_argv_runs_as_nobody_without_capabilities() -> None:
    argv = build_argv(ComputeRequest(code="print(1)"), NAME)
    assert argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in argv
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


def test_default_image_is_pinned_by_digest() -> None:
    assert DEFAULT_IMAGE == f"python@sha256:{DIGEST}"


@pytest.mark.parametrize(
    "image",
    [
        "python:latest",
        "python",
        "python:3.12-slim",
        f"python:latest@sha256:{DIGEST}",
        "python@sha256:abc",
        f"python@sha256:{DIGEST} --privileged",
        f"--privileged python@sha256:{DIGEST}",
        "",
    ],
)
def test_image_without_a_digest_or_with_latest_is_refused(image: str) -> None:
    with pytest.raises(ComputePolicyError):
        build_argv(ComputeRequest(code="print(1)", image=image), NAME)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_mib", 513),
        ("memory_mib", 0),
        ("timeout_s", 61),
        ("timeout_s", 0),
        ("cpus", 1.01),
        ("cpus", 0),
        ("output_bytes", 64 * 1024 + 1),
        ("language", "bash"),
        ("stdin", "data"),
    ],
)
def test_limit_over_the_ceiling_is_refused(field: str, value: object) -> None:
    req = ComputeRequest(code="print(1)", **{field: value})  # type: ignore[arg-type]
    with pytest.raises(ComputePolicyError):
        build_argv(req, NAME)


def test_code_over_64_kib_is_refused_and_64_kib_is_allowed() -> None:
    build_argv(ComputeRequest(code="#" * (64 * 1024)), NAME)
    with pytest.raises(ComputePolicyError):
        build_argv(ComputeRequest(code="#" * (64 * 1024 + 1)), NAME)
    # Bytes, not characters: 'ş' is two bytes in UTF-8.
    with pytest.raises(ComputePolicyError):
        build_argv(ComputeRequest(code="ş" * (32 * 1024 + 1)), NAME)


def test_empty_code_is_refused() -> None:
    with pytest.raises(ComputePolicyError):
        build_argv(ComputeRequest(code="   "), NAME)


@pytest.mark.parametrize("name", ["x", "pagentos-compute-", NAME + " -v", "--privileged", "a;b"])
def test_container_name_outside_the_pattern_is_refused(name: str) -> None:
    with pytest.raises(ComputePolicyError):
        build_argv(ComputeRequest(code="print(1)"), name)


_FORBIDDEN_EXACT = {
    "-v",
    "--volume",
    "--mount",
    "-e",
    "--env",
    "--env-file",
    "--privileged",
    "--device",
    "--pid",
    "--ipc",
    "--uts",
    "--userns",
    "--cap-add",
    "--volumes-from",
    "-p",
    "--publish",
    "-P",
    "--add-host",
    "--gpus",
}
_FORBIDDEN_FRAGMENTS = ("seccomp", "apparmor", "unconfined", "docker.sock", "host", "=")


@pytest.mark.parametrize(
    "req",
    [
        ComputeRequest(code="print(1)"),
        ComputeRequest(code="-v /:/host -e SECRET=1 --privileged", memory_mib=512, cpus=1.0),
        ComputeRequest(code="import os; print(os.environ)", memory_mib=1, cpus=0.1, timeout_s=1),
        ComputeRequest(code="x" * 1000, timeout_s=60, output_bytes=1),
    ],
)
def test_no_env_volume_mount_or_host_argument_can_be_produced(req: ComputeRequest) -> None:
    argv = build_argv(req, NAME)
    for arg in argv:
        assert arg not in _FORBIDDEN_EXACT, arg
        assert not arg.startswith(("--env", "--mount", "--volume", "-v=", "-e=", "--cap-add")), arg
        if arg.startswith("/tmp:") or arg.startswith("nofile="):
            continue  # the tmpfs and ulimit values carry ':' / '=' on purpose
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in arg, (fragment, arg)
    # The code never reaches the command line: it goes through stdin.
    assert req.code not in argv
    assert not any(req.code in a for a in argv if len(req.code) > 3)


def test_dockerfile_from_matches_the_policy_image() -> None:
    """The two halves read each other: the mirror image and the run image are one digest."""
    dockerfile = (REPO_ROOT / "infra/docker/sandbox/Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"(?m)^FROM\s+(\S+)", dockerfile)
    assert froms == [policy.DEFAULT_IMAGE]
    assert re.search(r"(?m)^USER\s+65534:65534\s*$", dockerfile)
    assert not re.search(r"(?m)^RUN\s", dockerfile), "no packages are added to the sandbox image"


def test_compose_fragment_carries_the_same_image_and_limits() -> None:
    text = (REPO_ROOT / "infra/docker/sandbox/compose.fragment.yml").read_text(encoding="utf-8")
    assert policy.DEFAULT_IMAGE in text
    assert "network_mode: none" in text
    assert f"mem_limit: {policy.MAX_MEMORY_MIB}m" in text


def test_compute_jobs_only_ever_run_in_the_cloud() -> None:
    decision = decide(ExecutionRequest(JobKind.COMPUTE, Availability(device_online=True)))
    assert decision.target is Target.CLOUD
    assert decision.chain == (Target.CLOUD,)


# --------------------------------------------------------------------- runner


class _Stdin:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data += data
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Pipe(io.BytesIO):
    """A pipe returns what it has, not what was asked for: odd sizes cross every boundary."""

    def read(self, size: int | None = -1) -> bytes:
        return super().read(min(size, 5000) if size and size > 0 else 5000)


class FakeProc:
    def __init__(
        self,
        argv: list[str],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self.argv = argv
        self.stdin = _Stdin()
        self.stdout = _Pipe(stdout)
        self.stderr = _Pipe(stderr)
        self._rc = returncode
        self._hang = hang
        self.killed = False
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, timeout or 0)
        self.returncode = 137 if self.killed else self._rc
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class Harness:
    def __init__(self, **proc_kwargs: object) -> None:
        self.proc_kwargs = proc_kwargs
        self.procs: list[FakeProc] = []
        self.container_kills: list[str] = []

    def popen(self, argv: list[str], **_kw: object) -> FakeProc:
        proc = FakeProc(argv, **self.proc_kwargs)  # type: ignore[arg-type]
        self.procs.append(proc)
        return proc

    def kill_container(self, name: str) -> None:
        self.container_kills.append(name)


def _run(h: Harness, req: ComputeRequest | None = None) -> ComputeReceipt:
    return run(
        req or ComputeRequest(code="print(2+2)", timeout_s=5),
        popen=h.popen,
        kill_container=h.kill_container,
        name=NAME,
    )


def test_ok_run_writes_the_code_to_stdin_and_keeps_stdout() -> None:
    h = Harness(stdout=b"4\n")
    receipt = _run(h)
    assert receipt.outcome is ComputeOutcome.OK
    assert receipt.exit_code == 0
    assert receipt.stdout == "4\n"
    assert not receipt.killed
    assert h.procs[0].stdin.data == b"print(2+2)"
    assert h.procs[0].stdin.closed
    assert h.procs[0].argv == build_argv(ComputeRequest(code="print(2+2)", timeout_s=5), NAME)
    assert h.container_kills == []
    assert receipt.image == DEFAULT_IMAGE


def test_deadline_kills_the_container_and_the_receipt_says_timeout() -> None:
    h = Harness(hang=True)
    receipt = _run(h)
    assert h.container_kills == [NAME]
    assert h.procs[0].killed
    assert receipt.killed
    assert receipt.outcome is ComputeOutcome.TIMEOUT


def test_exit_137_without_our_kill_is_oom() -> None:
    receipt = _run(Harness(returncode=137))
    assert receipt.outcome is ComputeOutcome.OOM
    assert receipt.exit_code == 137
    assert not receipt.killed


def test_a_clean_memory_error_is_a_non_zero_ok_not_oom() -> None:
    receipt = _run(Harness(returncode=1, stderr=b"MemoryError\n"))
    assert receipt.outcome is ComputeOutcome.OK
    assert receipt.exit_code == 1
    assert "MemoryError" in receipt.stderr


def test_stdout_over_64_kib_is_cut_and_the_receipt_says_truncated() -> None:
    big = b"a" * (64 * 1024 + 4096)
    receipt = _run(Harness(stdout=big, stderr=b"e" * 10))
    assert len(receipt.stdout.encode()) == 64 * 1024
    assert receipt.stdout_truncated
    assert not receipt.stderr_truncated
    assert receipt.to_detail_json()["truncated"] is True


def test_stderr_is_cut_too() -> None:
    receipt = _run(Harness(stderr=b"e" * (70 * 1024)))
    assert len(receipt.stderr) == 64 * 1024
    assert receipt.stderr_truncated


def test_requested_output_ceiling_below_64_kib_is_honoured() -> None:
    req = ComputeRequest(code="print(1)", output_bytes=10)
    receipt = _run(Harness(stdout=b"0123456789abc"), req)
    assert receipt.stdout == "0123456789"
    assert receipt.stdout_truncated


def test_no_docker_is_a_receipt_not_an_exception() -> None:
    def popen(argv: list[str], **_kw: object) -> FakeProc:
        raise FileNotFoundError("docker")

    receipt = run(ComputeRequest(code="print(1)"), popen=popen, name=NAME)
    assert receipt.outcome is ComputeOutcome.DOCKER_UNAVAILABLE
    assert receipt.exit_code is None


def test_docker_daemon_error_125_is_docker_unavailable() -> None:
    receipt = _run(Harness(returncode=125, stderr=b"Cannot connect to the Docker daemon"))
    assert receipt.outcome is ComputeOutcome.DOCKER_UNAVAILABLE


def test_refused_request_never_starts_a_process() -> None:
    h = Harness()
    receipt = _run(h, ComputeRequest(code="print(1)", memory_mib=513))
    assert receipt.outcome is ComputeOutcome.REFUSED
    assert receipt.detail and "memory" in receipt.detail
    assert h.procs == []


def test_detail_json_is_plain_and_carries_the_ceilings() -> None:
    receipt = _run(Harness(stdout=b"4\n"))
    detail = receipt.to_detail_json()
    assert detail["outcome"] == "ok"
    assert detail["image"] == DEFAULT_IMAGE
    assert detail["limits"] == {
        "memory_mib": 256,
        "cpus": 1.0,
        "timeout_s": 5,
        "output_bytes": 64 * 1024,
        "code_bytes": len("print(2+2)"),
    }
    assert detail["container"] == NAME
    assert set(detail) >= {"exit_code", "stdout", "stderr", "duration_ms", "killed", "truncated"}
    # Plain: only JSON types all the way down.
    import json

    assert json.loads(json.dumps(detail)) == detail


def test_package_entry_point_is_the_runner() -> None:
    import app.compute as compute

    assert compute.run_compute is run
    assert compute.TOOL_NAME == "compute.run"
    assert compute.LEDGER_KIND == "compute.run"
    assert compute.JOB_KIND is JobKind.COMPUTE
