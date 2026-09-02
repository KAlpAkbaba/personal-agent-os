<#
.SYNOPSIS
    Read the Windows agent's audit trail (JSONL) and correlate rows to a specific command.

.DESCRIPTION
    The agent's AuditLog writes one JSON object per line with EXACTLY these keys (see
    devices/windows-agent/src/PagentOS.Agent.Core/Audit/AuditLog.cs):

        ts          ISO-8601 UTC, round-trip "O" format   (always)
        event       e.g. command_received, command_ack     (always)
        device_id, command_id, trace_id, capability, status, detail   (only when present)

    The first cloud qualification run reported "no command row found" while real commands
    were succeeding, because the verifier read the timestamp from a field named `at`, which
    has never existed - every row failed the time filter. This module is the corrected,
    schema-true reader, and it correlates by the command's own identity rather than by a
    loose event-name match: the row that proves a command was executed is the one carrying
    THAT command_id (and trace_id), not "a row that mentions commands".

    Read-only. The audit directory is SYSTEM + Administrators, so callers run elevated.
#>

Set-StrictMode -Version Latest

$script:CommandAuditEvents = @("command_received", "command_ack", "command_duplicate_reack")

function Read-AgentAuditRows {
    <#
    .SYNOPSIS
        Every parseable row of the audit file as objects, in file order. Unparseable lines
        are counted, not hidden - a torn or foreign line is a finding, not noise.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$AuditPath)

    if (-not (Test-Path -LiteralPath $AuditPath -PathType Leaf)) {
        return [pscustomobject]@{ Rows = @(); Unparseable = 0; Exists = $false; Path = $AuditPath }
    }
    $rows = New-Object System.Collections.ArrayList
    $bad = 0
    foreach ($line in [System.IO.File]::ReadAllLines($AuditPath)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { [void]$rows.Add(($line | ConvertFrom-Json)) } catch { $bad++ }
    }
    return [pscustomobject]@{ Rows = @($rows.ToArray()); Unparseable = $bad; Exists = $true; Path = $AuditPath }
}

function Get-AuditRowTimestamp {
    <#  The row's `ts` as a UTC DateTime, or $null when absent/unparseable. Never `at`.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Row)
    $ts = Get-OptionalProperty -InputObject $Row -Name "ts"
    if ([string]::IsNullOrWhiteSpace([string]$ts)) { return $null }
    try { return ([DateTimeOffset]::Parse([string]$ts, [System.Globalization.CultureInfo]::InvariantCulture)).UtcDateTime }
    catch { return $null }
}

function Find-AgentAuditCommandRows {
    <#
    .SYNOPSIS
        The audit rows that belong to ONE command, identified by command_id (and, when
        given, trace_id). Returns a report: the matching rows, the events seen, and whether
        the ack row exists.

    .PARAMETER Since
        Optional lower bound on `ts` (UTC). A row earlier than this is reported separately
        as "stale" so a re-used id from an old run cannot masquerade as new evidence.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$AuditPath,
        [Parameter(Mandatory = $true)][string]$CommandId,
        [string]$TraceId,
        [datetime]$Since
    )

    $file = Read-AgentAuditRows -AuditPath $AuditPath
    $matching = New-Object System.Collections.ArrayList
    $stale = 0
    $traceMismatch = 0
    foreach ($row in $file.Rows) {
        $rowCommand = Get-OptionalProperty -InputObject $row -Name "command_id"
        if ([string]$rowCommand -ne $CommandId) { continue }
        if ($TraceId) {
            $rowTrace = Get-OptionalProperty -InputObject $row -Name "trace_id"
            if ([string]$rowTrace -ne $TraceId) { $traceMismatch++; continue }
        }
        if ($PSBoundParameters.ContainsKey("Since")) {
            $ts = Get-AuditRowTimestamp -Row $row
            if ($null -eq $ts -or $ts -lt $Since.ToUniversalTime()) { $stale++; continue }
        }
        [void]$matching.Add($row)
    }

    $events = @($matching | ForEach-Object { Get-OptionalProperty -InputObject $_ -Name "event" })
    return [pscustomobject]@{
        Path          = $AuditPath
        Exists        = $file.Exists
        TotalRows     = @($file.Rows).Count
        Unparseable   = $file.Unparseable
        CommandId     = $CommandId
        TraceId       = $TraceId
        Rows          = @($matching.ToArray())
        Events        = $events
        Received      = ($events -contains "command_received")
        Acked         = ($events -contains "command_ack")
        StaleRows     = $stale
        TraceMismatch = $traceMismatch
        Proven        = (($events -contains "command_received") -and ($events -contains "command_ack"))
    }
}
