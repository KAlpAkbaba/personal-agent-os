"""radicale-stack-ops: the owner's own CalDAV server, as a container part (ADR in
team/plans/radicale-stack-ops-adr.md until the lead numbers it).

The calendar layer (app/calendar, `CalDavCalendarProvider`) has had no account to talk to.
Radicale gives it one on the owner's own Cloud Core. These tests pin the shape the card
promised, read from the files themselves:

* the FRAGMENT publishes no port at all (the api reaches it over the compose network), runs
  without capabilities, with no-new-privileges, a read-only root, a healthcheck, and exactly
  the two binds the backup script reads;
* the image is built from the Dockerfile in this repository (its base pinned by digest), or a
  registry image pinned by digest - one of the two, nothing else;
* the dev stack binds it to 127.0.0.1:15232 only, without colliding with another service;
* the config file is htpasswd + bcrypt, owner_only, on 0.0.0.0:5232;
* staging never names it (it would be a real account there);
* the uid that owns /data is ONE number in the Dockerfile, the installer and the restore
  (contract halves must read each other);
* and the production compose either does not have a `radicale` service yet (the wiring card,
  whose full text is in the ADR, adds it) or has it EQUAL to the fragment.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
DOCKER = REPO / "infra" / "docker"
RADICALE = DOCKER / "radicale"
FRAGMENT = RADICALE / "compose.fragment.yml"
DOCKERFILE = RADICALE / "Dockerfile"
CONFIG = RADICALE / "config"
ENTRYPOINT = RADICALE / "entrypoint.sh"
DEV = DOCKER / "docker-compose.dev.yml"
PROD = DOCKER / "docker-compose.prod.yml"
STAGING = DOCKER / "docker-compose.staging.yml"
CLOUD = REPO / "scripts" / "cloud"

#: The contract with radicale-caldav-live (the api half), word for word from the card.
PROD_BINDS = [
    "/mnt/pagentos-data/radicale:/data",
    "/mnt/pagentos-data/radicale-auth/users:/etc/radicale/users:ro",
]
DEV_PORT = "127.0.0.1:15232:5232"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text("utf-8"))


def _fragment() -> dict[str, Any]:
    return _load(FRAGMENT)["services"]["radicale"]


def _dev() -> dict[str, Any]:
    return _load(DEV)["services"]["radicale"]


# ---- the fragment ---------------------------------------------------------------------------


def test_the_fragment_is_one_radicale_service_and_says_it_is_a_fragment() -> None:
    document = _load(FRAGMENT)
    assert list(document["services"]) == ["radicale"]
    head = FRAGMENT.read_text("utf-8").split("services:", 1)[0]
    assert "FRAGMENT" in head, "the header must say this file is not compose-complete"


def test_the_fragment_publishes_no_port_at_all() -> None:
    """Not on the tailnet, not on loopback: the api is its only client, over the compose
    network. `expose` would be harmless but is not needed either."""
    service = _fragment()
    assert "ports" not in service, f"the fragment publishes {service.get('ports')!r}"
    assert "network_mode" not in service, "host networking would publish every port"


def test_the_fragment_runs_hardened() -> None:
    service = _fragment()
    assert service["container_name"] == "pagentos-prod-radicale"
    assert service["restart"] == "unless-stopped"
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp"]
    assert service["mem_limit"] == "256m"
    assert service["pids_limit"] == 256
    assert "privileged" not in service


def test_the_fragment_binds_exactly_the_two_contract_paths() -> None:
    """The backup reads /mnt/pagentos-data/radicale directly and the installer writes the
    users file there: a named volume or a third bind would leave one of them looking at the
    wrong place."""
    assert _fragment()["volumes"] == PROD_BINDS


def test_the_fragment_has_a_healthcheck_that_counts_401_as_alive() -> None:
    check = _fragment()["healthcheck"]
    test = check["test"]
    assert test[0] == "CMD", "exec form: the image has no shell-dependent curl"
    body = " ".join(test)
    assert "http://127.0.0.1:5232/" in body
    assert "401" in body, "an unauthenticated GET answers 401 and that is a healthy server"
    assert (check["interval"], check["timeout"], check["retries"], check["start_period"]) == (
        "30s",
        "5s",
        3,
        "20s",
    )


def _image_is_pinned_or_built_here(service: dict[str, Any], compose_file: Path) -> str:
    """Exactly one of two ways: a registry image pinned by digest, or a build from the
    Dockerfile in infra/docker/radicale (whose own base is pinned by digest)."""
    build = service.get("build")
    if build is None:
        image = service.get("image", "")
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"image {image!r} is not pinned"
        return "registry"
    assert isinstance(build, dict), "build must be a context block"
    context = (compose_file.parent / build["context"]).resolve()
    assert context == RADICALE.resolve(), f"build context {context} is not infra/docker/radicale"
    dockerfile = (context / build.get("dockerfile", "Dockerfile")).resolve()
    assert dockerfile == DOCKERFILE.resolve()
    base = re.search(r"^FROM\s+(\S+)", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
    assert base and re.search(r"@sha256:[0-9a-f]{64}$", base.group(1)), (
        f"the Dockerfile's base {base.group(1) if base else None!r} is not pinned by digest"
    )
    return "built"


def test_the_fragment_image_is_pinned_or_built_from_this_repository() -> None:
    assert _image_is_pinned_or_built_here(_fragment(), FRAGMENT) in {"registry", "built"}


def test_the_dockerfile_pins_every_package_and_runs_as_a_user() -> None:
    text = DOCKERFILE.read_text("utf-8")
    assert re.search(r"radicale==\d+\.\d+\.\d+", text)
    assert re.search(r"bcrypt==\d+\.\d+\.\d+", text)
    assert "--no-deps" in text and "pip check" in text, "the closure is pinned, not resolved"
    assert re.search(r"^USER\s+radicale\s*$", text, re.MULTILINE), "never root at run time"
    assert "COPY entrypoint.sh" in text and "COPY config" in text


# ---- the dev stack --------------------------------------------------------------------------


def test_dev_binds_radicale_to_loopback_15232_only() -> None:
    service = _dev()
    assert service["ports"] == [DEV_PORT]
    assert service["environment"]["PAGENTOS_RADICALE_DEV_PASSWORD"] == "dev-takvim"
    assert service["volumes"] == ["pagentos-radicale-data:/data"]
    assert "pagentos-radicale-data" in _load(DEV)["volumes"]
    assert service["read_only"] is True
    assert "/etc/radicale:uid=10002,gid=10002,mode=0700" in service["tmpfs"]
    assert _image_is_pinned_or_built_here(service, DEV) in {"registry", "built"}
    assert service["image"] == _fragment()["image"], "dev runs the same image the fragment does"
    assert service["healthcheck"] == _fragment()["healthcheck"]


def test_dev_radicale_port_collides_with_no_other_service() -> None:
    host_ports: dict[str, str] = {}
    for name, service in _load(DEV)["services"].items():
        for mapping in service.get("ports", []):
            parts = str(mapping).split(":")
            assert parts[0] == "127.0.0.1", f"{name} publishes {mapping} beyond loopback"
            assert parts[1] not in host_ports, (
                f"{name} and {host_ports.get(parts[1])} share {parts[1]}"
            )
            host_ports[parts[1]] = name
    assert host_ports["15232"] == "radicale"
    header = DEV.read_text("utf-8").split("\nservices:", 1)[0]
    assert re.search(r"Radicale.*127\.0\.0\.1:15232 -> 5232", header), "the port table has no row"


# ---- the config, the entrypoint, staging ---------------------------------------------------


def test_the_config_is_htpasswd_bcrypt_owner_only() -> None:
    parser = configparser.ConfigParser()
    parser.read_string(CONFIG.read_text("utf-8"))
    assert parser["server"]["hosts"] == "0.0.0.0:5232"
    assert parser["auth"]["type"] == "htpasswd"
    assert parser["auth"]["htpasswd_filename"] == "/etc/radicale/users"
    assert parser["auth"]["htpasswd_encryption"] == "bcrypt"
    assert parser["rights"]["type"] == "owner_only"
    assert parser["storage"]["filesystem_folder"] == "/data"
    assert parser["web"]["type"] == "none"


def test_the_entrypoint_never_starts_radicale_without_a_users_file() -> None:
    """Docker creates a DIRECTORY where a bind source is missing. The entrypoint must stop
    rather than let Radicale start with an empty user list."""
    text = ENTRYPOINT.read_text("utf-8")
    assert text.startswith("#!/bin/sh")
    assert '[ -f "$users" ]' in text
    assert "PAGENTOS_RADICALE_DEV_PASSWORD" in text
    assert "exit 1" in text
    assert "exec radicale --config /usr/local/etc/radicale/config" in text
    assert "\r\n" not in ENTRYPOINT.read_bytes().decode("utf-8"), "a CRLF shebang does not run"


def test_staging_never_names_radicale() -> None:
    assert "radicale" not in STAGING.read_text("utf-8").lower()


def test_one_uid_owns_the_calendar_everywhere() -> None:
    """The Dockerfile's user, the installer's chown and the restore's chown are one number:
    if they drift, the container cannot write its own data after an install or a restore."""
    image = re.search(r"^ARG RADICALE_UID=(\d+)$", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
    install = re.search(
        r"^radicale_uid=(\d+)$", (CLOUD / "install-radicale.sh").read_text("utf-8"), re.MULTILINE
    )
    restore = re.search(
        r"^radicale_uid=\$\{PAGENTOS_RADICALE_UID:-(\d+)\}$",
        (CLOUD / "restore-cloud-core.sh").read_text("utf-8"),
        re.MULTILINE,
    )
    assert image and install and restore
    dev_tmpfs = next(t for t in _dev()["tmpfs"] if t.startswith("/etc/radicale"))
    assert {image.group(1), install.group(1), restore.group(1)} == {"10002"}
    assert f"uid={image.group(1)}" in dev_tmpfs


# ---- the wiring card's half, waiting ---------------------------------------------------------


def _normalised(service: dict[str, Any], compose_file: Path) -> dict[str, Any]:
    copy = dict(service)
    if isinstance(copy.get("build"), dict):
        build = dict(copy["build"])
        build["context"] = str((compose_file.parent / build["context"]).resolve())
        copy["build"] = build
    return copy


@pytest.mark.parametrize("compose_file", [PROD], ids=["prod"])
def test_production_radicale_is_the_fragment_once_wired(compose_file: Path) -> None:
    """The wiring card (radicale-prod-wire; its full text is in the ADR) copies the fragment
    into the production compose. Until then this passes as "not wired yet"; from then on it
    demands equality, so the fragment and production cannot drift apart unseen."""
    services = _load(compose_file)["services"]
    if "radicale" not in services:
        # Not wired yet: nothing in production may reach for it either.
        assert "radicale:5232" not in compose_file.read_text("utf-8")
        return
    assert _normalised(services["radicale"], compose_file) == _normalised(_fragment(), FRAGMENT)
