<#
.SYNOPSIS
    Regression tests for how the installer invokes native tools, and for its idempotency.

.DESCRIPTION
    These exist because a real install on the owner's machine failed at exactly one step:
    publishing succeeded, ACL hardening succeeded, and `sc create` returned 1639,
    ERROR_INVALID_COMMAND_LINE. The cause was not sc.exe and not the machine — Windows
    PowerShell 5.1 does not escape an argument that itself contains double quotes, so

        intended : binPath=   |   "C:\Program Files\...\PagentOS.DeviceService.exe" run
        emitted  : binPath= ""C:\Program Files\...\PagentOS.DeviceService.exe" run"
        received : binPath=   |   C:\Program   |   Files\...\PagentOS.DeviceService.exe run

    and sc.exe found a stray token where it expected `obj=`.

    Two things follow about how these tests are written:

    - the argv assertions round-trip through a REAL child process, so they measure what a
      native tool actually receives rather than what a string looks like. sc.exe parses its
      command line with the same CommandLineToArgvW rules as the child used here;
    - nothing here elevates, registers a service, or touches C:\Program Files. A test that
      needed to weaken an ACL or delete published files to pass would be testing something
      other than the installer.

    Run: powershell -NoProfile -File scripts\tests\installer-invocation.tests.ps1
    Exit code is the number of failed assertions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\ServiceInstall.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        $script:Passes++
        Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) {
        throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>"
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

# The exact path from the failing install, spaces and all.
$ServiceExe = "C:\Program Files\PagentOS\agent\service\PagentOS.DeviceService.exe"

Write-Host ""
Write-Host "Native argument serialization"

Test-Case "a plain token is passed through unquoted" {
    Assert-Equal -Expected "binPath=" -Actual (ConvertTo-NativeArgument -Argument "binPath=") `
        -Because "sc.exe's key tokens must not gain quotes"
}

Test-Case "a path with spaces is wrapped in quotes" {
    Assert-Equal -Expected "`"$ServiceExe`"" -Actual (ConvertTo-NativeArgument -Argument $ServiceExe) `
        -Because "an unquoted Program Files path splits into two arguments"
}

Test-Case "inner quotes are backslash-escaped, which is the whole bug" {
    $value = New-ScBinaryPathValue -ExecutablePath $ServiceExe -ServiceArguments @("run")
    $serialized = ConvertTo-NativeArgument -Argument $value
    Assert-Equal -Expected "`"\`"$ServiceExe\`" run`"" -Actual $serialized `
        -Because "unescaped inner quotes are what made sc.exe see a stray token and return 1639"
}

Test-Case "trailing backslashes are doubled before the closing quote" {
    # C:\dir\ must not let its backslash escape the quote that ends the argument.
    Assert-Equal -Expected '"C:\a b\\"' -Actual (ConvertTo-NativeArgument -Argument 'C:\a b\') `
        -Because "a trailing backslash would otherwise escape the closing quote"
}

Test-Case "an empty argument survives as an empty quoted token" {
    Assert-Equal -Expected '""' -Actual (ConvertTo-NativeArgument -Argument "") -Because "empty arguments must still occupy a slot"
}

Write-Host ""
Write-Host "sc.exe create argument shape"

Test-Case "create uses key= and value as SEPARATE tokens, in sc.exe's documented order" {
    $arguments = New-ScCreateArgumentList `
        -ServiceName "PagentOSDeviceAgent" `
        -BinaryPathValue (New-ScBinaryPathValue -ExecutablePath $ServiceExe -ServiceArguments @("run")) `
        -Account "LocalSystem" -StartMode "auto" -DisplayName "Personal Agent OS Device Agent"

    $expected = @(
        "create",
        "PagentOSDeviceAgent",
        "binPath=",
        "`"$ServiceExe`" run",
        "obj=",
        "LocalSystem",
        "start=",
        "auto",
        "DisplayName=",
        "Personal Agent OS Device Agent"
    )

    Assert-Equal -Expected $expected.Count -Actual $arguments.Count -Because "token count must match sc.exe's expected shape"
    for ($i = 0; $i -lt $expected.Count; $i++) {
        Assert-Equal -Expected $expected[$i] -Actual $arguments[$i] -Because "token $i differs"
    }
}

Test-Case "the serialized create command line is exactly what sc.exe expects" {
    $line = ConvertTo-NativeArgumentLine -Arguments (New-ScCreateArgumentList `
        -ServiceName "PagentOSDeviceAgent" `
        -BinaryPathValue (New-ScBinaryPathValue -ExecutablePath $ServiceExe -ServiceArguments @("run")) `
        -DisplayName "Personal Agent OS Device Agent")

    $expected = 'create PagentOSDeviceAgent binPath= "\"C:\Program Files\PagentOS\agent\service\PagentOS.DeviceService.exe\" run" obj= LocalSystem start= auto DisplayName= "Personal Agent OS Device Agent"'
    Assert-Equal -Expected $expected -Actual $line -Because "this exact string is what failed with 1639 when PowerShell built it"
}

Test-Case "config repoints an existing service with the same shape" {
    $arguments = New-ScConfigArgumentList -ServiceName "PagentOSDeviceAgent" `
        -BinaryPathValue (New-ScBinaryPathValue -ExecutablePath $ServiceExe -ServiceArguments @("run"))
    Assert-Equal -Expected "config" -Actual $arguments[0] -Because "config, not create, for an existing service"
    Assert-Equal -Expected "binPath=" -Actual $arguments[2] -Because "same key= value shape as create"
}

Test-Case "a service with no arguments gets a bare path, not a quoted-inside value" {
    Assert-Equal -Expected $ServiceExe -Actual (New-ScBinaryPathValue -ExecutablePath $ServiceExe) `
        -Because "with no service arguments there is nothing to disambiguate"
}

Write-Host ""
Write-Host "argv round-trip through a real child process"

Test-Case "a native child receives exactly the tokens intended (path with spaces)" {
    # This is the assertion that would have caught the original failure. The child prints the
    # argv it received; sc.exe parses its command line the same way.
    $echoScript = Join-Path $env:TEMP "pagentos-argv-echo-$([guid]::NewGuid().ToString('N')).ps1"
    Set-Content -LiteralPath $echoScript -Value 'for ($i = 0; $i -lt $args.Count; $i++) { Write-Output ("ARG[$i]=" + $args[$i]) }' -Encoding ASCII
    try {
        $tokens = New-ScCreateArgumentList `
            -ServiceName "PagentOSDeviceAgent" `
            -BinaryPathValue (New-ScBinaryPathValue -ExecutablePath $ServiceExe -ServiceArguments @("run")) `
            -DisplayName "Personal Agent OS Device Agent"

        $result = Invoke-NativeProcess `
            -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") `
            -Arguments (@("-NoProfile", "-File", $echoScript) + $tokens)

        Assert-True -Condition $result.Success -Because "the echo child failed: $($result.StdErr)"

        $received = @($result.StdOut -split "`r?`n" | Where-Object { $_ -match '^ARG\[\d+\]=' } |
            ForEach-Object { $_ -replace '^ARG\[\d+\]=', '' })

        Assert-Equal -Expected $tokens.Count -Actual $received.Count `
            -Because "the child received a different number of arguments than were sent - this is exactly the 1639 failure mode"
        for ($i = 0; $i -lt $tokens.Count; $i++) {
            Assert-Equal -Expected $tokens[$i] -Actual $received[$i] -Because "token $i was altered in transit"
        }

        # And specifically: the binPath value arrived whole, not split at the space in
        # "Program Files".
        Assert-Equal -Expected "`"$ServiceExe`" run" -Actual $received[3] `
            -Because "the binPath value must survive as ONE argument"
    }
    finally {
        Remove-Item -LiteralPath $echoScript -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "exit code, stdout and stderr are all captured" {
    $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
    $result = Invoke-NativeProcess -FilePath $cmd -Arguments @("/c", "echo out&& echo err 1>&2&& exit /b 7") -SuccessExitCodes @(0)

    Assert-Equal -Expected 7 -Actual $result.ExitCode -Because "the exact exit code must reach the caller"
    Assert-True -Condition (-not $result.Success) -Because "7 is not in the success list"
    Assert-True -Condition ($result.StdOut -match "out") -Because "stdout must be captured"
    Assert-True -Condition ($result.StdErr -match "err") -Because "stderr must be captured"
}

Test-Case "the child runs in the requested working directory, not the shell's" {
    # Push-Location changes PowerShell's location, NOT a .NET process's working directory.
    # Without an explicit WorkingDirectory the child inherited wherever this shell started,
    # which is how `uv run alembic` came back "program not found" from a script that had
    # Push-Location'd into services\api first.
    $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
    $target = Join-Path $env:TEMP "pagentos-cwd-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    try {
        $explicit = Invoke-NativeProcess -FilePath $cmd -Arguments @("/c", "cd") -WorkingDirectory $target
        Assert-True -Condition ($explicit.StdOut.Trim() -ieq $target) `
            -Because "the child should have run in $target but reported <$($explicit.StdOut.Trim())>"

        # And with no parameter it follows PowerShell's own location, so Push-Location reads
        # the way it looks.
        Push-Location $target
        try {
            $implicit = Invoke-NativeProcess -FilePath $cmd -Arguments @("/c", "cd")
            Assert-True -Condition ($implicit.StdOut.Trim() -ieq $target) `
                -Because "with no -WorkingDirectory the child should follow Push-Location, got <$($implicit.StdOut.Trim())>"
        }
        finally { Pop-Location }
    }
    finally {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a missing working directory is refused before the process starts" {
    $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
    try {
        Invoke-NativeProcess -FilePath $cmd -Arguments @("/c", "cd") -WorkingDirectory (Join-Path $env:TEMP "no-such-dir-$([guid]::NewGuid())") | Out-Null
        throw "should have refused"
    }
    catch {
        Assert-True -Condition ($_.Exception.Message -match "working directory does not exist") `
            -Because "the error should name the problem, not surface as a launch failure"
    }
}

Test-Case "a failure message names the tool, the exit code and its meaning" {
    $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
    $result = Invoke-NativeProcess -FilePath $cmd -Arguments @("/c", "exit /b 1639")
    try {
        Assert-NativeSuccess -Result $result -Activity "sc create PagentOSDeviceAgent"
        throw "Assert-NativeSuccess should have thrown"
    }
    catch {
        $message = $_.Exception.Message
        Assert-True -Condition ($message -match "1639") -Because "the exit code must be in the message"
        Assert-True -Condition ($message -match "INVALID_COMMAND_LINE") -Because "the operator should not have to look up 1639"
        Assert-True -Condition ($message -match "command:") -Because "the exact command line must be reported"
    }
}

Write-Host ""
Write-Host "installer idempotency"

Test-Case "no registered service: create" {
    $plan = Get-ServiceInstallPlan -Current $null -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "Create" -Actual $plan.Action -Because "a first install, or a rerun after registration failed, must create"
    Assert-Equal -Expected $false -Actual $plan.StopFirst -Because "there is nothing to stop"
}

Test-Case "already registered exactly as intended: change nothing" {
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"; PathName = "`"$ServiceExe`" run"
        StartName = "LocalSystem"; StartMode = "Auto"; State = "Running"; ProcessId = 42
    }
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "AlreadyCorrect" -Actual $plan.Action `
        -Because "rerunning the installer must not restart a healthy agent for no reason"
    Assert-Equal -Expected $false -Actual $plan.StopFirst -Because "a correct service is not stopped"
}

Test-Case "registered with a stale image path: reconfigure, stopping it first if running" {
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"; PathName = "`"C:\Old\PagentOS.DeviceService.exe`" run"
        StartName = "LocalSystem"; StartMode = "Auto"; State = "Running"; ProcessId = 42
    }
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "Reconfigure" -Actual $plan.Action -Because "the image path moved"
    Assert-Equal -Expected $true -Actual $plan.StopFirst -Because "a running service must stop before it is repointed"
    Assert-True -Condition ($plan.Reason -match "image path") -Because "the operator should be told what differs"
}

Test-Case "registered under the wrong account: reconfigure" {
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"; PathName = "`"$ServiceExe`" run"
        StartName = "NT AUTHORITY\NetworkService"; StartMode = "Auto"; State = "Stopped"; ProcessId = 0
    }
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "Reconfigure" -Actual $plan.Action -Because "the service must run as LocalSystem"
    Assert-Equal -Expected $false -Actual $plan.StopFirst -Because "it is already stopped"
    Assert-True -Condition ($plan.Reason -match "account") -Because "the account difference should be named"
}

Test-Case "a case- or whitespace-different path is the same path, not a stale one" {
    # Windows paths are case-insensitive and the SCM may hand the value back with different
    # surrounding whitespace. Treating that as a difference would repoint a correct service
    # on every run and fail the post-install verification of an install that worked.
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"
        PathName = "`"c:\program files\PagentOS\agent\service\pagentos.deviceservice.exe`" run "
        StartName = "localsystem"; StartMode = "Auto"; State = "Running"; ProcessId = 42
    }
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "AlreadyCorrect" -Actual $plan.Action -Because "case and outer whitespace are not real differences"
}

Test-Case "a genuinely different path is still detected" {
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"
        PathName = "`"C:\Program Files\PagentOS\agent\service\PagentOS.DeviceService.exe`""
        StartName = "LocalSystem"; StartMode = "Auto"; State = "Stopped"; ProcessId = 0
    }
    # Same executable, but the 'run' verb is missing: the service would start and do nothing.
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "Reconfigure" -Actual $plan.Action -Because "a missing service argument is a real difference"
}

Test-Case "registered as manual start: reconfigure so it survives a reboot" {
    $current = [pscustomobject]@{
        Name = "PagentOSDeviceAgent"; PathName = "`"$ServiceExe`" run"
        StartName = "LocalSystem"; StartMode = "Manual"; State = "Stopped"; ProcessId = 0
    }
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName "`"$ServiceExe`" run"
    Assert-Equal -Expected "Reconfigure" -Actual $plan.Action -Because "an agent that does not start at boot is not installed"
}

Write-Host ""
Write-Host "system tool resolution"

Test-Case "tools resolve to absolute System32 paths, never to a PowerShell alias" {
    $sc = Get-SystemTool -Name "sc.exe"
    Assert-True -Condition ($sc -like "*\System32\sc.exe") -Because "bare 'sc' is an alias for Set-Content"
    Assert-True -Condition (Test-Path -LiteralPath $sc) -Because "the resolved tool must exist"
}

Test-Case "a missing tool fails with a message naming the path" {
    try {
        Get-SystemTool -Name "definitely-not-a-real-tool.exe" | Out-Null
        throw "should have thrown"
    }
    catch {
        Assert-True -Condition ($_.Exception.Message -match "definitely-not-a-real-tool.exe") -Because "the error must name what is missing"
    }
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
