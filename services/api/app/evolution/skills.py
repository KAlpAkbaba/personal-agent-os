"""Generated-skill layout + the SkillGenerator seam (EVOLUTION_ENGINE_SPEC §10/§5).

Layout produced for every generated skill::

    <root>/<skill>/<version>/
      manifest.yaml
      README.md
      src/<skill>.py          # run(payload) -> dict, plus a stdin/stdout main
      tests/test_<skill>.py   # stdlib script: exit 0 pass / 1 fail
      evals/eval_<skill>.py   # stdlib script: prints EVAL-RESULT <json>
      evals/cases.json        # the eval set

Two generators implement the same seam:

- ``DeterministicSkillGenerator`` — the ACCEPTANCE GATE backend. For the
  CONTROLLED missing-capability class (a pure, deterministic single-input
  transform named by an operation from a fixed allowlist) it emits a complete,
  valid skill from a structured request spec. Fully offline, no model.
- ``ClaudeSkillGenerator`` — the Claude Agent SDK seam. INERT without
  configuration: raises a typed ``generator_not_configured`` error before any
  I/O, and is never exercised in tests (same discipline as
  ``ClaudeCodingBackend``).

SECURITY (ADR-0024 Critical lesson, restated as ADR-0025 §3): every value from
the request that reaches generated source is validated to a strict token shape
TWICE — once at the parsing choke point (``SkillSpec.parse``) and again at each
splice point (the ``_render_*`` helpers re-run the same ``require_*`` checks
immediately before interpolation). Case values are additionally restricted to
bounded scalars and emitted only through ``repr()``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.tokens import (
    require_capability_id,
    require_identifier,
    require_literal,
    require_owner_scope,
    require_slug,
    require_slug_list,
    require_summary,
    require_version,
)
from app.logging import get_logger

logger = get_logger("app.evolution.skills")

# The generated test/eval scripts locate their skill through this variable, the
# same pattern as the M6 regression runner's PAGENTOS_HANDLER_DIR.
SKILL_DIR_ENV = "PAGENTOS_SKILL_DIR"

MANIFEST_FILENAME = "manifest.yaml"
README_FILENAME = "README.md"
SRC_DIRNAME = "src"
TESTS_DIRNAME = "tests"
EVALS_DIRNAME = "evals"
CASES_FILENAME = "cases.json"

# Health metrics every generated skill declares and its eval set must emit.
GENERATED_HEALTH_METRICS = ("success_rate", "p95_latency_ms", "case_count")

MAX_CASES = 64


# --------------------------------------------------------------------- layout


@dataclass(slots=True)
class SkillLayout:
    """Filesystem view of one generated skill version (§10)."""

    root: Path
    skill_name: str
    capability_id: str
    version: str

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def readme_path(self) -> Path:
        return self.root / README_FILENAME

    @property
    def src_dir(self) -> Path:
        return self.root / SRC_DIRNAME

    @property
    def tests_dir(self) -> Path:
        return self.root / TESTS_DIRNAME

    @property
    def evals_dir(self) -> Path:
        return self.root / EVALS_DIRNAME

    @property
    def module_path(self) -> Path:
        return self.src_dir / f"{self.skill_name}.py"

    @property
    def test_path(self) -> Path:
        return self.tests_dir / f"test_{self.skill_name}.py"

    @property
    def eval_path(self) -> Path:
        return self.evals_dir / f"eval_{self.skill_name}.py"

    @property
    def cases_path(self) -> Path:
        return self.evals_dir / CASES_FILENAME

    def required_paths(self) -> list[Path]:
        return [
            self.manifest_path,
            self.readme_path,
            self.module_path,
            self.test_path,
            self.eval_path,
            self.cases_path,
        ]

    def missing_paths(self) -> list[str]:
        return [p.name for p in self.required_paths() if not p.is_file()]


# ------------------------------------------------------- minimal YAML subset
# Deliberately NOT PyYAML: the API declares no YAML dependency (it is only
# present transitively via uvicorn[standard]) and the manifest content here is
# a closed set of strict tokens and flat string lists, so a ~40-line
# deterministic emitter/parser is both sufficient and auditable. See the M7
# deviations note in the delivery report.

_YAML_KEY_RE = re.compile(r"^([a-z_][a-z0-9_]{0,63}):(?:[ ]+(.*))?$")
_YAML_ITEM_RE = re.compile(r"^[ ]{2}-[ ]+(.+)$")


def dump_manifest_yaml(manifest: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in sorted(manifest):
        value = manifest[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def load_manifest_yaml(text: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    current_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = _YAML_ITEM_RE.match(raw)
        if item is not None:
            if current_key is None or not isinstance(manifest.get(current_key), list):
                raise EvolutionError(
                    EvolutionErrorClass.VALIDATION_ERROR,
                    "manifest.yaml list item without a preceding list key",
                )
            manifest[current_key].append(item.group(1).strip())
            continue
        entry = _YAML_KEY_RE.match(raw)
        if entry is None:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "manifest.yaml line is not key: value"
            )
        key, value = entry.group(1), entry.group(2)
        current_key = key
        if value is None:
            manifest[key] = []
        elif value.strip() == "[]":
            manifest[key] = []
        else:
            manifest[key] = value.strip()
    return manifest


def read_manifest(layout_or_path: SkillLayout | Path) -> dict[str, Any]:
    path = (
        layout_or_path.manifest_path
        if isinstance(layout_or_path, SkillLayout)
        else Path(layout_or_path)
    )
    if not path.is_file():
        raise EvolutionError(
            EvolutionErrorClass.NOT_FOUND, f"manifest.yaml not found at {path}"
        )
    return load_manifest_yaml(path.read_text(encoding="utf-8"))


# ---------------------------------------------------- controlled operations
# The CONTROLLED missing-capability class the deterministic generator can
# actually satisfy: pure single-input transforms. Each entry carries the
# generated function body (a source fragment written HERE, never derived from
# the request) and a reference implementation used to derive canonical cases.


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    input_name: str
    output_name: str
    body: str
    reference: Callable[[Any], Any]
    sample_inputs: tuple[Any, ...]


def _ref_slugify(value: Any) -> str:
    out: list[str] = []
    prev_dash = False
    for ch in str(value).strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif out and not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _ref_word_count(value: Any) -> int:
    return len([w for w in str(value).split() if w])


def _ref_reverse_text(value: Any) -> str:
    return str(value)[::-1]


def _ref_char_checksum(value: Any) -> int:
    total = 0
    for ch in str(value):
        total = (total * 31 + ord(ch)) % 65521
    return total


OPERATIONS: dict[str, Operation] = {
    "slugify": Operation(
        name="slugify",
        input_name="text",
        output_name="slug",
        body=(
            "    out = []\n"
            "    prev_dash = False\n"
            "    for ch in str(value).strip().lower():\n"
            "        if ch.isalnum():\n"
            "            out.append(ch)\n"
            "            prev_dash = False\n"
            "        elif out and not prev_dash:\n"
            '            out.append("-")\n'
            "            prev_dash = True\n"
            '    return "".join(out).strip("-")\n'
        ),
        reference=_ref_slugify,
        sample_inputs=(
            "Personal Agent OS",
            "  Türkçe  Başlık  ",
            "a--b__c!!",
            "",
            "2026 Roadmap: M7 Evolution",
        ),
    ),
    "word_count": Operation(
        name="word_count",
        input_name="text",
        output_name="count",
        body="    return len([w for w in str(value).split() if w])\n",
        reference=_ref_word_count,
        sample_inputs=("one two three", "  ", "tek", "a b  c   d", "  bosluklu  metin  "),
    ),
    "reverse_text": Operation(
        name="reverse_text",
        input_name="text",
        output_name="reversed_text",
        body="    return str(value)[::-1]\n",
        reference=_ref_reverse_text,
        sample_inputs=("abc", "", "kayik", "12345", "Agent OS"),
    ),
    "char_checksum": Operation(
        name="char_checksum",
        input_name="text",
        output_name="checksum",
        body=(
            "    total = 0\n"
            "    for ch in str(value):\n"
            "        total = (total * 31 + ord(ch)) % 65521\n"
            "    return total\n"
        ),
        reference=_ref_char_checksum,
        sample_inputs=("abc", "", "personal-agent-os", "0", "uzun bir metin"),
    ),
}


# ------------------------------------------------------------------- the spec


@dataclass(slots=True)
class SkillCase:
    value: str | int | float | bool
    expected: str | int | float | bool


@dataclass(slots=True)
class SkillSpec:
    """Structured, fully validated request for one generated skill.

    ``parse`` is the CHOKE POINT: after it returns, every field is a strict
    token or a bounded scalar literal.
    """

    capability_id: str
    skill_name: str
    version: str
    operation: str
    summary: str
    owner_scope: str = "normal"
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    function_name: str = ""
    cases: list[SkillCase] = field(default_factory=list)

    @property
    def op(self) -> Operation:
        return OPERATIONS[self.operation]

    @property
    def inputs(self) -> list[str]:
        return [self.op.input_name]

    @property
    def outputs(self) -> list[str]:
        return [self.op.output_name]

    def capability_manifest(self, *, status: str = "experimental") -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "version": self.version,
            "status": status,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "owner_scope": self.owner_scope,
            "health_metrics": list(GENERATED_HEALTH_METRICS),
            "summary": self.summary,
            "skill": self.skill_name,
            "entrypoint": "run",
        }

    @classmethod
    def parse(cls, raw: Any) -> SkillSpec:
        if not isinstance(raw, dict):
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR, "skill spec must be an object"
            )
        unknown = set(raw) - {
            "capability_id",
            "skill_name",
            "version",
            "operation",
            "summary",
            "owner_scope",
            "permissions",
            "dependencies",
            "function_name",
            "cases",
        }
        if unknown:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"skill spec carries unknown keys: {sorted(unknown)}",
                details={"unknown": sorted(unknown)},
            )
        capability_id = require_capability_id(raw.get("capability_id"))
        operation = raw.get("operation")
        if operation not in OPERATIONS:
            # An operation outside the controlled allowlist is NOT a validation
            # nicety: it is the boundary of what a deterministic generator may
            # emit at all.
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED,
                "operation is not in the controlled generator allowlist",
                details={"allowed": sorted(OPERATIONS)},
            )
        op = OPERATIONS[str(operation)]
        skill_name = require_identifier(
            raw.get("skill_name") or capability_id.replace(".", "_"), field="skill_name"
        )
        function_name = require_identifier(
            raw.get("function_name") or op.name, field="function_name"
        )
        spec = cls(
            capability_id=capability_id,
            skill_name=skill_name,
            version=require_version(raw.get("version", "0.1.0")),
            operation=op.name,
            summary=require_summary(raw.get("summary") or f"generated {op.name} capability"),
            owner_scope=require_owner_scope(raw.get("owner_scope", "normal")),
            permissions=require_slug_list(raw.get("permissions") or [], field="permissions"),
            dependencies=require_slug_list(raw.get("dependencies") or [], field="dependencies"),
            function_name=function_name,
            cases=_parse_cases(raw.get("cases"), op),
        )
        return spec


def _parse_cases(raw_cases: Any, op: Operation) -> list[SkillCase]:
    if raw_cases is None:
        # Canonical cases derived from the reference implementation.
        return [SkillCase(value=v, expected=op.reference(v)) for v in op.sample_inputs]
    if not isinstance(raw_cases, list):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, "cases must be a list of {input, expected}"
        )
    if not raw_cases or len(raw_cases) > MAX_CASES:
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR,
            f"cases must contain between 1 and {MAX_CASES} entries",
        )
    parsed: list[SkillCase] = []
    for index, case in enumerate(raw_cases):
        if not isinstance(case, dict) or set(case) - {"input", "expected"}:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"cases[{index}] must be an object with keys input/expected",
            )
        parsed.append(
            SkillCase(
                value=require_literal(case.get("input"), field=f"cases[{index}].input"),
                expected=require_literal(case.get("expected"), field=f"cases[{index}].expected"),
            )
        )
    return parsed


# --------------------------------------------------------------- generator seam


@runtime_checkable
class SkillGenerator(Protocol):
    """Provider seam: spec + workspace -> a complete §10 skill directory."""

    name: str

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout: ...


class DeterministicSkillGenerator:
    """Emits a complete skill for the controlled operation class. No model."""

    name = "deterministic"

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        if not isinstance(spec, SkillSpec):
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED,
                "generator requires a parsed SkillSpec (choke-point validated)",
            )
        if spec.operation not in OPERATIONS:
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED,
                "operation is not in the controlled generator allowlist",
            )
        # Splice-point re-validation of every token that will be interpolated.
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        version = require_version(spec.version)
        root = Path(workspace) / skill_name / version
        layout = SkillLayout(
            root=root,
            skill_name=skill_name,
            capability_id=require_capability_id(spec.capability_id),
            version=version,
        )
        for directory in (layout.root, layout.src_dir, layout.tests_dir, layout.evals_dir):
            directory.mkdir(parents=True, exist_ok=True)
        layout.manifest_path.write_text(
            dump_manifest_yaml(spec.capability_manifest()), encoding="utf-8"
        )
        layout.readme_path.write_text(self._render_readme(spec), encoding="utf-8")
        layout.module_path.write_text(self._render_src(spec), encoding="utf-8")
        layout.test_path.write_text(self._render_tests(spec), encoding="utf-8")
        layout.eval_path.write_text(self._render_evals(spec), encoding="utf-8")
        layout.cases_path.write_text(
            json.dumps(
                {
                    "capability_id": layout.capability_id,
                    "input_name": spec.op.input_name,
                    "output_name": spec.op.output_name,
                    "cases": [
                        {"input": case.value, "expected": case.expected} for case in spec.cases
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info(
            "skill_generated",
            generator=self.name,
            capability_id=layout.capability_id,
            version=version,
            cases=len(spec.cases),
        )
        return layout

    # ------------------------------------------------------------- renderers
    # Each renderer re-validates the tokens it interpolates (splice point).

    def _render_readme(self, spec: SkillSpec) -> str:
        capability_id = require_capability_id(spec.capability_id)
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        version = require_version(spec.version)
        summary = require_summary(spec.summary)
        return (
            f"# {skill_name}\n\n"
            f"Generated skill for capability `{capability_id}` v{version}.\n\n"
            f"{summary}\n\n"
            "## Contract\n\n"
            f"- entrypoint: `run(payload: dict) -> dict`\n"
            f"- input key: `{require_slug(spec.op.input_name, field='input_name')}`\n"
            f"- output key: `{require_slug(spec.op.output_name, field='output_name')}`\n"
            f"- operation: `{require_identifier(spec.operation, field='operation')}`\n\n"
            "## Running\n\n"
            f"Set `{SKILL_DIR_ENV}` to this directory, then run\n"
            f"`tests/test_{skill_name}.py` (exit 0 = pass) and\n"
            f"`evals/eval_{skill_name}.py` (prints `EVAL-RESULT <json>`).\n\n"
            "Generated deterministically by the Evolution Engine; do not hand-edit.\n"
        )

    def _render_src(self, spec: SkillSpec) -> str:
        capability_id = require_capability_id(spec.capability_id)
        version = require_version(spec.version)
        summary = require_summary(spec.summary)
        function_name = require_identifier(spec.function_name, field="function_name")
        input_name = require_slug(spec.op.input_name, field="input_name")
        output_name = require_slug(spec.op.output_name, field="output_name")
        # spec.op.body is a source fragment authored in THIS module and selected
        # by an allowlisted operation name — never request text.
        body = OPERATIONS[spec.operation].body
        return (
            f'"""{summary}\n\n'
            f"Generated skill for capability {capability_id} v{version}.\n"
            'Deterministic, offline, standard library only.\n"""\n\n'
            "from __future__ import annotations\n\n"
            "import json\n"
            "import sys\n\n\n"
            f"def {function_name}(value):\n"
            f'    """Pure transform: {input_name} -> {output_name}."""\n'
            f"{body}"
            "\n\n"
            "def run(payload):\n"
            '    """Capability entrypoint: dict -> dict."""\n'
            "    if not isinstance(payload, dict):\n"
            '        raise TypeError("payload must be an object")\n'
            f"    if {input_name!r} not in payload:\n"
            f'        raise KeyError("missing required input: {input_name}")\n'
            f"    return {{{output_name!r}: {function_name}(payload[{input_name!r}])}}\n\n\n"
            "def main():\n"
            "    raw = sys.stdin.read().strip()\n"
            '    payload = json.loads(raw) if raw else {}\n'
            "    sys.stdout.write(json.dumps(run(payload), ensure_ascii=False))\n"
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )

    def _render_tests(self, spec: SkillSpec) -> str:
        capability_id = require_capability_id(spec.capability_id)
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        input_name = require_slug(spec.op.input_name, field="input_name")
        output_name = require_slug(spec.op.output_name, field="output_name")
        case_lines = "".join(
            f"    ({require_literal(c.value, field='case.input')!r}, "
            f"{require_literal(c.expected, field='case.expected')!r}),\n"
            for c in spec.cases
        )
        return (
            f'"""Auto-generated unit tests for {capability_id}.\n\n'
            f"Runs against the skill directory named by ${SKILL_DIR_ENV}. Exits 0 when\n"
            'every check passes, 1 otherwise. Standard library only, offline.\n"""\n\n'
            "import importlib.util\n"
            "import json\n"
            "import os\n"
            "import sys\n\n"
            f"SKILL_DIR = os.environ[{SKILL_DIR_ENV!r}]\n"
            "spec = importlib.util.spec_from_file_location(\n"
            f'    "skill_under_test", os.path.join(SKILL_DIR, "src", "{skill_name}.py")\n'
            ")\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n\n"
            "CASES = [\n"
            f"{case_lines}"
            "]\n\n"
            "failures = []\n"
            "checks = 0\n\n"
            "for value, expected in CASES:\n"
            "    checks += 1\n"
            "    try:\n"
            f"        actual = module.run({{{input_name!r}: value}})[{output_name!r}]\n"
            "    except Exception as exc:  # noqa: BLE001 - generated test harness\n"
            '        failures.append("run(%r) raised %s" % (value, type(exc).__name__))\n'
            "        continue\n"
            "    if actual != expected:\n"
            '        failures.append("run(%r) -> %r, expected %r" % (value, actual, expected))\n\n'
            "checks += 1\n"
            'if not callable(getattr(module, "run", None)):\n'
            '    failures.append("module does not expose a callable run entrypoint")\n\n'
            "checks += 1\n"
            "try:\n"
            '    module.run("not-an-object")\n'
            '    failures.append("run accepted a non-object payload")\n'
            "except TypeError:\n"
            "    pass\n\n"
            "checks += 1\n"
            "try:\n"
            "    module.run({})\n"
            '    failures.append("run accepted a payload without the required input")\n'
            "except KeyError:\n"
            "    pass\n\n"
            "print(\n"
            "    json.dumps(\n"
            '        {"total": checks, "failed": len(failures), "failures": failures[:10]},\n'
            "        ensure_ascii=False,\n"
            "    )\n"
            ")\n"
            "sys.exit(1 if failures else 0)\n"
        )

    def _render_evals(self, spec: SkillSpec) -> str:
        capability_id = require_capability_id(spec.capability_id)
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        return (
            f'"""Auto-generated eval set runner for {capability_id}.\n\n'
            f"Reads evals/{CASES_FILENAME}, executes every case against the generated\n"
            "entrypoint and prints a single machine-readable line:\n\n"
            "    EVAL-RESULT {json}\n\n"
            'Standard library only, offline, deterministic ordering."""\n\n'
            "import importlib.util\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "import time\n\n"
            f"SKILL_DIR = os.environ[{SKILL_DIR_ENV!r}]\n"
            "spec = importlib.util.spec_from_file_location(\n"
            f'    "skill_under_eval", os.path.join(SKILL_DIR, "src", "{skill_name}.py")\n'
            ")\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n\n"
            f'with open(os.path.join(SKILL_DIR, "{EVALS_DIRNAME}", "{CASES_FILENAME}"),'
            ' encoding="utf-8") as handle:\n'
            "    data = json.load(handle)\n\n"
            'input_name = data["input_name"]\n'
            'output_name = data["output_name"]\n'
            "latencies = []\n"
            "passed = 0\n"
            "failed_cases = []\n\n"
            'for index, case in enumerate(data["cases"]):\n'
            "    started = time.perf_counter()\n"
            "    try:\n"
            '        actual = module.run({input_name: case["input"]})[output_name]\n'
            "    except Exception as exc:  # noqa: BLE001 - generated eval harness\n"
            "        actual = \"<error:%s>\" % type(exc).__name__\n"
            "    latencies.append((time.perf_counter() - started) * 1000.0)\n"
            '    if actual == case["expected"]:\n'
            "        passed += 1\n"
            "    else:\n"
            '        failed_cases.append({"index": index, "actual": repr(actual)})\n\n'
            "total = len(latencies)\n"
            "ordered = sorted(latencies)\n"
            "p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0\n"
            "success_rate = (passed / total) if total else 0.0\n"
            "result = {\n"
            '    "capability_id": data["capability_id"],\n'
            '    "total": total,\n'
            '    "passed": passed,\n'
            '    "failed": total - passed,\n'
            '    "failed_cases": failed_cases[:10],\n'
            '    "metrics": {\n'
            '        "success_rate": round(success_rate, 6),\n'
            '        "p95_latency_ms": round(p95, 6),\n'
            '        "case_count": total,\n'
            "    },\n"
            "}\n"
            'print("EVAL-RESULT " + json.dumps(result, ensure_ascii=False))\n'
            "sys.exit(0 if passed == total and total > 0 else 1)\n"
        )


class ClaudeSkillGenerator:
    """Claude Agent SDK skill generator — a configured-later seam (§5).

    Without ``cli_path`` every entrypoint raises a typed
    ``generator_not_configured`` error BEFORE any I/O, so the vendor path exists
    but is inert (the same discipline as ``ClaudeCodingBackend`` and the voice
    providers). Domain code only ever sees ``SkillSpec``/``SkillLayout``, never
    Anthropic response formats. When configured, the CLI is invoked inside the
    disposable evolution sandbox (§13) — never with bypassPermissions on the
    owner workstation.
    """

    name = "claude"

    def __init__(self, cli_path: str = "", model: str = "") -> None:
        self.cli_path = cli_path
        self.model = model

    def _require_configured(self) -> None:
        if not self.cli_path:
            raise EvolutionError(
                EvolutionErrorClass.GENERATOR_NOT_CONFIGURED,
                "Claude skill generator is not configured "
                "(set PAGENTOS_EVOLUTION_CLAUDE_CLI to the Claude CLI path)",
            )

    def build_command(self, prompt: str, workspace: Path) -> list[str]:
        """Pure command construction (unit-testable without invocation)."""
        command = [
            self.cli_path,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--add-dir",
            str(workspace),
        ]
        if self.model:
            command += ["--model", self.model]
        return command

    def build_prompt(self, spec: SkillSpec) -> str:
        """The work order handed to the model, built only from validated tokens."""
        return (
            "Generate a skill implementing capability "
            f"{require_capability_id(spec.capability_id)} v{require_version(spec.version)} "
            f"named {require_identifier(spec.skill_name, field='skill_name')} in the "
            "EVOLUTION_ENGINE_SPEC section 10 layout "
            "(manifest.yaml, README.md, src/, tests/, evals/)."
        )

    def generate(self, spec: SkillSpec, workspace: Path) -> SkillLayout:
        self._require_configured()
        raise EvolutionError(
            EvolutionErrorClass.GENERATOR_NOT_CONFIGURED,
            "Claude skill generation flow is not enabled in this build",
        )


__all__ = [
    "CASES_FILENAME",
    "EVALS_DIRNAME",
    "GENERATED_HEALTH_METRICS",
    "MANIFEST_FILENAME",
    "MAX_CASES",
    "OPERATIONS",
    "README_FILENAME",
    "SKILL_DIR_ENV",
    "SRC_DIRNAME",
    "TESTS_DIRNAME",
    "ClaudeSkillGenerator",
    "DeterministicSkillGenerator",
    "Operation",
    "SkillCase",
    "SkillGenerator",
    "SkillLayout",
    "SkillSpec",
    "dump_manifest_yaml",
    "load_manifest_yaml",
    "read_manifest",
]
