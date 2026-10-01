<#
.SYNOPSIS
    A stand-in for `claude -p` in scripts/tests/team-cycle.tests.ps1. It starts no model.

.DESCRIPTION
    Reads the role from --append-system-prompt-file and the task card from standard input,
    writes every call to the log the test named in PAGENTOS_FAKE_CLAUDE_LOG, and prints the
    JSON document the real tool prints. What it answers is the scenario the test named in
    PAGENTOS_FAKE_CLAUDE_SCENARIO:

      approve    the worker commits one file inside the area; the inspector says APPROVE
      return     the inspector says RETURN every time
      outside    the worker commits a file OUTSIDE its area
      silent     every run prints something that is not the result document
      costly     as approve, and every run costs 4 USD
      slow       the run sleeps for longer than the cycle lets it
      split      the lead's split run writes two sound tasks to the file its card names;
                 split-overlap / split-shared / split-missing write one task that breaks the
                 rule named, any other scenario writes no file (cycle-lead-run)
      limited    the FIRST worker run of a task answers with the subscription's usage-limit
                 error (reset time 200 s in the past); every later run is as approve
#>
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)

$ErrorActionPreference = "Stop"
$scenario = [string]$env:PAGENTOS_FAKE_CLAUDE_SCENARIO
$log = [string]$env:PAGENTOS_FAKE_CLAUDE_LOG

$roleFile = ""
$budget = ""
$tools = ""
for ($i = 0; $i -lt $Rest.Length; $i++) {
    if ($Rest[$i] -eq "--append-system-prompt-file") { $roleFile = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--max-budget-usd") { $budget = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--allowedTools") { $tools = $Rest[$i + 1] }
}
$role = [System.IO.Path]::GetFileNameWithoutExtension($roleFile)
$card = [Console]::In.ReadToEnd()
$taskId = ""
if ($card -match '(?m)^- id: (\S+)') { $taskId = $Matches[1] }
$here = (Get-Location).ProviderPath

if ($log) {
    $entry = [pscustomobject]@{ role = $role; task = $taskId; cwd = $here; budget = $budget; tools = $tools; lines = @($card -split "`n").Length; subjects = @($card -split "`n" | Where-Object { $_ -match '^- ' -and $card -match 'The subjects the lead asks for' }); came_back = ($card -match 'Why this task came back') }
    Add-Content -LiteralPath $log -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
}

function Send-Result {
    param([string]$Text, [double]$Cost = 0.25)
    $document = [pscustomobject]@{ type = "result"; subtype = "success"; is_error = $false; result = $Text; total_cost_usd = $Cost }
    [Console]::Out.Write(($document | ConvertTo-Json -Compress))
    exit 0
}

function Invoke-Git {
    param([string[]]$Arguments)
    $output = & git.exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' '): $output" }
}

# office-cycle-status hooks, independent of the scenario (the run then answers as the scenario says):
#   PAGENTOS_FAKE_CLAUDE_SNAPSHOT + PAGENTOS_FAKE_CLAUDE_STATUS: the run stays 4 s, then copies the
#     cycle's live status file to <snapshot>\<role>-<task>.json - what a reader sees while a run is in flight;
#   PAGENTOS_FAKE_CLAUDE_STOPFLAG (+ _ROLE, default worker): that role's run creates the stop flag, as the
#     owner or the lead would while the cycle works.
$snapshotDir = [string]$env:PAGENTOS_FAKE_CLAUDE_SNAPSHOT
$statusFile = [string]$env:PAGENTOS_FAKE_CLAUDE_STATUS
if ($snapshotDir -and $statusFile) {
    Start-Sleep -Seconds 4
    if (-not (Test-Path -LiteralPath $snapshotDir)) { [void](New-Item -ItemType Directory -Force -Path $snapshotDir) }
    if (Test-Path -LiteralPath $statusFile) { Copy-Item -LiteralPath $statusFile -Destination (Join-Path $snapshotDir "$role-$taskId.json") -Force }
}
#   PAGENTOS_FAKE_CLAUDE_HEARTBEAT + PAGENTOS_FAKE_CLAUDE_STATUS: a worker run stays 7 s and appends the status
#     file's updated_at to <heartbeat> once a second - a reader's view of whether the status is refreshed.
$heartbeat = [string]$env:PAGENTOS_FAKE_CLAUDE_HEARTBEAT
if ($heartbeat -and $statusFile -and $role -eq "worker") {
    for ($i = 0; $i -lt 7; $i++) {
        if (Test-Path -LiteralPath $statusFile) {
            $stamp = [string]((Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json).updated_at)
            Add-Content -LiteralPath $heartbeat -Value $stamp -Encoding ASCII
        }
        Start-Sleep -Seconds 1
    }
}
$stopFlag = [string]$env:PAGENTOS_FAKE_CLAUDE_STOPFLAG
$stopRole = if ($env:PAGENTOS_FAKE_CLAUDE_STOPFLAG_ROLE) { [string]$env:PAGENTOS_FAKE_CLAUDE_STOPFLAG_ROLE } else { "worker" }
if ($stopFlag -and $role -eq $stopRole) { Set-Content -LiteralPath $stopFlag -Value "stop" -Encoding ASCII }

if ($scenario -eq "silent") {
    [Console]::Out.Write("I could not do that.")
    exit 0
}
if ($scenario -eq "slow") {
    Start-Sleep -Seconds 600
    exit 0
}
if ($scenario -eq "limited" -and $role -eq "worker") {
    # This call's own log entry is already written: one entry means the first call.
    $earlier = 0
    if ($log -and (Test-Path -LiteralPath $log)) {
        $earlier = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_ -match '"role":"worker"' -and $_ -match ('"task":"' + $taskId + '"') }).Count
    }
    if ($earlier -le 1) {
        $epoch = [DateTimeOffset]::UtcNow.AddSeconds(-200).ToUnixTimeSeconds()
        $document = [pscustomobject]@{ type = "result"; subtype = "success"; is_error = $true; result = "Claude AI usage limit reached|$epoch"; total_cost_usd = 0 }
        [Console]::Out.Write(($document | ConvertTo-Json -Compress))
        exit 1
    }
}
$cost = if ($scenario -eq "costly") { 4.0 } else { 0.25 }

switch ($role) {
    "researcher" {
        $folder = Join-Path $here "team\proposals"
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Set-Content -LiteralPath (Join-Path $folder "2026-09-30-anlati.md") -Value "# Anlatı: bu hafta ne oldu`n`nYapalım mı?" -Encoding UTF8
        Send-Result -Text "1 öneri yazıldı: team/proposals/2026-09-30-anlati.md" -Cost $cost
    }
    "lead" {
        # The split run (cycle-lead-run): writes the file the card names, in the shape the
        # scenario asks for. Any other scenario writes nothing, as a lead that failed would.
        $target = ""
        if ($card -match '(?m)^- split_file: (\S+)') { $target = Join-Path $here ($Matches[1] -replace "/", "\") }
        $one = [ordered]@{ id = "$taskId-a"; title = "first half of $taskId"; roadmap_row = "row"; area = @("src/s1"); goal = "g"; acceptance = "a"; evidence_expected = "PROVEN_AUTOMATED" }
        $two = [ordered]@{ id = "$taskId-b"; title = "second half of $taskId"; roadmap_row = "row"; area = @("src/s2"); goal = "g"; acceptance = "a"; evidence_expected = "PROVEN_AUTOMATED" }
        $split = $null
        switch ($scenario) {
            "split" { $split = @($one, $two) }
            "split-overlap" { $two.area = @("src/busy/deep"); $split = @($one, $two) }
            "split-shared" { $two.area = @("docs/HANDOFF.md"); $split = @($one, $two) }
            "split-missing" { $two.Remove("acceptance"); $split = @($one, $two) }
        }
        if ($null -ne $split -and $target) {
            $folder = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
            [System.IO.File]::WriteAllText($target, (ConvertTo-Json -InputObject @($split) -Depth 6), (New-Object System.Text.UTF8Encoding($false)))
            Send-Result -Text "split written: $target" -Cost $cost
        }
        Send-Result -Text "I wrote no split." -Cost $cost
    }
    "integrator" {
        $folder = Join-Path $here "team\plans"
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Set-Content -LiteralPath (Join-Path $folder "$taskId-integration.md") -Value "# plan`n`nnone exists" -Encoding UTF8
        Send-Result -Text "choice: none exists`nplan: team/plans/$taskId-integration.md" -Cost $cost
    }
    "worker" {
        # Inside the FIRST area the card names, as a worker that keeps to its area would.
        $area = "src/area"
        if ($card -match '(?m)^- area: ([^,\r\n]+)') { $area = $Matches[1].Trim().TrimEnd("*").TrimEnd("/") }
        $relative = if ($scenario -eq "outside") { "docs\outside.md" } else { ($area -replace "/", "\") + "\$taskId.txt" }
        $target = Join-Path $here $relative
        $folder = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Add-Content -LiteralPath $target -Value "work on $taskId" -Encoding UTF8
        Invoke-Git -Arguments @("add", "-A")
        Invoke-Git -Arguments @("-c", "user.name=worker", "-c", "user.email=worker@example.invalid", "commit", "-q", "-m", "work on $taskId")
        Send-Result -Text "sha: see the branch`nfiles changed: 1`ntests: NOT_RUN" -Cost $cost
    }
    "inspector" {
        $lines = New-Object System.Collections.ArrayList
        1..60 | ForEach-Object { [void]$lines.Add("line $_ of the inspection") }
        if ($scenario -eq "return") { [void]$lines.Add("RETURN (the test is missing)") }
        else { [void]$lines.Add("APPROVE") }
        Send-Result -Text (($lines.ToArray()) -join "`n") -Cost $cost
    }
    default { Send-Result -Text "nothing to do for '$role'" -Cost $cost }
}
