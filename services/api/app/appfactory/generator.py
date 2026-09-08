"""``AppGenerator``: ``AppSpec`` -> ``ProjectFiles`` (docs/M23_APP_FACTORY_SPEC.md §2,
ADR-0086 decision 1).

Two backends implement the same seam, mirroring ``app.selfhealing.backends.CodingBackend``
and ``app.evolution.skills``'s ``SkillGenerator``:

- ``DeterministicAppGenerator`` — renders one of the built-in templates
  (``app/appfactory/templates/<template>/``) by substituting a FIXED set of ``{{SLOT}}``
  markers with the spec's own values. Fully offline, no model; this is what the
  acceptance gate runs and what every corpus/unit test uses.
- ``ClaudeAppGenerator`` — the Claude Agent SDK/CLI seam, for filling a template's marked
  slots with model-authored content beyond what the deterministic backend renders
  (spec §2). INERT without configuration: raises a typed error before any I/O, never
  called in tests — the same discipline ``ClaudeCodingBackend``/``ClaudeSkillGenerator``
  already establish, and it may NEVER write a file outside the template's own file list.

SECURITY: every spec value that reaches generated *source* (JS/HTML/JSON, never a
comment) is HTML/JSON-escaped or re-validated against the same identifier shape
``AppSpec.Command`` already checked at parse time — the same "validate at the splice
point too" discipline ``app.evolution.skills``'s module docstring documents (ADR-0024/
0025), because a spec is assistant-authored from the owner's words and nothing here
trusts that pipeline blindly a second time.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.appfactory.spec import (
    TEMPLATE_CLI_TOOL,
    TEMPLATE_STATIC_PAGE,
    TEMPLATE_TASK_TRACKER,
    AppSpec,
)

TEMPLATES_ROOT = Path(__file__).resolve().parent / "templates"

_COMMAND_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


class AppGeneratorError(RuntimeError):
    """A generator could not produce ``ProjectFiles`` for a spec."""


@dataclass(frozen=True, slots=True)
class ProjectFile:
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class ProjectFiles:
    files: tuple[ProjectFile, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.files)

    def path_set(self) -> frozenset[str]:
        return frozenset(f.path for f in self.files)

    def get(self, path: str) -> str | None:
        for f in self.files:
            if f.path == path:
                return f.text
        return None

    def manifest(self) -> dict:
        raw = self.get("manifest.json")
        if raw is None:
            raise AppGeneratorError("generated project carries no manifest.json")
        return json.loads(raw)


@runtime_checkable
class AppGenerator(Protocol):
    name: str

    def generate(self, spec: AppSpec) -> ProjectFiles: ...


def _require_command_name(value: str) -> str:
    """Re-validated at the splice point (module docstring) — ``AppSpec.Command``
    already checked this shape at parse time; this is defence in depth before the
    value is written into generated JS via ``json.dumps``."""
    if not _COMMAND_NAME_RE.match(value):
        raise AppGeneratorError(f"command name {value!r} is not a valid identifier")
    return value


def _read_template_files(template: str) -> list[tuple[str, str]]:
    root = TEMPLATES_ROOT / template
    if not root.is_dir():
        raise AppGeneratorError(f"no built-in template directory for {template!r}")
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        out.append((rel, path.read_text(encoding="utf-8")))
    if not out:
        raise AppGeneratorError(f"template {template!r} has no files")
    return out


#: Files where a slot value becomes CODE. A value spliced here must be a closed-alphabet
#: token or a JSON literal the generator itself produced — never free text, not even
#: escaped free text, and never inside a comment (the M23 security review's finding: a
#: newline in an HTML-escaped title ended a ``//`` comment and the rest of the value ran
#: as top-level JavaScript in the owner's browser). Enforced at the splice point, in
#: code, on every render — the templates are data, this rule is not.
_CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".json", ".ts")
_CODE_SAFE_RE = re.compile(r"[A-Za-z0-9_-]{1,120}")
_LINE_TERMINATORS = ("\n", "\r", "\u2028", "\u2029")


class _JsonLiteral(str):
    """A slot value the generator produced with ``json.dumps`` — the only kind of free
    text allowed into a code file."""


def _code_safe(key: str, value: str) -> None:
    if isinstance(value, _JsonLiteral):
        if any(t in value for t in _LINE_TERMINATORS):
            raise AppGeneratorError(f"slot {key} carries a line terminator into code")
        return
    if _CODE_SAFE_RE.fullmatch(value) is None:
        raise AppGeneratorError(f"slot {key} is free text and may not be spliced into a code file")


def _substitute(text: str, slots: dict[str, str], *, path: str = "") -> str:
    out = text
    is_code = path.lower().endswith(_CODE_SUFFIXES)
    for key, value in slots.items():
        marker = "{{" + key + "}}"
        if marker not in out:
            continue
        if is_code:
            _code_safe(key, value)
        out = out.replace(marker, value)
    if "{{" in out and re.search(r"\{\{[A-Z_]+\}\}", out):
        raise AppGeneratorError(f"{path or 'template'} still carries an unfilled slot")
    return out


class DeterministicAppGenerator:
    """Renders a built-in template with the spec's own values. No model, no network,
    deterministic: the same spec always produces byte-identical files."""

    name = "deterministic"

    def generate(self, spec: AppSpec) -> ProjectFiles:
        if spec.template == TEMPLATE_TASK_TRACKER:
            slots = self._task_tracker_slots(spec)
        elif spec.template == TEMPLATE_STATIC_PAGE:
            slots = self._static_page_slots(spec)
        elif spec.template == TEMPLATE_CLI_TOOL:
            slots = self._cli_tool_slots(spec)
        else:  # pragma: no cover - AppSpec's Literal already excludes this
            raise AppGeneratorError(f"unknown template {spec.template!r}")
        rendered = [
            ProjectFile(path=rel, text=_substitute(text, slots, path=rel))
            for rel, text in _read_template_files(spec.template)
        ]
        return ProjectFiles(files=tuple(rendered))

    def _task_tracker_slots(self, spec: AppSpec) -> dict[str, str]:
        title = html.escape(spec.name)
        return {
            "APP_TITLE": title,
            "STORAGE_KEY": f"pagentos-tasks-{spec.slug()}",
            "PORT": "8765",
        }

    def _static_page_slots(self, spec: AppSpec) -> dict[str, str]:
        title = html.escape(spec.page_title or spec.name)
        heading = html.escape(spec.page_heading or spec.name)
        body = html.escape(spec.page_body or "")
        return {
            "PAGE_TITLE": title,
            "PAGE_HEADING": heading,
            "PAGE_BODY": body,
            "PORT": "8766",
        }

    def _cli_tool_slots(self, spec: AppSpec) -> dict[str, str]:
        commands = [_require_command_name(c.name) for c in (spec.commands or [])]
        return {
            "APP_TITLE": html.escape(spec.name),
            # json.dumps on a list of shape-checked identifiers: no string can ever
            # terminate the array literal or inject code (the same "closed alphabet,
            # then escape" discipline app.evolution.skills's splice points use).
            "COMMANDS_JSON": _JsonLiteral(json.dumps(commands, ensure_ascii=False)),
            "COMMANDS_LIST": ", ".join(commands),
        }


class ClaudeAppGenerator:
    """The Claude Agent SDK/CLI seam. INERT without configuration (module docstring):
    every method raises before any I/O. Never called in tests."""

    name = "claude"

    def __init__(self, cli_path: str = "") -> None:
        self.cli_path = cli_path

    def generate(self, spec: AppSpec) -> ProjectFiles:
        if not self.cli_path:
            raise AppGeneratorError(
                "Claude app generator is not configured "
                "(set PAGENTOS_APPFACTORY_CLAUDE_CLI to the Claude CLI path)"
            )
        raise AppGeneratorError("Claude app generation flow is not enabled in this build")


__all__ = [
    "AppGenerator",
    "AppGeneratorError",
    "ClaudeAppGenerator",
    "DeterministicAppGenerator",
    "ProjectFile",
    "ProjectFiles",
    "TEMPLATES_ROOT",
]
