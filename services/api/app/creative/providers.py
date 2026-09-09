"""Creative tool providers (docs/M27_CREATIVE_TOOLS_SPEC.md §1, §4, ADR-0093 decisions
1-3): each provider advertises the CAPABILITY SUBSET it really supports, detected
without ever launching an application binary.

CLAUDE.md carries a hard, repeat-paid-for rule against exactly this class of mistake
(the ``chrome --version`` incident: running a browser binary for detection opens a real
window under the window monitor). This module never executes ``mspaint.exe``,
``Photoshop.exe`` or ``Illustrator.exe`` for any reason — detection is filesystem
existence checks and Windows registry reads ONLY (``winreg``, guarded for import on a
non-Windows test host), exactly the same posture ``docs/evidence/
m27-m28-tool-detection-2026-09-08.json`` was measured with.

An application that is not installed answers ``dependency_unavailable`` carrying the
detection facts (ADR-0093 decision 3) — never imitated, never silently substituted for
the one the owner actually asked for. Paint is the one REAL, Pillow-backed provider
today (decision 1: the document model — the bitmap itself — is Paint's own structured
interface, so a Paint edit never touches the ``mspaint.exe`` process at all).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.creative.spec import (
    TOOL_FIGMA,
    TOOL_ILLUSTRATOR,
    TOOL_PAINT,
    TOOL_PHOTOSHOP,
)

#: The full capability vocabulary (docs/M27_CREATIVE_TOOLS_SPEC.md §4) — every provider
#: advertises a SUBSET of exactly this tuple, never a capability outside it.
ALL_CAPABILITIES: tuple[str, ...] = (
    "open",
    "new",
    "inspect",
    "draw",
    "add_text",
    "shape",
    "transform",
    "color_adjust",
    "crop",
    "background_remove",
    "layer",
    "export",
)

#: Paint has no layer model at all (ADR-0093 decision 2: "Paint today: everything
#: except layer") — its document IS the flat bitmap.
PAINT_CAPABILITIES: tuple[str, ...] = tuple(c for c in ALL_CAPABILITIES if c != "layer")
#: Adobe/Figma providers, once real, offer the full vocabulary including layers — the
#: SUBSET they advertise once genuinely detected and licensed; until then they answer
#: ``dependency_unavailable`` and advertise nothing (module docstring).
FULL_CAPABILITIES: tuple[str, ...] = ALL_CAPABILITIES

ERROR_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"


@dataclass(frozen=True, slots=True)
class DetectionFacts:
    """What was actually checked, and what was found — spoken to the owner verbatim
    rather than a bare boolean (ADR-0093 decision 3: "an application that is not
    installed is named, never imitated")."""

    installed: bool
    checked: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"installed": self.installed, "checked": list(self.checked), "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ProviderResult:
    ok: bool
    capabilities: tuple[str, ...] = ()
    error_class: str | None = None
    detail: dict[str, object] = field(default_factory=dict)


class CreativeProvider(Protocol):
    """One creative tool's own detection + advertised capability subset. Nothing here
    executes an operation — that is :mod:`app.creative.execute` for Paint and a fixed,
    pinned driver file (never generated from prose, ADR-0093 decision 6) for the
    others, once they are real."""

    tool: str

    def detect(self) -> DetectionFacts: ...

    def capabilities(self) -> ProviderResult:
        """``ok=True`` with the advertised subset when installed; ``ok=False`` with
        ``error_class="dependency_unavailable"`` and the detection facts otherwise."""
        ...


# --------------------------------------------------------------------------- Paint


def _mspaint_candidates() -> tuple[str, ...]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return (
        str(Path(system_root) / "System32" / "mspaint.exe"),
        str(Path(system_root) / "mspaint.exe"),
    )


def detect_mspaint() -> DetectionFacts:
    """Filesystem existence ONLY — ``mspaint.exe`` is never executed (module
    docstring). Matches ``docs/evidence/m27-m28-tool-detection-2026-09-08.json``'s own
    "classic mspaint.exe present" finding."""
    candidates = _mspaint_candidates()
    for candidate in candidates:
        if Path(candidate).is_file():
            return DetectionFacts(installed=True, checked=candidates, detail=candidate)
    return DetectionFacts(installed=False, checked=candidates, detail="mspaint.exe not found")


@dataclass(slots=True)
class PaintProvider:
    """The REAL provider (ADR-0093 decision 1): a Paint edit is performed on the FILE
    with Pillow (:mod:`app.creative.execute`) — the document model IS the bitmap, so
    this provider's own ``capabilities()`` never depends on ``mspaint.exe`` being
    launchable, only on it being PRESENT (so the owner can be shown the result in the
    real application, through the M19 operator, once the device side wires it)."""

    tool: str = TOOL_PAINT

    def detect(self) -> DetectionFacts:
        return detect_mspaint()

    def capabilities(self) -> ProviderResult:
        facts = self.detect()
        if not facts.installed:
            return ProviderResult(
                ok=False, error_class=ERROR_DEPENDENCY_UNAVAILABLE, detail=facts.as_dict()
            )
        return ProviderResult(ok=True, capabilities=PAINT_CAPABILITIES, detail=facts.as_dict())


# ------------------------------------------------------------------ Adobe (Photoshop/Illustrator)


def _program_files_candidates(product_dir_prefix: str, exe_name: str) -> tuple[str, ...]:
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files")]
    out: list[str] = []
    for root in roots:
        base = Path(root) / "Adobe"
        if base.is_dir():
            try:
                for child in base.iterdir():
                    if child.name.lower().startswith(product_dir_prefix.lower()):
                        out.append(str(child / exe_name))
            except OSError:
                pass
        else:
            # The directory itself does not exist — still name the path that WOULD
            # have been checked, so the detection facts are honest about what was
            # looked for (module docstring: "never imitated").
            out.append(str(base / f"{product_dir_prefix}*" / exe_name))
    return tuple(out)


def _registry_key_present(hive_key: str, subkey: str) -> bool:
    """A single ``HKEY_LOCAL_MACHINE`` (or ``HKEY_CURRENT_USER``) key read — never a
    process launch. Returns ``False`` (never raises) on a non-Windows host or any
    access failure, so this module imports and runs identically in the unit suite."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover - defensive only; win32 always has winreg
        return False
    hive = winreg.HKEY_LOCAL_MACHINE if hive_key == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, subkey):
            return True
    except OSError:
        return False


@dataclass(slots=True)
class PhotoshopProvider:
    """Ships complete — detection, capability list, a fixed driver reading a JSON
    plan (once ``drivers/manifest.json`` pins one) — and answers
    ``dependency_unavailable`` with the detection facts until the owner installs and
    licenses it (ADR-0093 decision 3). Never a licence bypass, never a login
    automated."""

    tool: str = TOOL_PHOTOSHOP
    _registry_subkey: str = r"SOFTWARE\Adobe\Photoshop"
    _product_dir_prefix: str = "Adobe Photoshop"
    _exe_name: str = "Photoshop.exe"

    def detect(self) -> DetectionFacts:
        # The registry key alone is NOT sufficient (measured on the owner's own
        # machine, 2026-09-08): Creative Cloud writes a bare
        # "SOFTWARE\Adobe\Photoshop" settings key even when Photoshop itself is not
        # installed, so trusting its mere presence would report "installed" for a
        # machine that only has the Creative Cloud launcher — exactly the
        # "imitated, not named" mistake ADR-0093 decision 3 forbids. The registry
        # read still runs and is carried in the facts (spoken to the owner, never
        # hidden), but only the real executable on disk decides ``installed``.
        registered = _registry_key_present("HKLM", self._registry_subkey)
        candidates = _program_files_candidates(self._product_dir_prefix, self._exe_name)
        found = next((c for c in candidates if Path(c).is_file()), None)
        checked = (f"HKLM\\{self._registry_subkey}", *candidates)
        detail = found or (
            "registry key present but no Photoshop.exe found" if registered else "not installed"
        )
        return DetectionFacts(installed=found is not None, checked=checked, detail=detail)

    def capabilities(self) -> ProviderResult:
        facts = self.detect()
        if not facts.installed:
            return ProviderResult(
                ok=False, error_class=ERROR_DEPENDENCY_UNAVAILABLE, detail=facts.as_dict()
            )
        return ProviderResult(ok=True, capabilities=FULL_CAPABILITIES, detail=facts.as_dict())


@dataclass(slots=True)
class IllustratorProvider:
    """The same shape as :class:`PhotoshopProvider`, for Illustrator (ADR-0093
    decision 3)."""

    tool: str = TOOL_ILLUSTRATOR
    _registry_subkey: str = r"SOFTWARE\Adobe\Illustrator"
    _product_dir_prefix: str = "Adobe Illustrator"
    _exe_name: str = "Illustrator.exe"

    def detect(self) -> DetectionFacts:
        # The same "the registry alone is not sufficient" rule ``PhotoshopProvider.
        # detect`` documents — only the real executable on disk decides ``installed``.
        registered = _registry_key_present("HKLM", self._registry_subkey)
        candidates = _program_files_candidates(self._product_dir_prefix, self._exe_name)
        found = next((c for c in candidates if Path(c).is_file()), None)
        checked = (f"HKLM\\{self._registry_subkey}", *candidates)
        detail = found or (
            "registry key present but no Illustrator.exe found" if registered else "not installed"
        )
        return DetectionFacts(installed=found is not None, checked=checked, detail=detail)

    def capabilities(self) -> ProviderResult:
        facts = self.detect()
        if not facts.installed:
            return ProviderResult(
                ok=False, error_class=ERROR_DEPENDENCY_UNAVAILABLE, detail=facts.as_dict()
            )
        return ProviderResult(ok=True, capabilities=FULL_CAPABILITIES, detail=facts.as_dict())


# --------------------------------------------------------------------------- Figma


@dataclass(slots=True)
class FigmaProvider:
    """Over Figma's REST API with a DPAPI-stored owner token (spec §1) — this Cloud
    Core half never reads or types the token itself; it is handed a plain
    ``token_present`` fact by whatever layer DOES hold the device-local DPAPI store
    (a named gap: wiring that store is windows-engineer-track work, not yet done —
    stated plainly rather than imitated, ADR-0093 decision 3's own rule applied to a
    missing credential exactly as it applies to a missing application). Without a
    token or an authorised file this answers ``dependency_unavailable``, never a
    credential bypass."""

    tool: str = TOOL_FIGMA
    token_present: bool = False

    def detect(self) -> DetectionFacts:
        if self.token_present:
            return DetectionFacts(installed=True, checked=("figma_token",), detail="token present")
        return DetectionFacts(
            installed=False, checked=("figma_token",), detail="no owner Figma token configured"
        )

    def capabilities(self) -> ProviderResult:
        facts = self.detect()
        if not facts.installed:
            return ProviderResult(
                ok=False, error_class=ERROR_DEPENDENCY_UNAVAILABLE, detail=facts.as_dict()
            )
        return ProviderResult(ok=True, capabilities=FULL_CAPABILITIES, detail=facts.as_dict())


# --------------------------------------------------------------------------- registry


def default_providers() -> dict[str, CreativeProvider]:
    """One instance per tool, keyed by :data:`app.creative.spec.TOOLS` — the SAME shape
    ``app.creative3d``'s own service holds its driver/manifest table in, never a second
    ad-hoc registry."""
    return {
        TOOL_PAINT: PaintProvider(),
        TOOL_PHOTOSHOP: PhotoshopProvider(),
        TOOL_ILLUSTRATOR: IllustratorProvider(),
        TOOL_FIGMA: FigmaProvider(),
    }


def installed_capability_union(providers: dict[str, CreativeProvider]) -> frozenset[str]:
    """ADR-0093 decision 2: "the router is offered only the union of the INSTALLED
    providers' subsets" — the Cockpit/voice capability surface, never a provider's own
    private detail."""
    union: set[str] = set()
    for provider in providers.values():
        result = provider.capabilities()
        if result.ok:
            union.update(result.capabilities)
    return frozenset(union)


__all__ = [
    "ALL_CAPABILITIES",
    "ERROR_DEPENDENCY_UNAVAILABLE",
    "FULL_CAPABILITIES",
    "PAINT_CAPABILITIES",
    "CreativeProvider",
    "DetectionFacts",
    "FigmaProvider",
    "IllustratorProvider",
    "PaintProvider",
    "PhotoshopProvider",
    "ProviderResult",
    "default_providers",
    "detect_mspaint",
    "installed_capability_union",
]
