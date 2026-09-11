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
        colour, hands the DEVICE sessions to the idle colour first (device upstream, drain
        the active colour, wait until the idle colour holds them), switches HTTP, verifies
        through the edge, drains, then stops the old colour and records RELEASE +
        LAST_KNOWN_GOOD + the active colour;
      * a device handoff that does not complete is refused (79) and undone: the active
        colour takes devices again, the idle colour is stopped;
      * a failure BEFORE the switch stops only the idle colour and restores the tree;
      * a failure AFTER the switch switches the edge back (devices first) to the old colour;
      * a wrong served release (the version model not wired) is refused (76);
      * --rollback hands the devices over and switches back to the previous colour;
      * a release interrupted (SIGKILL) after the idle colour is up, after the switch, or
        after the old colour is stopped, followed by --reconcile, ends with the last
        COMPLETED promotion live and the half-promoted candidate stopped - never live
        silently; a consistent state reconciles to itself; a canonical colour that cannot
        come up makes the reconcile fall back loudly (81).
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

# A fake docker that knows the blue/green shapes: `compose exec -T api-<colour> python3`
# answers the colour's health with the release the env file names for it (so the
# script's "served release == sha" check is real) and the device sessions the colour
# holds (state/sessions-<colour>); the drain route moves those sessions to the colour the
# APPLIED device upstream names (the reloaded config, state/edge-upstream) - if that is
# the draining colour itself, or a draining/stopped colour, they are lost, exactly as an
# agent refused with 1012 would be; `compose exec -T edge nginx` reloads; `compose ps`
# lists the colours that are up; `compose up` / `stop` mark the colours' states. Knobs:
# FAKE_CONFIG_EXIT, FAKE_UP_EXIT, FAKE_HEALTH_DOWN=<colour> (that colour never answers),
# FAKE_RELOAD_EXIT, FAKE_SERVED_RELEASE (override what a colour reports),
# FAKE_CONTRACT_VERSION, FAKE_HANDOFF_STUCK=1 (drained sessions never arrive anywhere),
# FAKE_HEALTH_STATUS (every colour's top-level status), FAKE_HEALTH_STATUS_<COLOUR> (one
# colour's, e.g. FAKE_HEALTH_STATUS_BLUE=degraded while green stays ok).
$docker = @(
    '#!/usr/bin/env bash',
    '# The real docker refuses to run from a deleted working directory ("getwd: no such file',
    '# or directory") - which is exactly what the controlled-failure rollback hit on the host',
    '# when the script rolled the tree back from inside it. The fake refuses the same way.',
    'if ! cd . 2>/dev/null; then echo "error in parsing compose-spec.json: getwd: no such file or directory" >&2; exit 1; fi',
    'echo "docker $*" >> "$FAKE_STATE/calls.log"',
    'colour_of() { printf "%s" "$1" | grep -oE "api-(blue|green)" | head -1 | sed "s/api-//"; }',
    'released_for() {',
    '  c=$(printf "%s" "$1" | tr "[:lower:]" "[:upper:]")',
    '  grep "^PAGENTOS_RELEASE_$c=" "$FAKE_ENV" 2>/dev/null | head -1 | sed "s/^[^=]*=//"',
    '}',
    'sessions_of() { cat "$FAKE_STATE/sessions-$1" 2>/dev/null || echo 0; }',
    'case "$*" in',
    '  compose*" ps --status running "*)',
    '    for f in "$FAKE_STATE"/up-*; do [ -e "$f" ] || continue; n=${f##*/up-}; case "$n" in blue|green) echo "api-$n";; edge) echo edge;; esac; done',
    '    exit 0;;',
    '  compose*" exec -T api-"*python*)',
    '    colour=$(colour_of "$*")',
    '    if [ ! -f "$FAKE_STATE/up-$colour" ]; then exit 1; fi',
    '    case "$*" in',
    '      *devices/drain*)',
    '        if [ "${FAKE_DRAIN_UNSUPPORTED:-}" = "$colour" ]; then printf "STATUS 404\n"; exit 0; fi',
    '        n=$(sessions_of "$colour"); echo 0 > "$FAKE_STATE/sessions-$colour"; touch "$FAKE_STATE/draining-$colour"',
    '        target=$(grep -oE "pagentos_devices \{ server api-(blue|green)" "$FAKE_STATE/edge-upstream" 2>/dev/null | grep -oE "(blue|green)$")',
    '        if [ -z "${FAKE_HANDOFF_STUCK:-}" ] && [ -n "$target" ] && [ "$target" != "$colour" ] && [ -f "$FAKE_STATE/up-$target" ] && [ ! -f "$FAKE_STATE/draining-$target" ]; then',
    '          echo $(( $(sessions_of "$target") + n )) > "$FAKE_STATE/sessions-$target"',
    '        fi',
    '        printf "STATUS 200\n{\"draining\":true,\"closed\":%s}" "$n"; exit 0;;',
    '      *devices/undrain*)',
    '        if [ "${FAKE_DRAIN_UNSUPPORTED:-}" = "$colour" ]; then printf "STATUS 404\n"; exit 0; fi',
    '        rm -f "$FAKE_STATE/draining-$colour"; printf "STATUS 200\n{\"draining\":false}"; exit 0;;',
    '    esac',
    '    if [ "${FAKE_HEALTH_DOWN:-}" = "$colour" ]; then exit 1; fi',
    '    rel="${FAKE_SERVED_RELEASE:-$(released_for "$colour")}"',
    '    draining=false; [ -f "$FAKE_STATE/draining-$colour" ] && draining=true',
    '    st_var="FAKE_HEALTH_STATUS_$(printf "%s" "$colour" | tr "[:lower:]" "[:upper:]")"; st="${!st_var:-${FAKE_HEALTH_STATUS:-ok}}"',
    '    printf "{\"status\":\"%s\",\"release\":{\"component\":\"cloud-core\",\"version\":\"%s\"},\"checks\":{\"voice_realtime\":{\"contract_version\":%s},\"broker\":{\"active_sessions\":%s,\"draining\":%s}}}" "$st" "$rel" "${FAKE_CONTRACT_VERSION:-2}" "$(sessions_of "$colour")" "$draining"',
    '    exit 0;;',
    '  compose*" exec -T edge nginx -t"*) exit 0;;',
    '  compose*" exec -T edge nginx -s reload"*)',
    '    if [ -n "${FAKE_RELOAD_EXIT:-}" ]; then exit "$FAKE_RELOAD_EXIT"; fi',
    '    cp "$FAKE_EDGE/upstream.conf" "$FAKE_STATE/edge-upstream"; exit 0;;',
    '  compose*" config -q"*) exit "${FAKE_CONFIG_EXIT:-0}";;',
    '  compose*" up "*api-*)',
    '    if [ -n "${FAKE_UP_EXIT:-}" ]; then exit "$FAKE_UP_EXIT"; fi',
    '    colour=$(colour_of "$*")',
    '    # a fresh container is not draining and holds no session; an already-running one is untouched',
    '    if [ ! -f "$FAKE_STATE/up-$colour" ]; then rm -f "$FAKE_STATE/draining-$colour"; echo 0 > "$FAKE_STATE/sessions-$colour"; fi',
    '    touch "$FAKE_STATE/up-$colour"; exit 0;;',
    '  compose*" up "*edge*)',
    '    if [ -n "${FAKE_EDGE_RECREATE:-}" ] && [ -f "$FAKE_STATE/up-edge" ]; then echo "Container pagentos-prod-edge Recreated"; fi',
    '    cp "$FAKE_EDGE/upstream.conf" "$FAKE_STATE/edge-upstream"; touch "$FAKE_STATE/up-edge"; exit 0;;',
    '  compose*" stop api-"*) colour=$(colour_of "$*"); rm -f "$FAKE_STATE/up-$colour" "$FAKE_STATE/draining-$colour"; echo 0 > "$FAKE_STATE/sessions-$colour"; exit 0;;',
    '  compose*" run "*|build\ *|stop\ *) exit 0;;',
    'esac',
    'exit 0'
)
# curl = health THROUGH the edge: whatever colour the edge's HTTP upstream names.
$curl = @(
    '#!/usr/bin/env bash',
    'colour=$(grep -oE "pagentos_api \{ server api-(blue|green)" "$FAKE_STATE/edge-upstream" 2>/dev/null | head -1 | grep -oE "(blue|green)$")',
    'c=$(printf "%s" "$colour" | tr "[:lower:]" "[:upper:]")',
    'rel=$(grep "^PAGENTOS_RELEASE_$c=" "$FAKE_ENV" 2>/dev/null | head -1 | sed "s/^[^=]*=//")',
    'printf "{\"status\":\"%s\",\"release\":{\"component\":\"cloud-core\",\"version\":\"%s\"}}" "${FAKE_EDGE_HEALTH_STATUS:-ok}" "${FAKE_EDGE_RELEASE:-$rel}"'
)
[IO.File]::WriteAllText((Join-Path $fakeBin "docker"), (($docker -join "`n") + "`n"))
[IO.File]::WriteAllText((Join-Path $fakeBin "curl"), (($curl -join "`n") + "`n"))
[IO.File]::WriteAllText((Join-Path $fakeBin "flock"), "#!/usr/bin/env bash`nexit 0`n")

function Get-UpstreamText { param([string]$Http, [string]$Devices = $Http) "upstream pagentos_api { server api-$Http`:8001; }`nupstream pagentos_devices { server api-$Devices`:8001; }`n" }

function Reset-Host {
    param([string]$Active = "blue", [string]$PreviousSha = "1111111111111111111111111111111111111111", [int]$Sessions = 1)
    Remove-Item -LiteralPath $hostBase -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\infra\docker"), (Join-Path $hostBase "app.next\services\api"), (Join-Path $hostBase "state"), (Join-Path $hostBase "edge") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\NEW_TREE"), "new`n")
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\infra\docker\edge"), (Join-Path $hostBase "app\infra\docker\edge") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\infra\docker\edge\nginx.conf"), "# new edge config`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\infra\docker\edge\nginx.conf"), "# old edge config`n")
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\services\api\app\voice\realtime_sessions") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\services\api\app\voice\realtime_sessions\contract_version.py"), "CONTRACT_VERSION = 2`n")
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app\infra\docker") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\OLD_TREE"), "old`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\RELEASE"), "$PreviousSha`n")
    $envText = "PAGENTOS_BIND_IP=100.64.0.1`n"
    if ($Active) {
        # PowerShell variables are case-insensitive: a `$ACTIVE` would BE `$Active`.
        $activeUpper = $Active.ToUpper()
        $envText += "PAGENTOS_IMAGE_$activeUpper=$PreviousSha`nPAGENTOS_RELEASE_$activeUpper=$PreviousSha`n"
        [IO.File]::WriteAllText((Join-Path $hostBase "edge\active.txt"), "$Active`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "edge\upstream.conf"), (Get-UpstreamText $Active))
        [IO.File]::WriteAllText((Join-Path $hostBase "state\edge-upstream"), (Get-UpstreamText $Active))
        [IO.File]::WriteAllText((Join-Path $hostBase "state\up-$Active"), "")
        [IO.File]::WriteAllText((Join-Path $hostBase "state\up-edge"), "")
        [IO.File]::WriteAllText((Join-Path $hostBase "state\sessions-$Active"), "$Sessions`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "RELEASE"), "$PreviousSha`n")
    }
    [IO.File]::WriteAllText((Join-Path $hostBase ".env"), $envText)
}

function Invoke-Release {
    param([string]$Sha = "2222222222222222222222222222222222222222", [string]$Mode = "", [hashtable]$Env = @{})
    $cmd = "PAGENTOS_ALLOW_NONROOT_ENV=1 PAGENTOS_BASE='$(& $u $hostBase)' PAGENTOS_EDGE_DIR='$(& $u (Join-Path $hostBase 'edge'))' PAGENTOS_HEALTH_URL=http://fake/health PAGENTOS_DRAIN_S=0 PAGENTOS_WAIT_STEP_S=0 PAGENTOS_HANDOFF_WAIT_S=1 PAGENTOS_EDGE_SETTLE_TRIES=2 PAGENTOS_EDGE_SETTLE_STEP_S=0 " +
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
function Get-Release { (Get-Content (Join-Path $hostBase "RELEASE") -Raw).Trim() }
function Test-Up { param([string]$Colour) Test-Path (Join-Path $hostBase "state\up-$Colour") }
function Test-Draining { param([string]$Colour) Test-Path (Join-Path $hostBase "state\draining-$Colour") }
function Get-Sessions { param([string]$Colour) $p = Join-Path $hostBase "state\sessions-$Colour"; if (Test-Path $p) { [int](Get-Content $p -Raw).Trim() } else { 0 } }
function Test-UpstreamBoth { param([string]$Colour) $t = Get-Upstream; ($t -match "pagentos_api \{ server api-$Colour`:8001") -and ($t -match "pagentos_devices \{ server api-$Colour`:8001") }
# A second colour with a release on record (the last known good, as a completed release
# leaves it): Reset-Host records only the active one.
function Set-RecordedRelease { param([string]$Colour, [string]$Sha) [IO.File]::AppendAllText((Join-Path $hostBase ".env"), "PAGENTOS_IMAGE_$($Colour.ToUpper())=$Sha`nPAGENTOS_RELEASE_$($Colour.ToUpper())=$Sha`n") }
function Clear-Calls { Remove-Item -LiteralPath (Join-Path $hostBase "state\calls.log") -ErrorAction SilentlyContinue }

try {
    if (-not (Test-Path $bash)) {
        Write-Host "  SKIP  blue/green release tests: Git Bash not found at $bash"
    }
    else {
        Write-Host "host side (release-cloud-core-bluegreen.sh under Git Bash, fake docker/curl)"
        $sha = "2222222222222222222222222222222222222222"
        $old = "1111111111111111111111111111111111111111"

        Reset-Host
        $p = Invoke-Release -Mode "--preflight"
        if ($p.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- preflight output (exit $($p.Exit)) ---"; Write-Host $p.Output }
        Assert-True ($p.Exit -eq 0 -and $p.Output -match "preflight: tree .* compose valid, .* active colour blue, idle green") "preflight names the active and idle colours and validates the new tree"
        Assert-True (-not (Test-Path (Join-Path $hostBase "app.next")) -and (Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not ($p.Calls -match " up |build|alembic")) "preflight leaves nothing behind and changes nothing"

        Reset-Host
        $r = Invoke-Release
        if ($r.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- release output (exit $($r.Exit)) ---"; Write-Host $r.Output; Write-Host "--- calls ---"; $r.Calls | ForEach-Object { Write-Host "  $_" } }
        Assert-True ($r.Exit -eq 0 -and $r.Output -match "RELEASE OK: $sha is running as api-green behind the edge") "release exits 0 with the idle colour active"
        Assert-True ((Test-UpstreamBoth "green") -and (Get-Active) -eq "green") "the edge's HTTP and device upstreams and the active marker name the new colour"
        Assert-True ((Get-Release) -eq $sha -and (Get-Content (Join-Path $hostBase "LAST_KNOWN_GOOD") -Raw).Trim() -eq $old) "RELEASE is the new sha and LAST_KNOWN_GOOD the previous one"
        $envAfter = Get-Content (Join-Path $hostBase ".env") -Raw
        Assert-True ($envAfter -match "PAGENTOS_IMAGE_GREEN=$sha" -and $envAfter -match "PAGENTOS_RELEASE_GREEN=$sha" -and $envAfter -match "PAGENTOS_LAST_KNOWN_GOOD=$old" -and $envAfter -match "PAGENTOS_RELEASE_BLUE=1111") "the env file carries the idle colour's image and release and the last known good, one line each"
        $calls = $r.Calls
        $iBuild = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "^docker build " } | Select-Object -First 1))
        $iMigrate = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "alembic upgrade head" } | Select-Object -First 1))
        $iUpGreen = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " up -d --no-deps --wait api-green$" } | Select-Object -First 1))
        $iHealth = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "exec -T api-green python" } | Select-Object -First 1))
        # The two reload lines are identical strings: IndexOf would find the first twice.
        $reloadIdx = @(0..($calls.Count - 1) | Where-Object { $calls[$_] -match "nginx -s reload" })
        $iDrainBlue = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "exec -T api-blue python.*devices/drain" } | Select-Object -First 1))
        # the device-upstream reload is the last reload before the drain; the HTTP reload the first after it
        $before = @($reloadIdx | Where-Object { $_ -lt $iDrainBlue }); $after = @($reloadIdx | Where-Object { $_ -gt $iDrainBlue })
        $iReload = if ($before.Count -gt 0) { $before[-1] } else { -1 }
        $iReload2 = if ($after.Count -gt 0) { $after[0] } else { -1 }
        $iStopBlue = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " stop api-blue$" } | Select-Object -First 1))
        $orderOk = ($iBuild -ge 0 -and $iBuild -lt $iMigrate -and $iMigrate -lt $iUpGreen -and $iUpGreen -lt $iHealth -and $iHealth -lt $iReload -and $iReload -lt $iDrainBlue -and $iDrainBlue -lt $iReload2 -and $iReload2 -lt $iStopBlue)
        if (-not $orderOk) { Write-Host "      indexes: build=$iBuild migrate=$iMigrate upGreen=$iUpGreen health=$iHealth reload=$iReload drainBlue=$iDrainBlue reload2=$iReload2 stopBlue=$iStopBlue"; $calls | ForEach-Object { Write-Host "      $_" } }
        Assert-True $orderOk "order: build -> migrate -> up idle -> health on idle -> device upstream reload -> drain old -> HTTP reload -> stop old (after the drain)"
        Assert-True ($r.Output -match "device handoff: 1/1 after 0s device session\(s\) on api-green" -and (Get-Sessions "green") -eq 1 -and (Get-Sessions "blue") -eq 0) "the device sessions moved to the new colour BEFORE it took HTTP, and the script waited for them"
        Assert-True (-not ($calls -match " stop api-blue" | Where-Object { [array]::IndexOf($calls, $_) -lt $iReload }) -and -not ($calls -match "force-recreate") -and -not ($calls -match "postgres|redis|minio|temporal")) "the active colour is never stopped before the switch; nothing is force-recreated; dependencies are never named"
        Assert-True ((Test-Up "green") -and -not (Test-Up "blue")) "afterwards only the new colour runs"
        Assert-True ($r.Output -match "health ok on api-green" -and $r.Output -match "api-green reports release $sha" -and $r.Output -match "health through the edge: release $sha \(settled after 0 retries\)") "the idle colour and the edge both report the new release before it counts (the edge probe is a bounded wait for the reload to settle)"
        Assert-True ((Get-Content (Join-Path $hostBase "edge\nginx.conf") -Raw) -match "new edge config" -and ($calls -match " up -d --no-deps --wait edge$").Count -eq 1 -and -not ($r.Output -match "edge RECREATED")) "the tree's nginx.conf is installed into the edge dir and the edge is brought up-to-date (unchanged: not recreated) before the handoff"

        Reset-Host
        $rE = Invoke-Release -Env @{ FAKE_EDGE_RECREATE = "1" }
        Assert-True ($rE.Exit -eq 0 -and $rE.Output -match "edge RECREATED: its compose definition changed") "an edge whose compose definition changed is recreated before the switch, and the release says so"

        Reset-Host -Sessions 0
        $r0 = Invoke-Release
        Assert-True ($r0.Exit -eq 0 -and $r0.Output -match "device handoff: no device session on api-blue; nothing to move") "with no device connected the handoff has nothing to wait for"

        # ADR-0122: the pre-migration safety point. A fake backup records into the same call
        # log as the fake docker, so its place in the order is visible.
        Write-Host "pre-migration backup (ADR-0122)"
        Assert-True ($r.Output -match "WARNING: no backup tooling at .*; migrating WITHOUT a safety point") "a host without the backup installed is told, and the release still goes through"

        Reset-Host
        $backupBin = Join-Path $hostBase "backup-bin"
        New-Item -ItemType Directory -Force -Path $backupBin | Out-Null
        [IO.File]::WriteAllText((Join-Path $backupBin "backup-cloud-core.sh"), "#!/usr/bin/env bash`necho `"backup `$*`" >> `"`$FAKE_STATE/calls.log`"`necho `"BACKUP OK: snapshot abc (pre-migration)`"`nexit `${FAKE_BACKUP_EXIT:-0}`n")
        $rb = Invoke-Release -Env @{ PAGENTOS_BACKUP_BIN = (& $u $backupBin) }
        $iBackup = [array]::IndexOf($rb.Calls, ($rb.Calls | Where-Object { $_ -match "^backup " } | Select-Object -First 1))
        $iMigrateB = [array]::IndexOf($rb.Calls, ($rb.Calls | Where-Object { $_ -match "alembic upgrade head" } | Select-Object -First 1))
        Assert-True ($rb.Exit -eq 0 -and $iBackup -ge 0 -and $iBackup -lt $iMigrateB -and $rb.Calls[$iBackup] -eq "backup --kind pre-migration --label release-222222222222" -and $rb.Output -match "BACKUP OK: snapshot abc") "with the backup installed, a pre-migration snapshot labelled with the release is taken BEFORE the migration"

        Reset-Host
        New-Item -ItemType Directory -Force -Path $backupBin | Out-Null
        [IO.File]::WriteAllText((Join-Path $backupBin "backup-cloud-core.sh"), "#!/usr/bin/env bash`necho `"backup `$*`" >> `"`$FAKE_STATE/calls.log`"`necho `"BACKUP FAILED (92): pg_dump pagentos_prod failed`" >&2`nexit 92`n")
        $rf2 = Invoke-Release -Env @{ PAGENTOS_BACKUP_BIN = (& $u $backupBin) }
        Assert-True ($rf2.Exit -eq 74 -and $rf2.Output -match "pre-migration backup FAILED; the release stops before any migration" -and $rf2.Output -match "pg_dump pagentos_prod failed" -and -not ($rf2.Calls -match "alembic upgrade head") -and (Get-Active) -eq "blue" -and (Test-Up "blue") -and -not (Test-Up "green") -and (Test-Path (Join-Path $hostBase "app\OLD_TREE"))) "a failed pre-migration backup stops the release before any migration: the active colour keeps serving and the old tree is back"

        Reset-Host
        $rl = Invoke-Release -Env @{ FAKE_DRAIN_UNSUPPORTED = "blue" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host $rl.Output }
        Assert-True ($rl.Exit -eq 0 -and $rl.Output -match "device handoff UNAVAILABLE: api-blue has no drain route .* LEGACY switch: the device moves when api-blue stops" -and $rl.Output -match "legacy switch" -and $rl.Output -match "RELEASE OK") "an active colour that predates the drain route (404) falls back to the legacy switch, said out loud, and the release still completes"
        Assert-True ((Test-UpstreamBoth "green") -and (Get-Active) -eq "green" -and -not (Test-Up "blue") -and (Get-Release) -eq $sha) "...the edge and the marker still land on the new colour and the old colour is stopped after the drain window"

        Reset-Host
        $rd = Invoke-Release -Env @{ FAKE_HEALTH_DOWN = "" ; FAKE_DRAIN_UNSUPPORTED = "" }
        Assert-True ($rd.Exit -eq 0) "(control) the same release with a draining colour completes through the handoff"

        Reset-Host
        $stuck = Invoke-Release -Env @{ FAKE_HANDOFF_STUCK = "1" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host $stuck.Output }
        Assert-True ($stuck.Exit -eq 79 -and $stuck.Output -match "device handoff INCOMPLETE: 0/1 after 1s") "a device handoff that never completes is refused (79): the new colour would be authoritative for devices it cannot reach"
        Assert-True ((Test-UpstreamBoth "blue") -and (Get-Active) -eq "blue" -and -not (Test-Draining "blue") -and (Test-Up "blue") -and -not (Test-Up "green") -and (Test-Path (Join-Path $hostBase "app\OLD_TREE"))) "...the active colour takes devices again (undrained), the edge points at it, the idle colour is stopped, the tree restored"

        Reset-Host
        $rb = Invoke-Release -Env @{ FAKE_UP_EXIT = "1" }
        Assert-True ($rb.Exit -ne 0 -and $rb.Output -match "failed before the switch; api-blue kept serving") "a failure before the switch keeps the active colour serving"
        Assert-True ((Test-UpstreamBoth "blue") -and (Get-Active) -eq "blue" -and (Test-Up "blue") -and (Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and (Get-Content (Join-Path $hostBase "edge\nginx.conf") -Raw) -match "old edge config") "...the edge is untouched (its old nginx.conf back) and the tree restored"

        Reset-Host
        $r76 = Invoke-Release -Env @{ FAKE_SERVED_RELEASE = "0000000000000000000000000000000000000000" }
        Assert-True ($r76.Exit -eq 75 -and $r76.Output -match "never answered healthy at $sha" -and (Get-Active) -eq "blue") "an idle colour that reports the wrong sha never passes the exact health gate"

        Reset-Host
        $r73 = Invoke-Release -Env @{ FAKE_CONTRACT_VERSION = "1" }
        Assert-True ($r73.Exit -eq 73 -and (Get-Active) -eq "blue" -and (Test-Up "blue")) "an older served contract is refused (73) before the switch"

        Reset-Host
        $ra = Invoke-Release -Env @{ FAKE_EDGE_RELEASE = "9999999999999999999999999999999999999999" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host $ra.Output }
        Assert-True ($ra.Exit -eq 76 -and $ra.Output -match "expected ok / '$sha' \(after 2 probes\)" -and $ra.Output -match "ROLLBACK: switching the edge back to api-blue" -and (Test-UpstreamBoth "blue") -and (Get-Active) -eq "blue" -and (Test-Up "blue")) "a failure after the switch (the edge never settles on the sha within the bounded wait) switches the edge back to the old colour, which is still up"
        Assert-True ($ra.Output -match "ROLLBACK: device sessions returning to api-blue: 1/1" -and (Get-Sessions "blue") -eq 1 -and -not (Test-Draining "blue") -and -not (Test-Up "green")) "...the old colour takes the device sessions back (undrained, devices first), then the new colour is stopped"

        Reset-Host
        $edgeDegraded = Invoke-Release -Env @{ FAKE_EDGE_HEALTH_STATUS = "degraded" }
        Assert-True ($edgeDegraded.Exit -eq 76 -and $edgeDegraded.Output -match "health is 'degraded'.*expected ok" -and $edgeDegraded.Output -match "ROLLBACK: switching the edge back to api-blue" -and (Test-UpstreamBoth "blue") -and (Get-Active) -eq "blue") "post-cutover edge health must be ok, even when it reports the expected sha"

        Reset-Host -Active "green"
        $rg = Invoke-Release
        Assert-True ($rg.Exit -eq 0 -and (Get-Active) -eq "blue" -and (Test-UpstreamBoth "blue") -and (Test-Up "blue") -and -not (Test-Up "green")) "from green the release lands on blue"

        # The recovery timer's pinned bundle vs. what a release ships (owner-approval review,
        # finding 3): a release that changes the Compose file or the edge policy must say so.
        Reset-Host
        $bundle = Join-Path $hostBase "recovery-bundle"
        New-Item -ItemType Directory -Force -Path $bundle | Out-Null
        [IO.File]::WriteAllText((Join-Path $bundle "docker-compose.prod.yml"), "services: {}`n")
        [IO.File]::WriteAllText((Join-Path $bundle "nginx.conf"), "# old edge config`n")
        $stale = Invoke-Release -Env @{ PAGENTOS_RECOVERY_ROOT = (& $u $bundle) }
        Assert-True ($stale.Exit -eq 0 -and $stale.Output -match "RECOVERY BUNDLE STALE: $sha changed" -and (Test-Path (Join-Path $hostBase "RECOVERY_BUNDLE_STALE"))) "a release that changes the edge policy the recovery timer pinned says so and leaves a marker - and still completes"

        Reset-Host
        New-Item -ItemType Directory -Force -Path $bundle | Out-Null
        [IO.File]::WriteAllText((Join-Path $bundle "docker-compose.prod.yml"), "services: {}`n")
        [IO.File]::WriteAllText((Join-Path $bundle "nginx.conf"), "# new edge config`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "RECOVERY_BUNDLE_STALE"), "earlier`n")
        $fresh = Invoke-Release -Env @{ PAGENTOS_RECOVERY_ROOT = (& $u $bundle) }
        Assert-True ($fresh.Exit -eq 0 -and $fresh.Output -notmatch "RECOVERY BUNDLE STALE" -and -not (Test-Path (Join-Path $hostBase "RECOVERY_BUNDLE_STALE"))) "a release whose inputs still match the pinned bundle clears the marker and says nothing"

        Reset-Host
        $none = Invoke-Release
        Assert-True ($none.Exit -eq 0 -and $none.Output -notmatch "RECOVERY BUNDLE STALE") "a host without the recovery timer hears nothing about it"

        Reset-Host
        [IO.File]::AppendAllText((Join-Path $hostBase ".env"), "PAGENTOS_IMAGE_GREEN=0000000000000000000000000000000000000000`nPAGENTOS_RELEASE_GREEN=0000000000000000000000000000000000000000`n")
        $rr = Invoke-Release -Mode "--rollback" -Env @{ }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host $rr.Output }
        Assert-True ($rr.Exit -eq 0 -and $rr.Output -match "ROLLBACK OK: api-green is active" -and (Get-Active) -eq "green" -and (Test-UpstreamBoth "green")) "--rollback switches to the other colour after bringing it up"
        Assert-True ($rr.Output -match "device handoff: 1/1 after 0s device session\(s\) on api-green" -and (Get-Sessions "green") -eq 1 -and -not (Test-Up "blue")) "...devices first: the other colour holds the sessions before it takes HTTP; the colour left is stopped"
        Assert-True ((Get-Release) -eq "0000000000000000000000000000000000000000" -and (Get-Content (Join-Path $hostBase "LAST_KNOWN_GOOD") -Raw).Trim() -eq $old) "...RELEASE names the sha the other colour runs and LAST_KNOWN_GOOD the sha it left"

        Reset-Host -Active ""
        [IO.File]::WriteAllText((Join-Path $hostBase ".env"), "PAGENTOS_BIND_IP=100.64.0.1`n")
        $rf = Invoke-Release
        Assert-True ($rf.Exit -eq 0 -and $rf.Output -match "first cutover: stopping the legacy" -and (Get-Active) -eq "blue" -and ($rf.Calls -match "^docker stop pagentos-prod-api").Count -eq 1 -and ($rf.Calls -match " up -d --no-deps --wait edge").Count -eq 1) "the first cutover stops the legacy api, starts the edge and lands on blue"

        Write-Host "interrupted promotions (SIGKILL at a chosen point) and --reconcile"

        Reset-Host
        $i1 = Invoke-Release -Env @{ PAGENTOS_INTERRUPT_AT = "after_idle_up" }
        Assert-True ($i1.Exit -ne 0 -and $i1.Output -match "INTERRUPT: simulated crash at 'after_idle_up'" -and (Test-Up "blue") -and (Test-Up "green") -and (Get-Active) -eq "blue" -and (Get-Release) -eq $old -and -not (Test-Path (Join-Path $hostBase "app\OLD_TREE"))) "a crash after the idle colour is up leaves both colours running, the marker and RELEASE on the old sha, and the candidate tree in place"
        Clear-Calls
        $c1 = Invoke-Release -Mode "--reconcile"
        if ($c1.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- reconcile 1 (exit $($c1.Exit)) ---"; Write-Host $c1.Output }
        Assert-True ($c1.Exit -eq 0 -and $c1.Output -match "RECONCILE OK: api-blue is canonical \(release $old\)") "reconcile after that crash: the old colour is canonical"
        Assert-True ($c1.Output -match "api-green \($sha\) runs without a completed promotion: a half-promoted candidate; draining and stopping it" -and -not (Test-Up "green") -and (Test-Up "blue") -and (Test-UpstreamBoth "blue") -and (Get-Release) -eq $old -and -not (Test-Draining "blue")) "...the half-promoted candidate is drained and stopped, never made live; the edge names the canonical colour twice; RELEASE is untouched"
        Assert-True ((Get-Content (Join-Path $hostBase "edge\nginx.conf") -Raw) -match "old edge config") "...and the edge runs the canonical tree's nginx.conf again"
        Assert-True ((Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not (Test-Path (Join-Path $hostBase "app.prev")) -and (Test-Path (Join-Path $hostBase "app.interrupted\NEW_TREE"))) "...the tree is the canonical release's again; the candidate tree is kept aside as app.interrupted"
        Assert-True (Test-Path (Join-Path $hostBase "LAST_RECONCILE")) "...and the reconcile leaves its timestamp"

        Reset-Host
        $i2 = Invoke-Release -Env @{ PAGENTOS_INTERRUPT_AT = "after_switch" }
        Assert-True ($i2.Exit -ne 0 -and (Get-Active) -eq "green" -and (Test-UpstreamBoth "green") -and (Test-Up "blue") -and (Test-Up "green") -and (Get-Release) -eq $old -and (Get-Sessions "green") -eq 1) "a crash right after the switch leaves the edge on the candidate, both colours up, the devices on the candidate, RELEASE still the old sha"
        Clear-Calls
        $c2 = Invoke-Release -Mode "--reconcile"
        if ($c2.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- reconcile 2 (exit $($c2.Exit)) ---"; Write-Host $c2.Output }
        Assert-True ($c2.Exit -eq 0 -and $c2.Output -match "the promotion of api-green was interrupted; api-blue is canonical" -and $c2.Output -match "RECONCILE OK: api-blue is canonical") "reconcile after a crash at the switch: the last COMPLETED promotion wins, not the colour the edge happened to name"
        Assert-True ((Get-Active) -eq "blue" -and (Test-UpstreamBoth "blue") -and (Test-Up "blue") -and -not (Test-Up "green") -and -not (Test-Draining "blue") -and (Get-Sessions "blue") -eq 1 -and (Get-Release) -eq $old) "...the edge returns to it, it takes the devices back (undrained first), the candidate is drained and stopped, RELEASE stays"

        Reset-Host
        $i3 = Invoke-Release -Env @{ PAGENTOS_INTERRUPT_AT = "after_drain" }
        Assert-True ($i3.Exit -ne 0 -and (Get-Active) -eq "green" -and -not (Test-Up "blue") -and (Test-Up "green") -and (Get-Release) -eq $old) "a crash after the old colour is stopped leaves the candidate alone and live, RELEASE never written"
        Clear-Calls
        $c3 = Invoke-Release -Mode "--reconcile"
        if ($c3.Exit -ne 0 -or $env:PAGENTOS_BG_VERBOSE) { Write-Host "--- reconcile 3 (exit $($c3.Exit)) ---"; Write-Host $c3.Output }
        Assert-True ($c3.Exit -eq 0 -and $c3.Output -match "api-blue is not running; starting it from its recorded image \($old\)" -and (Test-Up "blue") -and -not (Test-Up "green") -and (Test-UpstreamBoth "blue") -and (Get-Active) -eq "blue" -and (Get-Release) -eq $old) "reconcile: the canonical colour is started from its recorded image and the 99%-promoted candidate still does not become live silently"

        Reset-Host
        $i4 = Invoke-Release -Env @{ PAGENTOS_INTERRUPT_AT = "after_drain" }
        Clear-Calls
        $c4 = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_DOWN = "blue" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host "--- reconcile 4 (exit $($c4.Exit)) ---"; Write-Host $c4.Output }
        Assert-True ($c4.Exit -eq 81 -and $c4.Output -match "RECONCILE EMERGENCY: api-green \($sha\) is live because the canonical release could not start" -and (Test-Up "green") -and (Test-UpstreamBoth "green") -and (Get-Active) -eq "green" -and (Get-Release) -eq $sha) "when the canonical colour cannot come up the candidate takes over LOUDLY (81), with the marker and RELEASE made to say so"

        Reset-Host
        Clear-Calls
        $c5 = Invoke-Release -Mode "--reconcile"
        Assert-True ($c5.Exit -eq 0 -and $c5.Output -match "RECONCILE OK: api-blue is canonical" -and (Test-Up "blue") -and -not (Test-Up "green") -and (Test-UpstreamBoth "blue") -and (Get-Release) -eq $old -and (Get-Sessions "blue") -eq 1 -and -not ($c5.Calls -match " stop api-") -and -not ($c5.Calls -match " up -d")) "a consistent host reconciles to itself: nothing started, nothing stopped, the devices untouched"

        Reset-Host
        $degraded = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_STATUS = "degraded" }
        Assert-True ($degraded.Exit -eq 84 -and $degraded.Output -match "operator attention required" -and $degraded.Output -notmatch "RECONCILE OK") "HTTP 200 with status=degraded is never reported as RECONCILE OK"

        Reset-Host
        $wrong = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_SERVED_RELEASE = "9999999999999999999999999999999999999999" }
        Assert-True ($wrong.Exit -eq 80 -and $wrong.Output -match "operator attention required" -and $wrong.Output -notmatch "RECONCILE OK") "a healthy body from the wrong release cannot keep or promote a colour"

        Write-Host "healthy, then degraded: the periodic reconcile never trades a serving colour for an older one"
        $lkg = "3333333333333333333333333333333333333333"

        Reset-Host
        Set-RecordedRelease -Colour "green" -Sha $lkg
        Clear-Calls
        $blip = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_STATUS_BLUE = "degraded" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host "--- blip (exit $($blip.Exit)) ---"; Write-Host $blip.Output }
        Assert-True ($blip.Exit -eq 84 -and $blip.Output -match "RECONCILE DEGRADED: api-blue \($old\) stays canonical" -and (Get-Active) -eq "blue" -and (Test-UpstreamBoth "blue") -and (Get-Release) -eq $old -and -not (Test-Up "green") -and -not ($blip.Calls -match " up -d --no-deps --wait api-green")) "a canonical colour that serves its own release but reports degraded stays canonical, even with a healthy older colour on record: a dependency that recovers mid-takeover must not make the older build live and rewrite RELEASE"

        Reset-Host
        Set-RecordedRelease -Colour "green" -Sha $lkg
        Clear-Calls
        $shared = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_STATUS = "degraded" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host "--- shared (exit $($shared.Exit)) ---"; Write-Host $shared.Output }
        Assert-True ($shared.Exit -eq 84 -and $shared.Output -match "operator attention required" -and $shared.Output -notmatch "RECONCILE OK" -and -not (Test-Up "green") -and -not ($shared.Calls -match " up -d --no-deps --wait api-green") -and (Get-Active) -eq "blue" -and (Get-Release) -eq $old) "a shared outage (both colours degraded) starts nothing: the older colour does not sit running beside the canonical one with a second routine clock and worker"

        Reset-Host
        Set-RecordedRelease -Colour "green" -Sha $lkg
        Clear-Calls
        $dead = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_DOWN = "blue" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host "--- dead (exit $($dead.Exit)) ---"; Write-Host $dead.Output }
        Assert-True ($dead.Exit -eq 81 -and $dead.Output -match "RECONCILE EMERGENCY: api-green \($lkg\) is live" -and (Get-Active) -eq "green" -and (Test-UpstreamBoth "green") -and (Get-Release) -eq $lkg) "a canonical colour that does not answer at all is still replaced, loudly, by a healthy recorded colour"

        Reset-Host
        Set-RecordedRelease -Colour "green" -Sha $lkg
        Clear-Calls
        $both = Invoke-Release -Mode "--reconcile" -Env @{ FAKE_HEALTH_DOWN = "blue"; FAKE_HEALTH_STATUS_GREEN = "degraded" }
        if ($env:PAGENTOS_BG_VERBOSE) { Write-Host "--- both (exit $($both.Exit)) ---"; Write-Host $both.Output }
        Assert-True ($both.Exit -eq 80 -and $both.Output -match "operator attention required" -and -not (Test-Up "green") -and @($both.Calls -match " stop api-green").Count -ge 1 -and (Get-Active) -eq "blue" -and (Get-Release) -eq $old) "a takeover whose alternate never becomes healthy stops the alternate it started, switches nothing, and leaves RELEASE alone"

        Reset-Host
        Remove-Item -LiteralPath (Join-Path $hostBase "state\up-edge")
        $c6 = Invoke-Release -Mode "--reconcile"
        Assert-True ($c6.Exit -eq 0 -and $c6.Output -match "the edge is not running; starting it" -and (Test-Path (Join-Path $hostBase "state\up-edge"))) "reconcile starts the edge when it is down"

        Reset-Host -Active ""
        [IO.File]::WriteAllText((Join-Path $hostBase ".env"), "PAGENTOS_BIND_IP=100.64.0.1`n")
        $c7 = Invoke-Release -Mode "--reconcile"
        Assert-True ($c7.Exit -eq 0 -and $c7.Output -match "no active marker; no blue/green state to rebuild") "with no marker there is nothing to reconcile"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-release-bluegreen tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
