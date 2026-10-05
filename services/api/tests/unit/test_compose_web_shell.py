"""The owner's web shell as a Cloud Core `aux` workload: what the compose file, the image, the
release script and the tailnet script each promise, and that the halves agree.

Owner decision 2026-10-03 (docs/DECISIONS.md, "web on the Cloud Core"): the phone reaches the
web shell over the owner's tailnet when he is outside, with the home PC off. The posture this
file holds, because each line is what keeps it from becoming something else:

* the container's port is published on the host's LOOPBACK only - never on a public or a
  tailnet interface (HTTPS for the phone is `tailscale serve` in front of that loopback);
* it rewrites `/api/*` to the api THROUGH THE EDGE service, never to a fixed colour, so a
  blue/green switch is followed;
* it is an `aux` workload: brought up by the release AFTER the api's transaction, best
  effort, and nothing the recovery bundle or the reconcile path needs;
* it has a memory limit and a healthcheck, runs as a non-root user, and holds no secret.

The three places that must agree - the compose service, the Dockerfile, the tailnet script -
are read against each other, not each against a constant written here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
PROD = REPO / "infra" / "docker" / "docker-compose.prod.yml"
DOCKERFILE = REPO / "infra" / "docker" / "web" / "Dockerfile"
DOCKERIGNORE = REPO / "infra" / "docker" / "web" / "Dockerfile.dockerignore"
RELEASE = REPO / "scripts" / "cloud" / "release-cloud-core-bluegreen.sh"
TAILNET = REPO / "scripts" / "cloud" / "enable-web-tailnet-https.sh"
NGINX = REPO / "infra" / "docker" / "edge" / "nginx.conf"

SECRET_WORDS = re.compile(r"secret|token|passw|credential|api[_-]?key|private", re.IGNORECASE)


def _services() -> dict[str, dict]:
    return yaml.safe_load(PROD.read_text("utf-8"))["services"]


def _web() -> dict:
    services = _services()
    assert "web" in services, "docker-compose.prod.yml has no `web` service"
    return services["web"]


def _mem_bytes(value: str | int) -> int:
    match = re.fullmatch(r"(\d+)\s*([kmg]?)b?", str(value).strip().lower())
    assert match, f"unreadable memory value {value!r}"
    return int(match.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2)]


def _script_lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text("utf-8").splitlines() if not ln.lstrip().startswith("#")]


# ----------------------------------------------------------------------------- the service


def test_web_is_an_aux_workload_like_godseye() -> None:
    services = _services()
    assert services["web"]["profiles"] == ["aux"]
    assert services["web"]["profiles"] == services["godseye"]["profiles"]


def test_web_publishes_only_on_the_hosts_loopback() -> None:
    ports = _web().get("ports")
    assert ports, "the web service publishes nothing, so `tailscale serve` has nothing to reach"
    for entry in ports:
        text = str(entry)
        assert text.startswith("127.0.0.1:"), f"{text!r}: not bound to loopback"
        assert "0.0.0.0" not in text
        assert "PAGENTOS_BIND_IP" not in text, "the tailnet address would expose plain http"


def test_web_has_a_memory_limit_with_room_and_a_swap_cap() -> None:
    web = _web()
    assert "mem_limit" in web, "no memory limit on a container beside the api and Postgres"
    limit = _mem_bytes(web["mem_limit"])
    # Measured idle ~40 MiB, 95 MB peak RSS: 512 MiB is the budget, never more than 1 GiB on
    # a 7.7 GiB host that also runs the api, Postgres, Temporal, Redis and MinIO.
    assert 256 * 1024**2 <= limit <= 1024**3, (
        f"mem_limit {web['mem_limit']} is outside the measured budget"
    )
    assert _mem_bytes(web["memswap_limit"]) == limit, "swap must be capped to the same figure"


def test_web_has_a_healthcheck_on_the_port_it_publishes() -> None:
    web = _web()
    health = web.get("healthcheck")
    assert health and health.get("test"), "no healthcheck"
    container_port = re.fullmatch(r"127\.0\.0\.1:(\d+):(\d+)", web["ports"][0])
    assert container_port is not None
    assert f"127.0.0.1:{container_port.group(2)}" in " ".join(health["test"])
    assert health.get("retries", 0) >= 1 and "interval" in health


def test_web_restarts_and_is_hardened_like_the_other_side_workloads() -> None:
    web = _web()
    assert web["restart"] == "unless-stopped"
    assert web.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in web.get("security_opt", [])
    assert web.get("read_only") is True and "/tmp" in web.get("tmpfs", [])


def test_web_rewrites_to_the_edge_not_to_a_fixed_colour() -> None:
    web = _web()
    upstream = web["build"]["args"]["PAGENTOS_API_UPSTREAM"]
    host = re.fullmatch(r"http://([a-z0-9-]+):(\d+)", upstream)
    assert host is not None, upstream
    assert host.group(1) == "edge", "the upstream must be the edge, which follows the switch"
    assert host.group(1) in _services(), "the upstream names a service that does not exist"
    assert not re.search(r"api(-blue|-green)?$", host.group(1))
    # ...on the port the edge listens on.
    assert f"listen {host.group(2)};" in NGINX.read_text("utf-8")
    assert web["build"]["args"]["NEXT_PUBLIC_API_BASE"] == "/api"


def test_the_dockerfile_and_the_compose_service_name_the_same_upstream_and_port() -> None:
    web = _web()
    docker = DOCKERFILE.read_text("utf-8")
    arg = re.search(r"^ARG PAGENTOS_API_UPSTREAM=(\S+)", docker, re.MULTILINE)
    assert arg is not None and arg.group(1) == web["build"]["args"]["PAGENTOS_API_UPSTREAM"]
    api_base = re.search(r"^ARG NEXT_PUBLIC_API_BASE=(\S+)", docker, re.MULTILINE)
    assert (
        api_base is not None and api_base.group(1) == web["build"]["args"]["NEXT_PUBLIC_API_BASE"]
    )
    port = re.fullmatch(r"127\.0\.0\.1:(\d+):(\d+)", web["ports"][0])
    assert port is not None
    assert f"PORT={port.group(2)}" in docker
    assert f"EXPOSE {port.group(2)}" in docker


def test_the_tailnet_script_serves_the_port_the_compose_service_publishes() -> None:
    """Contract halves read each other: change one port and this fails, not the phone."""
    port = re.fullmatch(r"127\.0\.0\.1:(\d+):(\d+)", _web()["ports"][0])
    assert port is not None and port.group(1) == port.group(2)
    default = re.search(
        r"^port=\$\{PAGENTOS_WEB_PORT:-(\d+)\}", TAILNET.read_text("utf-8"), re.MULTILINE
    )
    assert default is not None and default.group(1) == port.group(1)
    assert 'target="http://127.0.0.1:$port"' in TAILNET.read_text("utf-8")


def test_web_holds_no_secret_in_the_compose_file() -> None:
    web = _web()
    assert "env_file" not in web, "a secret file would reach the container's environment"
    for section in ("environment", "args"):
        values = web.get("environment", {}) if section == "environment" else web["build"]["args"]
        mapping = values if isinstance(values, dict) else dict.fromkeys(values or [])
        for key, value in mapping.items():
            assert not SECRET_WORDS.search(str(key)), f"{section}: {key} looks like a secret"
            assert "${" not in str(value), f"{section}: {key} is read from the host's secrets file"
    assert not re.search(r"\$\{[A-Z_]+:\?", yaml.safe_dump(web)), (
        "a required host secret is interpolated"
    )


def test_nothing_waits_for_web_and_web_waits_for_nothing() -> None:
    services = _services()
    assert "depends_on" not in services["web"], "an aux workload is brought up --no-deps"
    for name, service in services.items():
        depends = service.get("depends_on") or {}
        assert "web" not in depends, f"{name} depends on the web shell"


# ----------------------------------------------------------------------------- the image


def test_the_image_runs_as_a_non_root_user_with_a_healthcheck_and_no_secret() -> None:
    docker = DOCKERFILE.read_text("utf-8")
    final = docker.split("AS run", 1)[1]
    users = re.findall(r"^USER (\S+)", final, re.MULTILINE)
    assert users and users[-1] not in {"root", "0"}, "the final stage runs as root"
    assert "HEALTHCHECK" in final
    assert "pnpm" not in final.replace("# ", ""), "the final stage carries the build tool"
    for line in docker.splitlines():
        if line.lstrip().startswith(("ARG ", "ENV ")):
            assert not SECRET_WORDS.search(line.split("=", 1)[0]), (
                f"a secret-looking variable: {line}"
            )
    # Nothing is copied from the build context into the final stage except the traced output.
    assert re.findall(r"^COPY (?!--from=build)", final, re.MULTILINE) == []


def test_the_build_context_is_an_allowlist_that_keeps_env_files_out() -> None:
    ignore = [
        ln.strip()
        for ln in DOCKERIGNORE.read_text("utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert ignore[0] == "*", (
        "the context must start from 'nothing' and allow back what the build needs"
    )
    assert "apps/web/**/.env" in ignore and "apps/web/**/.env.*" in ignore
    assert "apps/web/node_modules" in ignore and "apps/web/.next" in ignore
    allowed = {ln[1:] for ln in ignore if ln.startswith("!")}
    assert allowed == {
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "apps/web/**",
        "packages/protocol/realtime-session-contract.json",
    }
    # Every file the Dockerfile COPYs from the context is one the allowlist lets through.
    for source in re.findall(r"^COPY (?!--from)(\S+)", DOCKERFILE.read_text("utf-8"), re.MULTILINE):
        assert (
            any(source == a or (a.endswith("/**") and source.startswith(a[:-3])) for a in allowed)
            or source == "apps/web"
        ), source
    # The one import from outside the package really exists, so the build cannot lose it silently.
    assert (REPO / "packages" / "protocol" / "realtime-session-contract.json").is_file()
    assert "realtime-session-contract.json" in (
        REPO / "apps" / "web" / "app" / "lib" / "voice" / "session-contract.ts"
    ).read_text("utf-8")


# ----------------------------------------------------------------------------- the release


def test_the_release_brings_web_up_with_the_aux_workloads_never_inside_the_transaction() -> None:
    code = "\n".join(_script_lines(RELEASE))
    assert "--profile aux" in code, "the compose helper lost the aux profile"
    assert re.search(r"for svc in godseye web;", code), "web is not in the aux loop"
    assert 'compose up -d --no-deps $build_flag "$svc"' in code
    assert "--wait" not in re.search(r"aux_up\(\) \{.*?\n\}\n", code, re.DOTALL).group(0), (  # type: ignore[union-attr]
        "an aux workload is never waited on"
    )
    # Called exactly once, after the transaction's last line, and its failure is swallowed.
    calls = [
        i
        for i, ln in enumerate(code.splitlines())
        if re.fullmatch(r"aux_up( \|\| true)?", ln.strip())
    ]
    assert len(calls) == 1
    lines = code.splitlines()
    assert lines[calls[0]].strip() == "aux_up || true"
    assert any(ln.startswith('echo "RELEASE OK:') for ln in lines[: calls[0]])
    assert not any(ln.startswith('echo "RELEASE OK:') for ln in lines[calls[0] + 1 :])


def test_neither_the_reconcile_nor_the_recovery_path_mentions_the_web_shell() -> None:
    """The recovery bundle pins the Compose file and nginx.conf; the api's recovery needs no web."""
    code = "\n".join(_script_lines(RELEASE))
    outside_aux = re.sub(r"aux_up\(\) \{.*?\n\}\n", "", code, flags=re.DOTALL)
    assert outside_aux != code, "the aux_up function was not found"
    leaks = [ln.strip() for ln in outside_aux.splitlines() if re.search(r"\bweb\b", ln)]
    assert leaks == [], f"the colour / edge / reconcile logic mentions the web shell: {leaks}"
    recovery = REPO / "scripts" / "cloud" / "install-recovery-supervisor.sh"
    assert recovery.is_file()
    assert not re.search(r"pagentos-prod-web|service web\b", recovery.read_text("utf-8"))
