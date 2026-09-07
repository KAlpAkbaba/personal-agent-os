<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the zero-downtime Cloud Core release
    (M18.4 spec §6): scripts/cloud/release-cloud-core-bluegreen.sh under Git Bash with a
    fake docker/curl and a sandbox in place of /opt/pagentos.
.DESCRIPTION
    Proven here, with no real docker, host or network:
      * preflight validates the new tree and changes nothing;
      * a release brings the IDLE colour up on the new sha while the active colour is
        never stopped before the switch, verifies health / contract / release on the idle
        colour, switches the edge, verifies through the edge, drains, then stops the old
        colour and records RELEASE + LAST_KNOWN_GOOD + the active colour;
      * a failure BEFORE the switch stops only the idle colour and restores the tree;
      * a failure AFTER the switch switches the edge back to the old colour;
      * a wrong served release (the version model not wired) is refused (76);
      * --rollback switches back to the previous colour and restores RELEASE.
    Run: powershell -NoProfile -File scripts\tests\cloud-release-bluegreen.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\release-cloud-core-bluegreen.sh"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-bluegreen-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$hostBase = Join-Path $script:Sandbox "host"
$fakeBin = Join-Path $script:Sandbox "bin"
New-Item -ItemType Directory -Force -Path $fakeBin | Out-Null
$u = { param($p) ($p -replace '\\', '/') }
$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }

# A fake docker that knows the blue/green shapes: `compose exec -T api-<colour> python`
# answers the colour's health with the release the env file names for it (so the
# script's "served release == sha" check is real), `compose exec -T edge nginx` reloads,
# `compose up` / `stop` mark the colours' states. Knobs: FAKE_CONFIG_EXIT, FAKE_UP_EXIT,
# FAKE_HEALTH_DOWN=<colour> (that colour never answers), FAKE_RELOAD_EXIT,
# FAKE_SERVED_RELEASE (override what a colour reports), FAKE_CONTRACT_VERSION.
$docker = @(
    '#!/usr/bin/env bash',
    'echo "docker $*" >> "$FAKE_STATE/calls.log"',
    'colour_of() { printf "%s" "$1" | grep -oE "api-(blue|green)" | head -1 | sed "s/api-//"; }',
    'released_for() {',
    '  c=$(printf "%s" "$1" | tr "[:lower:]" "[:upper:]")',
    '  grep "^PAGENTOS_RELEASE_$c=" "$FAKE_ENV" 2>/dev/null | head -1 | sed "s/^[^=]*=//"',
    '}',
    'case "$*" in',
    '  compose*" exec -T api-"*python*)',
    '    colour=$(colour_of "$*")',
    '    if [ "${FAKE_HEALTH_DOWN:-}" = "$colour" ]; then exit 1; fi',
    '    if [ ! -f "$FAKE_STATE/up-$colour" ]; then exit 1; fi',
    '    rel="${FAKE_SERVED_RELEASE:-$(released_for "$colour")}"',
    '    printf "{\"status\":\"ok\",\"release\":{\"component\":\"cloud-core\",\"version\":\"%s\"},\"checks\":{\"voice_realtime\":{\"contract_version\":%s}}}" "$rel" "${FAKE_CONTRACT_VERSION:-2}"',
    '    exit 0;;',
    '  compose*" exec -T edge nginx -t"*) exit 0;;',
    '  compose*" exec -T edge nginx -s reload"*)',
    '    if [ -n "${FAKE_RELOAD_EXIT:-}" ]; then exit "$FAKE_RELOAD_EXIT"; fi',
    '    cp "$FAKE_EDGE/upstream.conf" "$FAKE_STATE/edge-upstream"; exit 0;;',
    '  compose*" config -q"*) exit "${FAKE_CONFIG_EXIT:-0}";;',
    '  compose*" up "*api-*)',
    '    if [ -n "${FAKE_UP_EXIT:-}" ]; then exit "$FAKE_UP_EXIT"; fi',
    '    touch "$FAKE_STATE/up-$(colour_of "$*")"; exit 0;;',
    '  compose*" up "*edge*) cp "$FAKE_EDGE/upstream.conf" "$FAKE_STATE/edge-upstream"; touch "$FAKE_STATE/up-edge"; exit 0;;',
    '  compose*" stop api-"*) rm -f "$FAKE_STATE/up-$(colour_of "$*")"; exit 0;;',
    '  compose*" run "*|build\ *|stop\ *) exit 0;;',
    'esac',
    'exit 0'
)
# curl = health THROUGH the edge: whatever colour the edge upstream names.
$curl = @(
    '#!/usr/bin/env bash',
    'colour=$(grep -oE "api-(blue|green)" "$FAKE_STATE/edge-upstream" 2>/dev/null | head -1 | sed "s/api-//")',
    'c=$(printf "%s" "$colour" | tr "[:lower:]" "[:upper:]")',
    'rel=$(grep "^PAGENTOS_RELEASE_$c=" "$FAKE_ENV" 2>/dev/null | head -1 | sed "s/^[^=]*=//")',
    'printf "{\"status\":\"ok\",\"release\":{\"component\":\"cloud-core\",\"version\":\"%s\"}}" "${FAKE_EDGE_RELEASE:-$rel}"'
)
[IO.File]::WriteAllText((Join-Path $fakeBin "docker"), (($docker -join "`n") + "`n"))
[IO.File]::WriteAllText((Join-Path $fakeBin "curl"), (($curl -join "`n") + "`n"))

function Reset-Host {
    param([string]$Active = "blue", [string]$PreviousSha = "1111111111111111111111111111111111111111")
    Remove-Item -LiteralPath $hostBase -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\infra\docker"), (Join-Path $hostBase "app.next\services\api"), (Join-Path $hostBase "state"), (Join-Path $hostBase "edge") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\NEW_TREE"), "new`n")
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\services\api\app\voice\realtime_sessions") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\services\api\app\voice\realtime_sessions\contract_version.py"), "CONTRACT_VERSION = 2`n")
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app\infra\docker") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\OLD_TREE"), "old`n")
    $envText = "PAGENTOS_BIND_IP=100.64.0.1`n"
    if ($Active) {
        # PowerShell variables are case-insensitive: a `$ACTIVE` would BE `$Active`.
        $activeUpper = $Active.ToUpper()
        $envText += "PAGENTOS_IMAGE_$activeUpper=$PreviousSha`nPAGENTOS_RELEASE_$activeUpper=$PreviousSha`n"
        [IO.File]::WriteAllText((Join-Path $hostBase "edge\active.txt"), "$Active`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "edge\upstream.conf"), "upstream pagentos_api { server api-$Active`:8001; }`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "state\edge-upstream"), "upstream pagentos_api { server api-$Active`:8001; }`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "state\up-$Active"), "")
        [IO.File]::WriteAllText((Join-Path $hostBase "RELEASE"), "$PreviousSha`n")
    }
    [IO.File]::WriteAllText((Join-Path $hostBase ".env"), $envText)
}

function Invoke-Release {
    param([string]$Sha = "2222222222222222222222222222222222222222", [string]$Mode = "", [hashtable]$Env = @{})
    $cmd = "PAGENTOS_ALLOW_NONROOT_ENV=1 PAGENTOS_BASE='$(& $u $hostBase)' PAGENTOS_EDGE_DIR='$(& $u (Join-Path $hostBase 'edge'))' PAGENTOS_HEALTH_URL=http://fake/health PAGENTOS_DRAIN_S=0 PAGENTOS_WAIT_STEP_S=0 " +
           "FAKE_STATE='$(& $u (Join-Path $hostBase 'state'))' FAKE_ENV='$(& $u (Join-Path $hostBase '.env'))' FAKE_EDGE='$(& $u (Join-Path $hostBase 'edge'))' " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" bash '$(& $u $hostScript)' $Sha $Mode 2>&1"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $bash -c (ConvertTo-NativeCallArgument -Value $cmd) 2>&1 | Out-String
        $exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $calls = if (Test-Path (Join-Path $hostBase "state\calls.log")) { @(Get-Content (Join-Path $hostBase "state\calls.log")) } else { @() }
    return [pscustomobject]@{ Output = $out; Exit = $exit; Calls = $calls }
}

function Get-Upstream { (Get-Content (Join-Path $hostBase "edge\upstream.conf") -Raw).Trim() }
function Get-Active { (Get-Content (Join-Path $hostBase "edge\active.txt") -Raw).Trim() }
function Test-Up { param([string]$Colour) Test-Path (Join-Path $hostBase "state\up-$Colour") }

try {
    if (-not (Test-Path $bash)) {
        Write-Host "  SKIP  blue/green release tests: Git Bash not found at $bash"
    }
    else {
        Write-Host "host side (release-cloud-core-bluegreen.sh under Git Bash, fake docker/curl)"
        $sha = "2222222222222222222222222222222222222222"

        Reset-Host
        $p = Invoke-Release -Mode "--preflight"
        if ($p.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- preflight output (exit $($p.Exit)) ---"; Write-Host $p.Output }
        Assert-True ($p.Exit -eq 0 -and $p.Output -match "preflight: tree .* compose valid, .* active colour blue, idle green") "preflight names the active and idle colours and validates the new tree"
        Assert-True (-not (Test-Path (Join-Path $hostBase "app.next")) -and (Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not ($p.Calls -match " up |build|alembic")) "preflight leaves nothing behind and changes nothing"

        Reset-Host
        $r = Invoke-Release
        if ($r.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- release output (exit $($r.Exit)) ---"; Write-Host $r.Output; Write-Host "--- calls ---"; $r.Calls | ForEach-Object { Write-Host "  $_" } }
        Assert-True ($r.Exit -eq 0 -and $r.Output -match "RELEASE OK: $sha is running as api-green behind the edge") "release exits 0 with the idle colour active"
        Assert-True ((Get-Upstream) -match "api-green" -and (Get-Active) -eq "green") "the edge upstream and the active marker name the new colour"
        Assert-True ((Get-Content (Join-Path $hostBase "RELEASE") -Raw).Trim() -eq $sha -and (Get-Content (Join-Path $hostBase "LAST_KNOWN_GOOD") -Raw).Trim() -eq "1111111111111111111111111111111111111111") "RELEASE is the new sha and LAST_KNOWN_GOOD the previous one"
        $envAfter = Get-Content (Join-Path $hostBase ".env") -Raw
        Assert-True ($envAfter -match "PAGENTOS_IMAGE_GREEN=$sha" -and $envAfter -match "PAGENTOS_RELEASE_GREEN=$sha" -and $envAfter -match "PAGENTOS_LAST_KNOWN_GOOD=1111111111111111111111111111111111111111" -and $envAfter -match "PAGENTOS_RELEASE_BLUE=1111") "the env file carries the idle colour's image and release and the last known good, one line each"
        $calls = $r.Calls
        $iBuild = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "^docker build " } | Select-Object -First 1))
        $iMigrate = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "alembic upgrade head" } | Select-Object -First 1))
        $iUpGreen = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " up -d --no-deps --wait api-green$" } | Select-Object -First 1))
        $iHealth = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "exec -T api-green python" } | Select-Object -First 1))
        $iReload = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "nginx -s reload" } | Select-Object -First 1))
        $iStopBlue = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " stop api-blue$" } | Select-Object -First 1))
        Assert-True ($iBuild -ge 0 -and $iBuild -lt $iMigrate -and $iMigrate -lt $iUpGreen -and $iUpGreen -lt $iHealth -and $iHealth -lt $iReload -and $iReload -lt $iStopBlue) "order: build -> migrate -> up idle -> health on idle -> edge reload -> stop old (after the drain)"
        Assert-True (-not ($calls -match " stop api-blue" | Where-Object { [array]::IndexOf($calls, $_) -lt $iReload }) -and -not ($calls -match "force-recreate") -and -not ($calls -match "postgres|redis|minio|temporal")) "the active colour is never stopped before the switch; nothing is force-recreated; dependencies are never named"
        Assert-True ((Test-Up "green") -and -not (Test-Up "blue")) "afterwards only the new colour runs"
        Assert-True ($r.Output -match "health ok on api-green" -and $r.Output -match "api-green reports release $sha" -and $r.Output -match "health through the edge: release $sha") "the idle colour and the edge both report the new release before it counts"

        Reset-Host
        $rb = Invoke-Release -Env @{ FAKE_UP_EXIT = "1" }
        Assert-True ($rb.Exit -ne 0 -and $rb.Output -match "failed before the switch; api-blue kept serving") "a failure before the switch keeps the active colour serving"
        Assert-True ((Get-Upstream) -match "api-blue" -and (Get-Active) -eq "blue" -and (Test-Up "blue") -and (Test-Path (Join-Path $hostBase "app\OLD_TREE"))) "...the edge is untouched and the tree restored"

        Reset-Host
        $r76 = Invoke-Release -Env @{ FAKE_SERVED_RELEASE = "0000000000000000000000000000000000000000" }
        Assert-True ($r76.Exit -eq 76 -and $r76.Output -match "reports release '0000" -and (Get-Active) -eq "blue") "an idle colour that does not report the new sha is refused (76) before the switch"

        Reset-Host
        $r73 = Invoke-Release -Env @{ FAKE_CONTRACT_VERSION = "1" }
        Assert-True ($r73.Exit -eq 73 -and (Get-Active) -eq "blue" -and (Test-Up "blue")) "an older served contract is refused (73) before the switch"

        Reset-Host
        $ra = Invoke-Release -Env @{ FAKE_EDGE_RELEASE = "9999999999999999999999999999999999999999" }
        Assert-True ($ra.Exit -eq 76 -and $ra.Output -match "ROLLBACK: switching the edge back to api-blue" -and (Get-Upstream) -match "api-blue" -and (Get-Active) -eq "blue" -and (Test-Up "blue")) "a failure after the switch switches the edge back to the old colour, which is still up"

        Reset-Host -Active "green"
        $rg = Invoke-Release
        Assert-True ($rg.Exit -eq 0 -and (Get-Active) -eq "blue" -and (Get-Upstream) -match "api-blue" -and (Test-Up "blue") -and -not (Test-Up "green")) "from green the release lands on blue"

        Reset-Host
        $rr = Invoke-Release -Mode "--rollback" -Env @{ }
        Assert-True ($rr.Exit -eq 0 -and $rr.Output -match "ROLLBACK OK: api-green is active" -and (Get-Active) -eq "green") "--rollback switches to the other colour after bringing it up"

        Reset-Host -Active ""
        [IO.File]::WriteAllText((Join-Path $hostBase ".env"), "PAGENTOS_BIND_IP=100.64.0.1`n")
        $rf = Invoke-Release
        Assert-True ($rf.Exit -eq 0 -and $rf.Output -match "first cutover: stopping the legacy" -and (Get-Active) -eq "blue" -and ($rf.Calls -match "^docker stop pagentos-prod-api").Count -eq 1 -and ($rf.Calls -match " up -d --no-deps --wait edge").Count -eq 1) "the first cutover stops the legacy api, starts the edge and lands on blue"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-release-bluegreen tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
