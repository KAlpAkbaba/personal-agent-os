"""Unit tests: isolation, capability-scoped access, resource budgets and the
self-extension recursion limit (ACCEPTANCE_TESTS M7 "Isolation, supply chain,
resources"; SECURITY_MODEL M7)."""

import os

import pytest

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.resources import (
    ENV_ALLOWLIST,
    MAX_EVOLUTION_DEPTH,
    SECRET_ENV_PREFIXES,
    SECRET_ENV_SUFFIXES,
    EvolutionDepth,
    ResourceBudget,
    assert_no_secrets,
    build_isolated_env,
    directory_size_bytes,
    enforce_disk_budget,
    enforce_output_budget,
    permission_findings,
    require_scoped_access,
    require_within_depth,
    secret_names,
)
from app.evolution.skills import SKILL_DIR_ENV

# A realistic slice of what the API process actually carries.
PRODUCTION_SECRETS = {
    "PAGENTOS_DATABASE_URL": "postgresql://user:pw@localhost/db",
    "DATABASE_URL": "postgresql://user:pw@localhost/db",
    "POSTGRES_PASSWORD": "hunter2",
    "REDIS_URL": "redis://localhost:6379",
    "S3_SECRET_ACCESS_KEY": "abc",
    "AWS_SESSION_TOKEN": "abc",
    "MINIO_ROOT_PASSWORD": "abc",
    "ANTHROPIC_API_KEY": "sk-ant-x",
    "OPENAI_API_KEY": "sk-x",
    "ELEVENLABS_API_KEY": "x",
    "TEMPORAL_ADDRESS": "localhost:7233",
    "GITHUB_TOKEN": "ghp_x",
    "SOME_SERVICE_CREDENTIALS": "x",
    "APP_DSN": "x",
}


# ------------------------------------------------ no production secrets


def test_isolated_env_is_allowlist_built(tmp_path, monkeypatch) -> None:
    for name, value in PRODUCTION_SECRETS.items():
        monkeypatch.setenv(name, value)
    env = build_isolated_env(tmp_path, skill_dir_var=SKILL_DIR_ENV)
    assert set(env) <= {SKILL_DIR_ENV, "PYTHONUTF8", *ENV_ALLOWLIST}
    for name in PRODUCTION_SECRETS:
        assert name not in env
    assert env[SKILL_DIR_ENV] == str(tmp_path)


@pytest.mark.parametrize("name", sorted(PRODUCTION_SECRETS))
def test_the_denylist_catches_every_production_secret(name) -> None:
    with pytest.raises(EvolutionError) as excinfo:
        assert_no_secrets({name: "value"})
    assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED
    assert name in excinfo.value.details["variables"]


def test_the_skill_locator_is_the_only_pagentos_exemption() -> None:
    assert secret_names({SKILL_DIR_ENV: "x"}) == []
    assert secret_names({"PAGENTOS_ROLLOUT_CASES": "x"}) == []
    assert secret_names({"PAGENTOS_OWNER_TOKEN": "x"}) == ["PAGENTOS_OWNER_TOKEN"]


def test_denylist_covers_prefixes_and_suffixes() -> None:
    assert "PAGENTOS_" in SECRET_ENV_PREFIXES
    assert "_KEY" in SECRET_ENV_SUFFIXES
    assert secret_names({"WHATEVER_TOKEN": "x"}) == ["WHATEVER_TOKEN"]


def test_allowlist_names_are_not_secret_bearing(monkeypatch) -> None:
    for name in ENV_ALLOWLIST:
        monkeypatch.setenv(name, "x")
    assert secret_names(dict.fromkeys(ENV_ALLOWLIST, "x")) == []


def test_extra_env_cannot_smuggle_a_secret(tmp_path) -> None:
    env = build_isolated_env(tmp_path, skill_dir_var=SKILL_DIR_ENV)
    env["ANTHROPIC_API_KEY"] = "sk-ant-x"
    with pytest.raises(EvolutionError):
        assert_no_secrets(env)


# ------------------------------------------- capability-scoped access


PURE_SOURCE = "import json\nimport sys\n\n\ndef run(payload):\n    return {}\n"


def test_deny_by_default_blocks_every_permission_class() -> None:
    for source, permission in (
        ("import socket\n", "network_permissions"),
        ("from urllib import request\n", "network_permissions"),
        ("import pathlib\n", "filesystem_permissions"),
        ("data = open('x')\n", "filesystem_permissions"),
        ("import ctypes\n", "device_permissions"),
        ("import winreg\n", "device_permissions"),
        ("token = os.environ['X']\n", "secret_requirements"),
    ):
        findings = permission_findings(source, {})
        assert findings, source
        assert permission in {f["permission"] for f in findings}
        with pytest.raises(EvolutionError) as excinfo:
            require_scoped_access(source, {})
        assert excinfo.value.error_class == EvolutionErrorClass.PERMISSION_DENIED


def test_a_pure_generated_skill_needs_no_grants() -> None:
    assert permission_findings(PURE_SOURCE, {}) == []
    require_scoped_access(PURE_SOURCE, {})


def test_an_explicit_grant_scopes_the_access() -> None:
    source = "import socket\n"
    assert permission_findings(source, {"network_permissions": ["api.example.com"]}) == []
    # ...but only for the class that was granted.
    assert permission_findings("import ctypes\n", {"network_permissions": ["a.example.com"]})


# ------------------------------------------------------------- budgets


def test_budget_defaults_are_configurable_and_reported() -> None:
    budget = ResourceBudget()
    payload = budget.to_dict()
    for key in (
        "timeout_s",
        "cpu_seconds",
        "memory_mb",
        "disk_mb",
        "max_output_bytes",
        "network",
        "retry_limit",
        "max_depth",
    ):
        assert key in payload
    assert payload["network"] is False
    assert payload["cpu_memory_enforced_by"] in ("rlimit", "wall_clock")


def test_disk_budget_is_enforced(tmp_path) -> None:
    (tmp_path / "small.txt").write_text("x" * 1024, encoding="utf-8")
    budget = ResourceBudget(disk_mb=1)
    assert enforce_disk_budget(tmp_path, budget) == directory_size_bytes(tmp_path)

    (tmp_path / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    with pytest.raises(EvolutionError) as excinfo:
        enforce_disk_budget(tmp_path, budget)
    assert excinfo.value.error_class == EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED


def test_output_budget_is_enforced() -> None:
    budget = ResourceBudget(max_output_bytes=16)
    assert enforce_output_budget("short", budget) == "short"
    with pytest.raises(EvolutionError) as excinfo:
        enforce_output_budget("x" * 64, budget)
    assert excinfo.value.error_class == EvolutionErrorClass.RESOURCE_BUDGET_EXCEEDED


def test_rlimit_preexec_matches_the_platform() -> None:
    preexec = ResourceBudget().rlimit_preexec()
    if hasattr(os, "fork"):  # pragma: no cover - POSIX
        assert callable(preexec)
    else:
        assert preexec is None  # Windows: the wall-clock timeout is the backstop


# ----------------------------------------------------------- recursion


def test_depth_limit_stops_agent_to_agent_capability_creation() -> None:
    assert require_within_depth(0) == 0
    assert require_within_depth(MAX_EVOLUTION_DEPTH) == MAX_EVOLUTION_DEPTH
    with pytest.raises(EvolutionError) as excinfo:
        require_within_depth(MAX_EVOLUTION_DEPTH + 1)
    assert excinfo.value.error_class == EvolutionErrorClass.RECURSION_LIMIT_EXCEEDED
    assert excinfo.value.details["limit"] == MAX_EVOLUTION_DEPTH


def test_depth_limit_is_configurable() -> None:
    budget = ResourceBudget(max_depth=0)
    assert require_within_depth(0, budget) == 0
    with pytest.raises(EvolutionError):
        require_within_depth(1, budget)


def test_depth_is_carried_through_the_chain() -> None:
    root = EvolutionDepth()
    first = root.child("gap-1")
    second = first.child("gap-2")
    assert (root.depth, first.depth, second.depth) == (0, 1, 2)
    assert second.origin_gap_id == "gap-1"
    assert second.chain == ["gap-1", "gap-2"]
    assert second.to_dict()["depth"] == 2


def test_negative_or_non_integer_depth_is_refused() -> None:
    for bad in (-1, "1", None, 1.5):
        with pytest.raises(EvolutionError):
            require_within_depth(bad)  # type: ignore[arg-type]
