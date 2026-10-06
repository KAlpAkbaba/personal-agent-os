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


def _integration_conftest():
    from tests.integration import conftest

    return conftest


def test_two_runs_fit_on_the_server_only_when_their_connections_do() -> None:
    """2026-10-04: two integration runs on two gate databases of the dev server (max_connections
    300) both died on `too many clients already`; one run alone peaked at 231 connections.
    The suite takes one of a server-wide number of run slots, and that number is what the
    server's connections hold - one on today's dev server, more on a larger one."""
    conftest = _integration_conftest()
    assert conftest._RUN_CONNECTION_BUDGET >= 231, "below the peak measured on 2026-10-04"
    assert conftest.server_run_slots(max_connections=300, reserved=3) == 1
    assert conftest.server_run_slots(max_connections=100, reserved=3) == 1
    budget = conftest._RUN_CONNECTION_BUDGET
    room = 3 + conftest._SERVER_HEADROOM_CONNECTIONS
    assert conftest.server_run_slots(max_connections=room + 2 * budget, reserved=3) == 2
    assert conftest.server_run_slots(max_connections=room + 2 * budget - 1, reserved=3) == 1


def test_the_run_slot_is_held_in_the_server_wide_database_not_the_runs_own() -> None:
    """An advisory lock is per database: taken in the run's own `pagentos_gate_*` it would
    never meet the other run's. The slot is taken in the `postgres` maintenance database."""
    conftest = _integration_conftest()
    url = "postgresql+psycopg://u@127.0.0.1:5432/pagentos_gate_20261004000000_x?sslmode=disable"
    assert (
        conftest.server_lock_url(url)
        == "postgresql+psycopg://u@127.0.0.1:5432/postgres?sslmode=disable"
    )
    assert conftest.server_lock_url("postgresql://u@h/pagentos") == "postgresql://u@h/postgres"


def test_no_file_of_the_card_names_production() -> None:
    for relative in CARD_FILES:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        text = path.read_text("utf-8")
        for marker in PRODUCTION_MARKERS:
            assert marker not in text, f"{relative} names {marker!r}"
