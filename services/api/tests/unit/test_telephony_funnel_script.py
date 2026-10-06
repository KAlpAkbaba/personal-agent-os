"""scripts/cloud/enable-telephony-funnel.sh, RUN under Git Bash against a fake `tailscale`.

The Cloud Core's one public path (inbound-calls-public-path): a Tailscale Funnel on port 8443
only, for exactly two path roots - /telephony/inbound and /v1/telephony/audio - proxied to the
edge nginx on the host's tailnet address; 443 (the web shell) is never funneled. Every claim
below is made by running the script, not by reading it, except the two absences in the last
test (a text can only prove what it does not contain).

The fake keeps the serve state in plain files and prints `serve status --json` in the shape of
tailscale 1.102.4's ipn.ServeConfig (field names from the integrator's plan: TCP, Web.Handlers.
Proxy, AllowFunnel keyed "<node>:<port>"; source: the integrator, from the v1.102.4 source and
one real `serve status --json` taken on the host). Every argv it gets is logged one per line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "cloud" / "enable-telephony-funnel.sh"
BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"

NODE = "pagentos-core.tail1234.ts.net"
IP = "100.64.0.9"
ROOTS = ("/telephony/inbound", "/v1/telephony/audio")
SECRET = "gizli-deger-123"

FAKE_TAILSCALE = r"""#!/usr/bin/env bash
S="$FAKE_STATE"
echo "tailscale $*" >> "$S/calls.log"
node=pagentos-core.tail1234.ts.net
emit_serve() {
  printf '{"TCP":{"443":{"HTTPS":true}'
  [ -s "$S/m8443" ] && printf ',"8443":{"HTTPS":true}'
  printf '},"Web":{"%s:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}}' "$node"
  if [ -s "$S/m8443" ]; then
    printf ',"%s:8443":{"Handlers":{' "$node"; sep=""
    while read -r m p; do
      printf '%s"%s":{"Proxy":"%s"}' "$sep" "$m" "$p"; sep=","
    done < "$S/m8443"
    printf '}}'
  fi
  printf '}'
  af=""
  [ -e "$S/f443" ] && af="\"$node:443\":true"
  if [ -e "$S/f8443" ] && [ -s "$S/m8443" ]; then
    [ -n "$af" ] && af="$af,"
    af="$af\"$node:8443\":true"
  fi
  [ -n "$af" ] && printf ',"AllowFunnel":{%s}' "$af"
  printf '}\n'
}
case "$1" in
  status)
    [ -n "${FAKE_TS_DOWN:-}" ] && { echo "Tailscale is stopped." >&2; exit 1; }
    cat "$S/status.json"; exit 0;;
  serve)
    shift
    case "$*" in
      "status --json") emit_serve; exit 0;;
      "--yes --https=8443 off") rm -f "$S/m8443" "$S/f8443"; exit 0;;
      "--https=8443 --set-path="*" off")
        m=${2#--set-path=}; { grep -v "^$m " "$S/m8443" || true; } > "$S/t"; mv "$S/t" "$S/m8443"
        [ -s "$S/m8443" ] || rm -f "$S/f8443"; exit 0;;
    esac
    echo "unexpected: tailscale serve $*" >&2; exit 2;;
  funnel)
    if [ -n "${FAKE_TS_CONSENT:-}" ]; then
      echo "Funnel is not enabled on your tailnet. To enable, visit:"
      echo "  https://login.tailscale.com/f/funnel?node=n1"; exit 0
    fi
    if [ "$2 $3 $4" != "--bg --yes --https=8443" ]; then
      echo "unexpected: tailscale funnel $*" >&2; exit 2
    fi
    m=${5#--set-path=}; echo "$m $6" >> "$S/m8443"; touch "$S/f8443"; exit 0;;
esac
echo "unexpected: tailscale $*" >&2
exit 2
"""

CAPS_OK = json.dumps({"Self": {"DNSName": f"{NODE}.", "CapMap": {"https": None, "funnel": None}}})
CAPS_NO_FUNNEL = json.dumps({"Self": {"DNSName": f"{NODE}.", "CapMap": {"https": None}}})


def _posix(p: Path) -> str:
    return str(p).replace("\\", "/")


class Host:
    """A sandbox: fake bin on PATH, a fake state, a fake /opt/pagentos/.env."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.state = root / "state"
        self.env_file = root / "pagentos.env"
        self.bin.mkdir()
        self.state.mkdir()
        (self.bin / "tailscale").write_bytes(FAKE_TAILSCALE.encode("utf-8"))
        self.set_caps(CAPS_OK)
        # The secret on the first AND the last line: no reading of the file may leak it.
        self.write_env(
            f"PAGENTOS_TWILIO_AUTH_TOKEN={SECRET}\nPAGENTOS_BIND_IP={IP}\n"
            f"PAGENTOS_TWILIO_AUTH_TOKEN_OLD={SECRET}\n"
        )

    def set_caps(self, text: str) -> None:
        (self.state / "status.json").write_bytes(text.encode("utf-8"))

    def write_env(self, text: str) -> None:
        self.env_file.write_bytes(text.encode("utf-8"))

    def mounts(self) -> list[str]:
        f = self.state / "m8443"
        return f.read_text("utf-8").splitlines() if f.exists() else []

    def seed(self, lines: list[str], funnel_8443: bool = True, funnel_443: bool = False) -> None:
        (self.state / "m8443").write_bytes("".join(ln + "\n" for ln in lines).encode("utf-8"))
        if funnel_8443:
            (self.state / "f8443").touch()
        if funnel_443:
            (self.state / "f443").touch()

    def calls(self) -> list[str]:
        log = self.state / "calls.log"
        return log.read_text("utf-8").splitlines() if log.exists() else []

    def changes(self) -> list[str]:
        """Every call that is not a read."""
        reads = ("tailscale status --json", "tailscale serve status --json")
        return [c for c in self.calls() if c not in reads]

    def clear_calls(self) -> None:
        (self.state / "calls.log").unlink(missing_ok=True)

    def run(self, *flags: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update(
            FAKE_STATE=_posix(self.state),
            PAGENTOS_ENV_FILE=_posix(self.env_file),
            PAGENTOS_JSON_PYTHON=_posix(Path(sys.executable)),
            PAGENTOS_TS_TIMEOUT_S="20",
            PATH=str(self.bin) + os.pathsep + env.get("PATH", ""),
        )
        env.update(extra)
        proc = subprocess.run(
            [str(BASH), _posix(SCRIPT), *flags],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )
        return subprocess.CompletedProcess(
            proc.args,
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
        )


@pytest.fixture
def host(tmp_path: Path) -> Host:
    # Not a skip: this machine has Git Bash, and a missing one must not turn the proof green.
    assert BASH.is_file(), f"Git Bash not found at {BASH}"
    assert SCRIPT.is_file(), f"{SCRIPT} does not exist"
    return Host(tmp_path)


def _ours() -> list[str]:
    return [f"{r} http://{IP}:8001{r}" for r in ROOTS]


# ----------------------------------------------------------------------------- (a) (b) open


def test_open_issues_exactly_two_funnel_commands_on_8443_for_the_two_roots(host: Host) -> None:
    r = host.run()
    assert r.returncode == 0, r.stdout + r.stderr
    funnel = [c for c in host.calls() if c.startswith("tailscale funnel")]
    assert funnel == [
        f"tailscale funnel --bg --yes --https=8443 --set-path={root} http://{IP}:8001{root}"
        for root in ROOTS
    ]
    assert host.changes() == funnel, "something besides the two funnel commands changed the state"
    assert not any("443" in c.replace("8443", "") for c in host.calls()), "a command named 443"
    assert f"FUNNEL https://{NODE}:8443 -> http://{IP}:8001 (telefon yolu" in r.stdout
    assert sorted(host.mounts()) == sorted(_ours())


def test_a_second_run_issues_no_tailscale_command_and_says_already(host: Host) -> None:
    assert host.run().returncode == 0
    host.clear_calls()
    r = host.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALREADY" in r.stdout
    assert host.changes() == []


def test_one_root_missing_is_completed_without_touching_the_other(host: Host) -> None:
    host.seed(_ours()[:1])
    r = host.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert host.changes() == [
        f"tailscale funnel --bg --yes --https=8443 --set-path={ROOTS[1]} http://{IP}:8001{ROOTS[1]}"
    ]


# ----------------------------------------------------------------------------- (c) (d) stop


def test_a_funnel_on_443_stops_with_4_and_changes_nothing(host: Host) -> None:
    host.seed([], funnel_8443=False, funnel_443=True)
    r = host.run()
    assert r.returncode == 4, r.stdout + r.stderr
    assert "443" in r.stderr and "STOP" in r.stderr
    assert host.changes() == [], "a funnel on 443 must be left to the owner - no command at all"


def test_a_third_mount_on_8443_stops_with_4_and_closes_8443(host: Host) -> None:
    host.seed([*_ours(), f"/ http://{IP}:8001"])
    r = host.run()
    assert r.returncode == 4, r.stdout + r.stderr
    assert "STOP: beklenmeyen Funnel durumu" in r.stderr
    assert host.changes() == ["tailscale serve --yes --https=8443 off"]
    assert host.mounts() == []


def test_a_root_aimed_at_another_target_is_unexpected(host: Host) -> None:
    host.seed([f"{ROOTS[0]} http://{IP}:8001", _ours()[1]])
    r = host.run()
    assert r.returncode == 4, r.stdout + r.stderr
    assert host.changes() == ["tailscale serve --yes --https=8443 off"]


# ----------------------------------------------------------------------------- --status, --off


def test_status_exit_codes(host: Host) -> None:
    r = host.run("--status")
    assert r.returncode == 1 and "NOT FUNNELED" in r.stdout
    host.run()
    host.clear_calls()
    r = host.run("--status")
    assert r.returncode == 0 and f"FUNNEL https://{NODE}:8443" in r.stdout
    assert host.changes() == []
    host.seed([*_ours(), f"/admin http://{IP}:8001/admin"])
    assert host.run("--status").returncode == 4
    assert host.changes() == []


def test_off_removes_the_two_mounts_and_never_touches_443(host: Host) -> None:
    host.run()
    host.clear_calls()
    r = host.run("--off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert host.changes() == [
        f"tailscale serve --https=8443 --set-path={root} off" for root in ROOTS
    ]
    assert host.mounts() == [] and not (host.state / "f8443").exists()
    assert not any("443" in c.replace("8443", "") for c in host.calls())
    host.clear_calls()
    r = host.run("--off")
    assert r.returncode == 0 and host.changes() == []


# ----------------------------------------------------------------------------- prerequisites


def test_no_bind_ip_in_the_env_file_is_exit_2_with_no_command(host: Host) -> None:
    host.write_env(f"PAGENTOS_TWILIO_AUTH_TOKEN={SECRET}\n")
    r = host.run()
    assert r.returncode == 2, r.stdout + r.stderr
    assert "set-cloud-secret.ps1" in r.stderr
    assert host.calls() == []


def test_funnel_not_enabled_for_the_node_is_exit_3_and_the_owner_line(host: Host) -> None:
    host.set_caps(CAPS_NO_FUNNEL)
    r = host.run()
    assert r.returncode == 3, r.stdout + r.stderr
    assert "OWNER STEP: Funnel is not enabled for this node" in r.stderr
    assert '"attr": ["funnel"]' in r.stderr
    assert host.changes() == []


def test_a_funnel_command_that_returns_0_through_the_consent_flow_is_exit_3(host: Host) -> None:
    r = host.run(FAKE_TS_CONSENT="1")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "OWNER STEP: Funnel is not enabled" in r.stderr
    assert host.mounts() == []


def test_without_python_the_script_fails_closed_with_2(host: Host) -> None:
    r = host.run(PAGENTOS_JSON_PYTHON="/no/such/python3")
    assert r.returncode == 2, r.stdout + r.stderr
    assert host.changes() == []


def test_tailscale_down_is_exit_2(host: Host) -> None:
    r = host.run(FAKE_TS_DOWN="1")
    assert r.returncode == 2 and host.changes() == []


def test_an_unknown_flag_is_a_usage_error(host: Host) -> None:
    r = host.run("--https=443")
    assert r.returncode == 64 and host.calls() == []


# ----------------------------------------------------------------------------- (g) the secret


def test_no_mode_ever_prints_a_line_of_the_env_file_but_the_bind_ip(host: Host) -> None:
    runs = [host.run(), host.run(), host.run("--status")]
    host.seed([*_ours(), f"/ http://{IP}:8001"])
    runs.append(host.run())
    runs.append(host.run("--off"))
    runs.append(host.run("--status"))
    host.set_caps(CAPS_NO_FUNNEL)
    runs.append(host.run())
    runs.append(host.run("--help"))
    host.write_env(f"PAGENTOS_TWILIO_AUTH_TOKEN={SECRET}\nPAGENTOS_BIND_IP=not-an-ip-{SECRET}\n")
    runs.append(host.run())
    assert {r.returncode for r in runs} >= {0, 1, 2, 3, 4}
    for r in runs:
        assert SECRET not in r.stdout and SECRET not in r.stderr, r.args


# ----------------------------------------------------------------------------- (h) absences


def test_the_script_text_never_resets_and_never_commands_443() -> None:
    text = SCRIPT.read_text("utf-8")
    assert "serve reset" not in text and "funnel reset" not in text
    assert "--https=443" not in text, "443 appears in this script only as a JSON port it compares"
    assert "\r" not in text, "the script is LF-only (bash)"
