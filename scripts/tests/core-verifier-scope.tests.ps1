<#
.SYNOPSIS
    The regression for the 2026-09-09 owner install: the installer's Cloud Core verifier,
    started from a CLEAN PowerShell process in which Invoke-JsonUtf8 is not preloaded.

.DESCRIPTION
    What happened. The owner ran

        .\scripts\install-device-service.ps1 -DisplayPower -Operator

    The candidate staged, verified file by file, swapped, and became live as device-service
    0.6.0 with 85 capabilities and a proven live browser worker. Then the Cloud Core health
    gate failed after 90.6 s having never sent a single HTTP request: thirty-one times, three
    seconds apart, "The term 'Invoke-JsonUtf8' is not recognized". The engine rolled back.

    Why the tests did not catch it. New-CoreDeviceFetcher returned
    `{ Invoke-JsonUtf8 ... }.GetNewClosure()`. GetNewClosure binds a script block to a new
    dynamic module linked to the GLOBAL session state, so it cannot see a function
    dot-sourced into a SCRIPT's scope - which is where install-device-service.ps1 puts every
    library it loads. Whether that matters depends entirely on how the outermost script was
    started:

        powershell -NoProfile -File  install.ps1        the script IS the top level -> works
        .\install.ps1   /   & install.ps1               the script gets a child scope -> FAILS

    Every harness in scripts\tests runs with -File, and the M18.4 unit tests inject a FAKE
    fetch script block, so the real one was never invoked by anything. The owner typed the
    one form nothing exercised.

    So these tests do the two things that would have caught it:

      * they drive the REAL New-CoreDeviceFetcher and the REAL Test-AgentHeartbeatOnCore -
        no fake fetch - against a loopback HTTP stub, so the whole chain runs, including the
        UTF-8 body decode whose helpers live in the same lost script scope;
      * they start it in a child process AS A COMMAND, and assert first of all that
        Invoke-JsonUtf8 was NOT already defined there. A green result from a shell that
        happens to have the function loaded proves nothing, so that precondition is a test.

    Run: powershell -NoProfile -File scripts\tests\core-verifier-scope.tests.ps1
    Exit code is the number of failed assertions. No elevation, no network, no device.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\AgentUpdate.ps1")
. (Join-Path $repoRoot "scripts\tests\lib\LoopbackJson.ps1")

$script:Failures = 0
$script:Passes = 0
$script:PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$script:Child = Join-Path $repoRoot "scripts\tests\lib\CoreVerifierChild.ps1"
$script:Sandbox = Join-Path $env:TEMP "pagentos-core-verifier-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$script:DeviceId = "3f60fdb5-5022-48cf-bb3c-d7192466b701"
$script:Version = "0.6.0"
$script:Caps = @("desktop.open_application", "browser.chrome", "operator.window_control")

$script:Body = New-CoreDeviceListingJson -DeviceId $script:DeviceId -Version $script:Version -Capabilities $script:Caps

function Invoke-Verifier {
    param([ValidateSet("command", "file")][string]$Mode, [int]$Port)
    return Invoke-CoreVerifierChild -Mode $Mode -RepoRoot $repoRoot -Sandbox $script:Sandbox -Port $Port `
        -DeviceId $script:DeviceId -ExpectedVersion $script:Version -ExpectedCapabilities $script:Caps
}

Write-Host "core verifier scope regression (the 2026-09-09 owner install)" -ForegroundColor Cyan

# ---------------------------------------------------------------------------- the fix
$stub = Start-JsonStub -Json $script:Body
try {
    $asCommand = Invoke-Verifier -Mode "command" -Port $stub.Port

    Assert-True (-not $asCommand.preloaded_globally) `
        "the child process had NO Invoke-JsonUtf8 preloaded - without this the rest proves nothing"
    Assert-True ($asCommand.built -and -not $asCommand.build_error) `
        "New-CoreDeviceFetcher built a fetcher in a clean process ($($asCommand.build_error))"
    Assert-True ([bool]$asCommand.ok) `
        "the REAL fetcher, invoked as a COMMAND, read Cloud Core and the candidate verified (reasons: $(@($asCommand.reasons) -join '; '))"
    Assert-True (-not [bool]$asCommand.verifier_fault) "no verifier fault was reported"
    Assert-True ($asCommand.observed_version -eq $script:Version -and [int]$asCommand.observed_caps -eq $script:Caps.Count) `
        "the whole chain ran, body decode included: version '$($asCommand.observed_version)', $($asCommand.observed_caps) capabilities"
    Assert-True ([int]$asCommand.attempts -eq 1) `
        "one attempt was enough - the loop did not spend a timeout on a fault it could not fix"

    # The mode that hid the defect must keep working too; the fix is not a swap of one
    # broken invocation form for another.
    $asFile = Invoke-Verifier -Mode "file" -Port $stub.Port
    Assert-True (-not $asFile.preloaded_globally) "the -File child had no Invoke-JsonUtf8 preloaded either"
    Assert-True ([bool]$asFile.ok) "the same verifier still passes under -File (reasons: $(@($asFile.reasons) -join '; '))"
}
finally { Stop-JsonStub -Stub $stub }

# ------------------------------------- a missing dependency is refused BEFORE the swap
$saved = Get-Item "Function:\Invoke-JsonUtf8"
Remove-Item "Function:\Invoke-JsonUtf8" -Force
try {
    $threw = ""
    try { $null = New-CoreDeviceFetcher -BaseUrl "http://127.0.0.1:9" -Token "t" }
    catch { $threw = [string]$_.Exception.Message }
    Assert-True ($threw -ne "") "a fetcher with no Invoke-JsonUtf8 refuses to be built at all"
    Assert-True ($threw -match "Invoke-JsonUtf8" -and $threw -match "HttpJson") `
        "and the refusal names the function and the file that defines it"
}
finally { Set-Item "Function:\Invoke-JsonUtf8" -Value $saved.ScriptBlock }

# ------------------------------------------- a broken verifier is not a silent Cloud Core
$script:Clock = [DateTime]::new(2026, 9, 9, 11, 4, 45, [DateTimeKind]::Utc)
$fault = Test-AgentHeartbeatOnCore `
    -FetchDevices { throw (New-Object System.Management.Automation.CommandNotFoundException("The term 'Invoke-JsonUtf8' is not recognized as the name of a cmdlet, function, script file, or operable program.")) } `
    -DeviceId $script:DeviceId -ExpectedVersion $script:Version -ExpectedCapabilities $script:Caps `
    -TimeoutSeconds 90 -PollSeconds 3 `
    -Sleep { param($s) $script:Clock = $script:Clock.AddSeconds($s) } `
    -Now { $script:Clock }

Assert-True (-not $fault.Ok) "a verifier fault still FAILS the health gate - the rollback is unchanged"
Assert-True ([bool]$fault.VerifierFault) "and it is reported as a verifier fault, not as a Cloud Core condition"
Assert-True ([int]$fault.Attempts -eq 1) "it returns on the first attempt instead of retrying 31 times over 90 s"
Assert-True ((@($fault.Reasons) -join ' ') -match "installer's own code") `
    "the reason names the installer's own code"
Assert-True ((@($fault.Reasons) -join ' ') -notmatch "does not list device") `
    "and it does not accuse Cloud Core of not listing a device it was never asked about"

# A genuine Cloud Core failure must still be retried to the timeout - the fast path above is
# for faults that waiting cannot change, and nothing else.
$script:Clock2 = [DateTime]::new(2026, 9, 9, 11, 4, 45, [DateTimeKind]::Utc)
$unreadable = Test-AgentHeartbeatOnCore `
    -FetchDevices { throw "The remote server returned an error: (503) Service Unavailable." } `
    -DeviceId $script:DeviceId -ExpectedVersion $script:Version `
    -TimeoutSeconds 30 -PollSeconds 3 `
    -Sleep { param($s) $script:Clock2 = $script:Clock2.AddSeconds($s) } -Now { $script:Clock2 }
Assert-True (-not $unreadable.Ok) "an unreadable Cloud Core still fails"
Assert-True (-not [bool]$unreadable.VerifierFault) "and is NOT mislabelled a verifier fault"
Assert-True ([int]$unreadable.Attempts -gt 1) "and is still retried across the timeout ($($unreadable.Attempts) attempts)"

# --------------------------------------------------- the installer must read the flag
# Both halves of a contract can be green while they drift. This one reads the OTHER file.
$installer = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\install-device-service.ps1") -Raw
Assert-True ($installer -match '\$heartbeat\.VerifierFault') `
    "install-device-service.ps1 branches on VerifierFault rather than printing one sentence for both causes"
Assert-True ($installer -match "Cloud Core was never asked") `
    "and says so plainly when its own verifier is what failed"

Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "passed $script:Passes, failed $script:Failures"
exit $script:Failures
