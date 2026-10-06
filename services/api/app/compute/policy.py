"""compute.run policy: a ComputeRequest -> the one docker argv that may run it, as pure code.

Model-written code is untrusted. It runs in a one-shot container with no network, no
capabilities, a read-only root, a nobody uid, hard memory/CPU/pid ceilings, no volume, no
environment variable and no secret; the code itself goes through stdin (``python3 -``), never
the command line. Anything outside the ceilings is refused, not clamped. Nothing here starts a
process: ``runner.run`` does. Decisions and the measured flag effects:
team/plans/compute-run-sandbox-adr.md and team/plans/compute-run-sandbox-integration.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: The official python:3.12-slim (3.12.15-slim-trixie) by its multi-arch INDEX digest.
#: infra/docker/sandbox/Dockerfile's FROM is this same string (a test reads both).
DEFAULT_IMAGE: Final = (
    "python@sha256:ddb0207ae1f0356c2b724d740769b0c5f5f51cc54a0525178f721825f78fe74c"
)

MAX_MEMORY_MIB: Final = 512
MAX_CPUS: Final = 1.0
MAX_TIMEOUT_S: Final = 60
MAX_OUTPUT_BYTES: Final = 64 * 1024
MAX_CODE_BYTES: Final = 64 * 1024
PIDS_LIMIT: Final = 128
TMPFS: Final = "/tmp:rw,noexec,nosuid,nodev,size=64m"
NOBODY: Final = "65534:65534"
LANGUAGES: Final = frozenset({"python"})

_IMAGE_RE: Final = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}")
_NAME_RE: Final = re.compile(r"pagentos-compute-[0-9a-f]{32}")


class ComputePolicyError(ValueError):
    """The request is not allowed (izin verilmiyor); the message names the broken rule."""


@dataclass(frozen=True, slots=True)
class ComputeRequest:
    code: str
    language: str = "python"
    #: ``python3 -`` reads the CODE from stdin to EOF, so the program has no stdin of its
    #: own in v1: anything but "" is refused rather than silently dropped.
    stdin: str = ""
    memory_mib: int = 256
    cpus: float = 1.0
    timeout_s: int = 30
    output_bytes: int = MAX_OUTPUT_BYTES
    image: str = DEFAULT_IMAGE

    @property
    def code_bytes(self) -> int:
        return len(self.code.encode("utf-8"))


def validate(request: ComputeRequest) -> None:
    """Raise ComputePolicyError for anything outside the fixed ceilings."""
    if request.language not in LANGUAGES:
        raise ComputePolicyError(f"language {request.language!r} is not allowed (python only)")
    if not request.code.strip():
        raise ComputePolicyError("code is empty")
    if request.code_bytes > MAX_CODE_BYTES:
        raise ComputePolicyError(f"code is {request.code_bytes} bytes; at most {MAX_CODE_BYTES}")
    if request.stdin:
        raise ComputePolicyError("stdin is not supported: the code itself is read from stdin")
    if not 1 <= request.memory_mib <= MAX_MEMORY_MIB:
        raise ComputePolicyError(f"memory {request.memory_mib} MiB; allowed 1..{MAX_MEMORY_MIB}")
    if not 0 < request.cpus <= MAX_CPUS:
        raise ComputePolicyError(f"cpus {request.cpus}; allowed (0, {MAX_CPUS}]")
    if not 1 <= request.timeout_s <= MAX_TIMEOUT_S:
        raise ComputePolicyError(f"timeout {request.timeout_s} s; allowed 1..{MAX_TIMEOUT_S}")
    if not 1 <= request.output_bytes <= MAX_OUTPUT_BYTES:
        raise ComputePolicyError(
            f"output ceiling {request.output_bytes} bytes; allowed 1..{MAX_OUTPUT_BYTES}"
        )
    if "latest" in request.image or not _IMAGE_RE.fullmatch(request.image):
        raise ComputePolicyError(f"image {request.image!r} must be name@sha256:<digest>, no tag")


def _cpus(value: float) -> str:
    return f"{value:g}"


def build_argv(request: ComputeRequest, name: str) -> list[str]:
    """The full ``docker run`` argv for ``request`` in a container called ``name``."""
    validate(request)
    if not _NAME_RE.fullmatch(name):
        raise ComputePolicyError(f"container name {name!r} is not pagentos-compute-<32 hex>")
    memory = f"{request.memory_mib}m"
    return [
        "docker",
        "run",
        "--rm",
        # The image is pulled once at release time; a run never touches a registry.
        "--pull",
        "never",
        # A name, so the deadline can kill the CONTAINER: killing the client does not.
        "--name",
        name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        TMPFS,
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        _cpus(request.cpus),
        "--pids-limit",
        str(PIDS_LIMIT),
        "--ulimit",
        "nofile=64:64",
        "--init",
        "--user",
        NOBODY,
        # Otherwise the json-file driver copies all output to the host disk as well.
        "--log-driver",
        "none",
        "-i",
        request.image,
        "python3",
        "-",
    ]
