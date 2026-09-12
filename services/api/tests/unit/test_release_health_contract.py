"""The release scripts and this server must mean the same thing by "health".

The release gates read the health body with `grep`. That makes the body a WIRE CONTRACT with
two halves in two languages, and this repository has already paid for the shape of bug that
lives there: on 2026-09-11 the Cloud Core sent ``"test": "dotnet test ..."`` while the device
required an object, both suites green, because each half only ever restated the shape to
itself.

So these tests read the SCRIPTS - the real ones, not a copy - and hold the server to the
fields those scripts extract. A rename on either side fails here rather than in a release.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings
from app.release.version import release_model

REPO_ROOT = Path(__file__).resolve().parents[4]
BLUEGREEN = REPO_ROOT / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"
SINGLE = REPO_ROOT / "scripts" / "cloud" / "release-cloud-core.sh"


def _grepped_fields(script: Path) -> set[str]:
    """Every health-body field the script actually reads.

    Two shapes, because the scripts use both: a literal name inside a grep pattern
    (``grep -oE '"release":[[:space:]]*...'``), and a name passed as an argument to one of the
    ``*_field`` helpers (``served_schema_field "$body" head``), where the pattern itself is
    built from ``$2``.
    """
    text = script.read_text("utf-8")
    fields: set[str] = set()
    for line in text.splitlines():
        if "grep" in line and "-oE" in line:
            fields.update(re.findall(r'"([a-z_]+)":\[\[:space:\]\]', line))
            fields.update(re.findall(r'\\"([a-z_]+)\\":', line))
    fields.update(re.findall(r'_field\s+"[^"]*"\s+([a-z_]+)', text))
    return fields


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        # The blue/green path gates on the served release as well: "merged but not released"
        # is the incident it exists to catch.
        (BLUEGREEN, {"release", "version", "build_id", "schema", "current", "head"}),
        # The single-container path never had a release gate; it reads the schema and the
        # realtime contract version.
        (SINGLE, {"schema", "current", "head", "contract_version"}),
    ],
    ids=["bluegreen", "single"],
)
def test_the_script_actually_reads_the_body_with_named_fields(
    script: Path, expected: set[str]
) -> None:
    """A guard on the guard: if the extraction is rewritten in a way this parser cannot see,
    the tests below would silently check nothing."""
    fields = _grepped_fields(script)
    assert expected <= fields, (script.name, sorted(expected - fields), sorted(fields))


def test_the_release_block_carries_every_field_the_scripts_extract() -> None:
    model = release_model(Settings(_env_file=None))
    wanted = (_grepped_fields(BLUEGREEN) | _grepped_fields(SINGLE)) & {
        "version",
        "build_id",
        "component",
    }
    assert wanted, "the scripts no longer read anything out of the release block"
    for field in wanted:
        assert field in model, f"the release gate greps release.{field} and the model has none"


def test_the_schema_check_carries_every_field_the_scripts_extract() -> None:
    from app.release.schema import schema_state

    body = schema_state(
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@127.0.0.1:1/x")
    )
    wanted = (_grepped_fields(BLUEGREEN) | _grepped_fields(SINGLE)) & {
        "current",
        "head",
        "status",
    }
    assert {"current", "head"} <= wanted, "the release gate no longer reads both revisions"
    for field in wanted:
        assert field in body, f"the release gate greps schema.{field} and the check has none"


def test_the_version_field_is_still_the_first_one_in_the_release_block() -> None:
    """``served_release`` takes the FIRST ``"version"`` inside the release block, and its
    regex stops at the first ``}``. A field ordered before ``version``, or a nested object
    moved ahead of it, would make a release read somebody else's value and compare it to the
    sha - the check that exists precisely to catch "merged but not released".
    """
    keys = list(release_model(Settings(_env_file=None)))
    assert "version" in keys
    nested_before_version = [
        key
        for key in keys[: keys.index("version")]
        if isinstance(release_model(Settings(_env_file=None))[key], dict | list)
    ]
    assert not nested_before_version, (
        "a nested value now sits before `version` in the release block; the release gate's "
        f"regex stops at the first closing brace: {nested_before_version}"
    )
    assert keys.index("version") < keys.index("contracts")


def test_the_two_release_scripts_agree_on_their_exit_codes() -> None:
    """Both paths refuse the same two things; a person reading one log should not have to
    learn a second numbering."""
    for script, code, phrase in (
        (BLUEGREEN, "82", "migration FAILED"),
        (SINGLE, "82", "migration FAILED"),
        (BLUEGREEN, "78", "image build FAILED"),
        (SINGLE, "78", "image build FAILED"),
        (BLUEGREEN, "83", "schema revision"),
        (SINGLE, "83", "schema revision"),
    ):
        text = script.read_text("utf-8")
        assert phrase in text, (script.name, phrase)
        assert f"exit {code}" in text, (script.name, code)


@pytest.mark.parametrize("script", [BLUEGREEN, SINGLE], ids=["bluegreen", "single"])
def test_neither_release_script_can_lose_a_failure_in_a_pipe_again(script: Path) -> None:
    """The defect itself, pinned: `set -eu` without pipefail plus `cmd | tail` reported the
    status of `tail`, so `alembic upgrade head` could fail and the release went on."""
    text = script.read_text("utf-8")
    assert re.search(r"^set -eu -o pipefail$", text, re.M), script.name
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "alembic upgrade head" in stripped:
            assert "| tail" not in stripped, (
                f"{script.name}: the migration is piped again - its status would be the "
                f"pipe's: {stripped}"
            )
