"""pilot-02: the lead's wiring of five worker packages into the shared files.

TEAM_PROTOCOL section 4: the lead writes the shared files at merge time. This file holds
what was wired: the allow-list is a bundled protocol file the Cloud Core really reads, the
cloud worker's container in the production compose carries the limits its own definition
names and starts only under its profile, its image is built on the Playwright the lock
resolves, and the team store is the file unless the setting says the database.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.config import Settings
from app.execution import allowlist
from app.main import create_app
from app.protocol_files import BUNDLED, protocol_file

REPO = Path(__file__).resolve().parents[4]
PROD = REPO / "infra" / "docker" / "docker-compose.prod.yml"
FRAGMENT = REPO / "infra" / "docker" / "cloud-browser" / "compose.fragment.yml"
DOCKERFILE = REPO / "infra" / "docker" / "cloud-browser" / "Dockerfile"
BROWSER_LOCK = REPO / "services" / "browser" / "uv.lock"
SHARED = REPO / "packages" / "protocol" / "browser-cloud-allowlist.json"

LIMITS = ("init", "shm_size", "mem_limit", "memswap_limit", "cpus", "pids_limit", "cap_drop")


def test_the_allow_list_is_read_from_the_bundle_the_image_carries() -> None:
    assert "browser-cloud-allowlist.json" in BUNDLED
    assert protocol_file("browser-cloud-allowlist.json").read_bytes() == SHARED.read_bytes()
    # The real read, through the bundle - not a monkeypatched list.
    assert allowlist.sites() == ()
    assert allowlist.acting_allowed("https://www.example.org/") == (
        False,
        "not_on_owner_allow_list",
    )


def test_the_cloud_worker_in_production_carries_the_limits_its_definition_names() -> None:
    prod = yaml.safe_load(PROD.read_text("utf-8"))["services"]["cloud-browser"]
    fragment = yaml.safe_load(FRAGMENT.read_text("utf-8"))["services"]["cloud-browser"]
    for name in LIMITS:
        assert prod[name] == fragment[name], name
    assert prod["security_opt"] == ["no-new-privileges:true"]
    assert prod["healthcheck"]["test"] == fragment["healthcheck"]["test"]
    assert "ports" not in prod, "the cloud worker publishes no socket"
    assert all(v.startswith("/mnt/pagentos-data/cloud-browser/") for v in prod["volumes"])


def test_the_cloud_worker_is_not_started_by_a_release() -> None:
    """Its profile is its own: the release script starts `bluegreen` and `aux`."""
    prod = yaml.safe_load(PROD.read_text("utf-8"))["services"]
    assert prod["cloud-browser"]["profiles"] == ["cloud-browser"]
    for name, service in prod.items():
        if name != "cloud-browser":
            assert "cloud-browser" not in (service.get("profiles") or []), name
    scripts = (REPO / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh").read_text("utf-8")
    assert "cloud-browser" not in scripts


def test_the_cloud_worker_dials_the_edge_like_every_other_device() -> None:
    prod = yaml.safe_load(PROD.read_text("utf-8"))["services"]["cloud-browser"]
    assert prod["environment"]["PAGENTOS_CLOUD_BROKER_URL"] == "http://edge:8001"
    nginx = (REPO / "infra" / "docker" / "edge" / "nginx.conf").read_text("utf-8")
    assert re.search(r"listen\s+8001;", nginx)


def test_the_image_is_built_on_the_playwright_the_lock_resolves() -> None:
    lock = BROWSER_LOCK.read_text("utf-8")
    locked = re.search(r'name = "playwright"\nversion = "([0-9.]+)"', lock)
    assert locked is not None
    tag = re.search(r"^ARG PLAYWRIGHT_TAG=v([0-9.]+)-noble$", DOCKERFILE.read_text("utf-8"), re.M)
    assert tag is not None and tag.group(1) == locked.group(1)


def test_the_team_store_is_the_file_unless_the_setting_says_the_database() -> None:
    assert Settings().team_store == "file"
    assert getattr(create_app(Settings()).state, "team_store", None) is None
    in_database = create_app(Settings(team_store="database"))
    assert type(in_database.state.team_store).__name__ == "DbStore"
    services = yaml.safe_load(PROD.read_text("utf-8"))["services"]
    for colour in ("api-blue", "api-green"):
        env = services[colour]["environment"]
        assert env["PAGENTOS_TEAM_STORE"] == "${PAGENTOS_TEAM_STORE:-file}", colour
