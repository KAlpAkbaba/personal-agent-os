"""The staging stack cannot reach production, the owner's accounts or the dev stack's data.

Owner's idea, 2026-10-03: a test team that tests "like the device owner" on a copy of the
real server - same code, its OWN database, test identities only - so nothing it does can
touch the owner's memory, ledger, devices or accounts. The copy is
infra/docker/docker-compose.staging.yml plus scripts/staging/*.ps1 (team/plans ADR
"staging-stack"). This file is the isolation proof, and it reads those files themselves:

* no production host, name or database anywhere (the tailnet address, `pagentos-core`, the
  `pagentos-prod*` containers, the `pagentos_prod` database, the tailnet DNS name);
* no setting that points at a real account (a mail/calendar host or address, a provider
  key) - and every `${...}` the compose interpolates is a `PAGENTOS_STAGING_*` name, so a
  stray `.env` or a shell that carries the owner's real key cannot leak one in;
* no volume shared with the dev stack the gate resets, no external volume, no bind mount;
* its own compose project, container names, ports, Temporal namespace + task queue and
  bucket - nothing that collides with the dev stack (the gate of 2026-10-03 22:29 was red
  because a gate and agents shared the dev stack's Temporal queue).

Each check is a function over the parsed file, and each is also run against a copy with
one violation planted, so the checker itself is proved to see what it is for.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[4]
DOCKER = REPO / "infra" / "docker"
STAGING = DOCKER / "docker-compose.staging.yml"
DEV = DOCKER / "docker-compose.dev.yml"
PROD = DOCKER / "docker-compose.prod.yml"
SCRIPTS = REPO / "scripts" / "staging"
SCRIPT_NAMES = ("up.ps1", "down.ps1", "deploy.ps1", "seed.ps1")

#: Production, by every name it is reachable or recognisable by.
PRODUCTION_MARKERS = (
    "100.90.158.26",  # the Cloud Core's tailnet address
    "pagentos-core",  # its tailnet host name (and the dev Temporal task queue's name)
    "tail0e6789",  # the tailnet's DNS suffix
    ".ts.net",
    "pagentos_prod",  # the production database
    "pagentos-prod",  # the production compose project / containers
    "/mnt/pagentos-data",  # the production data volume
    "/opt/pagentos",  # the production env file
)

#: Hosts and domains of real mail / calendar / identity accounts.
REAL_ACCOUNT_MARKERS = re.compile(
    r"gmail\.com|googlemail|google\.com|outlook\.|office365|hotmail|live\.com|icloud|"
    r"yahoo\.|yandex|protonmail|calendar\.google|graph\.microsoft",
    re.IGNORECASE,
)

#: Settings that would connect the api to a real account or a paid provider. In staging each
#: is absent, empty, or (for a key) an interpolation of a PAGENTOS_STAGING_* variable.
ACCOUNT_SETTINGS = re.compile(
    r"^PAGENTOS_(MAIL_|CALENDAR_ICS_URL|SMTP_|IMAP_|GOOGLE_|MICROSOFT_|GRAPH_|WEBPUSH_)"
)
KEY_SETTINGS = re.compile(r"(API_KEY|_TOKEN|_SECRET_KEY|_CLIENT_SECRET)$")
INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


# ----------------------------------------------------------------------------- reading


def _load(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"{path.relative_to(REPO)} is missing"
    return yaml.safe_load(path.read_text("utf-8"))


def _env(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(
            str(item).split("=", 1) if "=" in str(item) else (str(item), "") for item in env
        )
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def _host_ports(compose: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(service, bind address, host port) for every published port."""
    out = []
    for name, svc in (compose.get("services") or {}).items():
        for spec in svc.get("ports") or []:
            parts = str(spec).split(":")
            if len(parts) == 3:
                out.append((name, parts[0], parts[1]))
            elif len(parts) == 2:
                out.append((name, "", parts[0]))
            else:
                out.append((name, "", ""))
    return out


def _texts() -> dict[str, str]:
    texts = {"docker-compose.staging.yml": STAGING.read_text("utf-8")}
    for name in SCRIPT_NAMES:
        path = SCRIPTS / name
        assert path.is_file(), f"scripts/staging/{name} is missing"
        texts[f"scripts/staging/{name}"] = path.read_text("utf-8-sig")
    return texts


# ----------------------------------------------------------------------------- the checks
# Each returns a list of violations (empty = isolated), so the planted-violation tests below
# can prove the check sees what it is for.


def production_violations(texts: dict[str, str]) -> list[str]:
    found = []
    for name, text in texts.items():
        for marker in PRODUCTION_MARKERS:
            if marker.lower() in text.lower():
                found.append(f"{name}: names production ({marker})")
    return found


def account_violations(compose: dict[str, Any], texts: dict[str, str]) -> list[str]:
    found = []
    for name, text in texts.items():
        for match in REAL_ACCOUNT_MARKERS.finditer(text):
            found.append(f"{name}: names a real account domain ({match.group(0)})")
        for var in INTERPOLATION.findall(text if name.endswith(".yml") else ""):
            if not var.startswith("PAGENTOS_STAGING_"):
                found.append(
                    f"{name}: interpolates ${{{var}}} - only PAGENTOS_STAGING_* may reach staging"
                )
    for svc_name, svc in (compose.get("services") or {}).items():
        if svc.get("env_file"):
            found.append(
                f"{svc_name}: reads an env_file - staging's settings live in the compose file only"
            )
        for key, value in _env(svc).items():
            if (
                ACCOUNT_SETTINGS.match(key)
                and value.strip()
                and value.strip().lower() not in {"false", "0"}
            ):
                found.append(f"{svc_name}: {key}={value!r} connects a real account")
            if KEY_SETTINGS.search(key) and value.strip():
                # Either a literal staging-only constant, or ONLY PAGENTOS_STAGING_* variables.
                vars_ = INTERPOLATION.findall(value)
                literal_ok = not vars_ and value.strip().startswith("staging-only")
                if not literal_ok and not (
                    vars_ and all(v.startswith("PAGENTOS_STAGING_") for v in vars_)
                ):
                    found.append(
                        f"{svc_name}: {key} carries a key that is not a PAGENTOS_STAGING_* test key"
                    )
    return found


def volume_violations(
    compose: dict[str, Any], dev: dict[str, Any], prod: dict[str, Any]
) -> list[str]:
    found = []
    declared = compose.get("volumes") or {}
    other_names = set((dev.get("volumes") or {}).keys()) | set((prod.get("volumes") or {}).keys())
    for vol_name, spec in declared.items():
        spec = spec or {}
        real_name = str(spec.get("name", vol_name))
        if spec.get("external"):
            found.append(f"volume {vol_name}: external - may be another stack's data")
        if not real_name.startswith("pagentos-staging-"):
            found.append(f"volume {vol_name}: name {real_name!r} is not pagentos-staging-*")
        if vol_name in other_names or real_name in other_names or real_name.startswith("pagentos_"):
            found.append(f"volume {vol_name}: shares a name with the dev/prod stack ({real_name})")
    for svc_name, svc in (compose.get("services") or {}).items():
        for mount in svc.get("volumes") or []:
            source = str(
                mount.get("source", "") if isinstance(mount, dict) else str(mount).split(":", 1)[0]
            )
            if source not in declared:
                found.append(
                    f"{svc_name}: mounts {source!r}, which is not a staging-declared volume"
                )
    return found


def identity_violations(
    compose: dict[str, Any], dev: dict[str, Any], prod: dict[str, Any]
) -> list[str]:
    found = []
    if compose.get("name") != "pagentos-staging":
        found.append(f"compose project is {compose.get('name')!r}, not 'pagentos-staging'")
    other_ports = {port for _, _, port in _host_ports(dev) + _host_ports(prod)} | {"3000", "8001"}
    for svc_name, bind, port in _host_ports(compose):
        if bind != "127.0.0.1":
            found.append(f"{svc_name}: port {port} binds {bind or 'every interface'}, not loopback")
        if port in other_ports:
            found.append(f"{svc_name}: host port {port} collides with the dev/prod stack")
    dev_env: dict[str, str] = {}
    for svc in (dev.get("services") or {}).values():
        dev_env.update(_env(svc))
    for svc_name, svc in (compose.get("services") or {}).items():
        if not str(svc.get("container_name", "")).startswith("pagentos-staging-"):
            found.append(f"{svc_name}: container_name is not pagentos-staging-*")
        env = _env(svc)
        if svc_name == "api":
            for key, wanted in (
                ("PAGENTOS_TEMPORAL_NAMESPACE", "pagentos-staging"),
                ("PAGENTOS_TEMPORAL_TASK_QUEUE", "pagentos-staging"),
                ("PAGENTOS_S3_BUCKET", "pagentos-staging-artifacts"),
                ("PAGENTOS_ENVIRONMENT", "staging"),
            ):
                if env.get(key) != wanted:
                    found.append(f"api: {key}={env.get(key)!r}, expected {wanted!r}")
            url = env.get("PAGENTOS_DATABASE_URL", "")
            if "@postgres:5432/pagentos_staging" not in url:
                found.append(
                    f"api: database url {url!r} is not staging's own postgres/pagentos_staging"
                )
    return found


# ----------------------------------------------------------------------------- the real files


def test_staging_names_no_production_host() -> None:
    assert production_violations(_texts()) == []


def test_staging_connects_no_real_account() -> None:
    assert account_violations(_load(STAGING), _texts()) == []


def test_staging_shares_no_volume() -> None:
    assert volume_violations(_load(STAGING), _load(DEV), _load(PROD)) == []


def test_staging_is_its_own_stack() -> None:
    assert identity_violations(_load(STAGING), _load(DEV), _load(PROD)) == []


def test_staging_runs_the_real_api_and_web() -> None:
    services = _load(STAGING)["services"]
    for name in ("postgres", "redis", "minio", "temporal", "api", "web"):
        assert name in services, f"staging has no {name} service"
    assert services["api"]["build"]["context"] == "../../services/api"
    assert services["web"]["build"]["dockerfile"] == "infra/docker/web/Dockerfile"
    assert services["web"]["build"]["args"]["PAGENTOS_API_UPSTREAM"] == "http://api:8001"
    # Local mode works free: the simulator answers a voice session without a vendor key.
    assert _env(services["api"]).get("PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED") == "true"
    for name, svc in services.items():
        assert svc.get("mem_limit"), f"{name}: no mem_limit - the footprint must be bounded"


def test_deploy_refuses_a_sha_outside_main_and_the_lead_branch() -> None:
    text = (SCRIPTS / "deploy.ps1").read_text("utf-8-sig")
    assert "merge-base" in text and "--is-ancestor" in text
    assert "origin/main" in text and "team/nightly/lead" in text


def test_up_refuses_under_six_gigabytes_free() -> None:
    text = (SCRIPTS / "up.ps1").read_text("utf-8-sig")
    assert re.search(r"MinFreeMB\s*=\s*6144", text)


# ----------------------------------------------------------------------------- the checks see


def test_checker_catches_a_planted_production_host() -> None:
    texts = _texts()
    texts["docker-compose.staging.yml"] += (
        "\n# PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN: http://100.90.158.26:8001\n"
    )
    assert production_violations(texts)


def test_checker_catches_a_planted_real_account() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["environment"]["PAGENTOS_MAIL_IMAP_HOST"] = "imap.example.net"
    assert account_violations(compose, _texts())
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["environment"]["PAGENTOS_VOICE_OPENAI_API_KEY"] = (
        "${PAGENTOS_VOICE_OPENAI_API_KEY:-}"
    )
    assert account_violations(compose, _texts())


def test_checker_catches_a_planted_shared_volume() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["postgres"]["volumes"] = ["pagentos-postgres-data:/var/lib/postgresql/data"]
    assert volume_violations(compose, _load(DEV), _load(PROD))
    compose = copy.deepcopy(_load(STAGING))
    first = next(iter(compose["volumes"]))
    compose["volumes"][first] = {"name": "pagentos_pagentos-postgres-data", "external": True}
    assert volume_violations(compose, _load(DEV), _load(PROD))
