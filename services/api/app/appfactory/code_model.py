"""The model seam of the App Factory (B40 req 425, 435, 436): a ``CodeModel`` that may
author the free-text behaviour of a composed application as pure functions
(``custom.js`` + ``tests/custom.js``, the plan's model slots - never the server, the
store, the auth or the runner), diagnose a failed test run, and propose a fix as full-file
edits confined to the same slots. Every answer is a forced tool call whose input is
checked for shape; every file it returns goes through the lint, the security scan and
the validator before a byte reaches the device, and its tests are the device's to run.

Two implementations: ``ScriptedCodeModel`` (tests, the corpus - a scripted answer and a
record of what it was asked) and ``AnthropicCodeModel`` (the Messages API, the owner's
key, never echoed). ``ModelAssistedGenerator`` composes the deterministic files first
and adds the model's slots only under the owner's flag; a model that fails, or one that
is not configured, leaves the deterministic application whole - and the receipt says so.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

from app.appfactory.composer import ComposedAppGenerator
from app.appfactory.generator import AppGeneratorError, ProjectFiles
from app.appfactory.planner import MODEL_SLOT_PATHS, ArchitecturePlan, ProjectPlan
from app.appfactory.requirements import Requirements
from app.logging import get_logger

logger = get_logger("app.appfactory.code_model")

API_URL: Final = "https://api.anthropic.com/v1/messages"
API_VERSION: Final = "2023-06-01"
DEFAULT_MODEL: Final = "claude-sonnet-5"
MAX_TOKENS: Final = 6000
MAX_FILE_CHARS: Final = 40_000

SYSTEM: Final = (
    "You write small, dependency-free CommonJS modules for a stdlib-only Node application "
    "that PagentOS generated for its single owner. You may write ONLY the files you are "
    "asked for. No network access, no child processes, no eval, no new dependencies. "
    "Answer only through the tool you are given."
)

GENERATE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "custom_js": {
            "type": "string",
            "description": "custom.js: module.exports = { fn: function ... }; pure functions only",
        },
        "tests_custom_js": {
            "type": "string",
            "description": (
                "tests/custom.js: module.exports = function (check) "
                "{ check('name', function () { ... }); }"
            ),
        },
        "summary": {"type": "string"},
    },
    "required": ["custom_js", "tests_custom_js", "summary"],
}
DIAGNOSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "cause": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["cause", "files", "confidence"],
}
FIX_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
                "required": ["path", "text"],
            },
        },
        "explanation": {"type": "string"},
    },
    "required": ["edits", "explanation"],
}


class CodeModelError(RuntimeError):
    """The model could not answer in the shape asked for; never a partial answer used."""


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    cause: str
    files: tuple[str, ...]
    confidence: str

    def as_dict(self) -> dict[str, Any]:
        return {"cause": self.cause, "files": list(self.files), "confidence": self.confidence}


class CodeModel(Protocol):
    name: str

    def generate_custom(
        self, req: Requirements, arch: ArchitecturePlan, plan: ProjectPlan
    ) -> dict[str, str]: ...

    def diagnose(self, failure: str, files: dict[str, str]) -> FailureDiagnosis: ...

    def fix(
        self, failure: str, diagnosis: FailureDiagnosis, files: dict[str, str]
    ) -> dict[str, str]: ...


def _confine(edits: dict[str, str]) -> dict[str, str]:
    """Only the model slots, only text of a sane size - anything else is refused whole."""
    out: dict[str, str] = {}
    for path, text in edits.items():
        if path not in MODEL_SLOT_PATHS:
            raise CodeModelError(f"the model may not write {path!r}")
        if not isinstance(text, str) or not text.strip():
            raise CodeModelError(f"the model returned no text for {path!r}")
        if len(text) > MAX_FILE_CHARS:
            raise CodeModelError(f"{path!r} is over {MAX_FILE_CHARS} characters")
        out[path] = text if text.endswith("\n") else text + "\n"
    return out


@dataclass(slots=True)
class ScriptedCodeModel:
    """A scripted answer for the tests and the corpus, and a record of every question."""

    name: str = "scripted"
    custom: dict[str, str] = field(default_factory=dict)
    diagnosis: FailureDiagnosis = field(
        default_factory=lambda: FailureDiagnosis("scripted", ("custom.js",), "high")
    )
    fixes: list[dict[str, str]] = field(default_factory=list)
    asked: list[dict[str, Any]] = field(default_factory=list)
    fail_with: str | None = None

    def generate_custom(
        self, req: Requirements, arch: ArchitecturePlan, plan: ProjectPlan
    ) -> dict[str, str]:
        self.asked.append(
            {
                "op": "generate",
                "sentence": req.sentence,
                "slots": [f.path for f in plan.files if f.author == "model"],
            }
        )
        if self.fail_with:
            raise CodeModelError(self.fail_with)
        return _confine(dict(self.custom))

    def diagnose(self, failure: str, files: dict[str, str]) -> FailureDiagnosis:
        self.asked.append({"op": "diagnose", "failure": failure[:200], "files": sorted(files)})
        if self.fail_with:
            raise CodeModelError(self.fail_with)
        return self.diagnosis

    def fix(
        self, failure: str, diagnosis: FailureDiagnosis, files: dict[str, str]
    ) -> dict[str, str]:
        self.asked.append({"op": "fix", "failure": failure[:200], "cause": diagnosis.cause})
        if self.fail_with:
            raise CodeModelError(self.fail_with)
        if not self.fixes:
            raise CodeModelError("no scripted fix left")
        return _confine(dict(self.fixes.pop(0)))


@dataclass(slots=True)
class AnthropicCodeModel:
    """The Messages API through a forced tool call (the shape ``AnthropicPlannerModel``
    and ``AnthropicEngineeringModel`` already use). The key is read once per call and
    never travels in an error."""

    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_s: float = 240.0
    transport: httpx.BaseTransport | None = None
    name: str = "anthropic"

    def _call(
        self, tool: str, description: str, schema: dict[str, Any], content: str
    ) -> dict[str, Any]:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise CodeModelError("no Anthropic key is configured for the App Factory")
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "tools": [{"name": tool, "description": description, "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": tool},
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise CodeModelError(f"Anthropic API unreachable: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise CodeModelError(f"Anthropic API answered {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise CodeModelError("Anthropic API answered with a body that is not JSON") from None
        if data.get("stop_reason") == "max_tokens":
            raise CodeModelError(
                "the answer was cut off at max_tokens; an incomplete file is never used"
            )
        for block in data.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == tool
            ):
                answer = block.get("input")
                if isinstance(answer, dict):
                    return answer
        raise CodeModelError("the model did not answer through the tool")

    def generate_custom(
        self, req: Requirements, arch: ArchitecturePlan, plan: ProjectPlan
    ) -> dict[str, str]:
        content = (
            "The owner asked, in Turkish: " + json.dumps(req.sentence, ensure_ascii=False) + "\n"
            "The application already has these record kinds (schema.js): "
            + json.dumps([e.as_dict() for e in arch.entities], ensure_ascii=False)
            + "\nWrite custom.js: pure functions for whatever behaviour the sentence asks for "
            + "beyond "
            "storing and listing records (totals, filters, formatting), each a named export; and "
            "tests/custom.js: module.exports = function (check) { ... } calling check(name, fn) "
            "with assert from require('assert'). Turkish names in labels, ASCII identifiers."
        )
        answer = self._call(
            "write_custom_module", "custom.js and its tests", GENERATE_SCHEMA, content
        )
        return _confine(
            {
                "custom.js": str(answer.get("custom_js") or ""),
                "tests/custom.js": str(answer.get("tests_custom_js") or ""),
            }
        )

    def diagnose(self, failure: str, files: dict[str, str]) -> FailureDiagnosis:
        content = (
            "Test output:\n"
            + failure[:4000]
            + "\n\nFiles:\n"
            + "\n".join(f"--- {p}\n{t[:6000]}" for p, t in files.items())
        )
        answer = self._call("diagnose_failure", "why the tests failed", DIAGNOSE_SCHEMA, content)
        files_named = tuple(str(p) for p in (answer.get("files") or []) if isinstance(p, str))
        return FailureDiagnosis(
            str(answer.get("cause") or "")[:600],
            files_named,
            str(answer.get("confidence") or "low"),
        )

    def fix(
        self, failure: str, diagnosis: FailureDiagnosis, files: dict[str, str]
    ) -> dict[str, str]:
        content = (
            "Diagnosis: "
            + diagnosis.cause
            + "\nTest output:\n"
            + failure[:4000]
            + "\n\nReturn full new text for the files you change, ONLY among: "
            + ", ".join(MODEL_SLOT_PATHS)
            + "\n\nFiles:\n"
            + "\n".join(f"--- {p}\n{t[:6000]}" for p, t in files.items() if p in MODEL_SLOT_PATHS)
        )
        answer = self._call("propose_fix", "the fixed files", FIX_SCHEMA, content)
        edits = {
            str(e.get("path")): str(e.get("text") or "")
            for e in (answer.get("edits") or [])
            if isinstance(e, dict)
        }
        return _confine(edits)


@dataclass(slots=True)
class ModelAssistedGenerator:
    """The composed files, plus the model's slots when the owner's flag is on and a model
    is configured. ``last_note`` says what happened, for the receipt."""

    composer: ComposedAppGenerator = field(default_factory=ComposedAppGenerator)
    model: CodeModel | None = None
    enabled: Any = False
    last_note: str = ""

    @property
    def model_enabled(self) -> bool:
        flag = self.enabled() if callable(self.enabled) else bool(self.enabled)
        return bool(flag) and self.model is not None

    def generate(
        self, *, app_name: str, req: Requirements, arch: ArchitecturePlan, plan: ProjectPlan
    ) -> ProjectFiles:
        custom: dict[str, str] | None = None
        self.last_note = "deterministic"
        if self.model_enabled and self.model is not None:
            try:
                custom = self.model.generate_custom(req, arch, plan)
                self.last_note = f"model:{self.model.name}"
            except CodeModelError as exc:
                logger.warning("appfactory_model_generation_failed", detail=str(exc)[:200])
                self.last_note = f"deterministic (model refused: {str(exc)[:120]})"
        try:
            return self.composer.generate_from_plan(
                app_name=app_name, arch=arch, plan=plan, custom_files=custom
            )
        except AppGeneratorError:
            raise


__all__ = [
    "DEFAULT_MODEL",
    "AnthropicCodeModel",
    "CodeModel",
    "CodeModelError",
    "FailureDiagnosis",
    "ModelAssistedGenerator",
    "ScriptedCodeModel",
]
