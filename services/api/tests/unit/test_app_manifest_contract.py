"""B03 req 4/417-421: the manifest the App Factory sends, held to the device's contract.

Every app this factory ever produced was refused by the device at the first parse, for three
separate reasons, with both halves' suites green the whole time:

* two templates wrote ``{port}``; the device's placeholder is ``<port>`` and it refuses ``{``
  and ``}`` anywhere in a command;
* the cli-tool template carried no ``run`` section, which the device requires;
* and no ``port`` either, which the device also requires - so a CLI tool, which binds nothing,
  had no truthful manifest it could send at all.

Each half had tests. Neither read the other. ``packages/protocol/app-manifest.example.json``
is now the one file both read: this test keeps it equal to what the templates actually
produce, and ``AppManifestContractTests.cs`` scaffolds it through the device's real parser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.appfactory import generator as generator_module
from app.appfactory.generator import DeterministicAppGenerator
from app.appfactory.spec import AppSpec
from app.appfactory.validation import (
    ALLOWED_RUN_COMMANDS,
    ALLOWED_TEST_COMMANDS,
    MAX_PORT,
    MIN_PORT,
    NO_PORT,
    PORT_PLACEHOLDER,
    validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "packages" / "protocol" / "app-manifest.example.json"
TEMPLATES = Path(generator_module.__file__).resolve().parent / "templates"
TEMPLATE_NAMES = ("static-page", "task-tracker", "cli-tool")


def _manifest(template: str) -> dict:
    return json.loads((TEMPLATES / template / "manifest.json").read_text("utf-8"))


def _contract() -> dict:
    return json.loads(CONTRACT.read_text("utf-8"))


def test_the_shared_example_exists_and_shows_both_shapes() -> None:
    """A contract that only showed a server would have left the CLI shape - the one that was
    broken - undocumented."""
    assert CONTRACT.is_file(), CONTRACT
    examples = _contract()["examples"]
    assert set(examples) == {"server", "binds_nothing"}
    assert examples["server"]["port"] >= MIN_PORT
    assert examples["binds_nothing"]["port"] == NO_PORT


def test_the_shared_example_is_what_the_templates_actually_send() -> None:
    """The file the device's test reads must be the file this factory produces, or it proves
    something about a document nobody ships."""
    examples = _contract()["examples"]
    assert examples["server"] == _manifest("static-page")
    assert examples["binds_nothing"] == _manifest("cli-tool")


#: A minimal spec per template - enough for the real generator to render a real project.
_SPECS: dict[str, dict] = {
    "static-page": {
        "name": "Tanitim",
        "kind": "web_static",
        "template": "static-page",
        "page_title": "Tanitim",
        "page_heading": "Merhaba",
        "page_body": "Tek sayfa.",
    },
    "task-tracker": {
        "name": "Notlarim",
        "kind": "web_static",
        "template": "task-tracker",
        "entities": [{"name": "not", "fields": [{"name": "baslik", "type": "text"}]}],
        "screens": [{"name": "liste", "entity": "not"}],
    },
    "cli-tool": {
        "name": "Selamla",
        "kind": "cli",
        "template": "cli-tool",
        "commands": [{"name": "selamla", "description": "Selam verir"}],
    },
}


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_every_generated_project_passes_the_policy_it_will_be_judged_by(template: str) -> None:
    """The real generator, the real validator - not a restatement of the fixture.

    This test had a ``pytest.skip`` hedge in its first draft ("covered by the suite"), which
    is the pattern this whole batch exists to remove: a guard that goes green when it cannot
    run its subject guards nothing.
    """
    spec = AppSpec.model_validate(_SPECS[template])
    files = DeterministicAppGenerator().generate(spec)
    manifest = validate_manifest(files)
    assert manifest == _manifest(template)


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_no_template_carries_a_brace_the_device_refuses(template: str) -> None:
    """The defect itself. ``{`` and ``}`` are composition characters to the device and are
    refused anywhere in a command - which is every command two of these templates shipped."""
    manifest = _manifest(template)
    for section in ("run", "test"):
        for key, command in (manifest.get(section) or {}).items():
            assert "{" not in command and "}" not in command, f"{template}.{section}.{key}"


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_every_template_carries_the_fields_the_device_requires(template: str) -> None:
    manifest = _manifest(template)
    assert isinstance(manifest.get("entry"), str) and manifest["entry"]
    assert isinstance(manifest.get("run"), dict) and manifest["run"]
    port = manifest.get("port")
    assert isinstance(port, int) and not isinstance(port, bool)
    assert port == NO_PORT or MIN_PORT <= port <= MAX_PORT


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_every_template_command_is_on_the_allowlist(template: str) -> None:
    manifest = _manifest(template)
    for key, command in manifest["run"].items():
        assert command in ALLOWED_RUN_COMMANDS, f"{template}.run.{key}"
    for key, command in (manifest.get("test") or {}).items():
        assert command in ALLOWED_TEST_COMMANDS, f"{template}.test.{key}"


def test_a_project_that_binds_nothing_does_not_ask_for_a_port() -> None:
    """``port: 0`` and ``<port>`` in a command would be a manifest contradicting itself."""
    cli = _manifest("cli-tool")
    assert cli["port"] == NO_PORT
    assert all(PORT_PLACEHOLDER not in command for command in cli["run"].values())


def test_the_placeholder_is_spelled_the_device_s_way() -> None:
    assert PORT_PLACEHOLDER == _contract()["rules"]["port_placeholder"] == "<port>"
    served = _manifest("static-page")["run"]["serve"]
    assert PORT_PLACEHOLDER in served


def test_the_contract_lists_the_characters_the_device_refuses() -> None:
    forbidden = set(_contract()["rules"]["forbidden_in_commands"])
    assert {"{", "}"} <= forbidden, "the braces that caused this are not even written down"
