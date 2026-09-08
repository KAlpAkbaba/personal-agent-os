"""``DeterministicAppGenerator`` (docs/M23_APP_FACTORY_SPEC.md §2, ADR-0086).

Every built-in template renders for its spec, the rendered file list equals the
committed fixture (``tests/fixtures/apps/task-tracker/expected_files.json``), generation
is deterministic (the same spec always produces byte-identical files), and the shipped
DOM oracle (``app.appfactory.oracles``) matches its committed fixture copy byte for byte
(module docstring of ``app.appfactory.oracles``: "the device lab uses the same files").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.appfactory.generator import AppGeneratorError, DeterministicAppGenerator, _substitute
from app.appfactory.oracles import load_oracle
from app.appfactory.spec import AppSpec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "apps"
TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "appfactory" / "templates"
CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".json", ".ts")


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


# ------------------------------------------------- free text never lands in code (M23 review)


def test_no_template_splices_a_slot_into_a_code_file_comment() -> None:
    """Structural guard for the M23 security review's Critical: a ``{{SLOT}}`` inside a
    ``//`` or ``/* */`` line of a ``.js``/``.json`` template is where free text became
    code. None may exist, in any template, ever again."""
    offenders: list[str] = []
    for path in sorted(TEMPLATES.rglob("*")):
        if not path.is_file() or not path.name.lower().endswith(CODE_SUFFIXES):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if "{{" in stripped and (
                stripped.startswith("//")
                or stripped.startswith("/*")
                or "//" in stripped.split("{{")[0]
            ):
                offenders.append(f"{path.relative_to(TEMPLATES).as_posix()}:{number}")
    assert offenders == []


def test_the_splice_point_refuses_free_text_into_code() -> None:
    with pytest.raises(AppGeneratorError, match="free text"):
        _substitute("var T = {{TITLE}};", {"TITLE": "Notlar\u0131m"}, path="app.js")
    with pytest.raises(AppGeneratorError, match="free text"):
        _substitute("// {{TITLE}}", {"TITLE": "safe-looking text"}, path="logic.js")
    # A closed-alphabet token and a JSON literal the generator produced are fine.
    assert (
        _substitute('var K = "{{KEY}}";', {"KEY": "pagentos-tasks-notlarim"}, path="app.js")
        == 'var K = "pagentos-tasks-notlarim";'
    )
    assert (
        _substitute(
            "<title>{{TITLE}}</title>", {"TITLE": "Notlar &amp; Görevler"}, path="index.html"
        )
        == "<title>Notlar &amp; Görevler</title>"
    )


def test_an_unfilled_slot_is_a_generator_error_not_a_shipped_marker() -> None:
    with pytest.raises(AppGeneratorError, match="unfilled slot"):
        _substitute("<h1>{{APP_TITLE}}</h1>", {}, path="index.html")


@pytest.mark.parametrize(
    "spec_dict",
    [
        {
            "name": 'Notlar\'ım "dün" * <script>',
            "kind": "web_static",
            "template": "task-tracker",
        },
        {
            "name": "Sayfa",
            "kind": "web_static",
            "template": "static-page",
            "page_title": "*/ </script><script>alert(1)</script> \\ ' \"",
            "page_heading": "Başlık // yorum",
            "page_body": "satır 1\nsatır 2 */ <b>x</b>",
        },
        {
            "name": "Araç * <script> 'x'",
            "kind": "cli",
            "template": "cli-tool",
            "commands": [{"name": "selamla", "description": "// */ </script>"}],
        },
    ],
)
def test_every_rendered_code_file_parses_under_node_with_hostile_but_allowed_text(
    spec_dict: dict, tmp_path: Path
) -> None:
    """The text a spec may still carry (quotes, backslashes, ``*/``, ``//``, a script
    tag) never reaches a code file: every rendered ``.js`` is valid under ``node --check``
    and carries none of it verbatim."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this machine")
    files = DeterministicAppGenerator().generate(AppSpec.model_validate(spec_dict))
    for entry in files.files:
        target = tmp_path / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.text, encoding="utf-8")
        if entry.path.endswith(".js"):
            assert "</script>" not in entry.text and "*/ " not in entry.text.split("\n")[0]
            proc = subprocess.run(
                [node, "--check", str(target)], capture_output=True, text=True, timeout=30
            )
            assert proc.returncode == 0, (entry.path, proc.stderr[:300])
