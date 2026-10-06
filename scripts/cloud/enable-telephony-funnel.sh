#!/usr/bin/env bash
# The ONE public path of the Cloud Core, for telephony (ADR, inbound-calls-public-path; owner
# review pending). Run on the Cloud Core host as root by the lead / the owner. Twilio's inbound
# call webhooks (https POST), its Media Streams socket (wss) and the outbound call's one-time
# audio link must be reachable from the internet; the host has no domain and no public
# application port. A Tailscale Funnel on port 8443 of the node's own ts.net name gives a real
# certificate and WebSocket pass-through, and only for the path roots mounted on it:
#
#   https://<node>.<tailnet>.ts.net:8443/telephony/inbound   -> http://${PAGENTOS_BIND_IP}:8001/telephony/inbound
#   https://<node>.<tailnet>.ts.net:8443/v1/telephony/audio  -> http://${PAGENTOS_BIND_IP}:8001/v1/telephony/audio
#
# Funnel strips the mount and appends the rest to the target's path, so the target carries the
# same root and the edge nginx sees the path unchanged. Any other path on 8443 is a 404 in
# tailscaled and never reaches the edge.
#
# Port 443 is NEVER funneled: Funnel is per port, and 443 carries the web shell
# (enable-web-tailnet-https.sh), which stays tailnet-only. This script never issues a command
# for 443 and never resets the serve configuration (that would wipe the web shell's 443 entry):
# the tailscale wrapper below refuses both.
#
#   enable-telephony-funnel.sh            open the two mounts on 8443 with Funnel (idempotent:
#                                         a second run issues no tailscale command, says ALREADY)
#   enable-telephony-funnel.sh --status   print the state; exit 0 exactly the two roots are
#                                         funneled on 8443, 1 off (or incomplete), 4 a Funnel on
#                                         443 or anything else on 8443
#   enable-telephony-funnel.sh --off      remove the two mounts (and with them the 8443 Funnel);
#                                         443 is not touched; then verify
#
# Before any funnel command the node's capabilities are read (`tailscale status --json`,
# Self.CapMap): without "funnel" the CLI prints a consent link and exits 0 having done nothing,
# so its exit code cannot be trusted. After the commands `tailscale serve status --json` is
# read again and VERIFIED: AllowFunnel only for <node>:8443, the 8443 mounts exactly the two
# roots with their targets, no Funnel on 443. Any mismatch closes 8443 and stops (exit 4).
#
# Exit codes: 0 ok; 1 a tailscale command failed / (--status) not funneled or incomplete;
# 2 a prerequisite is missing (tailscale not installed/running, PAGENTOS_BIND_IP not in the
# env file, no python3 to read the JSON); 3 an OWNER step is missing (HTTPS certificates or
# the Funnel node attribute - the one line printed says what to click); 4 unexpected Funnel
# state (Funnel on 443, or anything but the two roots on 8443); 64 usage.
#
# Only PAGENTOS_BIND_IP is read from the env file - no other line of it reaches any output.
#
# House style: set -eu -o pipefail; status lines to stderr, the machine-readable result to
# stdout. Env overrides (tests): TAILSCALE_BIN, PAGENTOS_ENV_FILE, PAGENTOS_JSON_PYTHON,
# PAGENTOS_TS_TIMEOUT_S.
set -eu -o pipefail

ts_bin=${TAILSCALE_BIN:-tailscale}
env_file=${PAGENTOS_ENV_FILE:-/opt/pagentos/.env}
py=${PAGENTOS_JSON_PYTHON:-python3}
ts_timeout=${PAGENTOS_TS_TIMEOUT_S:-60}
mode=enable

# The contract (inbound-calls-bridge card): these two roots and nothing else, on this port.
# enable-web-tailnet-https.sh carries the same line; a test holds the two equal.
telephony_mounts="/telephony/inbound /v1/telephony/audio"
funnel_port=8443
edge_port=8001

say() { echo "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --status) mode=status;;
        --off) mode=off;;
        -h|--help) sed -n '2,45p' "$0" >&2; exit 0;;
        *) say "usage: $0 [--status | --off]"; exit 64;;
    esac
    shift
done

# The only door to tailscale. Refused whatever the caller meant: a reset (wipes 443 too), any
# --https= but 8443, the legacy positional form (`funnel 8443 on`), and a funnel without one of
# the two contract roots.
ts() {
    local a has_port=0 has_root=0
    case "${1:-}" in
        status|serve|funnel) ;;
        *) say "REFUSED: only 'tailscale status|serve|funnel' are run here (asked: $*)"; exit 64;;
    esac
    for a in "$@"; do
        case "$a" in
            reset) say "REFUSED: a reset would remove the web shell's 443 entry too"; exit 64;;
            --https=$funnel_port) has_port=1;;
            --https=*|--tcp=*|--tls-terminated-tcp=*) say "REFUSED: this script acts on port $funnel_port only (asked: $*)"; exit 64;;
            [0-9]*) say "REFUSED: the positional port form is never used (asked: $*)"; exit 64;;
            --set-path=*)
                case " $telephony_mounts " in *" ${a#--set-path=} "*) has_root=1;; esac;;
        esac
    done
    if [ "$1" = funnel ] && { [ "$has_port" -ne 1 ] || [ "$has_root" -ne 1 ]; }; then
        say "REFUSED: funnel only with --https=$funnel_port and one of: $telephony_mounts (asked: $*)"; exit 64
    fi
    if [ "$1" = serve ] && [ "${2:-}" != status ] && [ "$has_port" -ne 1 ]; then
        say "REFUSED: serve changes only port $funnel_port here (asked: $*)"; exit 64
    fi
    # Never wait for a consent page or a prompt: stdin is the null device and the call is bounded.
    if command -v timeout >/dev/null 2>&1; then
        timeout "$ts_timeout" "$ts_bin" "$@" </dev/null
    else
        "$ts_bin" "$@" </dev/null
    fi
}

owner_step_https() {
    say "OWNER STEP: HTTPS certificates are not enabled for this tailnet. In the Tailscale admin console (https://login.tailscale.com/admin/dns) make sure MagicDNS is on, then under 'HTTPS Certificates' click 'Enable HTTPS'; then re-run this script."
}
owner_step_funnel() {
    say "OWNER STEP: Funnel is not enabled for this node. Tailscale admin console -> Access controls (policy file, https://login.tailscale.com/admin/acls/file) -> add to nodeAttrs: {\"target\": [\"autogroup:member\"], \"attr\": [\"funnel\"]} (or target only this node/tag); save, then re-run this script."
}
funnel_not_enabled_pattern='Funnel (is )?not (available|enabled)|f/funnel\?node=|funnel" node attribute not set|not allowed for funnel'

# Reads a JSON document on stdin after three header lines (node, expected proxy base, roots)
# and prints: verdict (none | partial | ours | funnel443 | bad), the roots present on 8443,
# the roots missing. Same reading as enable-web-tailnet-https.sh.
classify_py='
import json, sys
head = sys.stdin.read().split("\n", 3)
node, base, want = head[0].strip(), head[1].strip(), head[2].split()
body = head[3].strip() if len(head) > 3 else ""
cfg = json.loads(body) if body else {}
def funnel_ports(c):
    return {k.rsplit(":", 1)[-1] for k, v in ((c or {}).get("AllowFunnel") or {}).items() if v}
ports = funnel_ports(cfg)
fg = list(((cfg.get("Foreground") or {}).values()))
fg_bad = any(funnel_ports(c) or any(k.endswith(":8443") for k in ((c or {}).get("Web") or {})) for c in fg)
mounts, hosts = {}, set()
for k, v in (cfg.get("Web") or {}).items():
    if k.rsplit(":", 1)[-1] == "8443":
        hosts.add(k.rsplit(":", 1)[0])
        mounts.update((v or {}).get("Handlers") or {})
hosts |= {k.rsplit(":", 1)[0] for k, v in (cfg.get("AllowFunnel") or {}).items() if v and k.endswith(":8443")}
tcp = ((cfg.get("TCP") or {}).get("8443")) or {}
present = [m for m in want if m in mounts]
missing = [m for m in want if m not in mounts]
extra = [m for m in mounts if m not in want]
def proxy_ok(m):
    p = (((mounts[m] or {}).get("Proxy")) or "").rstrip("/")
    return p == base + m if base else p.endswith(m)
if "443" in ports:
    verdict = "funnel443"
elif (ports - {"8443"}) or fg_bad or extra or tcp.get("TCPForward") or not all(proxy_ok(m) for m in present) or (node and hosts - {node}):
    verdict = "bad"
elif not missing and "8443" in ports:
    verdict = "ours"
elif not mounts and not ports:
    verdict = "none"
else:
    verdict = "partial"
sys.stdout.write(verdict + "\n" + " ".join(present) + "\n" + " ".join(missing) + "\n")
'
# Reads `tailscale status --json`; prints the node name (no trailing dot) and the capability
# answer: ok | nohttps | nofunnel | noport.
caps_py='
import json, sys
s = json.loads(sys.stdin.read() or "{}").get("Self") or {}
caps = s.get("CapMap") or {}
name = (s.get("DNSName") or "").rstrip(".")
ans = "ok"
if "https" not in caps:
    ans = "nohttps"
elif "funnel" not in caps:
    ans = "nofunnel"
else:
    for k in caps:
        if k.startswith("https://tailscale.com/cap/funnel-ports?ports="):
            allowed = k.split("=", 1)[1].split(",")
            if not any(p == "8443" or ("-" in p and int(p.split("-")[0]) <= 8443 <= int(p.split("-")[1])) for p in allowed):
                ans = "noport"
sys.stdout.write(name + "\n" + ans + "\n")
'
run_py() { MSYS_NO_PATHCONV=1 "$py" -c "$1" | tr -d '\r'; }

if ! command -v "$ts_bin" >/dev/null 2>&1; then say "tailscale is not installed on this host ($ts_bin not found)"; exit 2; fi

# PAGENTOS_BIND_IP and nothing else from the env file: never sourced, never echoed whole.
bind_ip=""
if [ -r "$env_file" ]; then
    bind_ip=$({ grep -E '^PAGENTOS_BIND_IP=' "$env_file" || true; } | head -1 | cut -d= -f2- | tr -d '\r"'"'"' ')
fi
if ! printf '%s' "$bind_ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    say "PAGENTOS_BIND_IP is missing or not an IPv4 address in $env_file; put it there with set-cloud-secret.ps1 (set-cloud-secret.ps1 ile koy) and re-run"
    exit 2
fi
target_base="http://$bind_ip:$edge_port"

if ! MSYS_NO_PATHCONV=1 "$py" -c 'import json' >/dev/null 2>&1; then
    say "python3 is needed to read 'tailscale serve status --json' ($py not runnable); the public-path decision is never left to a pattern match"
    exit 2
fi

rc=0
self_json="$(ts status --json 2>/dev/null)" || rc=$?
if [ "$rc" -ne 0 ]; then say "tailscale is not running or not logged in on this host (tailscale status exited $rc)"; exit 2; fi
caps_out="$(printf '%s' "$self_json" | run_py "$caps_py")" || { say "could not read 'tailscale status --json'"; exit 2; }
node=$(printf '%s\n' "$caps_out" | sed -n 1p)
caps=$(printf '%s\n' "$caps_out" | sed -n 2p)
if [ -z "$node" ]; then say "tailscale status --json names no DNSName for this node (MagicDNS off?)"; exit 2; fi
public_url="https://$node:$funnel_port"

verdict="" present="" missing="" serve_json=""
read_state() {
    local rc=0 out
    serve_json="$(ts serve status --json 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then say "tailscale serve status --json failed ($rc):"; printf '%s\n' "$serve_json" | tail -5 >&2; exit 1; fi
    out="$(printf '%s\n%s\n%s\n%s\n' "$node" "$target_base" "$telephony_mounts" "$serve_json" | run_py "$classify_py")" || { say "could not read 'tailscale serve status --json':"; printf '%s\n' "$serve_json" | tail -5 >&2; exit 1; }
    verdict=$(printf '%s\n' "$out" | sed -n 1p)
    present=$(printf '%s\n' "$out" | sed -n 2p)
    missing=$(printf '%s\n' "$out" | sed -n 3p)
}

# Close the whole of 8443 (the mismatch path): 443 is a separate key and stays.
close_8443() {
    local rc=0 out
    out="$(ts serve --yes --https=$funnel_port off 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then say "closing $funnel_port failed ($rc) - close it by hand: tailscale serve --yes --https=$funnel_port off"; printf '%s\n' "$out" | tail -5 >&2; fi
}

stop_unexpected() {
    say "STOP: beklenmeyen Funnel durumu ($1). State:"
    printf '%s\n' "$serve_json" >&2
}

read_state

case "$mode" in
    status)
        printf '%s\n' "$serve_json"
        case "$verdict" in
            ours) echo "FUNNEL $public_url -> $target_base (telefon yolu: $telephony_mounts)"; exit 0;;
            none) echo "NOT FUNNELED: nothing on $funnel_port"; exit 1;;
            partial) echo "INCOMPLETE: on $funnel_port: '${present}', missing: '${missing}'"; exit 1;;
            funnel443) stop_unexpected "Funnel on 443 - the web shell would be public; turn it off by hand"; exit 4;;
            *) stop_unexpected "something but the two telephony roots is on $funnel_port"; exit 4;;
        esac;;

    off)
        for m in $present; do
            rc=0
            out="$(ts serve --https=$funnel_port --set-path="$m" off 2>&1)" || rc=$?
            if [ "$rc" -ne 0 ]; then say "removing $m failed ($rc):"; printf '%s\n' "$out" | tail -5 >&2; exit 1; fi
        done
        read_state
        case "$verdict" in
            none) echo "OFF: nothing on $funnel_port is funneled; 443 untouched"; exit 0;;
            funnel443) stop_unexpected "the telephony roots are off, but a Funnel is on 443 - turn it off by hand"; exit 4;;
            *) close_8443; read_state
               if [ "$verdict" = none ]; then echo "OFF: $funnel_port closed (it held more than the two roots); 443 untouched"; exit 0; fi
               stop_unexpected "$funnel_port did not close"; exit 4;;
        esac;;
esac

# enable
case "$verdict" in
    ours) echo "ALREADY FUNNEL $public_url -> $target_base (telefon yolu: $telephony_mounts); nothing changed"; exit 0;;
    funnel443) stop_unexpected "Funnel on 443 - the web shell would be public; this script does not touch 443, turn it off by hand"; exit 4;;
    bad) stop_unexpected "something but the two telephony roots is on $funnel_port - closing $funnel_port"; close_8443; exit 4;;
esac

case "$caps" in
    ok) ;;
    nohttps) owner_step_https; exit 3;;
    nofunnel) owner_step_funnel; exit 3;;
    *) say "OWNER STEP: the tailnet's funnel-ports attribute does not allow $funnel_port; add it in the policy file, then re-run this script."; exit 3;;
esac

for m in $missing; do
    rc=0
    out="$(ts funnel --bg --yes --https=$funnel_port --set-path="$m" "$target_base$m" 2>&1)" || rc=$?
    if printf '%s\n' "$out" | grep -Eqi "$funnel_not_enabled_pattern"; then close_8443; owner_step_funnel; exit 3; fi
    if [ "$rc" -ne 0 ]; then
        say "tailscale funnel for $m failed ($rc) - closing $funnel_port:"; printf '%s\n' "$out" | tail -5 >&2
        close_8443; exit 1
    fi
done

read_state
case "$verdict" in
    ours) echo "FUNNEL $public_url -> $target_base (telefon yolu: $telephony_mounts)"; exit 0;;
    # The funnel command said 0 and nothing changed: the one known cause is the consent flow.
    none) owner_step_funnel; exit 3;;
    funnel443) stop_unexpected "a Funnel is on 443 after enabling $funnel_port - closing $funnel_port; turn 443 off by hand"; close_8443; exit 4;;
    partial) say "the funnel commands returned 0 but $funnel_port holds '${present}', missing '${missing}' - closing $funnel_port"; close_8443; exit 1;;
    *) stop_unexpected "verification failed - closing $funnel_port"; close_8443; exit 4;;
esac
