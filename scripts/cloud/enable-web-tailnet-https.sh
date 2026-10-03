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
#                                            4 a Funnel (public exposure) is on
#   enable-web-tailnet-https.sh --off        stop serving it (idempotent)
#   --port N                                 the loopback port of the web service (default 3000)
#
# The serve configuration is persistent (`--bg`): it survives a reboot and a tailscaled
# restart; nothing here needs a unit or a cron.
#
# NEVER PUBLIC. This script does not run `tailscale funnel` in any form - not to enable, not
# to disable, not to look: the tailscale wrapper below refuses it, and if the node's own
# status says a Funnel is on, the script stops (exit 4) and tells you to turn it off by hand.
# The tailnet is the boundary (constitution: no public application port).
#
# Exit codes: 0 ok; 1 serve failed / (--status) not served; 2 tailscale is missing, not
# running or not logged in; 3 an OWNER step is missing (HTTPS certificates are not enabled
# for the tailnet - the one line printed below says what to click); 4 a Funnel is on;
# 64 usage.
#
# House style: set -eu -o pipefail; status lines to stderr, the machine-readable result to
# stdout. Env overrides (tests): TAILSCALE_BIN, PAGENTOS_WEB_PORT, PAGENTOS_TS_TIMEOUT_S.
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
        -h|--help) sed -n '2,29p' "$0" >&2; exit 0;;
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
funnel_on() { printf '%s\n' "$serve_out" | grep -Eqi 'funnel on'; }
served_url() { printf '%s\n' "$serve_out" | grep -m1 -oE 'https://[^ ]+' || true; }

if funnel_on; then
    say "STOP: a Funnel is ON for this node (the status says 'Funnel on'): that is PUBLIC exposure. This script does not touch Funnel; turn it off yourself (Tailscale admin console, or 'tailscale serve reset' after reading what it serves) and re-run."
    printf '%s\n' "$serve_out" >&2
    exit 4
fi

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
if funnel_on; then say "STOP: the status now says a Funnel is on; this script never enables one - read: $serve_out"; exit 4; fi
if serving_ours; then
    echo "SERVED $(served_url) -> $target (tailnet only)"
    exit 0
fi
say "tailscale serve returned 0 but the status does not show $target:"; printf '%s\n' "$serve_out" | tail -5 >&2
exit 1
