"""Backend capability declarations and the orchestrator-side guard.

Every :class:`~browser_agent.backends.BrowserBackend` declares a
:class:`BrowserCapabilities` record with exactly these flags (ADR-0019 /
ACCEPTANCE_TESTS M2 architecture gates):

- ``authenticated_session`` — the backend carries the owner's (or a
  persistent profile's) logged-in state: cookies, local storage, sessions.
- ``downloads`` — file downloads can be captured and saved.
- ``uploads`` — file inputs can be populated from local files.
- ``extensions`` — the browser runs the owner's installed extensions.
- ``existing_tabs`` — tabs that existed before attach are visible/usable.
- ``multiple_windows`` — more than one tab/window can be driven.
- ``visual_fallback`` — the backend is a vision/coordinate fallback adapter
  (always False for the semantic backends; reserved for the future
  ``VisualFallbackBackend``).

The orchestrator queries these *before* dispatching work; the guard
:func:`require_capability` turns a missing flag into the project-taxonomy
``capability_missing`` typed error instead of a mid-task failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING

from .errors import BrowserError, ErrorClass

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .backends import BrowserBackend

#: The one and only legal flag set (order = documentation order).
CAPABILITY_FLAGS: tuple[str, ...] = (
    "authenticated_session",
    "downloads",
    "uploads",
    "extensions",
    "existing_tabs",
    "multiple_windows",
    "visual_fallback",
)


@dataclass(frozen=True, slots=True)
class BrowserCapabilities:
    """Immutable capability declaration of one backend instance."""

    authenticated_session: bool
    downloads: bool
    uploads: bool
    extensions: bool
    existing_tabs: bool
    multiple_windows: bool
    visual_fallback: bool

    def as_dict(self) -> dict[str, bool]:
        """Flag -> bool mapping (stable order, all flags present)."""
        return {name: getattr(self, name) for name in CAPABILITY_FLAGS}

    def with_overrides(self, overrides: Mapping[str, bool]) -> BrowserCapabilities:
        """Apply enrollment-granted overrides; unknown flags are typed errors."""
        unknown = set(overrides) - set(CAPABILITY_FLAGS)
        if unknown:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "unknown capability override(s): "
                f"{', '.join(sorted(str(u) for u in unknown))}",
                retryable=False,
                evidence={"known_flags": list(CAPABILITY_FLAGS)},
            )
        bad_types = {k for k, v in overrides.items() if not isinstance(v, bool)}
        if bad_types:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"capability overrides must be booleans: {', '.join(sorted(bad_types))}",
                retryable=False,
            )
        if not overrides:
            return self
        return replace(self, **dict(overrides))


# Defensive consistency check: the dataclass fields ARE the canonical flag set.
assert tuple(f.name for f in fields(BrowserCapabilities)) == CAPABILITY_FLAGS


def require_capability(backend: BrowserBackend, flag: str) -> None:
    """Orchestrator-style guard: raise ``capability_missing`` if not granted.

    ``flag`` must be one of :data:`CAPABILITY_FLAGS`; anything else is a
    ``validation_error`` (the caller asked about a capability that does not
    exist in the model, which is a programming error, not a backend gap).
    """
    if flag not in CAPABILITY_FLAGS:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"unknown capability flag {flag!r}; known flags: {', '.join(CAPABILITY_FLAGS)}",
            retryable=False,
        )
    capabilities = backend.capabilities()
    if not getattr(capabilities, flag):
        raise BrowserError(
            ErrorClass.CAPABILITY_MISSING,
            f"backend {type(backend).__name__} does not grant capability {flag!r}",
            retryable=False,
            evidence={
                "capability": flag,
                "backend": type(backend).__name__,
                "capabilities": capabilities.as_dict(),
            },
        )
