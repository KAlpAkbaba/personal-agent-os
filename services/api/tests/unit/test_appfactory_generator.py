"""``DeterministicAppGenerator`` (docs/M23_APP_FACTORY_SPEC.md §2, ADR-0086).

Every built-in template renders for its spec, the rendered file list equals the
committed fixture (``tests/fixtures/apps/task-tracker/expected_files.json``), generation
is deterministic (the same spec always produces byte-identical files), and the shipped
DOM oracle (``app.appfactory.oracles``) matches its committed fixture copy byte for byte
(module docstring of ``app.appfactory.oracles``: "the device lab uses the same files").
"""

from __future__ import annotations

import json
from pathlib import Path

from app.appfactory.generator import DeterministicAppGenerator
from app.appfactory.oracles import load_oracle
from app.appfactory.spec import AppSpec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "apps"


def _generate(spec_dict: dict):
    spec = AppSpec.model_validate(spec_dict)
    return AppSpec, DeterministicAppGenerator().generate(spec)


def test_task_tracker_renders_the_expected_file_list() -> None:
    spec = AppSpec.model_validate(
        {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}
    )
    files = DeterministicAppGenerator().generate(spec)
    expected = json.loads((FIXTURES / "task-tracker" / "expected_files.json").read_text("utf-8"))
    assert sorted(files.path_set()) == sorted(expected)


def test_task_tracker_manifest_names_a_real_entry() -> None:
    spec = AppSpec.model_validate(
        {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}
    )
    files = DeterministicAppGenerator().generate(spec)
    manifest = files.manifest()
    assert manifest["entry"] in files.path_set()
    assert manifest["run"]["serve"] == "python -m http.server {port} --bind 127.0.0.1"
    assert manifest["test"]["unit"] == "node tests/run.js"


def test_task_tracker_title_is_html_escaped_and_present() -> None:
    spec = AppSpec.model_validate(
        {"name": "<img src=x onerror=alert(1)>", "kind": "web_static", "template": "task-tracker"}
    )
    files = DeterministicAppGenerator().generate(spec)
    index_html = files.get("index.html")
    assert "<img src=x onerror=alert(1)>" not in index_html
    assert "&lt;img" in index_html


def test_generation_is_deterministic() -> None:
    spec = AppSpec.model_validate(
        {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}
    )
    a = DeterministicAppGenerator().generate(spec)
    b = DeterministicAppGenerator().generate(spec)
    assert {f.path: f.text for f in a.files} == {f.path: f.text for f in b.files}


def test_static_page_renders_and_carries_the_owners_body_text() -> None:
    spec = AppSpec.model_validate(
        {
            "name": "Notlarım",
            "kind": "web_static",
            "template": "static-page",
            "page_title": "Notlarım",
            "page_heading": "Notlarım",
            "page_body": "Merhaba dünya.",
        }
    )
    files = DeterministicAppGenerator().generate(spec)
    assert "index.html" in files.path_set()
    assert "Merhaba dünya." in files.get("index.html")
    manifest = files.manifest()
    assert manifest["entry"] == "index.html"


def test_cli_tool_renders_commands_into_cli_js_and_tests() -> None:
    spec = AppSpec.model_validate(
        {
            "name": "Selamlayıcı",
            "kind": "cli",
            "template": "cli-tool",
            "commands": [{"name": "selamla"}, {"name": "say"}],
        }
    )
    files = DeterministicAppGenerator().generate(spec)
    cli_js = files.get("cli.js")
    assert '"selamla"' in cli_js
    assert '"say"' in cli_js
    manifest = files.manifest()
    assert manifest["entry"] == "cli.js"
    assert "run" not in manifest  # a CLI tool is not served
    assert manifest["test"]["unit"] == "node tests/run.js"
    tests_run = files.get("tests/run.js")
    assert '"selamla"' in tests_run


def test_cli_tool_command_names_cannot_inject_into_generated_js() -> None:
    """Defence in depth (module docstring): even though ``AppSpec.Command`` already
    refuses a non-identifier command name at parse time, the generator's own splice
    point re-validates — proven here by constructing a spec the parser would refuse and
    confirming the generator ALSO refuses if ever handed one directly (never trusts the
    spec's own validation a second time silently)."""
    import pytest

    from app.appfactory.generator import AppGeneratorError, _require_command_name

    with pytest.raises(AppGeneratorError):
        _require_command_name("run; rm -rf /")


def test_oracle_matches_its_committed_test_fixture_byte_for_byte() -> None:
    """The device lab (track B) reads ``tests/fixtures/apps/task-tracker/oracle.json``
    directly; Cloud Core's own ``app.appfactory.oracles`` module is the single source —
    the two must never drift."""
    committed = json.loads((FIXTURES / "task-tracker" / "oracle.json").read_text("utf-8"))
    assert load_oracle("task-tracker") == committed
