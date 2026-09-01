<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for scripts/cloud/provision.ps1 and for the
    parameter-collision class that made a real provisioning run lie about what it did.

.DESCRIPTION
    The incident: provision.ps1 declared `[switch]$Apply` and later wrote

        $apply = Invoke-NativeProcess ... "apply" ...

    PowerShell variable names are case-INSENSITIVE, so that is not a new variable — it is
    an assignment to the switch parameter, and PowerShell fails converting the result
    object to a SwitchParameter. Crucially the assignment happens AFTER the child returns,
    so `tofu apply` had already created four billable resources when the script died. The
    run looked like "stopped before apply" and had in fact finished applying.

    These tests therefore prove three separate things:

      1. the language behaviour itself, so the class is understood and cannot silently
         come back (a real script with a real switch parameter, run on the real 5.1 engine);
      2. that no script in this repository has the collision, by parsing each one;
      3. that provision.ps1's plan/apply decisions behave, driven end to end through a FAKE
         tofu — plan-only never applies, -Apply applies exactly the plan it just showed,
         the saved plan is deleted afterwards because it embeds the auth key, and the
         expectation/destroy guards refuse before anything runs.

    No network, no credentials, no real infrastructure.

    Run: powershell -NoProfile -File scripts\tests\provision.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$provisionScript = Join-Path $repoRoot "scripts\cloud\provision.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-provision-tests-$([guid]::NewGuid().ToString('N'))"

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

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function New-FakeTofu {
    <#
    .SYNOPSIS
        A stand-in `tofu` that records every invocation and answers the four verbs
        provision.ps1 uses. A .cmd shim because that is what Invoke-NativeProcess can
        launch directly; the behaviour lives in PowerShell next to it.
    #>
    param([string]$Directory, [int]$Create = 4, [int]$Update = 0, [int]$Delete = 0)

    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $logPath = Join-Path $Directory "invocations.log"
    $body = Join-Path $Directory "fake-tofu.ps1"
    $shim = Join-Path $Directory "tofu.cmd"

    $changes = New-Object System.Collections.ArrayList
    for ($i = 0; $i -lt $Create; $i++) { [void]$changes.Add(@{ address = "hcloud_thing.create$i"; change = @{ actions = @("create") } }) }
    for ($i = 0; $i -lt $Update; $i++) { [void]$changes.Add(@{ address = "hcloud_thing.update$i"; change = @{ actions = @("update") } }) }
    for ($i = 0; $i -lt $Delete; $i++) { [void]$changes.Add(@{ address = "hcloud_thing.delete$i"; change = @{ actions = @("delete") } }) }
    $planJson = @{ resource_changes = $changes } | ConvertTo-Json -Depth 8 -Compress
    Set-Content -LiteralPath (Join-Path $Directory "plan.json") -Value $planJson -Encoding ASCII

    $script = @'
$logPath = Join-Path $PSScriptRoot "invocations.log"
Add-Content -LiteralPath $logPath -Value ($args -join " ")
switch ($args[0]) {
    "version" { Write-Output "OpenTofu v1.10.6"; exit 0 }
    "init"    { Write-Output "OpenTofu has been successfully initialized!"; exit 0 }
    "plan"    {
        $outIndex = [array]::IndexOf($args, "-out")
        if ($outIndex -ge 0) { Set-Content -LiteralPath $args[$outIndex + 1] -Value "fake saved plan" -Encoding ASCII }
        Write-Output "Plan: 4 to add, 0 to change, 0 to destroy."
        exit 0
    }
    "show"    { Get-Content -LiteralPath (Join-Path $PSScriptRoot "plan.json") -Raw; exit 0 }
    "apply"   { Write-Output "Apply complete! Resources: 4 added, 0 changed, 0 destroyed."; exit 0 }
    "output"  {
        # The real output set, so the post-apply tailnet confirmation is exercised rather
        # than skipped. TAILNET_HOSTNAME lets a test choose a name this machine will never
        # see on its tailnet, which is how the "never joined" failure is provable offline.
        $hostname = if ($env:FAKE_TOFU_TAILNET_HOSTNAME) { $env:FAKE_TOFU_TAILNET_HOSTNAME } else { "pagentos-core" }
        Write-Output ('{"server_id":{"value":"164238173","sensitive":false,"type":"string"},' +
            '"public_ipv4":{"value":"203.0.113.10","sensitive":false,"type":"string"},' +
            '"tailscale_hostname":{"value":"' + $hostname + '","sensitive":false,"type":"string"}}')
        exit 0
    }
    default   { Write-Error "fake tofu: unexpected verb $($args[0])"; exit 9 }
}
'@
    Set-Content -LiteralPath $body -Value $script -Encoding UTF8
    # Absolute path to the engine: the child inherits a minimal environment and `powershell`
    # is not necessarily resolvable from PATH there.
    $shimText = "@echo off`r`n`"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -File `"%~dp0fake-tofu.ps1`" %*`r`n"
    [System.IO.File]::WriteAllText($shim, $shimText, (New-Object System.Text.ASCIIEncoding))

    return [pscustomobject]@{ Shim = $shim; Log = $logPath; Directory = $Directory }
}

function Invoke-Provision {
    <#
    .SYNOPSIS
        Run the real provision.ps1 in a child 5.1 process against a fake tofu.

    .DESCRIPTION
        The fake reports a tailnet hostname that this machine will never see, and the join
        timeout is a few seconds, so the post-apply confirmation resolves the same way on
        every host: not joined. That keeps these tests offline and deterministic — pointing
        them at the REAL `pagentos-core` would make them pass or fail according to whether
        a Hetzner host happens to be up.
    #>
    param([object]$Fake, [string[]]$ExtraArguments = @(), [string]$TailnetHostname = "pagentos-core-absent-by-design")

    $sshKey = Join-Path $Fake.Directory "id_test.pub"
    if (-not (Test-Path -LiteralPath $sshKey)) {
        Set-Content -LiteralPath $sshKey -Value "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEYNOTREAL test@pagentos" -Encoding ASCII
    }

    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $provisionScript,
        "-TofuPath", $Fake.Shim, "-SshPublicKeyPath", $sshKey,
        "-TailnetJoinTimeoutSeconds", "5") + $ExtraArguments

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $powershell
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments $arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    # Credentials the preflight demands. Deliberately obvious non-secrets.
    $psi.EnvironmentVariables["TF_VAR_hcloud_token"] = "fake-token-for-tests-only"
    $psi.EnvironmentVariables["TF_VAR_tailscale_auth_key"] = "fake-authkey-for-tests-only"
    $psi.EnvironmentVariables["FAKE_TOFU_TAILNET_HOSTNAME"] = $TailnetHostname

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdOut = $process.StandardOutput.ReadToEndAsync()
    $stdErr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(180000)) { try { $process.Kill() } catch { }; throw "provision.ps1 did not exit" }
    $process.WaitForExit()

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        StdOut   = $stdOut.Result
        StdErr   = $stdErr.Result
        Log      = @(if (Test-Path -LiteralPath $Fake.Log) { Get-Content -LiteralPath $Fake.Log } else { @() })
    }
}

New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")

try {
    Write-Host ""
    Write-Host "the engine under test"

    Test-Case "this really is Windows PowerShell 5.1 with StrictMode" {
        Assert-Equal -Expected 5 -Actual $PSVersionTable.PSVersion.Major -Because "these are 5.1 compatibility tests"
        Assert-Equal -Expected "Desktop" -Actual $PSVersionTable.PSEdition -Because "must be Windows PowerShell"
        $threw = $false
        try { $nothing = $null; $null = $nothing.Count } catch { $threw = $true }
        Assert-True -Condition $threw -Because "StrictMode is not in force; the assertions below would be vacuous"
    }

    Write-Host ""
    Write-Host "the parameter-collision class (the incident itself)"

    Test-Case "assigning a PSCustomObject over a [switch] parameter really does throw here" {
        $child = Join-Path $script:Sandbox "collide.ps1"
        # ErrorActionPreference = Stop because that is what provision.ps1 sets, and it is
        # what turns this from a written-and-ignored error into a script that dies here.
        Set-Content -LiteralPath $child -Encoding UTF8 -Value @'
param([switch]$Apply)
$ErrorActionPreference = "Stop"
$apply = [pscustomobject]@{ ExitCode = 0 }
Write-Output "reached-the-end"
'@
        $result = Invoke-NativeProcess -FilePath $powershell `
            -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $child, "-Apply") -TimeoutSeconds 60
        # Anchored on the exception TYPE, not the message: this machine reports PowerShell
        # errors in Turkish, so matching English message text would make the test pass or
        # fail based on the host's display language rather than on the behaviour.
        Assert-True -Condition ($result.StdErr -match "ArgumentTransformationMetadataException" -or $result.StdErr -match "MetadataError") `
            -Because "the case-insensitive collision must still be reproducible on this engine (stderr: $($result.StdErr))"
        Assert-True -Condition ($result.StdOut -notmatch "reached-the-end") `
            -Because "the script must die at the assignment, which is why the incident looked like 'it stopped earlier'"
    }

    Test-Case "the shouldApply pattern is immune to the same assignment" {
        $child = Join-Path $script:Sandbox "safe.ps1"
        Set-Content -LiteralPath $child -Encoding UTF8 -Value @'
param([switch]$Apply)
$shouldApply = [bool]$Apply
$applyResult = [pscustomobject]@{ ExitCode = 0 }
Write-Output "shouldApply=$shouldApply exit=$($applyResult.ExitCode) reached-the-end"
'@
        $result = Invoke-NativeProcess -FilePath $powershell `
            -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $child, "-Apply") -TimeoutSeconds 60
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because "the safe pattern must run clean (stderr: $($result.StdErr))"
        Assert-True -Condition ($result.StdOut -match "shouldApply=True") -Because "intent must survive as a boolean"
        Assert-True -Condition ($result.StdOut -match "reached-the-end") -Because "and the script must finish"
    }

    Test-Case "no script assigns over its own parameter, in any casing" {
        # The lint for the class. A switch parameter must never be assigned at all; any
        # parameter assigned under DIFFERENT casing is the invisible form of the bug.
        $offenders = New-Object System.Collections.ArrayList
        foreach ($file in @(Get-ChildItem -Path (Join-Path $repoRoot "scripts") -Filter "*.ps1" -Recurse -File)) {
            $tokens = $null
            $errors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
            if (@($errors).Count -gt 0) { continue }   # script-syntax.tests.ps1 owns parse errors
            if ($null -eq $ast.ParamBlock) { continue }

            $parameters = @{}
            foreach ($parameter in @($ast.ParamBlock.Parameters)) {
                $parameters[$parameter.Name.VariablePath.UserPath] = [bool]($parameter.StaticType -eq [switch])
            }
            if ($parameters.Count -eq 0) { continue }

            $assignments = $ast.FindAll({
                    param($node)
                    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                    $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]
                }, $true)

            foreach ($assignment in @($assignments)) {
                $assignedName = $assignment.Left.VariablePath.UserPath
                foreach ($declared in $parameters.Keys) {
                    if ($assignedName -ne $declared -and $assignedName -ieq $declared) {
                        [void]$offenders.Add("$($file.Name):$($assignment.Extent.StartLineNumber): `$$assignedName collides with parameter `$$declared (case-insensitive)")
                    }
                    elseif ($assignedName -ieq $declared -and $parameters[$declared]) {
                        [void]$offenders.Add("$($file.Name):$($assignment.Extent.StartLineNumber): assigns to the [switch] parameter `$$declared")
                    }
                }
            }
        }
        Assert-Equal -Expected 0 -Actual $offenders.Count -Because "parameter-name collisions found:`n          $($offenders -join "`n          ")"
    }

    Write-Host ""
    Write-Host "provision.ps1 driven through a fake tofu"

    Test-Case "plan-only never applies and never saves a plan" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "plan-only")
        $result = Invoke-Provision -Fake $fake
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because "a plan-only run must succeed (stderr: $($result.StdErr))"
        Assert-True -Condition ($result.StdOut -match "PLAN ONLY") -Because "it must say so"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 0) `
            -Because "apply must NEVER be invoked without -Apply (log: $($result.Log -join ' | '))"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "plan*-out*" }).Count -eq 0) `
            -Because "and no saved plan should be written, because a saved plan holds the auth key"
    }

    Test-Case "-Apply saves a plan, shows it, and applies THAT plan" {
        # Asserts the tofu invocation SEQUENCE. The run's exit code belongs to the tailnet
        # confirmation, which has its own test below; conflating them would make this test
        # fail for a reason that has nothing to do with the apply mechanics.
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "apply")
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")

        $planLine = @($result.Log | Where-Object { $_ -like "plan *" })[0]
        $showLine = @($result.Log | Where-Object { $_ -like "show *" })[0]
        $applyLine = @($result.Log | Where-Object { $_ -like "apply *" })[0]
        Assert-True -Condition ($null -ne $planLine -and $planLine -match "-out") -Because "plan must be saved with -out"
        Assert-True -Condition ($null -ne $showLine) -Because "the saved plan must be read back before applying"
        Assert-True -Condition ($null -ne $applyLine) -Because "apply must run"

        # Saved-plan apply: the file applied is the file planned and the file shown - not a
        # fresh, unreviewed plan computed at apply time.
        $planFile = ($planLine -split "-out ")[1].Trim()
        Assert-True -Condition ($showLine -like "*$planFile*") -Because "show must read the SAVED plan ($planFile)"
        Assert-True -Condition ($applyLine -like "*$planFile*") -Because "apply must consume the same saved plan ($planFile)"
        Assert-True -Condition (-not (Test-Path -LiteralPath $planFile)) `
            -Because "the saved plan embeds the Tailscale auth key and must not outlive the run"
    }

    Test-Case "the change summary is reported before applying" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "summary")
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")
        Assert-True -Condition ($result.StdOut -match "4 to add, 0 to change, 0 to destroy") `
            -Because "the exact change must be shown from the saved plan before it executes"
    }

    Test-Case "a mismatched expectation refuses BEFORE applying" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "expect") -Create 4
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply", "-ExpectAdd", "3")
        Assert-True -Condition ($result.ExitCode -ne 0) -Because "an unexpected plan must stop the run"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 0) `
            -Because "nothing may be applied when the plan is not what was reviewed (log: $($result.Log -join ' | '))"
    }

    Test-Case "a matching expectation proceeds" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "expect-ok") -Create 4
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply", "-ExpectAdd", "4", "-ExpectChange", "0", "-ExpectDestroy", "0")
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 1) `
            -Because "4/0/0 was expected and is what the plan does, so it applies exactly once (log: $($result.Log -join ' | '))"
    }

    Test-Case "provisioning FAILS when the host never joins the tailnet" {
        # The defect this exists for: the first real run created four billable resources,
        # reported success, and left a host with no tailnet and - by design - no public
        # SSH. `tofu apply` succeeding is not the same as the host being reachable, and the
        # command must not claim otherwise.
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "no-tailnet")
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")
        $combined = $result.StdOut + $result.StdErr

        $tailscaleInstalled = Test-Path -LiteralPath (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe")
        if ($tailscaleInstalled) {
            Assert-True -Condition ($result.ExitCode -ne 0) -Because "a host that never joined the tailnet is a failed provision"
            Assert-True -Condition ($combined -match "never joined the tailnet") -Because "and it must say so plainly"
            Assert-True -Condition ($combined -match "breakglass-ssh") -Because "and point at the recovery path that does not need public SSH"
            Assert-True -Condition ($combined -match "do NOT destroy") -Because "and say the infrastructure is intact, so nobody re-creates a working host"
        }
        else {
            # No Tailscale here (a CI runner): it cannot confirm, and says so rather than
            # inventing a verdict in either direction.
            Assert-True -Condition ($combined -match "cannot be confirmed from here") `
                -Because "without Tailscale the run must report that it cannot confirm (out: $combined)"
        }
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 1) `
            -Because "the apply itself did happen - that is exactly why silence would be dangerous"
    }

    Test-Case "a plan that would DESTROY refuses unless explicitly allowed" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "destroy") -Create 1 -Delete 2
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")
        Assert-True -Condition ($result.ExitCode -ne 0) -Because "destroying real infrastructure is never implicit"
        # The refusal must be the DESTROY guard specifically — not an early failure that
        # happens to exit non-zero, which would pass this test while proving nothing.
        Assert-True -Condition (($result.StdErr + $result.StdOut) -match "would DESTROY 2 resource") `
            -Because "the refusal must name the destroy count (stderr: $($result.StdErr))"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "show *" }).Count -eq 1) `
            -Because "it must have got as far as reading the saved plan, or the guard was not what stopped it"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 0) `
            -Because "nothing may be applied (log: $($result.Log -join ' | '))"
    }

    Test-Case "a no-op plan stops without applying" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "noop") -Create 0
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because "nothing to do is success (stderr: $($result.StdErr))"
        Assert-True -Condition (@($result.Log | Where-Object { $_ -like "apply*" }).Count -eq 0) `
            -Because "applying an empty plan is pointless churn against real infrastructure"
    }

    Test-Case "no credential value is ever echoed" {
        $fake = New-FakeTofu -Directory (Join-Path $script:Sandbox "secrets")
        $result = Invoke-Provision -Fake $fake -ExtraArguments @("-Apply")
        $combined = $result.StdOut + $result.StdErr
        Assert-True -Condition ($combined -notmatch "fake-token-for-tests-only") -Because "the Hetzner token must never be printed"
        Assert-True -Condition ($combined -notmatch "fake-authkey-for-tests-only") -Because "the Tailscale auth key must never be printed"
        Assert-True -Condition ($combined -match "TF_VAR_hcloud_token\s+: SET") -Because "presence is reported instead"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
