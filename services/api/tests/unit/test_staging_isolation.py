"""The staging stack cannot reach production, the owner's accounts or the dev stack's data.

Owner's idea, 2026-10-03: a test team that tests "like the device owner" on a copy of the
real server - same code, its OWN database, test identities only - so nothing it does can
touch the owner's memory, ledger, devices or accounts. The copy is
infra/docker/docker-compose.staging.yml plus scripts/staging/*.ps1 (team/plans ADR
"staging-stack"). This file is the isolation proof, and it reads those files themselves:

* no production host, name or database anywhere (the tailnet address, `pagentos-core`, the
  `pagentos-prod*` containers, the `pagentos_prod` database, the tailnet DNS name);
* no setting that points at a real account (a mail/calendar host or address, a provider
  key) - and an ALLOW-LIST for every `$` of the compose's raw text, comments included: only
  the `$$` escape and `${PAGENTOS_STAGING_X}` (optionally `:-`/`-` a fixed default with no
  `$`); an unbraced `$NAME`, `${HOME}`, `${X:?..}` or a nested default is refused, so a
  stray `.env` or a shell that carries the owner's real values cannot leak one in;
* a build context is a relative path inside this repository - never a git URL or `git@`;
* an ALLOW-LIST of what the file may say at all: the top-level keys (name, services,
  volumes, networks, the blackhole anchor) and each service's keys are closed sets, so
  anything else - volumes_from, secrets, configs, network_mode, pid, ipc, extends, env_file,
  devices, privileged, ... - is refused without having to be foreseen;
* mounts only of named volumes declared in this file, called pagentos-staging-*, plain local
  (no external, no driver/driver_opts, no bind or host path, no `container:`); networks only
  staging's own, declared here, never external;
* an ALLOW-LIST of values: each service's environment equals an expected dictionary exactly,
  every address is parsed and its host is staging's own service on the expected port (no
  query), and extra_hosts is exactly the blackhole set (production's names, and Docker
  Desktop's names for the PC where the dev stack listens) - never `host-gateway`;
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
#: ALLOW-LIST for every `$` in the compose's RAW text (inspector's fourth return, 2026-10-04:
#: an unbraced `$PAGENTOS_REDIS_URL` passed, and compose filled it from the shell). Allowed:
#: the `$$` escape, and `${PAGENTOS_STAGING_X}` with at most a `:-`/`-` default of fixed text
#: (no `$`, no `}`). Everything else - `$NAME`, `${HOME}`, `${X:?..}`, a nested default - is
#: refused, comments included.
STAGING_VARIABLE = re.compile(r"\$\{PAGENTOS_STAGING_[A-Z0-9_]+(?::?-[^${}]*)?\}")
#: The one other `$` the file holds: the `${...}` placeholder inside a comment (it names no
#: variable, so it can read nothing).
COMMENT_PLACEHOLDER = "${...}"


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


def dollar_violations(raw: str) -> list[str]:
    """Every `$` of the raw text that is neither `$$` nor an allowed staging variable."""
    found = []
    for number, line in enumerate(raw.splitlines(), 1):
        at = 0
        while (at := line.find("$", at)) >= 0:
            if line.startswith("$$", at):
                at += 2
                continue
            allowed = STAGING_VARIABLE.match(line, at)
            if allowed:
                at = allowed.end()
                continue
            if line.lstrip().startswith("#") and line.startswith(COMMENT_PLACEHOLDER, at):
                at += len(COMMENT_PLACEHOLDER)
                continue
            found.append(
                f"line {number}: {line[at : at + 40]!r} - only ${{PAGENTOS_STAGING_*}} may reach"
            )
            at += 1
    return found


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
        if name.endswith(".yml"):
            found += [f"{name}: {v}" for v in dollar_violations(text)]
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


#: ALLOW-LIST, not a deny-list (inspector's third return, 2026-10-04: `volumes_from` and a
#: `secrets: file:` bind passed a list of forbidden things). Every key the file may use is
#: named here; any other key - volumes_from, secrets, configs, network_mode, pid, ipc,
#: extends, env_file, devices, privileged, cap_add, links, include, ... - is a violation.
TOP_LEVEL_KEYS = frozenset({"name", "services", "volumes", "networks", "x-production-blackhole"})
SERVICE_KEYS = frozenset(
    {
        "image",
        "build",
        "container_name",
        "extra_hosts",
        "command",
        "environment",
        "ports",
        "volumes",
        "networks",
        "healthcheck",
        "depends_on",
        "init",
        "mem_limit",
        "memswap_limit",
        "pids_limit",
        "read_only",
        "tmpfs",
        "cap_drop",
        "security_opt",
        "restart",
    }
)
BUILD_KEYS = frozenset({"context", "dockerfile", "args"})
#: A declared volume is a plain local named volume: no external, no driver / driver_opts (a
#: `local` driver with `o: bind, device: C:/...` is a host bind mount by another name).
VOLUME_SPEC_KEYS = frozenset({"name"})
NETWORK_SPEC_KEYS = frozenset({"name", "ipam"})
SECURITY_OPTS = frozenset({"no-new-privileges:true"})


def _local_context(context: str) -> bool:
    """A build context is `.`/`..`-relative, no URL, no `git@`, no drive, inside the repo
    (`github.com/x/y` and `https://...` are remote git contexts to docker)."""
    if not context.startswith(".") or ":" in context or "@" in context or "\\" in context:
        return False
    return (DOCKER / context).resolve().is_relative_to(REPO)


def _local_dockerfile(context: str, dockerfile: str) -> bool:
    """The same rule for `build.dockerfile` (relative to the context): no URL, drive, `@`,
    backslash or absolute path, and it resolves inside the repository (inspector, round 5:
    `C:/Users/...` passed)."""
    if (
        not dockerfile
        or dockerfile.startswith("/")
        or any(ch in dockerfile for ch in ":@\\")
        or not _local_context(context)
    ):
        return False
    return (DOCKER / context / dockerfile).resolve().is_relative_to(REPO)


def schema_violations(compose: dict[str, Any]) -> list[str]:
    found = [
        f"top-level key {key!r} is not in the allow-list"
        for key in compose
        if key not in TOP_LEVEL_KEYS
    ]
    for svc_name, svc in (compose.get("services") or {}).items():
        found += [
            f"{svc_name}: key {key!r} is not in the allow-list"
            for key in svc
            if key not in SERVICE_KEYS
        ]
        build = svc.get("build")
        if build is not None and not isinstance(build, dict):
            found.append(f"{svc_name}: build {build!r} is not a context/dockerfile/args block")
        found += [
            f"{svc_name}: build key {key!r} is not in the allow-list"
            for key in (build if isinstance(build, dict) else {})
            if key not in BUILD_KEYS
        ]
        context = build.get("context") if isinstance(build, dict) else None
        if context is not None and not _local_context(str(context)):
            found.append(f"{svc_name}: build context {context!r} is not a path in this repository")
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        if dockerfile is not None and not _local_dockerfile(str(context or "."), str(dockerfile)):
            found.append(
                f"{svc_name}: build dockerfile {dockerfile!r} is not a path in this repository"
            )
        found += [
            f"{svc_name}: security_opt {opt!r} is not in the allow-list"
            for opt in svc.get("security_opt") or []
            if str(opt) not in SECURITY_OPTS
        ]
    return found


def volume_violations(
    compose: dict[str, Any], dev: dict[str, Any], prod: dict[str, Any]
) -> list[str]:
    """Only named volumes declared in this file, called pagentos-staging-*, plain local."""
    found = []
    declared = compose.get("volumes") or {}
    other_names = set((dev.get("volumes") or {}).keys()) | set((prod.get("volumes") or {}).keys())
    for vol_name, spec in declared.items():
        spec = spec or {}
        if not isinstance(spec, dict):
            found.append(f"volume {vol_name}: spec {spec!r} is not a mapping")
            continue
        found += [
            f"volume {vol_name}: key {key!r} is not in the allow-list"
            + (" (external - another stack's data)" if key == "external" else "")
            for key in spec
            if key not in VOLUME_SPEC_KEYS
        ]
        real_name = str(spec.get("name", vol_name))
        for label in (str(vol_name), real_name):
            if not label.startswith("pagentos-staging-"):
                found.append(f"volume {vol_name}: name {label!r} is not pagentos-staging-*")
        if vol_name in other_names or real_name in other_names:
            found.append(f"volume {vol_name}: shares a name with the dev/prod stack ({real_name})")
    for svc_name, svc in (compose.get("services") or {}).items():
        if "volumes_from" in svc:
            found.append(f"{svc_name}: volumes_from mounts another container's volumes")
        for mount in svc.get("volumes") or []:
            if isinstance(mount, dict):
                kind = mount.get("type", "volume")
                source = str(mount.get("source", ""))
                if kind != "volume":
                    found.append(f"{svc_name}: a {kind!r} mount of {source!r} - only volumes")
            else:
                source = str(mount).split(":", 1)[0]
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
    for net_name, spec in networks.items():
        spec = spec or {}
        found += [
            f"network {net_name}: key {key!r} is not in the allow-list"
            + (" (external - another stack's network)" if key == "external" else "")
            for key in (spec if isinstance(spec, dict) else {"<not a mapping>": None})
            if key not in NETWORK_SPEC_KEYS
        ]
        real_name = str(spec.get("name", "")) if isinstance(spec, dict) else ""
        if not real_name.startswith("pagentos-staging-"):
            found.append(f"network {net_name}: name {real_name!r} is not pagentos-staging-*")
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


def _all_violations(compose: dict[str, Any]) -> list[str]:
    return (
        schema_violations(compose)
        + volume_violations(compose, _load(DEV), _load(PROD))
        + network_violations(compose)
    )


def test_checker_catches_volumes_from_another_container() -> None:
    # Inspector 2026-10-04 (round 3, M1): `volumes_from` mounts the dev Postgres container's
    # data and passed all 17 checks - the deny-list read only `volumes:`.
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["postgres"]["volumes_from"] = ["container:pagentos-postgres"]
    assert _all_violations(compose)


def test_checker_catches_a_secret_bound_from_the_host() -> None:
    # Inspector 2026-10-04 (round 3, M2): a top-level `secrets:` with `file:` is a bind mount
    # from the owner's home folder into the api, and passed green.
    compose = copy.deepcopy(_load(STAGING))
    compose["secrets"] = {
        "owner": {"file": "C:/Users/alpak/AppData/Local/PagentOS/identity/owner_credential"}
    }
    compose["services"]["api"]["secrets"] = ["owner"]
    assert schema_violations(compose)
    # Each half alone is refused too: the top-level block, and the service's use of it.
    only_top = copy.deepcopy(_load(STAGING))
    only_top["secrets"] = {"owner": {"environment": "PAGENTOS_IDENTITY_OWNER_CREDENTIAL"}}
    assert schema_violations(only_top)
    only_service = copy.deepcopy(_load(STAGING))
    only_service["services"]["api"]["configs"] = ["owner"]
    assert schema_violations(only_service)


@pytest.mark.parametrize(
    ("where", "key", "value"),
    [
        ("service", "volumes_from", ["container:pagentos-postgres"]),
        ("service", "secrets", ["owner"]),
        ("service", "configs", ["owner"]),
        ("service", "network_mode", "host"),
        ("service", "pid", "host"),
        ("service", "ipc", "host"),
        ("service", "extends", {"file": "docker-compose.dev.yml", "service": "postgres"}),
        ("service", "env_file", ["../../.env"]),
        ("service", "devices", ["/dev/sda:/dev/sda"]),
        ("service", "privileged", True),
        ("service", "cap_add", ["SYS_ADMIN"]),
        ("service", "external_links", ["pagentos-postgres:postgres"]),
        ("service", "links", ["postgres"]),
        ("service", "userns_mode", "host"),
        ("top", "secrets", {"owner": {"file": "C:/Users/owner/credential"}}),
        ("top", "configs", {"owner": {"file": "C:/Users/owner/credential"}}),
        ("top", "include", ["docker-compose.dev.yml"]),
    ],
)
def test_checker_refuses_every_key_outside_the_allow_list(where: str, key: str, value: Any) -> None:
    compose = copy.deepcopy(_load(STAGING))
    (compose if where == "top" else compose["services"]["api"])[key] = value
    assert any(key in v for v in schema_violations(compose)), key


@pytest.mark.parametrize(
    "mount",
    [
        "C:/Users/alpak/AppData/Local/PagentOS:/srv/pagentos/var/identity",
        "./data:/data",
        "/var/run/docker.sock:/var/run/docker.sock",
        "pagentos-postgres-data:/var/lib/postgresql/data",
        {"type": "bind", "source": "C:/Users/alpak", "target": "/srv"},
        {"type": "volume", "source": "pagentos_pagentos-postgres-data", "target": "/srv"},
    ],
)
def test_checker_refuses_a_mount_that_is_not_a_staging_volume(mount: Any) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["volumes"] = [mount]
    assert volume_violations(compose, _load(DEV), _load(PROD)), mount


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "pagentos-staging-postgres", "external": True},
        {
            "name": "pagentos-staging-postgres",
            "driver_opts": {"type": "none", "o": "bind", "device": "C:/Users/alpak"},
        },
        {"name": "pagentos-staging-postgres", "driver": "local"},
    ],
)
def test_checker_refuses_a_volume_that_is_not_a_plain_staging_volume(spec: Any) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["volumes"]["pagentos-staging-postgres"] = spec
    assert volume_violations(compose, _load(DEV), _load(PROD)), spec


def test_checker_refuses_a_network_that_is_not_staging_own() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["networks"]["default"]["external"] = True
    assert network_violations(compose)
    compose = copy.deepcopy(_load(STAGING))
    compose["networks"]["default"]["name"] = "pagentos_default"
    assert network_violations(compose)
    compose = copy.deepcopy(_load(STAGING))
    compose["networks"]["dev"] = {"name": "pagentos_default", "external": True}
    compose["services"]["api"]["networks"] = ["default", "dev"]
    assert network_violations(compose)


def test_staging_uses_only_allowed_keys() -> None:
    assert schema_violations(_load(STAGING)) == []


# Inspector 2026-10-04 (round 4): `PAGENTOS_REDIS_URL: $PAGENTOS_REDIS_URL` passed 47/47 green
# and `docker compose config` filled it from the shell - the old scan saw only `${...}`. So the
# RAW text is scanned for every `$` but the `$$` escape, comments included.
_PLANTED_AFTER = "      PAGENTOS_RELEASE: ${PAGENTOS_STAGING_RELEASE:-}\n"


@pytest.mark.parametrize(
    "line",
    [
        "      PAGENTOS_REDIS_URL: $PAGENTOS_REDIS_URL",
        "      PAGENTOS_DATABASE_URL: ${PAGENTOS_DATABASE_URL}",
        "      PAGENTOS_GODS_EYE_URL: ${HOME}",
        "      PAGENTOS_GODS_EYE_URL: ${PAGENTOS_STAGING_GODS_EYE_URL:-$PAGENTOS_GODS_EYE_URL}",
        "      PAGENTOS_GODS_EYE_URL: ${PAGENTOS_STAGING_GODS_EYE_URL:-${HOME}}",
        "      - PAGENTOS_REDIS_URL=$PAGENTOS_REDIS_URL",
        "      PAGENTOS_REDIS_URL: ${PAGENTOS_REDIS_URL:-redis://redis:6379/0}",
        "      PAGENTOS_REDIS_URL: ${PAGENTOS_REDIS_URL-redis://redis:6379/0}",
        "      PAGENTOS_REDIS_URL: ${PAGENTOS_STAGING_REDIS_URL:?unset}",
        "      PAGENTOS_REDIS_URL: $$$PAGENTOS_REDIS_URL",
        "      # PAGENTOS_REDIS_URL: $PAGENTOS_REDIS_URL",
        "      PAGENTOS_REDIS_URL: ${pagentos_staging_redis_url}",
    ],
)
def test_checker_refuses_every_dollar_but_a_staging_variable(line: str) -> None:
    texts = _texts()
    raw = texts["docker-compose.staging.yml"]
    assert _PLANTED_AFTER in raw
    texts["docker-compose.staging.yml"] = raw.replace(_PLANTED_AFTER, _PLANTED_AFTER + line + "\n")
    assert account_violations(_load(STAGING), texts), line


@pytest.mark.parametrize(
    "line",
    [
        '      command: ["sh", "-c", "echo $$HOME"]',
        "      PAGENTOS_STAGING_PROBE: ${PAGENTOS_STAGING_PROBE}",
        "      PAGENTOS_STAGING_PROBE: ${PAGENTOS_STAGING_PROBE:-local}",
        "      PAGENTOS_STAGING_PROBE: ${PAGENTOS_STAGING_PROBE-}",
    ],
)
def test_checker_keeps_the_escape_and_staging_variables(line: str) -> None:
    texts = _texts()
    raw = texts["docker-compose.staging.yml"]
    texts["docker-compose.staging.yml"] = raw.replace(_PLANTED_AFTER, _PLANTED_AFTER + line + "\n")
    assert account_violations(_load(STAGING), texts) == [], line


# ----------------------------------------------------------------------------- allow-list
# Inspector's fifth return, 2026-10-04: from inside pagentos-staging-api, host.docker.internal
# (Docker Desktop's name for the PC) reached the dev Postgres :15432, the dev Temporal :17233
# and jarvis_api :8000, and `PAGENTOS_TEMPORAL_ADDRESS: host.docker.internal:17233` (or a Redis /
# S3 url there, or a database url carrying "@postgres:5432/pagentos_staging" only in its query)
# passed every check green. So each service's environment is compared with an EXACT expected
# dictionary - a key too many, a key missing or a value changed is red - and every address is
# parsed: the host is exactly staging's own service and the port the expected one.

#: Every service's environment, key for key and value for value.
EXPECTED_ENV: dict[str, dict[str, str]] = {
    "postgres": {
        "POSTGRES_USER": "staging",
        "POSTGRES_PASSWORD": "staging-only",
        "POSTGRES_DB": "pagentos_staging",
    },
    "redis": {},
    "minio": {
        "MINIO_ROOT_USER": "staging-only-artifacts",
        "MINIO_ROOT_PASSWORD": "staging-only-minio",
    },
    "temporal": {
        "DB": "postgres12",
        "DB_PORT": "5432",
        "POSTGRES_USER": "staging",
        "POSTGRES_PWD": "staging-only",
        "POSTGRES_SEEDS": "postgres",
        "DEFAULT_NAMESPACE": "pagentos-staging",
        "TEMPORAL_ADDRESS": "temporal:7233",
        "TEMPORAL_CLI_ADDRESS": "temporal:7233",
    },
    "api": {
        "PAGENTOS_ENVIRONMENT": "staging",
        "PAGENTOS_DATABASE_URL": "postgresql+psycopg://staging:staging-only@postgres:5432/pagentos_staging",
        "PAGENTOS_REDIS_URL": "redis://redis:6379/0",
        "PAGENTOS_S3_ENDPOINT_URL": "http://minio:9000",
        "PAGENTOS_S3_ACCESS_KEY": "staging-only-artifacts",
        "PAGENTOS_S3_SECRET_KEY": "staging-only-minio",
        "PAGENTOS_S3_BUCKET": "pagentos-staging-artifacts",
        "PAGENTOS_TEMPORAL_ADDRESS": "temporal:7233",
        "PAGENTOS_TEMPORAL_NAMESPACE": "pagentos-staging",
        "PAGENTOS_TEMPORAL_TASK_QUEUE": "pagentos-staging",
        "PAGENTOS_WORKER_MODE": "embedded",
        "PAGENTOS_VOICE_PROFILE_SECRET": "staging-only-voice-profile",
        "PAGENTOS_ACCOUNTS_TOKEN_SECRET": "staging-only-accounts-token",
        "PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED": "true",
        "PAGENTOS_VOICE_OPENAI_API_KEY": "${PAGENTOS_STAGING_VOICE_OPENAI_API_KEY:-}",
        "PAGENTOS_MAIL_SEND_ENABLED": "false",
        "PAGENTOS_CALENDAR_WRITE_ENABLED": "false",
        "PAGENTOS_MEMORY_EMBEDDING_PROVIDER": "deterministic",
        "PAGENTOS_MEMORY_RERANK_PROVIDER": "none",
        "PAGENTOS_WEB_ORIGINS": '["http://127.0.0.1:28000","http://localhost:28000"]',
        "PAGENTOS_IDENTITY_BOOTSTRAP_LOOPBACK_ONLY": "true",
        "PAGENTOS_TEAM_STORE": "file",
        "PAGENTOS_RELEASE": "${PAGENTOS_STAGING_RELEASE:-}",
        "PAGENTOS_GODS_EYE_URL": "",
    },
    "web": {},
}

#: Every address a service is given: (service, where, value kind) -> (scheme, host, port, path).
#: The host is the compose service name - staging's own container, nothing else.
EXPECTED_ADDRESSES: dict[tuple[str, str], tuple[str, str, int, str]] = {
    ("api", "PAGENTOS_DATABASE_URL"): ("postgresql+psycopg", "postgres", 5432, "/pagentos_staging"),
    ("api", "PAGENTOS_REDIS_URL"): ("redis", "redis", 6379, "/0"),
    ("api", "PAGENTOS_S3_ENDPOINT_URL"): ("http", "minio", 9000, ""),
    ("api", "PAGENTOS_TEMPORAL_ADDRESS"): ("", "temporal", 7233, ""),
    ("temporal", "TEMPORAL_ADDRESS"): ("", "temporal", 7233, ""),
    ("temporal", "TEMPORAL_CLI_ADDRESS"): ("", "temporal", 7233, ""),
    ("web", "build.args.PAGENTOS_API_UPSTREAM"): ("http", "api", 8001, ""),
}

#: extra_hosts, exactly: production's names to TEST-NET-1, and Docker Desktop's names for the
#: PC (where the dev stack publishes its ports) to 0.0.0.0. `host-gateway` - the PC's address
#: - or any other entry is red.
EXPECTED_EXTRA_HOSTS = {
    "pagentos-core": BLACKHOLE,
    "pagentos-core.tail0e6789.ts.net": BLACKHOLE,
    "host.docker.internal": "0.0.0.0",
    "gateway.docker.internal": "0.0.0.0",
}


def env_violations(compose: dict[str, Any]) -> list[str]:
    found = []
    services = compose.get("services") or {}
    if set(services) != set(EXPECTED_ENV):
        found.append(f"services {sorted(services)} are not {sorted(EXPECTED_ENV)}")
    for svc_name, svc in services.items():
        env = _env(svc)
        wanted = EXPECTED_ENV.get(svc_name, {})
        found += [
            f"{svc_name}: {k} is not in the expected environment"
            for k in env.keys() - wanted.keys()
        ]
        found += [f"{svc_name}: {k} is missing" for k in wanted.keys() - env.keys()]
        found += [
            f"{svc_name}: {k}={env[k]!r}, expected {wanted[k]!r}"
            for k in env.keys() & wanted.keys()
            if env[k] != wanted[k]
        ]
    return found


def _address(value: str) -> tuple[str, str, int | None, str, str]:
    """(scheme, host, port, path, query+fragment+userinfo-oddities) of a url or `host:port`."""
    from urllib.parse import urlsplit

    parts = urlsplit(value if "://" in value else f"//{value}")
    try:
        port = parts.port
    except ValueError:
        port = None
    return parts.scheme, parts.hostname or "", port, parts.path, parts.query + parts.fragment


def address_violations(compose: dict[str, Any]) -> list[str]:
    found = []
    services = compose.get("services") or {}
    for (svc_name, where), (scheme, host, port, path) in EXPECTED_ADDRESSES.items():
        svc = services.get(svc_name) or {}
        if where.startswith("build.args."):
            build = svc.get("build") if isinstance(svc.get("build"), dict) else {}
            value = str(
                ((build or {}).get("args") or {}).get(where.removeprefix("build.args."), "")
            )
        else:
            value = _env(svc).get(where, "")
        got = _address(value)
        # A query is refused outright: libpq reads `?host=` / `?port=` and goes there instead.
        if got != (scheme, host, port, path, ""):
            found.append(
                f"{svc_name}: {where}={value!r} is not {scheme or 'host'}://{host}:{port}{path} "
                f"(parsed scheme={got[0]!r} host={got[1]!r} port={got[2]!r} path={got[3]!r} "
                f"query={got[4]!r})"
            )
    return found


def extra_hosts_violations(compose: dict[str, Any]) -> list[str]:
    found = []
    for svc_name, svc in (compose.get("services") or {}).items():
        entries = [str(e) for e in svc.get("extra_hosts") or []]
        hosts: dict[str, str] = {}
        for entry in entries:
            name, sep, addr = entry.partition(":")
            if not sep or name in hosts:
                found.append(f"{svc_name}: extra_hosts entry {entry!r} is malformed or repeated")
            hosts[name] = addr
        if hosts != EXPECTED_EXTRA_HOSTS:
            found.append(f"{svc_name}: extra_hosts {entries} is not exactly {EXPECTED_EXTRA_HOSTS}")
        found += [
            f"{svc_name}: extra_hosts {entry!r} sends a name to the PC (host-gateway)"
            for entry in entries
            if "host-gateway" in entry
        ]
    return found


def test_staging_environment_is_exactly_the_expected_one() -> None:
    assert env_violations(_load(STAGING)) == []


def test_staging_addresses_are_staging_own_services() -> None:
    assert address_violations(_load(STAGING)) == []


def test_staging_sends_the_pc_names_nowhere() -> None:
    assert extra_hosts_violations(_load(STAGING)) == []


@pytest.mark.parametrize(
    ("service", "key", "value"),
    [
        ("api", "PAGENTOS_TEMPORAL_ADDRESS", "host.docker.internal:17233"),
        ("api", "PAGENTOS_REDIS_URL", "redis://host.docker.internal:16379/0"),
        ("api", "PAGENTOS_S3_ENDPOINT_URL", "http://host.docker.internal:19000"),
        ("api", "PAGENTOS_TEMPORAL_ADDRESS", "temporal:17233"),
        ("api", "PAGENTOS_REDIS_URL", "redis://redis.evil:6379/0"),
        (
            "api",
            "PAGENTOS_DATABASE_URL",
            "postgresql+psycopg://staging:staging-only@host.docker.internal:15432/pagentos"
            "?x=@postgres:5432/pagentos_staging",
        ),
        (
            "api",
            "PAGENTOS_DATABASE_URL",
            "postgresql+psycopg://staging:staging-only@postgres:5432/pagentos_staging"
            "?host=host.docker.internal&port=15432",
        ),
        (
            "api",
            "PAGENTOS_DATABASE_URL",
            "postgresql+psycopg://staging:staging-only@postgres:5432/pagentos",
        ),
        ("temporal", "TEMPORAL_ADDRESS", "192.168.65.254:17233"),
    ],
)
def test_checker_refuses_an_address_outside_staging(service: str, key: str, value: str) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"][service]["environment"][key] = value
    assert address_violations(compose), value
    assert env_violations(compose), value


def test_checker_refuses_a_web_upstream_outside_staging() -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["web"]["build"]["args"]["PAGENTOS_API_UPSTREAM"] = (
        "http://host.docker.internal:8001"
    )
    assert address_violations(compose)


@pytest.mark.parametrize(
    "change",
    ["extra", "missing", "changed"],
)
def test_checker_refuses_any_environment_drift(change: str) -> None:
    compose = copy.deepcopy(_load(STAGING))
    env = compose["services"]["api"]["environment"]
    if change == "extra":
        env["PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN"] = "http://127.0.0.1:28001"
    elif change == "missing":
        env.pop("PAGENTOS_GODS_EYE_URL")
    else:
        env["PAGENTOS_WORKER_MODE"] = "external"
    assert env_violations(compose), change


@pytest.mark.parametrize(
    "hosts",
    [
        ["pagentos-core:192.0.2.1", "pagentos-core.tail0e6789.ts.net:192.0.2.1"],
        [
            "pagentos-core:192.0.2.1",
            "pagentos-core.tail0e6789.ts.net:192.0.2.1",
            "host.docker.internal:host-gateway",
            "gateway.docker.internal:0.0.0.0",
        ],
        [
            "pagentos-core:192.0.2.1",
            "pagentos-core.tail0e6789.ts.net:192.0.2.1",
            "host.docker.internal:0.0.0.0",
            "gateway.docker.internal:0.0.0.0",
            "devpg:host-gateway",
        ],
        [
            "pagentos-core:192.0.2.1",
            "pagentos-core.tail0e6789.ts.net:192.0.2.1",
            "host.docker.internal:192.168.65.254",
            "gateway.docker.internal:0.0.0.0",
        ],
    ],
)
def test_checker_refuses_extra_hosts_that_reach_the_pc(hosts: list[str]) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["extra_hosts"] = hosts
    assert extra_hosts_violations(compose), hosts


@pytest.mark.parametrize(
    "dockerfile",
    [
        "C:/Users/alpak/src/pagentos/infra/docker/web/Dockerfile",
        "/home/owner/Dockerfile",
        "../../../../outside/Dockerfile",
        "https://example.net/Dockerfile",
        "infra\\docker\\web\\Dockerfile",
    ],
)
def test_checker_refuses_a_dockerfile_outside_the_repository(dockerfile: str) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["web"]["build"]["dockerfile"] = dockerfile
    assert schema_violations(compose), dockerfile


@pytest.mark.parametrize(
    "context",
    [
        "https://github.com/owner/pagentos.git#main",
        "git@github.com:owner/pagentos.git",
        "github.com/owner/pagentos",
        "C:/Users/alpak/src/pagentos",
        "/home/owner/pagentos",
        "../../..",
    ],
)
def test_checker_refuses_a_build_context_outside_the_repository(context: str) -> None:
    compose = copy.deepcopy(_load(STAGING))
    compose["services"]["api"]["build"]["context"] = context
    assert schema_violations(compose), context
