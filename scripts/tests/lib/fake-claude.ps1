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

    The output has the shape the run asked for: with `--output-format stream-json` it is the
    line stream of the real tool (2.1.285: an init line, one `rate_limit_event`, and a result
    line that does NOT start with {"type" and whose `modelUsage` names the model of --model);
    otherwise the single result document. The model policy's hooks (model-policy-cycle),
    independent of the scenario:

      PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS  ids, comma separated: a run started on one of them
          answers the real limit sentence ("You've hit your Opus limit ...") and a `rejected`
          event of that model's limit type; a run on any other model works
      PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES   only these roles are limited (default: every role)
      PAGENTOS_FAKE_CLAUDE_LIMIT_TYPE      the rejected event's rateLimitType instead of the
          model's own (five_hour closes every model)
      PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS  the reset, seconds from now (default 3600)
      PAGENTOS_FAKE_CLAUDE_RAN_MODEL       "<role>=<id>": that role's modelUsage names <id>
          whatever --model said, as a tool that substituted the model itself would print
#>
# No param block, on purpose: with one, PowerShell binds the tool's `-p` to its own common
# parameter -PipelineVariable and swallows the argument after it (`--output-format` never
# reached this script; it went unnoticed while nothing read it).
$Rest = @($args | ForEach-Object { [string]$_ })

$ErrorActionPreference = "Stop"
$scenario = [string]$env:PAGENTOS_FAKE_CLAUDE_SCENARIO
$log = [string]$env:PAGENTOS_FAKE_CLAUDE_LOG

$roleFile = ""
$budget = ""
$tools = ""
$model = ""
$format = ""
$verbose = $false
$fallbackFlag = $false
for ($i = 0; $i -lt $Rest.Length; $i++) {
    if ($Rest[$i] -eq "--output-format") { $format = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--verbose") { $verbose = $true }
    if ($Rest[$i] -eq "--fallback-model") { $fallbackFlag = $true }
    if ($Rest[$i] -eq "--append-system-prompt-file") { $roleFile = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--max-budget-usd") { $budget = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--allowedTools") { $tools = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--model") { $model = $Rest[$i + 1] }
}
$role = [System.IO.Path]::GetFileNameWithoutExtension($roleFile)
$card = [Console]::In.ReadToEnd()
$taskId = ""
if ($card -match '(?m)^- id: (\S+)') { $taskId = $Matches[1] }
$here = (Get-Location).ProviderPath

if ($log) {
    $entry = [pscustomobject]@{ role = $role; task = $taskId; cwd = $here; budget = $budget; tools = $tools; model = $model; output = $format; verbose = $verbose; fallback_flag = $fallbackFlag; no_fallback_env = [string]$env:CLAUDE_CODE_NO_MODEL_FALLBACK; lines = @($card -split "`n").Length; subjects = @($card -split "`n" | Where-Object { $_ -match '^- ' -and $card -match 'The subjects the lead asks for' }); came_back = ($card -match 'Why this task came back') }
    Add-Content -LiteralPath $log -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
}

# The model the result says really ran: --model, unless the test names another one for this role.
$ranModel = $model
if ([string]$env:PAGENTOS_FAKE_CLAUDE_RAN_MODEL -match ('(?:^|,)' + [regex]::Escape($role) + '=([A-Za-z0-9._-]+)')) { $ranModel = $Matches[1] }

function Get-LimitEvent {
    <# One `rate_limit_event` line, field for field as 2.1.285 prints it. The Fable week is in
       the windows only when the run is on Fable - as in the real tool. #>
    param([string]$Status, [string]$Type, [long]$ResetsAt)
    $week = [DateTimeOffset]::UtcNow.AddDays(4).ToUnixTimeSeconds()
    $windows = [ordered]@{
        five_hour = [ordered]@{ utilization = 0.06; resetsAt = [DateTimeOffset]::UtcNow.AddHours(3).ToUnixTimeSeconds() }
        seven_day = [ordered]@{ utilization = 0.46; resetsAt = $week }
    }
    if ($model -eq "claude-fable-5-1") { $windows["seven_day_overage_included"] = [ordered]@{ utilization = 0.81; resetsAt = $week } }
    $info = [ordered]@{ status = $Status; resetsAt = $ResetsAt; rateLimitType = $Type; isUsingOverage = $false; unifiedWindows = $windows }
    return (([ordered]@{ type = "rate_limit_event"; rate_limit_info = $info; uuid = "u"; session_id = "s" }) | ConvertTo-Json -Compress -Depth 6)
}

function Write-Answer {
    <# The result, as one document or as the stream's lines, whichever the run asked for. #>
    param([string]$Text, [double]$Cost, [bool]$IsError, [string]$EventLine)
    if ($format -ne "stream-json") {
        $document = [pscustomobject]@{ type = "result"; subtype = "success"; is_error = $IsError; result = $Text; total_cost_usd = $Cost }
        [Console]::Out.Write(($document | ConvertTo-Json -Compress))
        return
    }
    $usage = [ordered]@{}
    if ($ranModel) { $usage[$ranModel] = [ordered]@{ inputTokens = 10; outputTokens = 5; costUSD = $Cost } }
    $result = [ordered]@{
        duration_api_ms = 1200; type = "result"; subtype = "success"; is_error = $IsError; result = $Text
        total_cost_usd = $Cost; modelUsage = $usage
    }
    $lines = @(
        (([ordered]@{ type = "system"; subtype = "init"; model = $model; session_id = "s" }) | ConvertTo-Json -Compress),
        $EventLine,
        ($result | ConvertTo-Json -Compress -Depth 6)
    )
    [Console]::Out.Write(($lines -join "`n") + "`n")
}

function Send-Result {
    param([string]$Text, [double]$Cost = 0.25)
    $session = [DateTimeOffset]::UtcNow.AddHours(3).ToUnixTimeSeconds()
    Write-Answer -Text $Text -Cost $Cost -IsError $false -EventLine (Get-LimitEvent -Status "allowed" -Type "five_hour" -ResetsAt $session)
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
# The model policy: a run on a model the test named as limited answers what the real tool answers.
$limitedModels = @(([string]$env:PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS).Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$limitedRoles = @(([string]$env:PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES).Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($model -and $limitedModels -contains $model -and ($limitedRoles.Length -eq 0 -or $limitedRoles -contains $role)) {
    $names = @{ "claude-fable-5-1" = @("Fable", "seven_day_overage_included"); "claude-opus-5-5" = @("Opus", "seven_day_opus"); "claude-sonnet-5-5" = @("Sonnet", "seven_day_sonnet") }
    $type = $names[$model][1]
    $name = $names[$model][0]
    if ($env:PAGENTOS_FAKE_CLAUDE_LIMIT_TYPE) {
        $type = [string]$env:PAGENTOS_FAKE_CLAUDE_LIMIT_TYPE
        if ($type -eq "five_hour") { $name = "session" } elseif ($type -eq "seven_day") { $name = "weekly" }
    }
    $seconds = 3600
    if ([string]$env:PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS -match '^-?\d+$') { $seconds = [int]$env:PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS }
    $epoch = [DateTimeOffset]::UtcNow.AddSeconds($seconds).ToUnixTimeSeconds()
    Write-Answer -Text ("You've hit your $name limit " + [char]0x00B7 + " resets 8:40pm") -Cost 0 -IsError $true -EventLine (Get-LimitEvent -Status "rejected" -Type $type -ResetsAt $epoch)
    exit 1
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
