<#
.SYNOPSIS
    The installer's Cloud Core verification path, reduced to the part that failed on the
    owner's machine, so a test can run it in a CLEAN PowerShell process.

.DESCRIPTION
    This file is deliberately a SCRIPT, and the test that uses it starts it BOTH ways. That
    is not a detail - it is the whole point:

        powershell -NoProfile -File  child.ps1          the script is the top-level scope
        <a script that does> & child.ps1                the script gets a CHILD scope

    install-device-service.ps1 runs the second way (the owner types `.\scripts\...`), and
    only in that mode does a script block bound by `.GetNewClosure()` - whose module is
    linked to the GLOBAL session state - fail to see a function dot-sourced into the script
    scope. Every harness under scripts\tests runs with -File, which is why the defect
    survived two days of green tests and appeared first on the owner's install.

    So this script loads the libraries the way the installer loads them, builds the real
    fetcher with New-CoreDeviceFetcher, and runs the real Test-AgentHeartbeatOnCore against
    whatever URL it is given. It prints one line of JSON on stdout; the caller judges it.

    Capabilities arrive as one comma-separated string rather than an array: `-File` argument
    binding differs between the two launch modes, and a test whose two halves are not given
    identical input is comparing two things at once.

    Nothing here elevates, installs, or touches the live agent.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [Parameter(Mandatory = $true)][string]$DeviceId,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [string]$ExpectedCapabilities = "",
    [int]$TimeoutSeconds = 6,
    [int]$PollSeconds = 1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

#: scripts\tests\lib -> scripts\tests -> scripts -> repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

# Exactly the installer's import: AgentUpdate.ps1 and nothing else. If Invoke-JsonUtf8 is
# reachable it is because AgentUpdate.ps1 pulled HttpJson.ps1 in - not because an
# interactive shell happened to have it.
. (Join-Path $repoRoot "scripts\lib\AgentUpdate.ps1")

$caps = @($ExpectedCapabilities -split "," | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })

$result = [ordered]@{
    preloaded_globally = [bool](Get-Item "Function:\global:Invoke-JsonUtf8" -ErrorAction SilentlyContinue)
    built              = $false
    build_error        = ""
    ok                 = $false
    verifier_fault     = $false
    attempts           = 0
    reasons            = @()
    observed_version   = ""
    observed_caps      = 0
}

try {
    $fetch = New-CoreDeviceFetcher -BaseUrl $BaseUrl -Token "test-token"
    $result.built = $true
}
catch {
    $result.build_error = $_.Exception.Message
    $result | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

$heartbeat = Test-AgentHeartbeatOnCore -FetchDevices $fetch -DeviceId $DeviceId `
    -ExpectedVersion $ExpectedVersion -ExpectedCapabilities $caps `
    -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds

$result.ok = [bool]$heartbeat.Ok
$result.verifier_fault = [bool]$heartbeat.VerifierFault
$result.attempts = [int]$heartbeat.Attempts
$result.reasons = @($heartbeat.Reasons)
$result.observed_version = [string]$heartbeat.Observed.software_version
$result.observed_caps = [int]$heartbeat.Observed.capability_count

$result | ConvertTo-Json -Depth 5 -Compress
