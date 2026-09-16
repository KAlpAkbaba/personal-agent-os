"""The Cloud Core's half of ``packages/protocol/operator-allowlists.json`` (B30 req 117-122).

Two allowlists used to live twice. ``app.operator.plans.APP_ALLOWLIST`` said six
applications; the device's ``DefaultApplications()`` said seven. The Cloud Core's shell
vocabulary knew two commands; the device's ``TerminalRunner.DefaultAllowlist`` admitted
eight patterns. Neither half read the other, which is the shape ``docs/DECISIONS.md`` calls
*contract halves that must read each other*: both suites green, the product refusing a
name the owner could say.

This module reads the file at import and exposes what the router, the plans and the tools
need. The device keeps its compiled tables (it has no repository at runtime) and a C# test
holds them equal to the same file - the ``file-search-roots.json`` discipline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_CONTRACT: Final[Path] = (
    Path(__file__).resolve().parents[4] / "packages" / "protocol" / "operator-allowlists.json"
)


@dataclass(frozen=True, slots=True)
class Application:
    id: str
    name_tr: str
    image: str
    aliases: tuple[str, ...]


def _load() -> dict[str, Any]:
    payload = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("applications"):
        raise ValueError(f"{_CONTRACT} declares no applications")
    return payload


_RAW: Final[dict[str, Any]] = _load()

APPLICATIONS: Final[tuple[Application, ...]] = tuple(
    Application(
        id=str(entry["id"]),
        name_tr=str(entry["name_tr"]),
        image=str(entry["image"]).lower(),
        aliases=tuple(str(a).lower() for a in entry.get("aliases", ())),
    )
    for entry in _RAW["applications"]
)
#: The ids ``app.launch`` accepts, in the contract's order.
APP_IDS: Final[tuple[str, ...]] = tuple(app.id for app in APPLICATIONS)
#: Turkish name per id, for the sentence the owner hears.
APP_NAMES_TR: Final[dict[str, str]] = {app.id: app.name_tr for app in APPLICATIONS}
#: Process image per id, lower-case.
APP_IMAGES: Final[dict[str, str]] = {app.id: app.image for app in APPLICATIONS}
#: (alias phrase, id), longest phrase first so "google chrome" is not shadowed by "chrome".
APP_ALIAS_PHRASES: Final[tuple[tuple[str, str], ...]] = tuple(
    sorted(
        ((alias, app.id) for app in APPLICATIONS for alias in app.aliases),
        key=lambda pair: -len(pair[0]),
    )
)

#: The device's terminal patterns, verbatim, and the commands the Cloud Core actually
#: sends by shell-query kind. ``tests/unit/test_operator_allowlists.py`` asserts every
#: command matches a pattern.
TERMINAL_PATTERNS: Final[tuple[str, ...]] = tuple(_RAW["terminal"]["patterns"])
SHELL_COMMANDS: Final[dict[str, str]] = dict(_RAW["terminal"]["cloud_commands"])

#: Images ``process.stop`` may be asked for (req 120): never a system process.
STOPPABLE_IMAGES: Final[frozenset[str]] = frozenset(
    str(i).lower() for i in _RAW["processes"]["stoppable_images"]
)
#: Services ``service.restart`` may be asked for (req 122).
RESTARTABLE_SERVICES: Final[frozenset[str]] = frozenset(
    str(s).lower() for s in _RAW["services"]["restartable"]
)


def image_stoppable(image: str) -> bool:
    bare = (image or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return bare in STOPPABLE_IMAGES


def service_restartable(name: str) -> bool:
    return (name or "").strip().lower() in RESTARTABLE_SERVICES


def terminal_pattern_matches(pattern: str, command: str) -> bool:
    """The companion's ``TerminalRunner.Matches`` rule, restated once: token by token,
    ``*`` matches one token, ``<path>`` / ``<project-entry>`` match one token, and the
    command may carry no composition characters."""
    if any(ch in command for ch in ";|&$(){}<>`\r\n\0"):
        return False
    wanted = pattern.split()
    got = command.split()
    if len(wanted) != len(got):
        return False
    for w, g in zip(wanted, got, strict=True):
        if w in ("*", "<path>", "<project-entry>"):
            continue
        if w.lower() != g.lower():
            return False
    return True


def command_allowed(command: str) -> bool:
    return any(terminal_pattern_matches(p, command) for p in TERMINAL_PATTERNS)


__all__ = [
    "APPLICATIONS",
    "APP_ALIAS_PHRASES",
    "APP_IDS",
    "APP_IMAGES",
    "APP_NAMES_TR",
    "RESTARTABLE_SERVICES",
    "SHELL_COMMANDS",
    "STOPPABLE_IMAGES",
    "TERMINAL_PATTERNS",
    "Application",
    "command_allowed",
    "image_stoppable",
    "service_restartable",
    "terminal_pattern_matches",
]
