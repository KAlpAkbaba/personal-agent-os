"""The gate's own database, the contract between its two halves (team/plans/gate-faster-adr.md).

scripts/quality-gate.ps1 points its PostgreSQL steps at a database of their own by setting ONE
environment variable for those steps' children. That only works while the variable is one the
application's Settings really read for the database. The two halves live in two languages and
neither suite would notice the other drifting, so this test reads the gate's source for the
name and asks the Settings class - never a second copy of the name typed here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[4]
GATE = REPO_ROOT / "scripts" / "quality-gate.ps1"

#: The files of the card (team/plans/gate-faster-adr.md). None may name production.
CARD_FILES = (
    "scripts/quality-gate.ps1",
    "scripts/lib/GateDatabase.ps1",
    "scripts/tests/gate-database.tests.ps1",
    "scripts/lib/GateSteps.ps1",
    "scripts/tests/gate-steps.tests.ps1",
    ".github/workflows/ci.yml",
    "services/api/tests/integration/conftest.py",
    "services/api/tests/unit/test_gate_database_contract.py",
    "team/plans/gate-faster-adr.md",
)

#: Built from parts so this file does not name what it forbids.
PRODUCTION_MARKERS = ("pagentos" + "-prod", "100" + ".90.", "pagentos" + "-core")


def _gate_text() -> str:
    return GATE.read_text("utf-8")


def gate_variable(text: str) -> str:
    match = re.search(r'\$script:GateDatabaseVariable\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"', text)
    assert match, (
        "scripts/quality-gate.ps1 no longer names its database variable "
        "in $script:GateDatabaseVariable"
    )
    return match.group(1)


def settings_database_url_from(variable: str, value: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(variable, value)
    return Settings(_env_file=None).database_url  # type: ignore[call-arg]


def test_the_variable_the_gate_sets_is_the_one_settings_read_for_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable = gate_variable(_gate_text())
    probe = "postgresql+psycopg://user@127.0.0.1:1/pagentos_gate_contract_probe"
    assert settings_database_url_from(variable, probe, monkeypatch) == probe


def test_the_check_would_catch_a_variable_settings_do_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The falsification: a misspelt variable leaves the setting at its default."""
    probe = "postgresql+psycopg://user@127.0.0.1:1/pagentos_gate_contract_probe"
    monkeypatch.delenv(gate_variable(_gate_text()), raising=False)
    assert settings_database_url_from("PAGENTOS_DATABASE", probe, monkeypatch) != probe


def test_the_gate_sets_the_variable_only_through_the_step_scoped_helper() -> None:
    """A plain `$env:X = ...` in the gate would hold for every later step too."""
    text = _gate_text()
    variable = gate_variable(text)
    assert not re.search(r"\$env:" + re.escape(variable) + r"\s*=", text, re.IGNORECASE)
    assert "Invoke-WithGateDatabase -Variable $script:GateDatabaseVariable" in text


def test_no_file_of_the_card_names_production() -> None:
    for relative in CARD_FILES:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        text = path.read_text("utf-8")
        for marker in PRODUCTION_MARKERS:
            assert marker not in text, f"{relative} names {marker!r}"
