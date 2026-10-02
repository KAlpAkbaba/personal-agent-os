"""The production compose file forwards the research rule's switch - and forwards it OFF.

ADR-0248: `research_execution_rule_enabled` puts the research start behind the
execution_target rule. `docker-compose.prod.yml` forwards only the variables it names, so
without a line for it the setting could not be turned on in production at all: the
inspector rendered `docker compose config` with the variable set and found it 0 times -
release-order step 4 ("set it in /opt/pagentos/.env and restart the api") would silently
have done nothing. The owner approved the line on 2026-10-02. The line changes nothing by
itself: unset on the host, the default it carries is the code's own default.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.config import Settings

REPO = Path(__file__).resolve().parents[4]
PROD = REPO / "infra" / "docker" / "docker-compose.prod.yml"
VARIABLE = "PAGENTOS_RESEARCH_EXECUTION_RULE_ENABLED"


def _api_environments() -> dict[str, dict[str, object]]:
    services = yaml.safe_load(PROD.read_text("utf-8"))["services"]
    found = {
        name: service["environment"]
        for name, service in services.items()
        if isinstance(service.get("environment"), dict)
        and "PAGENTOS_TEAM_STORE" in service["environment"]
    }
    assert found, "no service of the production compose file carries the api's environment"
    return found


def test_every_api_colour_is_handed_the_switch_from_the_hosts_environment() -> None:
    for name, environment in _api_environments().items():
        assert environment.get(VARIABLE) == "${" + VARIABLE + ":-false}", (
            f"{name}: the switch is not forwarded, or not with the code's default"
        )


def test_the_default_the_compose_file_carries_is_the_codes_own_default() -> None:
    """Unset on the host, the rule is OFF both ways: a release of this line changes nothing."""
    assert Settings().research_execution_rule_enabled is False
    assert Settings.model_config.get("env_prefix") == "PAGENTOS_"
    assert VARIABLE == "PAGENTOS_" + "research_execution_rule_enabled".upper()
