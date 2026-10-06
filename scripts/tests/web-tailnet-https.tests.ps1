<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the web shell's HTTPS on the tailnet
    (docs/DECISIONS.md, "web on the Cloud Core"): scripts/cloud/enable-web-tailnet-https.sh
    under Git Bash with a fake `tailscale` on PATH. Nothing here touches a real tailnet.
.DESCRIPTION
    Proven here:
      * enabling calls `tailscale serve --bg --https=443 http://127.0.0.1:3000` - the
        loopback target, HTTPS 443, backgrounded (persistent) - and nothing else that serves;
      * a second run is idempotent: it sees the target served and changes nothing;
      * `tailscale funnel` (public exposure) is never called, on any path, and the script
        text has no funnel invocation; a Funnel found ON on 443 stops the script (exit 4);
      * the telephony Funnel (8443, exactly /telephony/inbound and /v1/telephony/audio, from
        enable-telephony-funnel.sh) is recognised: a 'telefon yolu' line and 443 is served;
        a third root on 8443 stops it (4); without python an AllowFunnel fails closed (4);
      * --off removes exactly the serve entry and is itself idempotent;
      * --status prints what is served (exit 0 served, 1 not served);
      * a tailnet without HTTPS certificates gives exit 3 and the one owner line naming
        MagicDNS and "Enable HTTPS", and serves nothing;
      * tailscale stopped / not logged in gives exit 2 and no serve call;
      * --port changes the loopback target; a bad port or flag is a usage error (64);
      * the script is LF-only.
    Run: powershell -NoProfile -File scripts\tests\web-tailnet-https.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\enable-web-tailnet-https.sh"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-webts-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$state = Join-Path $script:Sandbox "state"
$fakeBin = Join-Path $script:Sandbox "bin"
New-Item -ItemType Directory -Force -Path $fakeBin, $state | Out-Null
$u = { param($p) ($p -replace '\\', '/') }
$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }

# A fake tailscale with the real CLI's shapes: `serve status` prints "No serve config" or the
# https URL line and its "proxy" handler; `serve --bg --https=443 URL` stores the target;
# `serve --https=443 off` clears it; `funnel` is logged to its own file and fails. Knobs:
# FAKE_TS_DOWN (status fails), FAKE_TS_NOT_ENABLED (serve refuses with the real wording of an
# unenabled tailnet), FAKE_TS_FUNNEL (the serve status says "Funnel on" and its JSON has
# AllowFunnel on <node>:443), FAKE_TS_JSON (a file: `serve status --json` prints it as is).
$tailscale = @(
    '#!/usr/bin/env bash',
    'echo "tailscale $*" >> "$FAKE_STATE/calls.log"',
    'case "$1" in',
    '  funnel) echo "FUNNEL $*" >> "$FAKE_STATE/funnel.log"; echo "funnel is public" >&2; exit 99;;',
    '  status)',
    '    if [ -n "${FAKE_TS_DOWN:-}" ]; then echo "Tailscale is stopped."; exit 1; fi',
    '    echo "100.90.158.26  pagentos-core  owner@  linux  -"; exit 0;;',
    '  serve)',
    '    shift',
    '    case "$*" in',
    '      "status --json")',
    '        if [ -n "${FAKE_TS_JSON:-}" ]; then cat "$FAKE_TS_JSON"; exit 0; fi',
    '        if [ -s "$FAKE_STATE/serve" ]; then',
    '          af=""; [ -n "${FAKE_TS_FUNNEL:-}" ] && af=",\"AllowFunnel\":{\"pagentos-core.tail1234.ts.net:443\":true}"',
    '          printf "{\"TCP\":{\"443\":{\"HTTPS\":true}},\"Web\":{\"pagentos-core.tail1234.ts.net:443\":{\"Handlers\":{\"/\":{\"Proxy\":\"%s\"}}}}%s}\n" "$(cat "$FAKE_STATE/serve")" "$af"',
    '        else echo "{}"; fi',
    '        exit 0;;',
    '      status)',
    '        if [ -s "$FAKE_STATE/serve" ]; then',
    '          label="tailnet only"; [ -n "${FAKE_TS_FUNNEL:-}" ] && label="Funnel on"',
    '          printf "https://pagentos-core.tail1234.ts.net (%s)\n|-- / proxy %s\n" "$label" "$(cat "$FAKE_STATE/serve")"',
    '        else echo "No serve config"; fi',
    '        exit 0;;',
    '      "--bg --https=443 "*)',
    '        if [ -n "${FAKE_TS_NOT_ENABLED:-}" ]; then',
    '          printf "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/serve?node=n123\n" >&2; exit 1',
    '        fi',
    '        printf "%s" "${3}" > "$FAKE_STATE/serve"',
    '        printf "Available within your tailnet:\n\nhttps://pagentos-core.tail1234.ts.net/\n|-- proxy %s\n" "$3"; exit 0;;',
    '      "--https=443 off") : > "$FAKE_STATE/serve"; exit 0;;',
    '    esac',
    '    echo "unexpected: tailscale serve $*" >&2; exit 2;;',
    'esac',
    'exit 0'
)
[IO.File]::WriteAllText((Join-Path $fakeBin "tailscale"), (($tailscale -join "`n") + "`n"))

function Reset-State {
    Remove-Item -LiteralPath $state -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $state | Out-Null
}
function Invoke-Web {
    param([string[]]$Flags = @(), [hashtable]$Env = @{})
    $cmd = "FAKE_STATE='$(& $u $state)' PAGENTOS_TS_TIMEOUT_S=20 " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" bash '$(& $u $hostScript)' $($Flags -join ' ') 2>&1"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $bash -c (ConvertTo-NativeCallArgument -Value $cmd) 2>&1 | Out-String; $exit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
    $log = Join-Path $state "calls.log"
    $calls = @(if (Test-Path $log) { Get-Content $log })
    return [pscustomobject]@{ Output = $out; Exit = $exit; Calls = $calls }
}
function Get-Served { $p = Join-Path $state "serve"; if (Test-Path $p) { ([IO.File]::ReadAllText($p)).Trim() } else { "" } }
function Test-FunnelCalled { (Test-Path (Join-Path $state "funnel.log")) }
function Clear-Calls { Remove-Item -LiteralPath (Join-Path $state "calls.log") -ErrorAction SilentlyContinue }

try {
    if (-not (Test-Path $bash)) { Write-Host "  SKIP  web tailnet HTTPS tests: Git Bash not found at $bash" }
    else {
        Write-Host "web shell on the tailnet (enable-web-tailnet-https.sh under Git Bash, fake tailscale)"
        $target = "http://127.0.0.1:3000"

        Reset-State
        $r = Invoke-Web
        if ($r.Exit -ne 0 -or $env:PAGENTOS_WEBTS_VERBOSE) { Write-Host $r.Output; $r.Calls | ForEach-Object { Write-Host "    $_" } }
        $serveCalls = @($r.Calls | Where-Object { $_ -match "^tailscale serve --bg" })
        Assert-True ($r.Exit -eq 0 -and $serveCalls.Count -eq 1 -and $serveCalls[0] -eq "tailscale serve --bg --https=443 $target") "enabling calls exactly: tailscale serve --bg --https=443 http://127.0.0.1:3000 (loopback target, HTTPS 443, background = persistent)"
        Assert-True ((Get-Served) -eq $target -and $r.Output -match "SERVED https://pagentos-core.tail1234.ts.net/? -> $([regex]::Escape($target)) \(tailnet only\)") "the target is served and the result line names the tailnet URL"
        Assert-True (-not (Test-FunnelCalled) -and -not ($r.Calls -match "funnel")) "enabling never calls tailscale funnel"

        Clear-Calls
        $r2 = Invoke-Web
        Assert-True ($r2.Exit -eq 0 -and $r2.Output -match "ALREADY SERVED" -and $r2.Output -match "nothing changed" -and @($r2.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0 -and (Get-Served) -eq $target) "a second run is idempotent: it reads the status, sees the target served, and issues no serve change"
        Assert-True (-not (Test-FunnelCalled)) "the idempotent run did not call funnel either"

        $rs = Invoke-Web -Flags @("--status")
        Assert-True ($rs.Exit -eq 0 -and $rs.Output -match "proxy $([regex]::Escape($target))" -and $rs.Output -match "SERVED " -and @($rs.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0) "--status prints what is served, exits 0, and changes nothing"

        Clear-Calls
        $ro = Invoke-Web -Flags @("--off")
        Assert-True ($ro.Exit -eq 0 -and (Get-Served) -eq "" -and @($ro.Calls | Where-Object { $_ -eq "tailscale serve --https=443 off" }).Count -eq 1 -and $ro.Output -match "OFF: ") "--off removes the serve entry with: tailscale serve --https=443 off"
        Clear-Calls
        $ro2 = Invoke-Web -Flags @("--off")
        Assert-True ($ro2.Exit -eq 0 -and $ro2.Output -match "nothing to do" -and @($ro2.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0) "--off is idempotent: with nothing served it changes nothing"
        $rs2 = Invoke-Web -Flags @("--status")
        Assert-True ($rs2.Exit -eq 1 -and $rs2.Output -match "NOT SERVED") "--status after --off says NOT SERVED and exits 1"
        Assert-True (-not (Test-FunnelCalled)) "--off and --status never call funnel"

        Reset-State
        $rp = Invoke-Web -Flags @("--port", "3100")
        Assert-True ($rp.Exit -eq 0 -and (Get-Served) -eq "http://127.0.0.1:3100" -and ($rp.Calls -contains "tailscale serve --bg --https=443 http://127.0.0.1:3100")) "--port moves the loopback target; the target is always 127.0.0.1"
        Reset-State
        $rb = Invoke-Web -Flags @("--port", "abc")
        Assert-True ($rb.Exit -eq 64 -and $rb.Calls.Count -eq 0) "a non-numeric port is a usage error (64) and nothing is called"
        $rb2 = Invoke-Web -Flags @("--funnel")
        Assert-True ($rb2.Exit -eq 64 -and -not (Test-FunnelCalled) -and $rb2.Calls.Count -eq 0) "a --funnel flag is refused as a usage error: no tailscale call at all"
        $rb3 = Invoke-Web -Flags @("--bogus")
        Assert-True ($rb3.Exit -eq 64) "an unknown flag is a usage error (64)"

        # The Funnel state is read per port from `serve status --json` with python (the host has
        # python3; Git Bash here has none on PATH, so the venv's or the PATH's python is passed).
        $python = @(
            (Join-Path $repoRoot "services\api\.venv\Scripts\python.exe"),
            (Join-Path (Split-Path -Parent (& git -C $repoRoot rev-parse --path-format=absolute --git-common-dir)) "services\api\.venv\Scripts\python.exe")
        ) + @(Get-Command python, python3 -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source } | Where-Object { $_ -notmatch 'WindowsApps' }) |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
        Assert-True ([bool]$python) "a python for the JSON cases was found (venv or PATH)"
        $pyEnv = @{ PAGENTOS_JSON_PYTHON = (& $u "$python") }

        # A Funnel found on 443: stop, say so, touch nothing (not even to turn it off).
        Reset-State
        [IO.File]::WriteAllText((Join-Path $state "serve"), "http://127.0.0.1:3000")
        $rf = Invoke-Web -Env (@{ FAKE_TS_FUNNEL = "1" } + $pyEnv)
        Assert-True ($rf.Exit -eq 4 -and $rf.Output -match "Funnel is ON" -and @($rf.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0 -and -not (Test-FunnelCalled)) "a Funnel on 443 stops the script (4) with no change and no funnel call"
        $rfo = Invoke-Web -Flags @("--off") -Env (@{ FAKE_TS_FUNNEL = "1" } + $pyEnv)
        Assert-True ($rfo.Exit -eq 4 -and -not (Test-FunnelCalled)) "--off also stops at a Funnel rather than reaching for it"

        # The telephony Funnel (enable-telephony-funnel.sh): 8443 with exactly the two roots is
        # recognised and the web shell is served on 443 as before; anything else is still a STOP.
        # The JSON is read with python (the host has python3; Git Bash here has none on PATH).
        $node = "pagentos-core.tail1234.ts.net"
        $web443 = "`"$($node):443`":{`"Handlers`":{`"/`":{`"Proxy`":`"http://127.0.0.1:3000`"}}}"
        $phone = "`"/telephony/inbound`":{`"Proxy`":`"http://100.64.0.9:8001/telephony/inbound`"},`"/v1/telephony/audio`":{`"Proxy`":`"http://100.64.0.9:8001/v1/telephony/audio`"}"
        $jsonPhone = Join-Path $script:Sandbox "phone.json"
        $jsonExtra = Join-Path $script:Sandbox "extra.json"
        [IO.File]::WriteAllText($jsonPhone, "{`"TCP`":{`"443`":{`"HTTPS`":true},`"8443`":{`"HTTPS`":true}},`"Web`":{$web443,`"$($node):8443`":{`"Handlers`":{$phone}}},`"AllowFunnel`":{`"$($node):8443`":true}}")
        [IO.File]::WriteAllText($jsonExtra, "{`"TCP`":{`"443`":{`"HTTPS`":true},`"8443`":{`"HTTPS`":true}},`"Web`":{$web443,`"$($node):8443`":{`"Handlers`":{$phone,`"/`":{`"Proxy`":`"http://100.64.0.9:8001`"}}}},`"AllowFunnel`":{`"$($node):8443`":true}}")

        Reset-State
        $rt = Invoke-Web -Env (@{ FAKE_TS_JSON = (& $u $jsonPhone) } + $pyEnv)
        if ($rt.Exit -ne 0) { Write-Host $rt.Output }
        Assert-True ($rt.Exit -eq 0 -and $rt.Output -match "Funnel 8443: telefon yolu" -and $rt.Output -match "SERVED https://" -and ($rt.Calls -contains "tailscale serve --bg --https=443 $target") -and -not (Test-FunnelCalled)) "a Funnel on 8443 with exactly the two telephony roots: the 'telefon yolu' line, 443 served on the tailnet, exit 0, no funnel call"
        $rts = Invoke-Web -Flags @("--status") -Env (@{ FAKE_TS_JSON = (& $u $jsonPhone) } + $pyEnv)
        Assert-True ($rts.Exit -eq 0 -and $rts.Output -match "telefon yolu") "--status beside the telephony Funnel: exit 0 and the 'telefon yolu' line"

        Reset-State
        $rx = Invoke-Web -Env (@{ FAKE_TS_JSON = (& $u $jsonExtra) } + $pyEnv)
        Assert-True ($rx.Exit -eq 4 -and $rx.Output -match "STOP" -and @($rx.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0 -and -not (Test-FunnelCalled)) "a third root on the 8443 Funnel stops the script (4): no serve change, no funnel call"

        Reset-State
        $rnp = Invoke-Web -Env @{ FAKE_TS_JSON = (& $u $jsonPhone); PAGENTOS_JSON_PYTHON = "/no/such/python3" }
        Assert-True ($rnp.Exit -eq 4 -and @($rnp.Calls | Where-Object { $_ -match "^tailscale serve (--bg|--https)" }).Count -eq 0) "without python any AllowFunnel in the JSON fails closed: STOP (4), nothing served"
        Reset-State
        $rnp2 = Invoke-Web -Env @{ PAGENTOS_JSON_PYTHON = "/no/such/python3" }
        Assert-True ($rnp2.Exit -eq 0 -and (Get-Served) -eq $target) "without python and with no Funnel at all the web shell is still served"

        # Certificates not enabled: an owner step, one line, nothing served.
        Reset-State
        $rn = Invoke-Web -Env @{ FAKE_TS_NOT_ENABLED = "1" }
        Assert-True ($rn.Exit -eq 3 -and $rn.Output -match "OWNER STEP: HTTPS certificates are not enabled" -and $rn.Output -match "MagicDNS" -and $rn.Output -match "Enable HTTPS" -and (Get-Served) -eq "") "a tailnet without HTTPS certificates: exit 3 and the one owner line (MagicDNS on, HTTPS Certificates -> Enable HTTPS); nothing served"
        Assert-True (-not (Test-FunnelCalled)) "the owner-step path never calls funnel"

        Reset-State
        $rd = Invoke-Web -Env @{ FAKE_TS_DOWN = "1" }
        Assert-True ($rd.Exit -eq 2 -and $rd.Output -match "not running or not logged in" -and @($rd.Calls | Where-Object { $_ -match "^tailscale serve" }).Count -eq 0) "tailscale stopped or logged out: exit 2 and no serve call"

        # The text of the script: no funnel invocation, LF only.
        $text = [IO.File]::ReadAllText($hostScript)
        $code = @($text -split "`n" | Where-Object { $_ -notmatch '^\s*#' })
        Assert-True (@($code | Where-Object { $_ -match '\bts\s+funnel\b' -or $_ -match '"\$ts_bin"\s+funnel' -or $_ -match '\btailscale\s+funnel\b.*\$\(' }).Count -eq 0) "no non-comment line of the script invokes funnel"
        Assert-True (-not $text.Contains("`r")) "the script is LF-only (bash)"
    }
}
finally { Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host ""
Write-Host "passed: $($script:Passes)  failed: $($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
