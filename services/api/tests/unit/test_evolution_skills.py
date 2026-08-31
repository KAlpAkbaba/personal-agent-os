"""Unit tests: the SkillGenerator seam, the §10 layout and — above all — the
strict token validation on everything that reaches GENERATED SOURCE.

Acceptance coverage: "tests/evals generated" (the layout half; the "they really
run" half is in test_evolution_evaluation.py), plus the ADR-0024/ADR-0025
security lesson: hostile skill-spec values are refused, and generated source
always parses.
"""

import ast
import json

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.registry import validate_manifest
from app.evolution.skills import (
    GENERATED_HEALTH_METRICS,
    MAX_CASES,
    OPERATIONS,
    ClaudeSkillGenerator,
    DeterministicSkillGenerator,
    SkillGenerator,
    SkillSpec,
    dump_manifest_yaml,
    load_manifest_yaml,
    read_manifest,
)

BASE_SPEC = {
    "capability_id": "text.slugify",
    "operation": "slugify",
    "version": "0.1.0",
    "summary": "turn a title into a url slug",
}


def generate(tmp_path, **overrides):
    spec = SkillSpec.parse({**BASE_SPEC, **overrides})
    return spec, DeterministicSkillGenerator().generate(spec, tmp_path)


# ------------------------------------------------------------------- layout


def test_generated_skill_matches_the_section10_layout(tmp_path) -> None:
    _, layout = generate(tmp_path)
    assert layout.missing_paths() == []
    assert layout.manifest_path.name == "manifest.yaml"
    assert layout.readme_path.name == "README.md"
    assert layout.module_path.parent.name == "src"
    assert layout.test_path.parent.name == "tests"
    assert layout.eval_path.parent.name == "evals"
    assert layout.cases_path.parent.name == "evals"
    # <skill>/<version>/ keeps published versions immutable (documented deviation).
    assert layout.root.name == "0.1.0"
    assert layout.root.parent.name == "text_slugify"


def test_generated_manifest_is_a_valid_section2_manifest(tmp_path) -> None:
    _, layout = generate(tmp_path)
    manifest = read_manifest(layout)
    normalized = validate_manifest(manifest)
    assert normalized["id"] == "text.slugify"
    assert normalized["entrypoint"] == "run"
    assert normalized["skill"] == "text_slugify"
    assert normalized["health_metrics"] == list(GENERATED_HEALTH_METRICS)


def test_generated_eval_cases_are_recorded_as_data(tmp_path) -> None:
    spec, layout = generate(tmp_path)
    payload = json.loads(layout.cases_path.read_text(encoding="utf-8"))
    assert payload["capability_id"] == "text.slugify"
    assert payload["input_name"] == "text"
    assert payload["output_name"] == "slug"
    assert len(payload["cases"]) == len(spec.cases) >= 3


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_generated_sources_always_parse(tmp_path, operation) -> None:
    """Every emitted python file must be syntactically valid, for every
    operation in the controlled allowlist."""
    _, layout = generate(
        tmp_path / operation,
        operation=operation,
        capability_id=f"text.{operation}",
        skill_name=f"text_{operation}",
        function_name=operation,
    )
    emitted = sorted(layout.root.rglob("*.py"))
    assert len(emitted) == 3
    for source_file in emitted:
        ast.parse(source_file.read_text(encoding="utf-8"))


def test_generated_module_exposes_the_run_entrypoint(tmp_path) -> None:
    spec, layout = generate(tmp_path)
    tree = ast.parse(layout.module_path.read_text(encoding="utf-8"))
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"run", "main", spec.function_name} <= functions


def test_deterministic_generator_is_reproducible(tmp_path) -> None:
    _, first = generate(tmp_path / "a")
    _, second = generate(tmp_path / "b")
    for name in ("module_path", "test_path", "eval_path", "manifest_path"):
        assert getattr(first, name).read_text(encoding="utf-8") == getattr(
            second, name
        ).read_text(encoding="utf-8")


# --------------------------------------------- hostile input (ADR-0024 lesson)


HOSTILE_SPECS = [
    # capability id that would terminate a string literal / inject code
    {"capability_id": "text.slug'); import os; os.system('calc'); ('"},
    {"capability_id": "../../services/recovery-supervisor"},
    {"capability_id": "text"},  # single segment
    {"capability_id": "text.slugify\nstatus: production"},  # yaml injection
    # skill name / function name that would escape a path or shadow a builtin
    {"skill_name": "../../evil"},
    {"skill_name": "text slugify"},
    {"skill_name": "__init__"},
    {"function_name": "eval"},
    {"function_name": "exec"},
    {"function_name": "run"},  # reserved: would shadow the entrypoint
    {"function_name": "slugify; import os"},
    # version
    {"version": "0.1.0; DROP TABLE capabilities"},
    {"version": "latest"},
    # operation outside the controlled allowlist
    {"operation": "arbitrary_python"},
    {"operation": "__import__"},
    # manifest-ish list fields
    {"permissions": ["filesystem.write'; rm -rf /"]},
    {"dependencies": ["../../x"]},
    {"owner_scope": "root"},
    # prose that reaches the README/docstring
    {"summary": 'x"""\nimport os\nos.system("calc")\n"""'},
    {"summary": "x" * 500},
    # unknown key smuggling
    {"body": "import os"},
]


@pytest.mark.parametrize(
    "override", HOSTILE_SPECS, ids=[str(sorted(o))[:60] for o in HOSTILE_SPECS]
)
def test_hostile_skill_spec_values_are_refused_at_the_choke_point(override) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        SkillSpec.parse({**BASE_SPEC, **override})
    assert excinfo.value.error_class in (
        EvolutionErrorClass.VALIDATION_ERROR,
        EvolutionErrorClass.GENERATION_FAILED,
    )
    # The offending value is never echoed back into the error message.
    for value in override.values():
        if isinstance(value, str) and len(value) > 8:
            assert value not in excinfo.value.message


def test_hostile_case_literals_are_refused() -> None:
    for cases in (
        [{"input": "x" * 500, "expected": "y"}],
        [{"input": {"nested": 1}, "expected": "y"}],
        [{"input": "x", "expected": None}],
        [{"input": "x", "expected": "y", "extra": 1}],
        [],
        [{"input": "x", "expected": "y"}] * (MAX_CASES + 1),
        "not-a-list",
    ):
        with pytest.raises(EvolutionError):
            SkillSpec.parse({**BASE_SPEC, "cases": cases})


def test_case_values_are_emitted_escaped_and_source_still_parses(tmp_path) -> None:
    """A case value full of quotes/backslashes is legal data — it must land in
    the generated tests through repr() and leave the file parseable."""
    nasty = "a'\"b\\c) if False else __import__('os')#"
    _, layout = generate(
        tmp_path,
        cases=[{"input": nasty, "expected": "a-b-c-if-false-else-import-os"}],
    )
    source = layout.test_path.read_text(encoding="utf-8")
    ast.parse(source)
    assert "__import__('os')" not in source  # escaped, never bare
    assert repr(nasty) in source


def test_generator_refuses_a_raw_dict_spec(tmp_path) -> None:
    """The generator only accepts a parsed SkillSpec — a dict would bypass the
    choke point entirely."""
    with pytest.raises(EvolutionError) as excinfo:
        DeterministicSkillGenerator().generate(dict(BASE_SPEC), tmp_path)  # type: ignore[arg-type]
    assert excinfo.value.error_class == EvolutionErrorClass.GENERATION_FAILED


def test_generator_revalidates_at_the_splice_point(tmp_path) -> None:
    """Defense in depth: mutating a parsed spec after the choke point must still
    be caught when the value is about to be spliced into source."""
    spec = SkillSpec.parse(BASE_SPEC)
    spec.skill_name = "../escape"
    with pytest.raises(EvolutionError):
        DeterministicSkillGenerator().generate(spec, tmp_path)

    spec = SkillSpec.parse(BASE_SPEC)
    spec.operation = "arbitrary_python"
    with pytest.raises(EvolutionError):
        DeterministicSkillGenerator().generate(spec, tmp_path)


# ------------------------------------------------------------- manifest yaml


def test_manifest_yaml_round_trips() -> None:
    manifest = {
        "id": "text.slugify",
        "version": "0.1.0",
        "status": "production",
        "inputs": ["text"],
        "outputs": ["slug"],
        "permissions": [],
        "dependencies": [],
        "owner_scope": "normal",
        "health_metrics": list(GENERATED_HEALTH_METRICS),
    }
    assert load_manifest_yaml(dump_manifest_yaml(manifest)) == manifest


def test_manifest_yaml_refuses_malformed_text() -> None:
    with pytest.raises(EvolutionError):
        load_manifest_yaml("  - orphan item\n")
    with pytest.raises(EvolutionError):
        load_manifest_yaml("this is not a mapping\n")


# ------------------------------------------------------------ claude seam


def test_claude_generator_is_inert_without_configuration(tmp_path) -> None:
    generator = ClaudeSkillGenerator()
    assert isinstance(generator, SkillGenerator)
    spec = SkillSpec.parse(BASE_SPEC)
    with pytest.raises(EvolutionError) as excinfo:
        generator.generate(spec, tmp_path)
    assert excinfo.value.error_class == EvolutionErrorClass.GENERATOR_NOT_CONFIGURED
    assert not any(tmp_path.iterdir())  # refused BEFORE any I/O


def test_claude_generator_builds_a_command_and_prompt_from_tokens_only() -> None:
    generator = ClaudeSkillGenerator(cli_path="C:/tools/claude.exe", model="claude-opus-5")
    command = generator.build_command("do the thing", tmp_workspace := "C:/tmp/ws")
    assert command[0] == "C:/tools/claude.exe"
    assert "--output-format" in command and "--model" in command
    assert str(tmp_workspace) in command
    prompt = generator.build_prompt(SkillSpec.parse(BASE_SPEC))
    assert "text.slugify" in prompt and "text_slugify" in prompt


def test_deterministic_generator_satisfies_the_seam() -> None:
    assert isinstance(DeterministicSkillGenerator(), SkillGenerator)
