"""temporal-init-reaper: the temporal container runs under an init that reaps (ADR-0223 addendum 2).

The image's entrypoint runs `auto-setup.sh`, which leaves `setup_server &` behind and exits;
the entrypoint then execs `temporal-server` as pid 1. The orphan is re-parented to pid 1,
finishes two seconds after the server answers, and is never waited for: one defunct
`auto-setup.sh` per container start, which failed the first maintenance window's
"zombies = 0" by itself. `init: true` makes Docker's tini pid 1, and tini reaps it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
COMPOSE = {
    "prod": REPO / "infra" / "docker" / "docker-compose.prod.yml",
    "dev": REPO / "infra" / "docker" / "docker-compose.dev.yml",
}


@pytest.mark.parametrize("name", sorted(COMPOSE))
def test_the_temporal_service_runs_under_an_init_that_reaps(name: str) -> None:
    temporal = yaml.safe_load(COMPOSE[name].read_text("utf-8"))["services"]["temporal"]
    # `is True`, not truthy: compose rejects a string here, and "false" is truthy.
    assert temporal.get("init") is True, f"{name}: the temporal service has no `init: true`"


@pytest.mark.parametrize("name", sorted(COMPOSE))
def test_the_init_wraps_the_image_entrypoint_it_was_proven_with(name: str) -> None:
    """The proof on real Docker is for this image's own entrypoint under tini.

    An `entrypoint:` or `command:` override would change which process the orphan is
    re-parented to, and a new image may ship another entrypoint: either one is a new proof.
    """
    temporal = yaml.safe_load(COMPOSE[name].read_text("utf-8"))["services"]["temporal"]
    assert temporal["image"] == "temporalio/auto-setup:1.27.2"
    assert "entrypoint" not in temporal
    assert "command" not in temporal
