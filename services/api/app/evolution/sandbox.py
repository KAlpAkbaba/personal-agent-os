"""Evolution sandbox policy (EVOLUTION_ENGINE_SPEC §13, ADR-0025 boundary 2).

The generator and every generated artifact are confined to a configured
sandbox root. The policy refuses, at construction time, any root that would put
generated code inside a protected tree:

- ``services/recovery-supervisor`` — the recovery root must stay independently
  runnable and outside the blast radius (constitution §6);
- the API's own source (``services/api/app``) and the migrations directory — the
  engine must never rewrite the running product in place (self-development
  rule);
- the repository root itself, and any filesystem root.

``ensure_within`` is the runtime check for every path the pipeline touches.
This module owns no I/O beyond ``resolve``/``mkdir``.
"""

from __future__ import annotations

from pathlib import Path

from app.evolution.errors import EvolutionError, EvolutionErrorClass

REPO_ROOT = Path(__file__).resolve().parents[4]

# Trees a sandbox root may never overlap (either direction).
PROTECTED_TREES: tuple[Path, ...] = (
    REPO_ROOT / "services" / "recovery-supervisor",
    REPO_ROOT / "services" / "api" / "app",
    REPO_ROOT / "services" / "api" / "alembic",
    REPO_ROOT / "services" / "api" / "tests",
    REPO_ROOT / "config",
    REPO_ROOT / "state",
    REPO_ROOT / "docs",
)


def protected_trees() -> tuple[Path, ...]:
    return tuple(p.resolve() for p in PROTECTED_TREES)


class SandboxPolicy:
    """A validated, disposable-friendly workspace root for generated code."""

    def __init__(self, root: Path | str) -> None:
        resolved = Path(root).resolve()
        if resolved == resolved.anchor or resolved.parent == resolved:
            raise EvolutionError(
                EvolutionErrorClass.SANDBOX_VIOLATION,
                "the evolution sandbox root may not be a filesystem root",
                details={"root": str(resolved)},
            )
        if resolved == REPO_ROOT:
            raise EvolutionError(
                EvolutionErrorClass.SANDBOX_VIOLATION,
                "the evolution sandbox root may not be the repository root",
                details={"root": str(resolved)},
            )
        for protected in protected_trees():
            if resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
                raise EvolutionError(
                    EvolutionErrorClass.SANDBOX_VIOLATION,
                    "the evolution sandbox root overlaps a protected core/recovery tree",
                    details={"root": str(resolved), "protected": str(protected)},
                )
        self.root = resolved

    def ensure_within(self, path: Path | str, *, label: str = "path") -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise EvolutionError(
                EvolutionErrorClass.SANDBOX_VIOLATION,
                f"{label} escapes the evolution sandbox root",
                details={"root": str(self.root)},
            )
        return resolved

    def prepare(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


__all__ = ["PROTECTED_TREES", "REPO_ROOT", "SandboxPolicy", "protected_trees"]
