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
import ipaddress
import os
import re
from pathlib import Path
from typing import Any

import pytest
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
    r"yahoo\.|yandex|protonmail|proton\.me|calendar\.google|graph\.microsoft|fastmail|"
    r"zoho|gmx\.|mail\.ru|caldav\.|carddav\.|nextcloud|radicale",
    re.IGNORECASE,
)

#: Settings that would connect the api to a real account or a paid provider. In staging each
#: is absent, empty, or (for a key) an interpolation of a PAGENTOS_STAGING_* variable.
ACCOUNT_SETTINGS = re.compile(
    r"^PAGENTOS_(MAIL_|CALDAV_|CALENDAR_ICS_URL|SMTP_|IMAP_|GOOGLE_|MICROSOFT_|GRAPH_|WEBPUSH_)"
)
KEY_SETTINGS = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$")
#: The same idea on the api's RESOLVED Settings (field names, any position): every such str
#: field is empty, a `staging-only*` literal, or set from a PAGENTOS_STAGING_* variable.
SECRET_FIELD = re.compile(r"key|secret|password|passwd|token|credential", re.IGNORECASE)
#: Settings fields that bind the api to an account; in staging they stay empty.
ACCOUNT_FIELD = re.compile(
    r"^(caldav_|calendar_ics_url$|mail_(imap|smtp)_(host|user|password)$|mail_from$)"
)
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


def _staging_key_only(raw: str) -> bool:
    """`${PAGENTOS_STAGING_X}` / `${PAGENTOS_STAGING_X:-}` and nothing else: no literal around
    it and no default that could itself be a key."""
    refs = list(re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}", raw))
    return (
        bool(refs)
        and all(m.group(1).startswith("PAGENTOS_STAGING_") and not m.group(2) for m in refs)
        and not re.sub(r"\$\{[^}]*\}", "", raw).strip()
    )


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
                literal_ok = not INTERPOLATION.findall(value) and value.strip().startswith(
                    "staging-only"
                )
                if not literal_ok and not _staging_key_only(value):
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
    assert production_violations(_blackhole_lines_removed(_texts())) == []


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
    # Only the remote's refs: a hand-made local branch named `main` must not wave a sha through.
    refs = re.search(r"\$allowedRefs\s*=\s*@\(([^)]*)\)", text)
    assert refs, "deploy.ps1 has no $allowedRefs list"
    assert re.findall(r'"([^"]+)"', refs.group(1)) == ["origin/main", "origin/team/nightly/lead"]


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


# ----------------------------------------------------------------------------- what the api runs on
# The compose text alone missed a production url the api INHERITS from a Settings default
# (gods_eye_url = http://pagentos-core:4173/, inspector 2026-10-04): so the api's settings are
# built here from staging's own environment, exactly as the container builds them, and every
# value is searched. And a name is not the only way in: the container could reach the tailnet
# by address, so staging's network has to make the whole tailnet range unroutable.

#: Where staging sends a production NAME: TEST-NET-1 (RFC 5737), routed nowhere.
BLACKHOLE = "192.0.2.1"
#: The tailnet's address range (CGNAT, RFC 6598): every tailnet device lives in it.
TAILNET_RANGE = ipaddress.ip_network("100.64.0.0/10")
PRODUCTION_NAMES = ("pagentos-core", "pagentos-core.tail0e6789.ts.net")
_DEFAULT = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::?-([^}]*))?\}")


def _effective_settings(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The api's Settings as the staging container builds them: its env, no .env file."""
    from app.config import Settings

    for key in list(os.environ):
        if key.upper().startswith("PAGENTOS_"):
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, _DEFAULT.sub(lambda m: m.group(1) or "", value))
    return Settings(_env_file=None).model_dump(mode="json")  # type: ignore[call-arg]


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(v, f"{path}.{k}" if path else k)]
    if isinstance(value, (list, tuple)):
        return [s for i, v in enumerate(value) for s in _strings(v, f"{path}[{i}]")]
    return []


def settings_violations(settings: dict[str, Any]) -> list[str]:
    return [
        f"settings.{field}={value!r} names production ({marker})"
        for field, value in _strings(settings)
        for marker in PRODUCTION_MARKERS
        if marker.lower() in value.lower()
    ]


def secret_violations(settings: dict[str, Any], env: dict[str, str]) -> list[str]:
    """Every secret-named or account field of the resolved Settings, whatever its env name."""
    found = []
    for field, value in settings.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if ACCOUNT_FIELD.match(field):
            found.append(f"settings.{field} is set - it connects a real account")
            continue
        if not SECRET_FIELD.search(field) or value.strip().startswith("staging-only"):
            continue
        if not _staging_key_only(env.get(f"PAGENTOS_{field.upper()}", "")):
            found.append(
                f"settings.{field} carries a secret that is neither staging-only nor a "
                "PAGENTOS_STAGING_* test key"
            )
    return found


def network_violations(compose: dict[str, Any]) -> list[str]:
    found = []
    networks = compose.get("networks") or {}
    default = networks.get("default") or {}
    subnets = [
        ipaddress.ip_network(str(c.get("subnet")), strict=False)
        for c in (default.get("ipam") or {}).get("config") or []
        if c.get("subnet")
    ]
    if not any(TAILNET_RANGE.subnet_of(s) for s in subnets if s.version == 4):  # type: ignore[arg-type]
        found.append(
            f"the default network's subnet {[str(s) for s in subnets]} does not cover the tailnet "
            f"{TAILNET_RANGE} - the containers could route to production by address"
        )
    if set(networks) - {"default"}:
        found.append(f"extra networks {sorted(set(networks) - {'default'})}")
    for svc_name, svc in (compose.get("services") or {}).items():
        if svc.get("network_mode"):
            found.append(
                f"{svc_name}: network_mode {svc['network_mode']!r} leaves staging's network"
            )
        nets = svc.get("networks")
        if nets and set(nets) != {"default"}:
            found.append(f"{svc_name}: joins {sorted(nets)}, not only staging's own network")
        hosts = {}
        for entry in svc.get("extra_hosts") or []:
            name, _, addr = str(entry).partition(":")
            hosts[name] = addr
        for name in PRODUCTION_NAMES:
            if hosts.get(name) != BLACKHOLE:
                found.append(f"{svc_name}: {name} is not sent to {BLACKHOLE} (extra_hosts)")
    return found


def _blackhole_lines_removed(texts: dict[str, str]) -> dict[str, str]:
    """The extra_hosts lines that send a production name to the blackhole are the one place a
    production name may be written; anything else on such a line still counts."""
    line = re.compile(
        r'^\s*-\s*"(?:'
        + "|".join(re.escape(n) for n in PRODUCTION_NAMES)
        + r"):"
        + re.escape(BLACKHOLE)
        + r'"\s*$',
        re.MULTILINE,
    )
    return {name: line.sub("", text) for name, text in texts.items()}


def test_staging_api_settings_name_no_production(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(_load(STAGING)["services"]["api"])
    assert settings_violations(_effective_settings(env, monkeypatch)) == []


def test_staging_network_cannot_reach_production() -> None:
    assert network_violations(_load(STAGING)) == []


def test_checker_catches_an_inherited_production_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(_load(STAGING)["services"]["api"])
    env.pop("PAGENTOS_GODS_EYE_URL", None)  # the Settings default is production's aux url
    assert settings_violations(_effective_settings(env, monkeypatch))


def test_checker_catches_a_route_to_the_tailnet() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["networks"]["default"]["ipam"]["config"] = [{"subnet": "172.30.0.0/16"}]
    assert network_violations(compose)
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["extra_hosts"] = ["pagentos-core:100.90.158.26"]
    assert network_violations(compose)
    texts = _texts()
    texts["docker-compose.staging.yml"] += '\n      - "pagentos-core:100.90.158.26"\n'
    assert production_violations(_blackhole_lines_removed(texts))


def test_staging_api_settings_carry_no_secret_or_account(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _env(_load(STAGING)["services"]["api"])
    assert secret_violations(_effective_settings(env, monkeypatch), env) == []


def test_checker_catches_a_planted_caldav_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inspector 2026-10-04: a real CalDAV account passed the name-based checks green.
    for key, value in (
        ("PAGENTOS_CALDAV_URL", "https://caldav.fastmail.com/dav/"),
        ("PAGENTOS_CALDAV_USER", "owner@fastmail.com"),
        ("PAGENTOS_CALDAV_PASSWORD", "hunter2-real"),
    ):
        compose = copy.deepcopy(_load(STAGING))
        compose["services"]["api"]["environment"][key] = value
        env = _env(compose["services"]["api"])
        assert account_violations(compose, _texts()), key
        assert secret_violations(_effective_settings(env, monkeypatch), env), key


def test_checker_catches_a_planted_vendor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inspector 2026-10-04: a hand-written Azure speech key passed green (_SPEECH_KEY was not
    # in the name list) - so the check reads every secret-named field of the resolved Settings.
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["environment"]["PAGENTOS_VOICE_AZURE_SPEECH_KEY"] = "0123" * 8
    env = _env(compose["services"]["api"])
    assert account_violations(compose, _texts())
    assert secret_violations(_effective_settings(env, monkeypatch), env)
    # A staging variable whose DEFAULT is a real key is still a real key.
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["environment"]["PAGENTOS_VOICE_OPENAI_API_KEY"] = (
        "${PAGENTOS_STAGING_VOICE_OPENAI_API_KEY:-sk-real}"
    )
    env = _env(compose["services"]["api"])
    assert account_violations(compose, _texts())
    assert secret_violations(_effective_settings(env, monkeypatch), env)


def test_checker_catches_a_planted_shared_volume() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["postgres"]["volumes"] = ["pagentos-postgres-data:/var/lib/postgresql/data"]
    assert volume_violations(compose, _load(DEV), _load(PROD))
    compose = copy.deepcopy(_load(STAGING))
    first = next(iter(compose["volumes"]))
    compose["volumes"][first] = {"name": "pagentos_pagentos-postgres-data", "external": True}
    assert volume_violations(compose, _load(DEV), _load(PROD))
