<#
.SYNOPSIS
    Collects the read-only snapshot of the Cloud Core host into
    scripts/tests/fixtures/host-snapshot.json. The LEAD runs it, from the home PC; workers
    and inspectors never reach the host. Reads only; changes nothing on the host.

.DESCRIPTION
    scripts/cloud/host-snapshot.sh is sent over ssh on the stdin of 'bash -s' (BatchMode,
    never interactive, nothing is copied to the host). Only its stdout is the document:
    stderr is read separately and never merged (the release scripts' rule - a host's routine
    stderr must not become part of what is parsed). The document is checked against the
    schema this script holds, 'collected_at' and 'source' are added, and the fixture is
    replaced in one move. The fake hosts of scripts/tests/maintenance-reboot.tests.ps1 and
    services/api/tests/unit/test_host_snapshot_schema.py are built from that file.

    Exit codes: 0 the fixture was written (or -ValidateFile found the file valid);
    3 the host answered, but not with a document the schema accepts - nothing was changed;
    4 ssh could not reach the host (Tailscale's browser check, no route, no ssh) - nothing
    was changed; run it again after the check.

    The schema (version 1) - no member beyond these is accepted, so an environment dump or a
    file's content cannot ride along:
      schema_version   1
      host             kernel (text), reboot_required (true/false)
      serving_colour   blue | green | missing | invalid
      markers          release, last_known_good, recovery_pin: 40 hex | missing | invalid
      containers       at least one {name, state}
      timers           {name: pagentos-*.timer, active}
      operation_lock   present, samples, interval_s, held <= samples, longest_run <= held
      columns          at least one {table, column, data_type, character_maximum_length:
                       a positive whole number or null}
      collected_at, source, notes (optional)   only in the fixture, not in the script's output

.EXAMPLE
    .\scripts\cloud\collect-host-snapshot.ps1
.EXAMPLE
    .\scripts\cloud\collect-host-snapshot.ps1 -ValidateFile scripts\tests\fixtures\host-snapshot.json
#>
[CmdletBinding()]
param(
    [string]$CloudHost = "root@100.90.158.26",
    [string]$OutFile = "",
    [string]$SshExe = "ssh.exe",
    [int]$TimeoutS = 240,
    # Validate a file against the schema and stop: the fixture, or with -Raw the script's output.
    [string]$ValidateFile = "",
    [switch]$Raw
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hostScript = Join-Path $repoRoot "scripts\cloud\host-snapshot.sh"
$target = if ($OutFile) { $OutFile } else { Join-Path $repoRoot "scripts\tests\fixtures\host-snapshot.json" }
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-SnapshotMember {
    # The value of a member, or $null when the object does not have it (StrictMode-safe).
    param([object]$Object, [string]$Name)
    if ($null -eq $Object -or $Object -isnot [pscustomobject]) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-MemberProblems {
    # Members an object must have, and members it may not have.
    param([object]$Object, [string]$Where, [string[]]$Required, [string[]]$Optional = @())
    $found = New-Object System.Collections.ArrayList
    if ($null -eq $Object -or $Object -isnot [pscustomobject]) { [void]$found.Add("${Where}: not an object"); return @($found.ToArray()) }
    $names = @($Object.PSObject.Properties | ForEach-Object { $_.Name })
    foreach ($name in $Required) { if ($names -notcontains $name) { [void]$found.Add("${Where}: '$name' is missing") } }
    foreach ($name in $names) { if ($Required -notcontains $name -and $Optional -notcontains $name) { [void]$found.Add("${Where}: '$name' is not a member of the schema") } }
    return @($found.ToArray())
}

function Get-HostSnapshotProblems {
    # Every way the document breaks the schema; an empty list when it holds.
    param([object]$Document, [bool]$Collected)
    $problems = New-Object System.Collections.ArrayList
    $required = @("schema_version", "host", "serving_colour", "markers", "containers", "timers", "operation_lock", "columns")
    $optional = @()
    if ($Collected) { $required += @("collected_at", "source"); $optional = @("notes") }
    foreach ($p in @(Get-MemberProblems -Object $Document -Where "document" -Required $required -Optional $optional)) { [void]$problems.Add($p) }
    if (@($problems).Count -gt 0) { return @($problems.ToArray()) }

    $isText = { param($v) ($v -is [string]) -and ($v.Length -gt 0) }
    $isWhole = { param($v) ($v -is [int]) -and ($v -ge 0) }
    $sha = '^([0-9a-f]{40}|missing|invalid)$'
    if ($Document.schema_version -isnot [int] -or $Document.schema_version -ne 1) { [void]$problems.Add("schema_version: not 1") }
    if ($Collected) {
        if (-not (& $isText $Document.collected_at) -or $Document.collected_at -cnotmatch '^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$') { [void]$problems.Add("collected_at: not a UTC date or timestamp") }
        if (-not (& $isText $Document.source)) { [void]$problems.Add("source: not text") }
    }

    $h = $Document.host
    foreach ($p in @(Get-MemberProblems -Object $h -Where "host" -Required @("kernel", "reboot_required"))) { [void]$problems.Add($p) }
    if (-not (& $isText (Get-SnapshotMember $h "kernel"))) { [void]$problems.Add("host.kernel: not text") }
    if ((Get-SnapshotMember $h "reboot_required") -isnot [bool]) { [void]$problems.Add("host.reboot_required: not true/false") }

    if (-not (& $isText $Document.serving_colour) -or $Document.serving_colour -cnotmatch '^(blue|green|missing|invalid)$') { [void]$problems.Add("serving_colour: not blue, green, missing or invalid") }

    $m = $Document.markers
    foreach ($p in @(Get-MemberProblems -Object $m -Where "markers" -Required @("release", "last_known_good", "recovery_pin"))) { [void]$problems.Add($p) }
    foreach ($name in "release", "last_known_good", "recovery_pin") {
        $v = Get-SnapshotMember $m $name
        if (-not (& $isText $v) -or $v -cnotmatch $sha) { [void]$problems.Add("markers.${name}: not 40 hex, 'missing' or 'invalid'") }
    }

    $containers = @($Document.containers)
    if ($containers.Count -eq 0) { [void]$problems.Add("containers: empty") }
    for ($i = 0; $i -lt $containers.Count; $i++) {
        $c = $containers[$i]
        $bad = @(Get-MemberProblems -Object $c -Where "containers[$i]" -Required @("name", "state"))
        foreach ($p in $bad) { [void]$problems.Add($p) }
        if ($bad.Count -gt 0) { continue }
        if (-not (& $isText $c.name) -or $c.name -cnotmatch '^[A-Za-z0-9_.-]+$') { [void]$problems.Add("containers[$i].name: not a container name") }
        if (-not (& $isText $c.state) -or $c.state -cnotmatch '^(created|running|paused|restarting|removing|exited|dead)$') { [void]$problems.Add("containers[$i].state: not a docker state") }
    }

    $timers = @($Document.timers)
    for ($i = 0; $i -lt $timers.Count; $i++) {
        $t = $timers[$i]
        $bad = @(Get-MemberProblems -Object $t -Where "timers[$i]" -Required @("name", "active"))
        foreach ($p in $bad) { [void]$problems.Add($p) }
        if ($bad.Count -gt 0) { continue }
        if (-not (& $isText $t.name) -or $t.name -cnotmatch '^pagentos-[A-Za-z0-9_.@-]+\.timer$') { [void]$problems.Add("timers[$i].name: not a pagentos-*.timer") }
        if (-not (& $isText $t.active) -or $t.active -cnotmatch '^[a-z]+$') { [void]$problems.Add("timers[$i].active: not a systemd state") }
    }

    $l = $Document.operation_lock
    $bad = @(Get-MemberProblems -Object $l -Where "operation_lock" -Required @("present", "samples", "interval_s", "held", "longest_run"))
    foreach ($p in $bad) { [void]$problems.Add($p) }
    if ($bad.Count -eq 0) {
        if ($l.present -isnot [bool]) { [void]$problems.Add("operation_lock.present: not true/false") }
        $numbers = $true
        foreach ($name in "samples", "interval_s", "held", "longest_run") {
            if (-not (& $isWhole (Get-SnapshotMember $l $name))) { [void]$problems.Add("operation_lock.${name}: not a whole number"); $numbers = $false }
        }
        if ($numbers) {
            if ($l.held -gt $l.samples) { [void]$problems.Add("operation_lock.held: more than the samples taken") }
            if ($l.longest_run -gt $l.held -or (($l.held -gt 0) -and ($l.longest_run -lt 1))) { [void]$problems.Add("operation_lock.longest_run: does not fit 'held'") }
            if (($l.present -is [bool]) -and (($l.present -and $l.samples -lt 1) -or (-not $l.present -and $l.samples -ne 0))) { [void]$problems.Add("operation_lock.samples: does not fit 'present'") }
        }
    }

    $columns = @($Document.columns)
    if ($columns.Count -eq 0) { [void]$problems.Add("columns: empty") }
    $name = '^[A-Za-z_][A-Za-z0-9_]*$'
    for ($i = 0; $i -lt $columns.Count; $i++) {
        $c = $columns[$i]
        $bad = @(Get-MemberProblems -Object $c -Where "columns[$i]" -Required @("table", "column", "data_type", "character_maximum_length"))
        foreach ($p in $bad) { [void]$problems.Add($p) }
        if ($bad.Count -gt 0) { continue }
        if (-not (& $isText $c.table) -or $c.table -cnotmatch $name) { [void]$problems.Add("columns[$i].table: not a table name") }
        if (-not (& $isText $c.column) -or $c.column -cnotmatch $name) { [void]$problems.Add("columns[$i].column: not a column name") }
        if (-not (& $isText $c.data_type) -or $c.data_type -cnotmatch '^[A-Za-z][A-Za-z -]*$') { [void]$problems.Add("columns[$i].data_type: not a type name") }
        $length = $c.character_maximum_length
        if ($null -ne $length -and (($length -isnot [int]) -or $length -lt 1)) { [void]$problems.Add("columns[$i].character_maximum_length: not a positive whole number or null") }
        if (@($problems).Count -ge 20) { [void]$problems.Add("(more than 20 problems; the rest is not listed)"); break }
    }
    return @($problems.ToArray())
}

function Get-TextProblems {
    # The schema's verdict on a JSON text.
    param([string]$Text, [bool]$Collected)
    try { $document = $Text | ConvertFrom-Json }
    catch { return @("not one JSON document: $($_.Exception.Message)") }
    return @(Get-HostSnapshotProblems -Document $document -Collected $Collected)
}

if ($ValidateFile) {
    if (-not (Test-Path -LiteralPath $ValidateFile)) { Write-Host "no such file: $ValidateFile"; exit 3 }
    $found = @(Get-TextProblems -Text ([System.IO.File]::ReadAllText($ValidateFile)) -Collected (-not $Raw))
    if ($found.Count -gt 0) {
        Write-Host "$ValidateFile does not hold the host-snapshot schema:"
        foreach ($p in $found) { Write-Host "  $p" }
        exit 3
    }
    Write-Host "$ValidateFile holds the host-snapshot schema"
    exit 0
}

if ($CloudHost -cnotmatch '^[A-Za-z0-9_.@-]+$') { throw "-CloudHost is user@host" }
# The script travels as LF whatever this checkout's line endings are.
$scriptBytes = $utf8.GetBytes(([System.IO.File]::ReadAllText($hostScript) -replace "`r`n", "`n"))

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $SshExe
$psi.Arguments = "-o BatchMode=yes -o ConnectTimeout=20 $CloudHost bash -s"
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$unreached = ""
$exitCode = -1
$stdout = ""
$stderrLast = ""
try {
    $process = [System.Diagnostics.Process]::Start($psi)
    # stdout and stderr are two streams and stay two: only stdout is the document.
    $out = $process.StandardOutput.ReadToEndAsync()
    $err = $process.StandardError.ReadToEndAsync()
    try {
        $process.StandardInput.BaseStream.Write($scriptBytes, 0, $scriptBytes.Length)
        $process.StandardInput.Close()
    }
    catch { }   # an ssh that died before reading closes the pipe; its exit code tells why
    if (-not $process.WaitForExit($TimeoutS * 1000)) {
        try { $process.Kill() } catch { }
        $unreached = "ssh did not answer in $TimeoutS s"
    }
    else {
        [void]$out.Wait(5000); [void]$err.Wait(5000)
        $exitCode = $process.ExitCode
        $stdout = [string]$out.Result
        $stderrLast = @(([string]$err.Result) -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1) -join ""
        if ($exitCode -eq 255) { $unreached = "ssh exit 255: $($stderrLast.Trim())" }
    }
}
catch { $unreached = "ssh could not be started: $($_.Exception.Message)" }

if ($unreached) {
    Write-Host "the host was not reached ($unreached); nothing was changed: $target is as it was"
    Write-Host "Tailscale SSH may be asking for its browser check; run this again after it."
    exit 4
}
if ($exitCode -ne 0) {
    Write-Host "the host answered, but host-snapshot.sh failed (exit ${exitCode}: $($stderrLast.Trim())); nothing was changed"
    exit 3
}
$text = ($stdout -replace "`r`n", "`n").Trim() + "`n"
$found = @(Get-TextProblems -Text $text -Collected $false)
if ($found.Count -eq 0 -and -not $text.StartsWith("{`n")) { $found = @("the document does not open with '{' on a line of its own") }
if ($found.Count -eq 0) {
    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $text = "{`n  `"collected_at`": `"$stamp`",`n  `"source`": `"host-snapshot.sh over ssh (collect-host-snapshot.ps1)`",`n" + $text.Substring(2)
    $found = @(Get-TextProblems -Text $text -Collected $true)
}
if ($found.Count -gt 0) {
    Write-Host "the host answered, but not with a document the schema accepts; nothing was changed:"
    foreach ($p in $found) { Write-Host "  $p" }
    exit 3
}
$next = "$target.next"
[System.IO.File]::WriteAllText($next, $text, $utf8)
Move-Item -LiteralPath $next -Destination $target -Force
$document = $text | ConvertFrom-Json
Write-Host "fixture: $target (collected_at $stamp; serving colour $($document.serving_colour); lock held $($document.operation_lock.held) of $($document.operation_lock.samples); $(@($document.columns).Count) columns)"
exit 0
