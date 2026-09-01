<#
.SYNOPSIS
    The machine-readable child-process protocol, under Windows PowerShell 5.1.

.DESCRIPTION
    A real owner-credential rotation committed and then lost its replacement. The wrapper ran
    `python -m app.identity.recover --rotate --json`, the child exited 0, and stdout carried
    TWO JSON documents — an `identity_owner_credential_minted` log line (this application
    logs to stdout) followed by the payload. `ConvertFrom-Json` refused it, the wrapper threw,
    and the only copy of the new credential died with the child process. The owner identity
    had already been rotated.

    Two failures in one: a protocol that allowed logging onto stdout, and a wrapper that
    parsed leniently and would have printed the raw output while diagnosing.

    These tests drive a fake child through every shape that mattered, on the engine that
    matters. Each case runs a real process, not a string fixture, because the bug lived in
    the boundary between processes.

    Run: powershell -NoProfile -File scripts\tests\machine-readable.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\IdentityStatus.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-mr-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

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

function New-FakeChild {
    <#  A real child process emitting a chosen stdout/stderr/exit-code shape.  #>
    param([string]$Name, [string]$Body)
    $path = Join-Path $script:Sandbox "$Name.ps1"
    Set-Content -LiteralPath $path -Value $Body -Encoding ASCII
    return $path
}

# The secret a rotation would carry. If any assertion below ever quotes it, the test itself
# would be the leak - so the tests check that error text does NOT contain it.
$secret = "pagentos_ok_TESTSECRET_do_not_leak_0123456789"

try {
    Write-Host ""
    Write-Host "the engine under test"

    Test-Case "running on Windows PowerShell 5.1" {
        Assert-Equal -Expected 5 -Actual $PSVersionTable.PSVersion.Major `
            -Because "the wrapper runs on 5.1; parsing on 7 would prove nothing about it"
    }

    Write-Host ""
    Write-Host "the shape that actually happened"

    Test-Case "stderr logging plus a single JSON document on stdout parses" {
        # The fixed protocol: logs on stderr, payload alone on stdout.
        $child = New-FakeChild -Name "clean" -Body @"
[Console]::Error.WriteLine('{"event":"identity_owner_credential_minted","level":"info"}')
[Console]::Error.WriteLine('some other diagnostic')
Write-Output '{"action":"rotate","owner_credential":"$secret","sessions_revoked":3}'
"@
        $payload = Invoke-MachineReadableProcess -FilePath $powershell `
            -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate"

        Assert-Equal -Expected "rotate" -Actual $payload.action -Because "the payload must round-trip"
        Assert-Equal -Expected $secret -Actual $payload.owner_credential -Because "the credential must survive intact"
        Assert-Equal -Expected 3 -Actual $payload.sessions_revoked -Because "and so must the rest"
    }

    Test-Case "a log line BEFORE the payload on stdout is refused, not salvaged" {
        # Exactly the production failure: two JSON documents on stdout.
        $child = New-FakeChild -Name "prelog" -Body @"
Write-Output '{"event":"identity_owner_credential_minted","level":"info"}'
Write-Output '{"action":"rotate","owner_credential":"$secret"}'
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "contaminated stdout must not parse"
        }
        catch {
            $message = $_.Exception.Message
            Assert-True -Condition ($message -notmatch [regex]::Escape($secret)) `
                -Because "the error message leaked the credential"
            Assert-True -Condition ($message -match "withheld|single|JSON") `
                -Because "the error should say the output was not one JSON document: $message"
        }
    }

    Test-Case "a log line AFTER the payload is refused too" {
        $child = New-FakeChild -Name "postlog" -Body @"
Write-Output '{"action":"rotate","owner_credential":"$secret"}'
Write-Output '{"event":"sessions_revoked","level":"info"}'
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "trailing contamination must not parse"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -notmatch [regex]::Escape($secret)) `
                -Because "the error message leaked the credential"
        }
    }

    Test-Case "duplicate JSON documents are refused rather than first-one-wins" {
        $child = New-FakeChild -Name "duplicate" -Body @"
Write-Output '{"action":"rotate","owner_credential":"$secret"}'
Write-Output '{"action":"rotate","owner_credential":"second-value"}'
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "two payloads must not parse"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -notmatch [regex]::Escape($secret)) -Because "leaked the credential"
        }
    }

    Write-Host ""
    Write-Host "malformed, empty and failing children"

    Test-Case "plain text on stdout is refused without echoing it" {
        $child = New-FakeChild -Name "plaintext" -Body "Write-Output 'OWNER CREDENTIAL: $secret'"
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "non-JSON must not parse"
        }
        catch {
            $message = $_.Exception.Message
            Assert-True -Condition ($message -notmatch [regex]::Escape($secret)) `
                -Because "THE important one: a human-readable credential must never reach an error message"
            Assert-True -Condition ($message -match "other than JSON") -Because "and the reason should be plain: $message"
        }
    }

    Test-Case "truncated JSON is refused" {
        $child = New-FakeChild -Name "truncated" -Body "Write-Output '{`"action`":`"rotate`",`"owner_credential`":'"
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "truncated JSON must not parse"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "single valid JSON|other than JSON") `
                -Because "expected a parse refusal, got: $($_.Exception.Message)"
        }
    }

    Test-Case "empty stdout is refused" {
        $child = New-FakeChild -Name "empty" -Body "[Console]::Error.WriteLine('only diagnostics here')"
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "empty stdout must not parse"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "no output") -Because "expected an empty-output refusal: $($_.Exception.Message)"
        }
    }

    Test-Case "a nonzero exit code is reported BEFORE stdout is parsed" {
        # Even when stdout holds a perfectly good payload: the exit code is authoritative.
        $child = New-FakeChild -Name "failing" -Body @"
Write-Output '{"action":"rotate","owner_credential":"$secret"}'
exit 4
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "a failing child must not be parsed as success"
        }
        catch {
            $message = $_.Exception.Message
            Assert-True -Condition ($message -match "exit code 4") -Because "the exit code must be named: $message"
            Assert-True -Condition ($message -notmatch [regex]::Escape($secret)) -Because "and stdout must not be quoted"
        }
    }

    Test-Case "an interrupted child - output then a hard exit - is refused" {
        # An interrupted rotation: partial output, no clean payload.
        $child = New-FakeChild -Name "interrupted" -Body @"
Write-Output '{"action":"rotate",'
[Environment]::Exit(1)
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake rotate" | Out-Null
            throw "an interrupted child must not parse"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "exit code 1") -Because "the exit code is checked first: $($_.Exception.Message)"
        }
    }

    Test-Case "stderr is quotable only when the caller says the output is not sensitive" {
        $child = New-FakeChild -Name "stderr-detail" -Body @"
[Console]::Error.WriteLine('database unreachable')
exit 2
"@
        try {
            Invoke-MachineReadableProcess -FilePath $powershell `
                -Arguments @("-NoProfile", "-File", $child) -Activity "fake status" -SensitiveOutput $false | Out-Null
            throw "should have failed"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "database unreachable") `
                -Because "a non-sensitive tool should surface its stderr: $($_.Exception.Message)"
        }
    }

    Write-Host ""
    Write-Host "identity status field extraction (the rotations 0 -> 0 bug)"

    # The REAL shape the recovery tool emits, captured from the owner's machine. `rotations`
    # is top-level; `root` is a storage descriptor with no counter in it. The wrapper read
    # `root.rotations`, and a defensive existence check turned that into a silent zero.
    $realShape = @'
{
  "action": "status",
  "active_sessions": 0,
  "bootstrapped": true,
  "created_at": "2026-09-01T11:44:57.479379+00:00",
  "root": { "bootstrapped": true, "kind": "file", "path": "E:\\api\\var\\identity\\owner_credential.json" },
  "rotated_at": "2026-09-01T13:34:28.637831+00:00",
  "rotations": 2,
  "session_idle_timeout_s": 604800,
  "session_ttl_s": 2592000
}
'@ | ConvertFrom-Json

    Test-Case "rotations is read from the top level of the real payload shape" {
        Assert-Equal -Expected 2 -Actual (Get-IdentityRotationCount -Status $realShape) `
            -Because "this exact payload was read as 0 by the old root.rotations path"
    }

    Test-Case "root path and created_at come from where they actually live" {
        Assert-True -Condition ((Get-IdentityRootPath -Status $realShape) -match "owner_credential\.json") `
            -Because "root.path is the canonical root file"
        Assert-True -Condition ((Get-IdentityCreatedAt -Status $realShape) -match "^2026-09-01T11:44:57") `
            -Because "created_at identifies the owner identity across rotations"
    }

    Test-Case "a payload missing the counter throws instead of defaulting to zero" {
        # The defensive default is the bug: a missing field must be a loud protocol error,
        # never a quiet 0 that produces '0 -> 0' while the real counter advances.
        $withoutCounter = '{"action":"status","root":{"kind":"file","path":"x"}}' | ConvertFrom-Json
        try {
            Get-IdentityRotationCount -Status $withoutCounter | Out-Null
            throw "a missing rotations field must not be read as zero"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "no top-level 'rotations'") `
                -Because "the error should name the protocol problem: $($_.Exception.Message)"
        }
    }

    Test-Case "the old wrong path (root.rotations) is genuinely absent from the real shape" {
        # Documents WHY the bug produced zero: the property never existed.
        Assert-True -Condition ($realShape.root.PSObject.Properties.Name -notcontains "rotations") `
            -Because "if root ever grows a rotations field this test forces a decision about which is canonical"
    }

    Write-Host ""
    Write-Host "the parser on its own"

    Test-Case "a JSON array is refused: exactly one document means one object" {
        try {
            ConvertFrom-SingleJsonDocument -Text '[{"a":1},{"b":2}]' -Activity "probe" | Out-Null
            throw "an array must not be accepted"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "JSON values|exactly one") -Because "got: $($_.Exception.Message)"
        }
    }

    Test-Case "whitespace around a valid document is fine" {
        $parsed = ConvertFrom-SingleJsonDocument -Text "  `r`n{`"ok`":true}`r`n  " -Activity "probe"
        Assert-Equal -Expected $true -Actual $parsed.ok -Because "leading and trailing whitespace is not contamination"
    }

    Test-Case "null and whitespace-only input are refused" {
        foreach ($input in @($null, "", "   `r`n  ")) {
            try {
                ConvertFrom-SingleJsonDocument -Text $input -Activity "probe" | Out-Null
                throw "empty input must not be accepted"
            }
            catch {
                Assert-True -Condition ($_.Exception.Message -match "no output") -Because "got: $($_.Exception.Message)"
            }
        }
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
