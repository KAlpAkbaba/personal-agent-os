<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for scripts/lib/AgentAudit.ps1 - the reader that
    correlates the agent's audit trail to one broker command.

.DESCRIPTION
    The incident: the cloud qualification driver reported "no command row found in
    agent-audit.jsonl" while real commands were succeeding with ACKs. The writer was right;
    the verifier read the timestamp from `at`, a field the writer has never emitted (it is
    `ts`), so every row failed the time filter.

    The fixture rows below are byte-identical in SHAPE to what the real AuditLog emits -
    the key set and formats are pinned by AuditLogSchemaTests.cs, which writes real rows
    through the real class. If the writer's schema ever changes, that C# test fails first
    and this fixture must be updated with it; the two are one contract.

    Run: powershell -NoProfile -File scripts\tests\agent-audit.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\AgentAudit.ps1")

$script:Failures = 0; $script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-audit-tests-$([guid]::NewGuid().ToString('N'))"

function Test-Case { param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red } }
function Assert-True { param([bool]$Condition, [string]$Because) if (-not $Condition) { throw $Because } }
function Assert-Equal { param($Expected, $Actual, [string]$Because) if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" } }

# Real-shape rows (see AuditLogSchemaTests.cs). ts is ISO-8601 "O" UTC; keys as the writer emits them.
$t0 = [DateTime]::UtcNow.AddMinutes(-10)
function Row { param([string]$Event, [hashtable]$Fields, [datetime]$At)
    $o = [ordered]@{ ts = $At.ToString("O"); event = $Event }
    foreach ($k in $Fields.Keys) { $o[$k] = $Fields[$k] }
    return ($o | ConvertTo-Json -Compress)
}
function New-AuditFile { param([string]$Name, [string[]]$Lines)
    New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null
    $p = Join-Path $script:Sandbox "$Name.jsonl"
    [System.IO.File]::WriteAllLines($p, $Lines)
    return $p
}

try {
    Write-Host ""
    Write-Host "the engine under test"
    Test-Case "this really is Windows PowerShell 5.1 with StrictMode" {
        Assert-Equal -Expected 5 -Actual $PSVersionTable.PSVersion.Major -Because "5.1"
        $threw = $false; try { $n = $null; $null = $n.Count } catch { $threw = $true }
        Assert-True -Condition $threw -Because "StrictMode not in force"
    }

    Write-Host ""
    Write-Host 'the incident: the field is ts; at never existed'
    Test-Case "a real-shape command_ack row parses its timestamp from ts" {
        $line = Row "command_ack" @{ command_id = "cmd-1"; trace_id = "t-1"; status = "running" } $t0.AddMinutes(5)
        $row = $line | ConvertFrom-Json
        $ts = Get-AuditRowTimestamp -Row $row
        Assert-True -Condition ($null -ne $ts) -Because "ts must parse"
        Assert-True -Condition ([math]::Abs(($ts - $t0.AddMinutes(5)).TotalSeconds) -lt 1) -Because "and round-trip to the same instant (UTC)"
        Assert-True -Condition (-not (Test-ObjectProperty -InputObject $row -Name "at")) -Because 'there is no at field - the old verifier assumed one'
    }

    Test-Case "a run's command is PROVEN by its own received + ack rows, keyed on command_id and trace_id" {
        $p = New-AuditFile "proven" @(
            (Row "ipc_pipe_created" @{ status = "ok" } $t0),
            (Row "command_received" @{ command_id = "cmd-42"; trace_id = "tr-42"; capability = "desktop.open_application" } $t0.AddMinutes(1)),
            (Row "command_ack"      @{ command_id = "cmd-42"; trace_id = "tr-42"; status = "accepted" } $t0.AddMinutes(1)),
            (Row "command_ack"      @{ command_id = "cmd-42"; trace_id = "tr-42"; status = "running" } $t0.AddMinutes(1))
        )
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-True -Condition $r.Proven -Because "received + ack for THIS command must prove it (events: $($r.Events -join ','))"
        Assert-Equal -Expected 3 -Actual @($r.Rows).Count -Because "exactly this command's rows"
        Assert-Equal -Expected 0 -Actual $r.Unparseable -Because "all lines parse"
    }

    Test-Case "a DIFFERENT command's rows never count as evidence for this one" {
        $p = New-AuditFile "other" @(
            (Row "command_received" @{ command_id = "cmd-OTHER"; trace_id = "tr-OTHER"; capability = "desktop.open_application" } $t0.AddMinutes(1)),
            (Row "command_ack"      @{ command_id = "cmd-OTHER"; trace_id = "tr-OTHER"; status = "running" } $t0.AddMinutes(1))
        )
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-True -Condition (-not $r.Proven) -Because "a loose 'mentions command' match would have passed this; identity must not"
        Assert-Equal -Expected 0 -Actual @($r.Rows).Count -Because "no rows"
    }

    Test-Case "a trace_id mismatch on the right command_id is reported, not accepted" {
        $p = New-AuditFile "trace" @(
            (Row "command_ack" @{ command_id = "cmd-42"; trace_id = "tr-WRONG"; status = "running" } $t0.AddMinutes(1))
        )
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-True -Condition (-not $r.Proven) -Because "wrong trace"
        Assert-Equal -Expected 1 -Actual $r.TraceMismatch -Because "and the mismatch is counted so the operator sees it"
    }

    Test-Case "rows older than the run start are stale, not evidence (a re-used id from an old run)" {
        $p = New-AuditFile "stale" @(
            (Row "command_received" @{ command_id = "cmd-42"; trace_id = "tr-42"; capability = "x" } $t0.AddMinutes(-30)),
            (Row "command_ack"      @{ command_id = "cmd-42"; trace_id = "tr-42"; status = "running" } $t0.AddMinutes(-30))
        )
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-True -Condition (-not $r.Proven) -Because "stale"
        Assert-Equal -Expected 2 -Actual $r.StaleRows -Because "counted as stale"
    }

    Test-Case "an absent file is a clear 'no evidence', and a torn line is counted rather than hidden" {
        $missing = Find-AgentAuditCommandRows -AuditPath (Join-Path $script:Sandbox "nope.jsonl") -CommandId "c"
        Assert-True -Condition (-not $missing.Exists -and -not $missing.Proven) -Because "absent file"
        $p = New-AuditFile "torn" @(
            (Row "command_received" @{ command_id = "cmd-42"; trace_id = "tr-42"; capability = "x" } $t0.AddMinutes(1)),
            '{"ts":"2026-09-02T10:00:00.0000000Z","event":"command_ack","command_id":"cmd-42","trace_id":"tr-42","st',
            (Row "command_ack" @{ command_id = "cmd-42"; trace_id = "tr-42"; status = "running" } $t0.AddMinutes(1))
        )
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-Equal -Expected 1 -Actual $r.Unparseable -Because "the torn line is reported"
        Assert-True -Condition $r.Proven -Because "and the intact rows still prove the command"
    }

    Test-Case "the whole file is read, not a tail window (evidence early in a long file still counts)" {
        $lines = @()
        $lines += (Row "command_received" @{ command_id = "cmd-42"; trace_id = "tr-42"; capability = "x" } $t0.AddMinutes(1))
        $lines += (Row "command_ack"      @{ command_id = "cmd-42"; trace_id = "tr-42"; status = "running" } $t0.AddMinutes(1))
        for ($i = 0; $i -lt 500; $i++) { $lines += (Row "ipc_peer_refused" @{ status = "SidMismatch" } $t0.AddMinutes(2)) }
        $p = New-AuditFile "long" $lines
        $r = Find-AgentAuditCommandRows -AuditPath $p -CommandId "cmd-42" -TraceId "tr-42" -Since $t0
        Assert-True -Condition $r.Proven -Because "a 200-line tail would have missed these; the old verifier had exactly that cap"
        Assert-Equal -Expected 502 -Actual $r.TotalRows -Because "all rows were read"
    }
}
finally { Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
