#!/usr/bin/env bash
# HTTPS on the tailnet for the owner's web shell, run ONCE on the Cloud Core host by the lead
# (docs/DECISIONS.md, "web on the Cloud Core"). The `web` compose service listens on the
# host's loopback only (127.0.0.1:3000); this puts `tailscale serve` in front of it so the
# owner's phone - signed into his tailnet - reaches https://<this-host>.<tailnet>.ts.net/
# over a real certificate. A browser gives the microphone only to a secure context, which is
# why plain http on the tailnet address is not enough for the voice page.
#
#   enable-web-tailnet-https.sh              serve it (idempotent: a second run changes nothing)
#   enable-web-tailnet-https.sh --status     print what is served; exit 0 served, 1 not served,
#                                            4 a Funnel (public exposure) is on, beyond the
#                                            telephony path below
#   enable-web-tailnet-https.sh --off        stop serving it (idempotent)
#   --port N                                 the loopback port of the web service (default 3000)
#
# The serve configuration is persistent (`--bg`): it survives a reboot and a tailscaled
# restart; nothing here needs a unit or a cron.
#
# NEVER PUBLIC. This script does not run `tailscale funnel` in any form - not to enable, not
# to disable, not to look: the tailscale wrapper below refuses it. It reads the Funnel state
# per port from `tailscale serve status --json`: a Funnel on 443 (this web shell) or on
# anything but the telephony path stops the script (exit 4) and tells you to turn it off by
# hand. The one Funnel it tolerates is enable-telephony-funnel.sh's: port 8443, exactly
# /telephony/inbound and /v1/telephony/audio (ADR inbound-calls-public-path, owner review
# pending) - Funnel is per port, so 443 stays tailnet-only beside it.
#
# Exit codes: 0 ok; 1 serve failed / (--status) not served; 2 tailscale is missing, not
# running or not logged in; 3 an OWNER step is missing (HTTPS certificates are not enabled
# for the tailnet - the one line printed below says what to click); 4 a Funnel is on beyond
# the telephony path (or one is named and python3 is missing to verify it); 64 usage.
#
# House style: set -eu -o pipefail; status lines to stderr, the machine-readable result to
# stdout. Env overrides (tests): TAILSCALE_BIN, PAGENTOS_WEB_PORT, PAGENTOS_TS_TIMEOUT_S,
# PAGENTOS_JSON_PYTHON.
set -eu -o pipefail

ts_bin=${TAILSCALE_BIN:-tailscale}
port=${PAGENTOS_WEB_PORT:-3000}
ts_timeout=${PAGENTOS_TS_TIMEOUT_S:-60}
mode=enable

say() { echo "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --status) mode=status;;
        --off) mode=off;;
        --port) shift; port=${1:-};;
        --port=*) port=${1#--port=};;
        -h|--help) sed -n '2,34p' "$0" >&2; exit 0;;
        *) say "usage: $0 [--status | --off] [--port N]"; exit 64;;
    esac
    shift
done
case "$port" in
    ''|*[!0-9]*) say "usage: --port wants a whole number, got '$port'"; exit 64;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then say "usage: --port wants 1..65535"; exit 64; fi

target="http://127.0.0.1:$port"

# The only door to tailscale. `funnel` publishes to the PUBLIC internet and is refused here
# whatever the caller meant; only `serve` and `status` are ever run.
ts() {
    local a
    for a in "$@"; do
        case "$a" in
            funnel|--funnel|--funnel=*|funnel=*) say "REFUSED: tailscale funnel exposes the service to the public internet; this script never runs it"; exit 64;;
        esac
    done
    case "${1:-}" in
        serve|status) ;;
        *) say "REFUSED: this script runs only 'tailscale serve' and 'tailscale status' (asked: $*)"; exit 64;;
    esac
    # Never wait for a consent page or a prompt: stdin is the null device and the call is bounded.
    if command -v timeout >/dev/null 2>&1; then
        timeout "$ts_timeout" "$ts_bin" "$@" </dev/null
    else
        "$ts_bin" "$@" </dev/null
    fi
}

# The one line for the owner when certificates are not enabled (an owner step: only he can
# click it in the Tailscale admin console).
owner_step() {
    say "OWNER STEP: HTTPS certificates are not enabled for this tailnet. In the Tailscale admin console (https://login.tailscale.com/admin/dns) make sure MagicDNS is on, then under 'HTTPS Certificates' click 'Enable HTTPS'; then re-run this script."
}

not_enabled_pattern='not enabled on your tailnet|f/serve\?node=|HTTPS (cert[a-z]* )?(support )?(is )?not enabled|certificates? .*not (been )?(enabled|configured)'

if ! command -v "$ts_bin" >/dev/null 2>&1; then say "tailscale is not installed on this host ($ts_bin not found)"; exit 2; fi

rc=0
status_out="$(ts status 2>&1)" || rc=$?
if [ "$rc" -ne 0 ]; then
    say "tailscale is not running or not logged in on this host (tailscale status exited $rc):"
    printf '%s\n' "$status_out" | tail -3 >&2
    exit 2
fi

# What is served now. `tailscale serve status` prints "No serve config" when nothing is.
rc=0
serve_out="$(ts serve status 2>&1)" || rc=$?
if [ "$rc" -ne 0 ]; then
    if printf '%s\n' "$serve_out" | grep -Eqi "$not_enabled_pattern"; then owner_step; exit 3; fi
    say "tailscale serve status failed ($rc):"; printf '%s\n' "$serve_out" | tail -5 >&2; exit 1
fi

serving_ours() { printf '%s\n' "$serve_out" | grep -Fq "proxy $target"; }
served_url() { printf '%s\n' "$serve_out" | grep -m1 -oE 'https://[^ ]+' || true; }

# Funnel, read PER PORT from `tailscale serve status --json` (AllowFunnel is keyed
# "<node>:<port>"). One Funnel is expected and tolerated: the telephony path of
# enable-telephony-funnel.sh - port 8443, exactly these two roots and nothing else
# (ADR inbound-calls-public-path; owner review pending). Funnel on 443 (this web shell) or
# anything else funneled is a STOP, as before. The roots are the same line as in
# enable-telephony-funnel.sh; a test holds the two equal.
telephony_mounts="/telephony/inbound /v1/telephony/audio"
py=${PAGENTOS_JSON_PYTHON:-python3}
# Same reading as enable-telephony-funnel.sh: three header lines (node, proxy base, roots),
# then the JSON; prints the verdict none | partial | ours | funnel443 | bad.
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
mounts = {}
for k, v in (cfg.get("Web") or {}).items():
    if k.rsplit(":", 1)[-1] == "8443":
        mounts.update((v or {}).get("Handlers") or {})
tcp = ((cfg.get("TCP") or {}).get("8443")) or {}
missing = [m for m in want if m not in mounts]
extra = [m for m in mounts if m not in want]
def proxy_ok(m):
    p = (((mounts[m] or {}).get("Proxy")) or "").rstrip("/")
    return p == base + m if base else p.endswith(m)
if "443" in ports:
    verdict = "funnel443"
elif (ports - {"8443"}) or fg_bad or extra or tcp.get("TCPForward") or not all(proxy_ok(m) for m in mounts if m in want):
    verdict = "bad"
elif not missing and "8443" in ports:
    verdict = "ours"
elif not mounts and not ports:
    verdict = "none"
else:
    verdict = "partial"
sys.stdout.write(verdict + "\n")
'
funnel_verdict=""
# Sets funnel_verdict; a JSON that cannot be read fails CLOSED: without python3 any
# AllowFunnel at all is a STOP - the public-exposure decision is never left to a regex.
read_funnel() {
    local rc=0 json
    json="$(ts serve status --json 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then say "tailscale serve status --json failed ($rc):"; printf '%s\n' "$json" | tail -5 >&2; exit 1; fi
    if MSYS_NO_PATHCONV=1 "$py" -c 'import json' >/dev/null 2>&1; then
        funnel_verdict="$(printf '\n\n%s\n%s\n' "$telephony_mounts" "$json" | MSYS_NO_PATHCONV=1 "$py" -c "$classify_py" | tr -d '\r')" || funnel_verdict=unreadable
    elif printf '%s' "$json" | grep -q '"AllowFunnel"'; then
        funnel_verdict=unreadable
    else
        funnel_verdict=none
    fi
    serve_json="$json"
}
serve_json=""
told_phone=""
funnel_stop() {
    case "$funnel_verdict" in
        none) return 1;;
        ours|partial)
            [ -n "$told_phone" ] || say "Funnel 8443: telefon yolu (enable-telephony-funnel.sh), web 443 tailnet'te"
            told_phone=1; return 1;;
        unreadable) say "STOP: the serve status JSON names a Funnel and cannot be verified here ($py not runnable): treated as PUBLIC exposure.";;
        *) say "STOP: a Funnel is ON for this node beyond the telephony path (443, another port, or another root on 8443): that is PUBLIC exposure. This script does not touch Funnel; turn it off yourself (Tailscale admin console; enable-telephony-funnel.sh --off for 8443) and re-run.";;
    esac
    printf '%s\n' "$serve_json" >&2
    return 0
}

read_funnel
if funnel_stop; then exit 4; fi

case "$mode" in
    status)
        printf '%s\n' "$serve_out"
        if serving_ours; then
            echo "SERVED $(served_url) -> $target (tailnet only)"
            exit 0
        fi
        echo "NOT SERVED: nothing on this node proxies $target"
        exit 1;;

    off)
        if ! serving_ours; then
            echo "OFF: nothing proxies $target; nothing to do"
            exit 0
        fi
        rc=0
        out="$(ts serve --https=443 off 2>&1)" || rc=$?
        if [ "$rc" -ne 0 ]; then say "tailscale serve --https=443 off failed ($rc):"; printf '%s\n' "$out" | tail -5 >&2; exit 1; fi
        echo "OFF: $target is no longer served"
        exit 0;;
esac

# enable
if serving_ours; then
    echo "ALREADY SERVED $(served_url) -> $target (tailnet only); nothing changed"
    exit 0
fi
if command -v curl >/dev/null 2>&1 && ! curl -fsS -m 5 -o /dev/null "$target/" 2>/dev/null; then
    say "note: nothing answers on $target yet (the web service is not up). Serving it anyway; it works as soon as the container is."
fi
rc=0
out="$(ts serve --bg --https=443 "$target" 2>&1)" || rc=$?
if [ "$rc" -ne 0 ]; then
    # A tailnet whose HTTPS certificates are off makes serve print a consent-page link and
    # exit non-zero (or, with no terminal, wait until the bound above kills it: 124).
    if printf '%s\n' "$out" | grep -Eqi "$not_enabled_pattern"; then owner_step; exit 3; fi
    say "tailscale serve failed ($rc):"; printf '%s\n' "$out" | tail -5 >&2; exit 1
fi
rc=0
serve_out="$(ts serve status 2>&1)" || rc=$?
read_funnel
if funnel_stop; then say "STOP: after serving, the status shows a Funnel beyond the telephony path; this script never enables one"; exit 4; fi
if serving_ours; then
    echo "SERVED $(served_url) -> $target (tailnet only)"
    exit 0
fi
say "tailscale serve returned 0 but the status does not show $target:"; printf '%s\n' "$serve_out" | tail -5 >&2
exit 1
