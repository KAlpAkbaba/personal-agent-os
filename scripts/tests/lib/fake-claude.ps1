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
    $entry = [pscustomobject]@{ role = $role; task = $taskId; cwd = $here; budget = $budget; tools = $tools; lines = @($card -split "`n").Length }
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

if ($scenario -eq "silent") {
    [Console]::Out.Write("I could not do that.")
    exit 0
}
if ($scenario -eq "slow") {
    Start-Sleep -Seconds 600
    exit 0
}
$cost = if ($scenario -eq "costly") { 4.0 } else { 0.25 }

switch ($role) {
    "researcher" {
        $folder = Join-Path $here "team\proposals"
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Set-Content -LiteralPath (Join-Path $folder "2026-09-30-anlati.md") -Value "# Anlatı: bu hafta ne oldu`n`nYapalım mı?" -Encoding UTF8
        Send-Result -Text "1 öneri yazıldı: team/proposals/2026-09-30-anlati.md" -Cost $cost
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
