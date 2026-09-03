"""DeviceCapabilities matching: family marker or per-operation name.

BROWSER_CAPABILITIES.md §1/§8: a device advertises a *family marker*
(``browser.chrome`` — present when the device has a configured Browser
Worker at all) plus per-operation capability names (``browser.fetch_evidence``,
``browser.search``, ...). Selection may be asked for either shape: an exact
operation name, or (for a coarser check) the family marker itself. A device
satisfies a requested capability when its advertised list contains that exact
name, OR contains the family marker for the requested name's namespace.

Nothing here is browser-specific in principle: ``_FAMILY_MARKERS`` maps a
capability namespace to its family marker, so a future ``desktop.*`` or
``fs.*`` family marker plugs in without touching the matching logic.
"""

from __future__ import annotations

from collections.abc import Iterable

#: namespace (the part before the first '.') -> the family marker capability
#: name that, alone, implies every per-operation capability in that namespace.
_FAMILY_MARKERS: dict[str, str] = {
    "browser": "browser.chrome",
}


def family_marker(namespace: str) -> str | None:
    return _FAMILY_MARKERS.get(namespace)


def has_capability(capabilities: Iterable[str], required: str) -> bool:
    """True when ``capabilities`` satisfies ``required`` (exact or family)."""
    caps = set(capabilities)
    if required in caps:
        return True
    namespace = required.split(".", 1)[0]
    marker = _FAMILY_MARKERS.get(namespace)
    return marker is not None and marker in caps


def missing_capabilities(capabilities: Iterable[str], required: Iterable[str]) -> list[str]:
    caps = set(capabilities)
    return [r for r in required if not has_capability(caps, r)]


__all__ = ["family_marker", "has_capability", "missing_capabilities"]
