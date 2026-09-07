"""Unit tests: the version model (M18.4 spec §2) - what is running, by name, never guessed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import __version__
from app.actions.receipt import ACTION_CONTRACT_VERSION
from app.config import Settings
from app.main import create_app
from app.release.version import COMPONENT_CLOUD_CORE, STARTED_AT, contract_versions, release_model
from app.uistate.contract import CONTRACT_VERSION as UI_STATE_CONTRACT_VERSION


def test_an_unset_release_is_reported_as_unknown_not_guessed() -> None:
    model = release_model(Settings(_env_file=None))
    assert model["component"] == COMPONENT_CLOUD_CORE
    assert model["version"] == "unknown"
    assert model["version_source"] == "unknown"
    assert model["last_known_good"] is None
    assert model["last_known_good_source"] == "unknown"
    assert model["app_version"] == __version__


def test_the_release_and_last_known_good_come_from_the_host_env(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_RELEASE", "65459a4")
    monkeypatch.setenv("PAGENTOS_LAST_KNOWN_GOOD", "8f3e005")
    model = release_model(Settings(_env_file=None))
    assert (model["version"], model["version_source"]) == ("65459a4", "env")
    assert (model["last_known_good"], model["last_known_good_source"]) == ("8f3e005", "env")


def test_contract_versions_are_read_from_their_owners() -> None:
    contracts = contract_versions()
    assert contracts["action"] == ACTION_CONTRACT_VERSION
    assert contracts["ui_state"] == UI_STATE_CONTRACT_VERSION
    assert set(contracts) == {"action", "ui_state", "ambient", "voice_qualification"}


def test_uptime_is_measured_from_process_start() -> None:
    later = STARTED_AT + timedelta(seconds=90)
    model = release_model(Settings(_env_file=None), now=later)
    assert model["uptime_s"] == 90.0
    assert model["started_at"] == STARTED_AT.isoformat().replace("+00:00", "Z")
    assert datetime.fromisoformat(model["started_at"].replace("Z", "+00:00")).tzinfo is not None
    assert STARTED_AT.tzinfo is UTC


def test_the_health_manifest_carries_the_release_model(monkeypatch) -> None:
    monkeypatch.setenv("PAGENTOS_RELEASE", "65459a4")
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/system/health").json()
    assert body["release"]["component"] == COMPONENT_CLOUD_CORE
    assert body["release"]["version"] == "65459a4"
    assert body["release"]["contracts"]["action"] == ACTION_CONTRACT_VERSION
    assert body["version"] == __version__
