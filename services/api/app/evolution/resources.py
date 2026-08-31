"""Isolation, resource budgets and the self-extension recursion limit
(ACCEPTANCE_TESTS M7 "Isolation, supply chain, resources"; SECURITY_MODEL M7).

Three separable concerns live here so each is independently testable:

1. **No production secrets in generated code.** ``build_isolated_env`` builds the
   subprocess environment from an ALLOWLIST (never by filtering the parent
   environment), and ``assert_no_secrets`` then re-checks the result against an
   explicit DENY-LIST of secret-bearing names/suffixes. Allowlist-first plus a
   deny-list assertion means a newly invented ``PAGENTOS_NEW_SECRET`` is
   excluded by construction *and* would be caught by the assertion if the
   allowlist were ever widened carelessly.

2. **Capability-scoped access, deny-by-default.** ``permission_findings`` maps
   constructs in the generated source to the permission classes they would
   need, and refuses anything the manifest does not grant. A generated skill
   with the default (empty) grants therefore cannot open a socket, touch the
   filesystem, reach a device or read a secret — enforcement, not documentation.

3. **Budgets and recursion.** ``ResourceBudget`` carries wall-clock timeout,
   CPU/memory/disk/output caps, a network flag, a retry limit and the
   self-extension depth limit. Timeout, disk, output and network are enforced on
   every platform; CPU/memory are enforced with POSIX rlimits and reported as
   ``enforced_by="wall_clock"`` on Windows, where the hard wall-clock timeout is
   the backstop (documented deviation).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.evolution.errors import EvolutionError, EvolutionErrorClass

# Only these names are ever copied from the parent environment into a generated
# skill's subprocess. Everything else is absent by construction.
ENV_ALLOWLIST = ("SYSTEMROOT", "PATH", "TEMP", "TMP", "COMSPEC")

# Belt-and-braces: names/suffixes that must NEVER appear in a generated-code
# environment, asserted after the environment is built.
SECRET_ENV_PREFIXES = (
    "PAGENTOS_",
    "DATABASE_",
    "POSTGRES_",
    "PG",
    "REDIS_",
    "S3_",
    "AWS_",
    "MINIO_",
    "TEMPORAL_",
    "ANTHROPIC_",
    "OPENAI_",
    "ELEVENLABS_",
    "DEEPGRAM_",
    "AZURE_",
    "GOOGLE_",
    "TAILSCALE_",
    "GITHUB_",
    "GH_",
)
SECRET_ENV_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASS", "_CREDENTIALS", "_DSN")
# The only PAGENTOS_* names generated code legitimately needs: where its skill
# lives, and (for a rollout replay) where its case file is. Both are paths into
# the sandbox, never credentials.
SECRET_ENV_EXEMPT = ("PAGENTOS_SKILL_DIR", "PAGENTOS_ROLLOUT_CASES")

# Constructs -> the permission class they require. Deny-by-default means any of
# these in a skill whose manifest grants nothing is a violation.
PERMISSION_CONSTRUCTS: dict[str, tuple[str, ...]] = {
    "network_permissions": ("socket", "urllib", "http", "ftplib", "smtplib", "requests", "asyncio"),
    "filesystem_permissions": ("pathlib", "shutil", "tempfile", "glob", "sqlite3", "csv"),
    "device_permissions": ("ctypes", "serial", "winreg", "msvcrt", "fcntl", "termios"),
    "secret_requirements": ("keyring", "getpass"),
}
# Bare calls (not imports) that need a permission class.
PERMISSION_CALLS: dict[str, tuple[str, ...]] = {
    "filesystem_permissions": ("open(", "os.remove", "os.rename", "os.mkdir", "os.rmdir"),
    "secret_requirements": ("os.environ", "os.getenv"),
}

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_CPU_SECONDS = 30
DEFAULT_MEMORY_MB = 512
DEFAULT_DISK_MB = 8
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_RETRY_LIMIT = 1

# Self-extension recursion limit: an evolution triggered BY an evolution may go
# at most this deep. Depth 0 is an owner/task-triggered gap; depth 1 is an
# evolution that another evolution asked for; depth 2 is refused.
MAX_EVOLUTION_DEPTH = 1


@dataclass(slots=True)
class ResourceBudget:
    timeout_s: float = DEFAULT_TIMEOUT_S
    cpu_seconds: int = DEFAULT_CPU_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    disk_mb: int = DEFAULT_DISK_MB
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    network: bool = False
    retry_limit: int = DEFAULT_RETRY_LIMIT
    max_depth: int = MAX_EVOLUTION_DEPTH

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_s": self.timeout_s,
            "cpu_seconds": self.cpu_seconds,
            "memory_mb": self.memory_mb,
            "disk_mb": self.disk_mb,
            "max_output_bytes": self.max_output_bytes,
            "network": self.network,
            "retry_limit": self.retry_limit,
            "max_depth": self.max_depth,
            "cpu_memory_enforced_by": "rlimit" if hasattr(os, "fork") else "wall_clock",
        }

    def rlimit_preexec(self):  # pragma: no cover - POSIX only
        """CPU/address-space rlimits for POSIX. ``None`` on Windows."""
        if not hasattr(os, "fork"):
            return None
        import resource as _resource

        cpu_seconds = self.cpu_seconds
        memory_bytes = self.memory_mb * 1024 * 1024

        def _apply() -> None:
            _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            _resource.setrlimit(_resource.RLIMIT_AS, (memory_bytes, memory_bytes))

        return _apply


# --------------------------------------------------------------- environment


def build_isolated_env(skill_dir: Path | str, *, skill_dir_var: str) -> dict[str, str]:
    """Allowlist-built environment for generated code. No secrets, ever."""
    env: dict[str, str] = {skill_dir_var: str(skill_dir), "PYTHONUTF8": "1"}
    for name in ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value:
            env[name] = value
    assert_no_secrets(env, skill_dir_var=skill_dir_var)
    return env


def secret_names(env: dict[str, str], *, skill_dir_var: str = "PAGENTOS_SKILL_DIR") -> list[str]:
    exempt = set(SECRET_ENV_EXEMPT) | {skill_dir_var}
    offenders: list[str] = []
    for name in env:
        if name in exempt:
            continue
        upper = name.upper()
        if upper.startswith(SECRET_ENV_PREFIXES) or upper.endswith(SECRET_ENV_SUFFIXES):
            offenders.append(name)
    return sorted(offenders)


def assert_no_secrets(
    env: dict[str, str], *, skill_dir_var: str = "PAGENTOS_SKILL_DIR"
) -> None:
    offenders = secret_names(env, skill_dir_var=skill_dir_var)
    if offenders:
        raise EvolutionError(
            EvolutionErrorClass.PERMISSION_DENIED,
            "generated-code environment carries production secret variables",
            details={"variables": offenders},
        )


# ---------------------------------------------------------- permission scope


def permission_findings(source: str, manifest: dict[str, Any] | None) -> list[dict[str, str]]:
    """Constructs the source uses that the manifest does not grant.

    Deny-by-default: an EMPTY grant list means the class is forbidden entirely.
    """
    manifest = manifest or {}
    findings: list[dict[str, str]] = []
    stripped = source
    for permission, modules in PERMISSION_CONSTRUCTS.items():
        if manifest.get(permission):
            continue  # explicitly granted (and reviewer-approved at registration)
        for module in modules:
            if (
                f"import {module}" in stripped
                or f"from {module}" in stripped
                or f"import {module}." in stripped
            ):
                findings.append(
                    {"permission": permission, "construct": f"import {module}"}
                )
    for permission, calls in PERMISSION_CALLS.items():
        if manifest.get(permission):
            continue
        for call in calls:
            if call in stripped:
                findings.append({"permission": permission, "construct": call})
    return findings


def require_scoped_access(source: str, manifest: dict[str, Any] | None) -> None:
    findings = permission_findings(source, manifest)
    if findings:
        raise EvolutionError(
            EvolutionErrorClass.PERMISSION_DENIED,
            "generated source uses capabilities the manifest does not grant "
            "(generated skills are deny-by-default)",
            details={"findings": findings},
        )


# ---------------------------------------------------------------- budgets


def directory_size_bytes(path: Path | str) -> int:
    total = 0
    for entry in Path(path).rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def enforce_disk_budget(path: Path | str, budget: ResourceBudget) -> int:
    size = directory_size_bytes(path)
    if size > budget.disk_mb * 1024 * 1024:
        raise EvolutionError(
            EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED,
            f"generated artifact exceeds the {budget.disk_mb} MiB disk budget",
            details={"bytes": size, "budget_mb": budget.disk_mb},
        )
    return size


def enforce_output_budget(output: str, budget: ResourceBudget) -> str:
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) > budget.max_output_bytes:
        raise EvolutionError(
            EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED,
            f"generated process wrote more than {budget.max_output_bytes} bytes",
            details={"bytes": len(encoded)},
        )
    return output


# -------------------------------------------------------------- recursion


@dataclass(slots=True)
class EvolutionDepth:
    """Self-extension depth carried through gap -> pipeline -> nested gap."""

    depth: int = 0
    origin_gap_id: str | None = None
    chain: list[str] = field(default_factory=list)

    def child(self, gap_id: str) -> EvolutionDepth:
        return EvolutionDepth(
            depth=self.depth + 1,
            origin_gap_id=self.origin_gap_id or gap_id,
            chain=[*self.chain, gap_id],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"depth": self.depth, "origin_gap_id": self.origin_gap_id, "chain": list(self.chain)}


def require_within_depth(depth: int, budget: ResourceBudget | None = None) -> int:
    """Structural stop for agent -> agent -> agent capability-creation loops."""
    limit = (budget or ResourceBudget()).max_depth
    if not isinstance(depth, int) or depth < 0:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "evolution depth must be a non-negative integer"
        )
    if depth > limit:
        raise EvolutionError(
            EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED,
            f"self-extension depth {depth} exceeds the configured limit of {limit}; "
            "refusing to let an evolution trigger further evolutions",
            details={"depth": depth, "limit": limit},
        )
    return depth


def python_isolation_flags() -> list[str]:
    """``-I -S``: no PYTHON* env, no user site, no site-packages at all."""
    return [sys.executable, "-I", "-S"]


__all__ = [
    "DEFAULT_CPU_SECONDS",
    "DEFAULT_DISK_MB",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MEMORY_MB",
    "DEFAULT_RETRY_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "ENV_ALLOWLIST",
    "MAX_EVOLUTION_DEPTH",
    "PERMISSION_CALLS",
    "PERMISSION_CONSTRUCTS",
    "SECRET_ENV_EXEMPT",
    "SECRET_ENV_PREFIXES",
    "SECRET_ENV_SUFFIXES",
    "EvolutionDepth",
    "ResourceBudget",
    "assert_no_secrets",
    "build_isolated_env",
    "directory_size_bytes",
    "enforce_disk_budget",
    "enforce_output_budget",
    "permission_findings",
    "python_isolation_flags",
    "require_scoped_access",
    "require_within_depth",
    "secret_names",
]
