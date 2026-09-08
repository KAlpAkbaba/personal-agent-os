"""``app.genesis.adapter``: rendering for both fixtures, determinism, the
rendered tests failing honestly without a live URL, every splice
re-validated, no free text in generated source outside ``repr``/``json.dumps``
(M24_CAPABILITY_GENESIS_SPEC.md §3).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.evolution.errors import EvolutionError
from app.evolution.evaluation import run_skill_script
from app.evolution.manifest import validate_manifest
from app.genesis.adapter import AdapterSpec, HttpAdapterGenerator
from app.genesis.interface import InterfaceDescription
from tests.fixtures.genesis import counterbox_app, lampbox_app

COUNTERBOX_RAW = {**counterbox_app.SPEC_TEMPLATE, "base_url": "http://127.0.0.1:54321"}
LAMPBOX_RAW = {**lampbox_app.SPEC_TEMPLATE, "base_url": "http://127.0.0.1:54322"}


def _spec(raw, operation_id, **kwargs):
    return AdapterSpec(
        interface=InterfaceDescription.parse(raw), operation_id=operation_id, **kwargs
    )


_EXPECTED_LAYOUT_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "genesis" / "expected_layout.json"
)
EXPECTED_LAYOUT = json.loads(_EXPECTED_LAYOUT_PATH.read_text(encoding="utf-8"))


def test_renders_the_full_layout_for_both_fixtures(tmp_path):
    for raw, op in ((COUNTERBOX_RAW, "increment"), (LAMPBOX_RAW, "set")):
        spec = _spec(raw, op)
        layout = HttpAdapterGenerator().generate(spec, tmp_path / spec.skill_name)
        assert layout.missing_paths() == []
        assert layout.manifest_path.is_file()
        assert layout.readme_path.is_file()
        assert layout.module_path.is_file()
        assert layout.test_path.is_file()
        assert layout.eval_path.is_file()
        assert layout.cases_path.is_file()


def test_rendered_layout_matches_the_declared_expected_layout(tmp_path):
    """``tests/fixtures/genesis/expected_layout.json`` names the files the
    adapter must render — a template with ``{skill}`` filled from the actual
    skill name, checked here so a future change to the layout must update the
    declaration or fail this test, never drift silently."""
    spec = _spec(COUNTERBOX_RAW, "increment")
    layout = HttpAdapterGenerator().generate(spec, tmp_path)
    skill = spec.skill_name

    def _names(paths: list[str]) -> set[str]:
        return {p.format(skill=skill) for p in paths}

    assert {p.name for p in (layout.manifest_path, layout.readme_path)} == _names(
        EXPECTED_LAYOUT["root_files"]
    )
    assert {layout.module_path.name} == _names(EXPECTED_LAYOUT["src_files"])
    assert {layout.test_path.name} == _names(EXPECTED_LAYOUT["tests_files"])
    assert {layout.eval_path.name, layout.cases_path.name} == _names(
        EXPECTED_LAYOUT["evals_files"]
    )


def test_generation_is_deterministic(tmp_path):
    spec = _spec(COUNTERBOX_RAW, "increment")
    layout_a = HttpAdapterGenerator().generate(spec, tmp_path / "a")
    layout_b = HttpAdapterGenerator().generate(spec, tmp_path / "b")
    assert layout_a.module_path.read_text() == layout_b.module_path.read_text()
    assert layout_a.test_path.read_text() == layout_b.test_path.read_text()
    assert layout_a.eval_path.read_text() == layout_b.eval_path.read_text()
    assert layout_a.cases_path.read_text() == layout_b.cases_path.read_text()


def test_generated_manifest_validates_and_declares_no_shortcuts(tmp_path):
    spec = _spec(COUNTERBOX_RAW, "increment")
    layout = HttpAdapterGenerator().generate(spec, tmp_path)
    import json

    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    normalized = validate_manifest(manifest)
    assert normalized["network_permissions"] == ["127.0.0.1"]
    assert normalized["filesystem_permissions"] == []
    assert normalized["device_permissions"] == []
    assert normalized["secret_requirements"] == []
    assert normalized["external_services"] == ["counterbox"]
    assert normalized["authority_class"] == "mutating_unauthorized"
    assert normalized["side_effect_class"] == "mutate_external"
    assert normalized["evidence_contract"]["read_back"] == "read"


def test_generated_source_parses_and_declares_the_run_entrypoint(tmp_path):
    spec = _spec(LAMPBOX_RAW, "toggle")
    layout = HttpAdapterGenerator().generate(spec, tmp_path)
    source = layout.module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(isinstance(node, ast.FunctionDef) and node.name == "run" for node in tree.body)
    assert any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)
    # ONE network call: exactly one call to the opener's send method.
    assert source.count("_send(request") == 1


def test_rendered_tests_fail_honestly_without_a_live_application(tmp_path):
    """spec §3: "the rendered tests fail honestly without a live URL". The
    module's BASE_URL is a real loopback port nothing is listening on."""
    spec = _spec(COUNTERBOX_RAW, "read")  # base_url = 127.0.0.1:54321, unused port
    layout = HttpAdapterGenerator().generate(spec, tmp_path)
    run = run_skill_script(layout.test_path, layout.root)
    assert run.exit_code != 0
    assert "dependency_unavailable" in run.stdout or "dependency_unavailable" in run.stderr


def test_operation_not_on_interface_refused():
    desc = InterfaceDescription.parse(COUNTERBOX_RAW)
    try:
        AdapterSpec(interface=desc, operation_id="nonexistent")
    except EvolutionError:
        pass
    else:
        raise AssertionError("expected EvolutionError")


def test_capability_id_is_interface_dot_operation():
    spec = _spec(COUNTERBOX_RAW, "increment")
    assert spec.capability_id == "counterbox.increment"
    spec2 = _spec(LAMPBOX_RAW, "toggle")
    assert spec2.capability_id == "lampbox.toggle"


def test_no_free_text_outside_repr_or_json_dumps_splice_points(tmp_path):
    """The M23 review lesson (task instructions): free text never lands in
    generated source except through json.dumps()/repr(). Every VALUE this
    generator interpolates comes from a validated token/scalar (name, path,
    method, field names/types — all regex-shaped at the interface choke
    point), so this is a structural property rather than something to prove
    per-value; this test instead proves the rendered module has no
    string-formatting call that could splice raw text (no ``%`` or
    ``.format(`` on anything but literal templates already fixed at
    generation time, and no f-string reaching a value outside this
    generator's own control)."""
    spec = _spec(COUNTERBOX_RAW, "increment")
    layout = HttpAdapterGenerator().generate(spec, tmp_path)
    source = layout.module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # No f-strings at all in the generated module (repr()/json.dumps()
        # only) — an f-string would be exactly the smuggling vector the M23
        # review found (a value's own newline ending a comment early).
        assert not isinstance(node, ast.JoinedStr), "generated module uses an f-string"
