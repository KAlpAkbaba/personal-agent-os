<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the Cloud Core maintenance window script
    (ADR-0223): scripts/cloud/maintenance-reboot.sh under Git Bash with a fake
    docker/apt-get/apt-mark/systemctl/curl/df/uname/reboot/journalctl/ps on PATH and a
    sandbox in place of /opt/pagentos.
.DESCRIPTION
    Proven here, with no real host:
      * --preflight refuses (exit 10, the failing line named) on each of: health not ok,
        failing_checks set, backup older than 24 h, a failure marker, pin != RELEASE,
        disk over 80 %, a held docker-ce, a removal in `apt-get -s upgrade`; passes when
        all hold; changes nothing (no systemctl stop / apt upgrade / reboot);
      * --run without --preflight is refused (64); with it, the reconcile timer is stopped
        BEFORE the upgrade and the maintenance marker exists when `reboot` is called;
      * --verify refuses (exit 21) when the kernel is still the old one, the reconcile did
        not say RECONCILE OK, or reboot-required remains; on success it writes
        LAST_MAINTENANCE.json with the downtime measured from the marker and removes the
        marker; without a marker it refuses (20).
    Run: powershell -NoProfile -File scripts\tests\maintenance-reboot.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\maintenance-reboot.sh"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-maint-tests-$([guid]::NewGuid().ToString('N'))"
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
$sha = "3333333333333333333333333333333333333333"

# Fakes. Every one logs its call to $FAKE_STATE/calls.log; knobs are env variables.
$fakes = @{
    docker = @(
        '#!/usr/bin/env bash',
        'echo "docker $*" >> "$FAKE_STATE/calls.log"',
        'case "$*" in',
        '  inspect*) echo "${FAKE_LEFTOVER_STATE:-created}"; exit 0;;',
        '  ps*) printf "pagentos-prod-postgres-1\npagentos-prod-redis-1\npagentos-prod-minio-1\npagentos-prod-temporal-1\npagentos-prod-edge-1\n${FAKE_API_LINE-pagentos-prod-api-blue-1\n}pagentos-prod-godseye-1\n"; exit 0;;',
        'esac',
        'exit 0')
    curl = @(
        '#!/usr/bin/env bash',
        'echo "curl $*" >> "$FAKE_STATE/calls.log"',
        'if [ -n "${FAKE_HEALTH_DOWN:-}" ]; then exit 22; fi',
        'printf "{\"status\":\"%s\",\"failing_checks\":\"%s\",\"release\":{\"version\":\"%s\"}}" "${FAKE_HEALTH_STATUS:-ok}" "${FAKE_FAILING_CHECKS:-}" "$(cat "$FAKE_STATE/release")"')
    "apt-get" = @(
        '#!/usr/bin/env bash',
        'echo "apt-get $*" >> "$FAKE_STATE/calls.log"',
        'case "$*" in',
        '  *"-s upgrade"*) echo "Inst tailscale [1.100] (1.102.3)"; if [ -n "${FAKE_APT_REMOVES:-}" ]; then echo "Remv libfoo [1.0]"; fi; exit 0;;',
        'esac',
        'exit 0')
    "apt-mark" = @(
        '#!/usr/bin/env bash',
        'echo "apt-mark $*" >> "$FAKE_STATE/calls.log"',
        'if [ -n "${FAKE_HELD:-}" ]; then echo "$FAKE_HELD"; fi; exit 0')
    systemctl = @(
        '#!/usr/bin/env bash',
        'echo "systemctl $*" >> "$FAKE_STATE/calls.log"',
        'exit 0')
    journalctl = @(
        '#!/usr/bin/env bash',
        'echo "journalctl $*" >> "$FAKE_STATE/calls.log"',
        'echo "${FAKE_RECONCILE_LINE:-RECONCILE OK: api-blue is canonical (release 3333)}"')
    df = @(
        '#!/usr/bin/env bash',
        'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 50 50 %s%% /\n" "${FAKE_DISK_PCT:-40}"')
    uname = @(
        '#!/usr/bin/env bash',
        'echo "${FAKE_KERNEL:-6.8.0-138-generic}"')
    ps = @(
        '#!/usr/bin/env bash',
        'printf "STAT\nSs\n"; for i in $(seq 1 ${FAKE_ZOMBIES:-0}); do echo Z; done')
    reboot = @(
        '#!/usr/bin/env bash',
        'if [ -f "$FAKE_MARKER" ]; then m=yes; else m=no; fi',
        'echo "reboot marker=$m" >> "$FAKE_STATE/calls.log"',
        'exit 0')
    flock = @('#!/usr/bin/env bash', 'echo "flock $*" >> "$FAKE_STATE/calls.log"', 'exit "${FAKE_FLOCK_EXIT:-0}"')
}
foreach ($k in $fakes.Keys) { [IO.File]::WriteAllText((Join-Path $fakeBin $k), (($fakes[$k] -join "`n") + "`n")) }

function Reset-Host {
    Remove-Item -LiteralPath $hostBase -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($d in "state", "backup\failures", "recovery", "app\scripts\cloud") { New-Item -ItemType Directory -Force -Path (Join-Path $hostBase $d) | Out-Null }
    [IO.File]::WriteAllText((Join-Path $hostBase "RELEASE"), "$sha`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "state\release"), "$sha`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "LAST_KNOWN_GOOD"), ("1" * 40) + "`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "recovery\APPROVED_SHA"), "$sha`n")
    $fin = [DateTime]::UtcNow.AddHours(-3).ToString("yyyy-MM-ddTHH:mm:ssZ")
    [IO.File]::WriteAllText((Join-Path $hostBase "backup\LAST_BACKUP.json"), "{`"snapshot`":`"abc`",`"finished_at`":`"$fin`"}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\scripts\cloud\backup-cloud-core.sh"), "#!/usr/bin/env bash`necho `"backup-script`" >> `"`$FAKE_STATE/calls.log`"`n")
}

function Invoke-Maint {
    param([string[]]$Flags = @("--preflight"), [hashtable]$Env = @{})
    $cmd = "PAGENTOS_BASE='$(& $u $hostBase)' PAGENTOS_BACKUP_ROOT='$(& $u (Join-Path $hostBase 'backup'))' PAGENTOS_RECOVERY_ROOT='$(& $u (Join-Path $hostBase 'recovery'))' " +
           "PAGENTOS_HEALTH_URL=http://fake/health PAGENTOS_REBOOT_REQUIRED='$(& $u (Join-Path $hostBase 'state\reboot-required'))' PAGENTOS_WAIT_STEP_S=0 PAGENTOS_WAIT_TRIES=2 " +
           "FAKE_STATE='$(& $u (Join-Path $hostBase 'state'))' FAKE_MARKER='$(& $u (Join-Path $hostBase 'MAINTENANCE_MARKER'))' " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" bash '$(& $u $hostScript)' $($Flags -join ' ') 2>&1"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $bash -c (ConvertTo-NativeCallArgument -Value $cmd) 2>&1 | Out-String; $exit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
    $log = Join-Path $hostBase "state\calls.log"
    $calls = if (Test-Path $log) { @(Get-Content $log) } else { @() }
    return [pscustomobject]@{ Output = $out; Exit = $exit; Calls = $calls }
}
function Write-Marker {
    param([int]$AgoS = 120, [string]$Kernel = "6.8.0-138-generic")
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    [IO.File]::WriteAllText((Join-Path $hostBase "MAINTENANCE_MARKER"), "start_epoch=$($now - $AgoS)`nstart_iso=x`nkernel_before=$Kernel`nrelease=$sha`n")
}

try {
    if (-not (Test-Path $bash)) { Write-Host "  SKIP  maintenance tests: Git Bash not found at $bash" }
    else {
        Write-Host "maintenance window (maintenance-reboot.sh under Git Bash, fakes)"
        Reset-Host
        $p = Invoke-Maint
        if ($p.Exit -ne 0 -or $env:PAGENTOS_MAINT_VERBOSE) { Write-Host $p.Output }
        Assert-True ($p.Exit -eq 0 -and $p.Output -match "PREFLIGHT OK") "preflight passes when every condition holds"
        Assert-True ($p.Output -match "read .*health" -and $p.Output -match "read .*LAST_BACKUP.json" -and $p.Output -match "read .*APPROVED_SHA") "every check names what it read"
        Assert-True (-not (@($p.Calls | Where-Object { $_ -match "systemctl stop|^apt-get update|^reboot|docker rm" -or ($_ -match "^apt-get .*upgrade$" -and $_ -notmatch " -s ") }).Count -gt 0)) "preflight changes nothing"

        $refusals = @(
            @{ Name = "health not ok"; Env = @{ FAKE_HEALTH_STATUS = "degraded" }; Match = "FAIL health" },
            @{ Name = "failing_checks set"; Env = @{ FAKE_FAILING_CHECKS = "backup" }; Match = "FAIL health" },
            @{ Name = "health unreachable"; Env = @{ FAKE_HEALTH_DOWN = "1" }; Match = "FAIL health" },
            @{ Name = "held docker-ce"; Env = @{ FAKE_HELD = "docker-ce" }; Match = "FAIL apt-hold" },
            @{ Name = "disk over 80 percent"; Env = @{ FAKE_DISK_PCT = "85" }; Match = "FAIL disk" },
            @{ Name = "apt removes a package"; Env = @{ FAKE_APT_REMOVES = "1" }; Match = "FAIL apt-simulate" }
        )
        foreach ($c in $refusals) {
            Reset-Host
            $r = Invoke-Maint -Env $c.Env
            Assert-True ($r.Exit -eq 10 -and $r.Output -match $c.Match -and $r.Output -notmatch "PREFLIGHT OK") "preflight refuses (10): $($c.Name)"
        }
        Reset-Host
        $old = [DateTime]::UtcNow.AddHours(-30).ToString("yyyy-MM-ddTHH:mm:ssZ")
        [IO.File]::WriteAllText((Join-Path $hostBase "backup\LAST_BACKUP.json"), "{`"finished_at`":`"$old`"}`n")
        $r = Invoke-Maint
        Assert-True ($r.Exit -eq 10 -and $r.Output -match "FAIL backup-age") "preflight refuses (10): backup older than 24 h"
        Reset-Host
        [IO.File]::WriteAllText((Join-Path $hostBase "backup\failures\pagentos-backup.service.json"), "{}")
        $r = Invoke-Maint
        Assert-True ($r.Exit -eq 10 -and $r.Output -match "FAIL failure-marker") "preflight refuses (10): a failure marker"
        Reset-Host
        [IO.File]::WriteAllText((Join-Path $hostBase "recovery\APPROVED_SHA"), ("4" * 40) + "`n")
        $r = Invoke-Maint
        Assert-True ($r.Exit -eq 10 -and $r.Output -match "FAIL pin") "preflight refuses (10): recovery pin != RELEASE"

        Reset-Host
        $r = Invoke-Maint -Flags @("--run")
        Assert-True ($r.Exit -eq 64 -and -not ($r.Calls -match "systemctl|apt-get|reboot")) "--run without --preflight is refused (64) and does nothing"
        Reset-Host
        $r = Invoke-Maint -Flags @("--preflight", "--run") -Env @{ FAKE_DISK_PCT = "90" }
        Assert-True ($r.Exit -eq 10 -and -not ($r.Calls -match "systemctl stop|reboot")) "--run stops at a failed preflight"

        Reset-Host
        $r = Invoke-Maint -Flags @("--preflight", "--run")
        if ($r.Exit -ne 0 -or $env:PAGENTOS_MAINT_VERBOSE) { Write-Host $r.Output; $r.Calls | ForEach-Object { Write-Host "    $_" } }
        $c = $r.Calls
        $iStop = [array]::IndexOf($c, ($c | Where-Object { $_ -match "^systemctl stop .*reconcile.timer" } | Select-Object -First 1))
        $iUp = [array]::IndexOf($c, ($c | Where-Object { $_ -match "^apt-get .*upgrade$" -and $_ -notmatch " -s " } | Select-Object -First 1))
        $iBk = [array]::IndexOf($c, "backup-script")
        $iRm = [array]::IndexOf($c, ($c | Where-Object { $_ -match "^docker rm .*pagentos-prod-api" } | Select-Object -First 1))
        Assert-True ($r.Exit -eq 0 -and $iBk -ge 0 -and $iBk -lt $iStop -and $iStop -lt $iRm -and $iStop -ge 0 -and $iStop -lt $iUp) "--run order: backup -> timer stopped -> leftover removed -> apt upgrade"
        Assert-True (($c -contains "reboot marker=yes") -and (Test-Path (Join-Path $hostBase "MAINTENANCE_MARKER"))) "the maintenance marker exists when reboot is called"
        $mk = if (Test-Path (Join-Path $hostBase "MAINTENANCE_MARKER")) { Get-Content (Join-Path $hostBase "MAINTENANCE_MARKER") -Raw } else { "" }
        Assert-True ($mk -match "start_epoch=\d+" -and $mk -match "kernel_before=6.8.0-138") "the marker carries the start time and the old kernel"

        # The operation lock: the minute reconcile holds it for a few seconds every minute
        # (measured on the host, 2026-10-01: one preflight in forty said "held"). A one-shot
        # window that asked without waiting would be postponed by its own housekeeping, so the
        # check WAITS for the lock; a release, which holds it for minutes, still refuses.
        Reset-Host
        [IO.File]::WriteAllText((Join-Path $hostBase ".bluegreen-operation.lock"), "")
        $r = Invoke-Maint
        Assert-True ($r.Exit -eq 0 -and @($r.Calls | Where-Object { $_ -match "^flock -w 45 " }).Count -eq 1 -and @($r.Calls | Where-Object { $_ -match "^flock -n" }).Count -eq 0) "the lock check waits (flock -w 45), it does not ask once"
        Reset-Host
        [IO.File]::WriteAllText((Join-Path $hostBase ".bluegreen-operation.lock"), "")
        $r = Invoke-Maint -Env @{ FAKE_FLOCK_EXIT = "1" }
        Assert-True ($r.Exit -eq 10 -and $r.Output -match "FAIL no-release") "a lock still held after the wait refuses the window (10)"

        # The serving colour is whichever the last release left (found 2026-10-01: the release of
        # that afternoon made GREEN the active colour, and the script waited for api-blue by name -
        # that evening's window would have upgraded, waited five minutes and not rebooted).
        Reset-Host
        $r = Invoke-Maint -Flags @("--preflight", "--run") -Env @{ FAKE_API_LINE = "pagentos-prod-api-green-1\n" }
        if ($r.Exit -ne 0 -or $env:PAGENTOS_MAINT_VERBOSE) { Write-Host $r.Output }
        Assert-True ($r.Exit -eq 0 -and ($r.Calls -contains "reboot marker=yes")) "--run reboots when GREEN is the serving colour"
        Reset-Host
        $r = Invoke-Maint -Flags @("--preflight", "--run") -Env @{ FAKE_API_LINE = "" }
        Assert-True ($r.Exit -eq 11 -and $r.Output -match "containers still missing: api" -and @($r.Calls | Where-Object { $_ -match "^reboot" }).Count -eq 0) "--run does not reboot when NO api colour came back"
        Reset-Host; Write-Marker -AgoS 60
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-142-generic"; FAKE_API_LINE = "pagentos-prod-api-green-1\n"; FAKE_RECONCILE_LINE = "RECONCILE OK: api-green is canonical (release 3333)" }
        Assert-True ($r.Exit -eq 0 -and $r.Output -match "VERIFY OK") "--verify passes when GREEN is the serving colour"

        # --verify
        Reset-Host
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-142-generic" }
        Assert-True ($r.Exit -eq 20) "--verify without a marker is refused (20)"
        Reset-Host; Write-Marker
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-138-generic" }
        Assert-True ($r.Exit -eq 21 -and $r.Output -match "FAIL kernel" -and (Test-Path (Join-Path $hostBase "MAINTENANCE_MARKER"))) "--verify refuses (21) when the kernel is still the old one; the marker stays"
        Reset-Host; Write-Marker
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-142-generic"; FAKE_RECONCILE_LINE = "RECONCILE FAILED: nothing serves" }
        Assert-True ($r.Exit -eq 21 -and $r.Output -match "FAIL reconcile") "--verify refuses (21) when the reconcile did not say OK"
        Reset-Host; Write-Marker
        [IO.File]::WriteAllText((Join-Path $hostBase "state\reboot-required"), "")
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-142-generic" }
        Assert-True ($r.Exit -eq 21 -and $r.Output -match "FAIL reboot-required") "--verify refuses (21) while reboot-required remains"
        Reset-Host; Write-Marker -AgoS 120
        $r = Invoke-Maint -Flags @("--verify") -Env @{ FAKE_KERNEL = "6.8.0-142-generic" }
        if ($r.Exit -ne 0 -or $env:PAGENTOS_MAINT_VERBOSE) { Write-Host $r.Output }
        $lm = Join-Path $hostBase "LAST_MAINTENANCE.json"
        $json = if (Test-Path $lm) { Get-Content $lm -Raw | ConvertFrom-Json } else { $null }
        Assert-True ($r.Exit -eq 0 -and $r.Output -match "VERIFY OK" -and $null -ne $json -and [int]$json.downtime_seconds -ge 120 -and [int]$json.downtime_seconds -lt 200) "--verify writes LAST_MAINTENANCE.json with the downtime measured from the marker"
        Assert-True ($null -ne $json -and $json.kernel_after -eq "6.8.0-142-generic" -and $json.kernel_before -eq "6.8.0-138-generic" -and -not (Test-Path (Join-Path $hostBase "MAINTENANCE_MARKER"))) "the record names both kernels and the marker is removed"
    }
}
finally { Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host ""
Write-Host "passed: $($script:Passes)  failed: $($script:Failures)"
if ($script:Failures -gt 0) { exit 1 }
