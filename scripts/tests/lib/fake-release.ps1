<#
.SYNOPSIS
    A stand-in for BOTH scripts/cloud/release-cloud-core.ps1 and ssh.exe in
    scripts/tests/team-release.tests.ps1. It never reaches a host: the "host" is a folder.

.DESCRIPTION
    scripts/team/release.ps1 is given this file twice:
      * as -ReleaseScript: it is then called with the release script's own parameters
        (-BlueGreen, -Preflight, -RepoRoot <tree>, ...). It reads HEAD of -RepoRoot - the sha a
        real release would ship - and plays the host transaction;
      * through -SshPath powershell.exe and -SshPrefixArguments '...','-File','<this>','ssh': the
        first argument is then 'ssh' and the LAST one is the remote command. Two commands are
        known: the read-only probe (it prints "release=...") and install-recovery-supervisor.sh
        <40 hex>. Anything else is exit 2.

    The host is PAGENTOS_FAKE_HOST: state.json there holds release, app_release, pin, lkg,
    colour, maintenance_marker, maintenance_in_s, health_status, health_release, reconcile and
    probe_exit. Every call is one line in calls.log there:
      release|preflight|release (the mode)|bluegreen=<bool>|<repo root>|<HEAD>|dirty=<bool>
      ssh|probe   ssh|pin|<sha>   ssh|unknown|<command>

    PAGENTOS_FAKE_RELEASE_SCENARIO:
      ok              preflight passes; the release switches colour, RELEASE/LKG move, the edge
                      serves the new sha; the pin is installed and the reconcile says OK for it
      preflight-fail  the preflight exits 71 (compose invalid; nothing changed)
      rollback        the release exits 1 after "never answered healthy; rolled back": the host
                      is left exactly as it was
      pin-wrong       the install exits 0 but APPROVED_SHA stays what it was

    Like the real ones it writes to BOTH streams: a routine line on stderr (nginx's notice, the
    installer's progress) beside its stdout, so a caller that merges them is visible.
#>

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$hostDir = [string]$env:PAGENTOS_FAKE_HOST
if (-not $hostDir) { [Console]::Error.WriteLine("PAGENTOS_FAKE_HOST is not set"); exit 9 }
$scenario = [string]$env:PAGENTOS_FAKE_RELEASE_SCENARIO
if (-not $scenario) { $scenario = "ok" }
$statePath = Join-Path $hostDir "state.json"
$state = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($statePath, $utf8))

function Save-HostState { [System.IO.File]::WriteAllText($statePath, (ConvertTo-Json -InputObject $state -Depth 5), $utf8) }
function Add-HostCall { param([string]$Line) [System.IO.File]::AppendAllText((Join-Path $hostDir "calls.log"), $Line + "`n", $utf8) }

$all = @($args)
if ($all.Count -gt 0 -and [string]$all[0] -eq "ssh") {
    $command = [string]$all[$all.Count - 1]
    if ($command -match 'install-recovery-supervisor\.sh\s+([0-9a-f]{40})') {
        $sha = $Matches[1]
        Add-HostCall "ssh|pin|$sha"
        [Console]::Error.WriteLine("install-recovery-supervisor: waiting for the operation lock")
        if ($scenario -ne "pin-wrong") {
            $state.pin = $sha
            $state.reconcile = "RECONCILE OK: api-$($state.colour) is canonical (release $sha); markers, upstreams and containers agree"
            Save-HostState
        }
        [Console]::Out.WriteLine("recovery supervisor installed for $sha")
        exit 0
    }
    if ($command -match 'release=') {
        Add-HostCall "ssh|probe"
        $health = ConvertTo-Json -Compress -InputObject ([ordered]@{ status = [string]$state.health_status; release = [ordered]@{ version = [string]$state.health_release } })
        [Console]::Out.WriteLine("release=$($state.release)")
        [Console]::Out.WriteLine("app_release=$($state.app_release)")
        [Console]::Out.WriteLine("pin=$($state.pin)")
        [Console]::Out.WriteLine("lkg=$($state.lkg)")
        [Console]::Out.WriteLine("colour=$($state.colour)")
        [Console]::Out.WriteLine("maintenance_marker=$($state.maintenance_marker)")
        [Console]::Out.WriteLine("maintenance_in_s=$($state.maintenance_in_s)")
        [Console]::Out.WriteLine("health_b64=" + [Convert]::ToBase64String($utf8.GetBytes($health)))
        [Console]::Out.WriteLine("reconcile=$($state.reconcile)")
        [Console]::Error.WriteLine("probe: read-only")
        exit ([int]$state.probe_exit)
    }
    Add-HostCall "ssh|unknown|$command"
    [Console]::Error.WriteLine("fake ssh: unknown command")
    exit 2
}

# ---- the release script
$preflight = $all -contains "-Preflight"
$blueGreen = $all -contains "-BlueGreen"
$repo = (Get-Location).ProviderPath
$at = [Array]::IndexOf([object[]]$all, "-RepoRoot")
if ($at -ge 0 -and $at + 1 -lt $all.Count) { $repo = [string]$all[$at + 1] }
$head = (& git.exe -C $repo rev-parse HEAD | Out-String).Trim()
$dirty = [bool]((& git.exe -C $repo status --porcelain | Out-String).Trim())
Add-HostCall ("release|" + $(if ($preflight) { "preflight" } else { "release" }) + "|bluegreen=$blueGreen|$repo|$head|dirty=$dirty")
[Console]::Error.WriteLine("nginx: [notice] signal process started")
if ($preflight) {
    if ($scenario -eq "preflight-fail") { [Console]::Out.WriteLine("compose config is INVALID"); exit 71 }
    [Console]::Out.WriteLine("preflight OK: the host can take $head; active colour $($state.colour)")
    exit 0
}
if ($scenario -eq "rollback") {
    [Console]::Out.WriteLine("api-idle never answered healthy at $head; rolled back")
    [Console]::Error.WriteLine("release FAILED (exit 75): the idle colour never answered health; rolled back")
    exit 1
}
$state.lkg = [string]$state.release
$state.colour = if ([string]$state.colour -eq "blue") { "green" } else { "blue" }
$state.release = $head
$state.app_release = $head
$state.health_release = $head
Save-HostState
[Console]::Out.WriteLine("RELEASE OK: $head is running as api-$($state.colour) behind the edge")
exit 0
