<#
.SYNOPSIS
    The Danisman's night watch: every 15 minutes, from the scheduled task "PagentOS Danisman
    Watch" (register-nightly.ps1 -Watch). Danisman-watch-in-repo.

.DESCRIPTION
    The owner, 2026-10-06 23:40: "15 dakikada bir kontrol et, takilma var mi, senden beklenen
    bir sey var mi; bulursan bir daha yasanmamasi adina duzelecek sekilde yazdir." It ran
    outside the repository from 23:45 that night; this is that watch, tested.

    One look (the decisions are scripts/lib/TeamWatch.ps1's): the cycle's live status and the
    queue from the Cloud Core, the machine's process list, the newest test round folder. It
    starts the nightly task when no cycle runs and a test round when none ran for two hours.
    Only NEW findings (not seen in three hours) start ONE headless Danisman run
    (.claude/agents/danisman-watch.md) that diagnoses read-only and drafts at most three cards;
    those the queue's rules accept are queued approved, so the team builds them for the next
    release. Every look appends to watch.log and rewrites watch-latest.txt in -OutDir, where
    the chat session reads it. The run always ends 0: a failed look is a finding, not a stop.

    -ProcessListFile, -Launcher and -ModelScript are the tests' (scripts/tests/team-watch.tests.ps1).
#>
[CmdletBinding()]
param(
    [string]$QueueUrl = "http://100.90.158.26:8001",
    # The PATH of the token file (scripts/team/write-queue-token.ps1), never the token.
    [string]$QueueToken = (Join-Path $env:LOCALAPPDATA "PagentOS\team-queue.token"),
    [string]$OutDir = (Join-Path $env:USERPROFILE ".pagentos-team"),
    [string]$CycleTaskName = "PagentOS Team Nightly Cycle",
    # The folder the test rounds write; empty = <run_temp_root of team/cycle-settings.json>\testteam.
    [string]$RoundsRoot = "",
    # "none" skips the look; empty = the Cloud Core's ($QueueUrl) / the local staging's.
    [string]$CoreHealthUrl = "",
    [string]$StagingHealthUrl = "http://127.0.0.1:28001/v1/system/health",
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string]$Model = "claude-opus-5-5",
    [int]$ModelMinutes = 25,
    # The Danisman run's role; empty = .claude\agents\danisman-watch.md, or, while that is not
    # in the checkout, the interim danisman-watch.md beside the output.
    [string]$RoleFile = "",
    [switch]$NoModel,
    # For the tests: a JSON list of { ProcessId, CommandLine } in place of the machine's.
    [string]$ProcessListFile = "",
    # For the tests: a script called as -Kind cycle|round -Name <task or round> in place of starting.
    [string]$Launcher = "",
    # For the tests: a script called as -PromptFile <file> in place of the Danisman's model run.
    [string]$ModelScript = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamWatch.ps1")

$utf8 = New-Object System.Text.UTF8Encoding($false)
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir -Force) }
$log = Join-Path $OutDir "watch.log"
$latest = Join-Path $OutDir "watch-latest.txt"
$stateFile = Join-Path $OutDir "watch-state.json"
$settingsPath = Join-Path $repoRoot "team\cycle-settings.json"
if (-not $CoreHealthUrl) { $CoreHealthUrl = $QueueUrl.TrimEnd("/") + "/v1/system/health" }
if (-not $RoundsRoot) {
    $tempRoot = Read-TeamRunTempRoot -Path $settingsPath
    $RoundsRoot = if ($tempRoot) { Join-Path $tempRoot "testteam" } else { Join-Path $env:TEMP "testteam" }
}
$env:GIT_ASK_YESNO = "false"
$now = [datetime]::UtcNow
$stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm")
$lines = New-Object System.Collections.ArrayList
$actions = New-Object System.Collections.ArrayList
$findings = New-Object System.Collections.ArrayList
$state = Read-TeamWatchState -Path $stateFile
$tasks = @()
$store = $null

function Start-WatchThing {
    param([string]$Kind, [string]$Name)
    if ($Launcher) { & $powershell -NoProfile -ExecutionPolicy Bypass -File $Launcher -Kind $Kind -Name $Name | Out-Null; return "" }
    if ($Kind -eq "cycle") { & schtasks.exe /Run /TN $Name | Out-Null; return "" }
    $arguments = @("-NoProfile", "-File", "`"$repoRoot\scripts\testteam\test-round.ps1`"", "-Round", $Name, "-TeamRoot", "`"$repoRoot\team`"",
        "-ClaudePath", "`"$ClaudePath`"", "-QueueUrl", $QueueUrl, "-QueueToken", "`"$QueueToken`"")
    $p = Start-Process -FilePath $powershell -ArgumentList $arguments -WindowStyle Hidden -PassThru -WorkingDirectory $repoRoot `
        -RedirectStandardOutput (Join-Path $OutDir "round-$Name.log") -RedirectStandardError (Join-Path $OutDir "round-$Name.err.log")
    return " (pid $($p.Id))"
}

try {
    $store = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken
    $status = Invoke-TeamApi -Store $store -Method GET -Path "/v1/team/queue/status"
    $queue = Read-TeamQueueApi -Store $store
    $tasks = @(Get-TeamTasks -Queue $queue)
    $processes = if ($ProcessListFile) { @(ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($ProcessListFile, $utf8)) | ForEach-Object { $_ }) }
    else { @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ForEach-Object { [pscustomobject]@{ ProcessId = $_.ProcessId; CommandLine = [string]$_.CommandLine } }) }
    $maxParallel = (Read-TeamCycleSettings -Path $settingsPath -Workers 4 -Inspectors 3 -Integrators 1).Workers
    $lastRoundEnd = $null
    $newest = @(Get-ChildItem -LiteralPath $RoundsRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1)
    if ($newest.Count -gt 0) { $lastRoundEnd = $newest[0].LastWriteTimeUtc }

    $check = Invoke-TeamWatchCheck -Status $status -Queue $queue -Processes $processes -State $state -MaxParallel $maxParallel -Now $now -LastRoundEnd $lastRoundEnd
    $state = $check.State
    foreach ($l in $check.Lines) { [void]$lines.Add($l) }
    foreach ($f in $check.Findings) { [void]$findings.Add($f) }
    foreach ($a in $check.Actions) {
        if ($a -eq "start-cycle") {
            $pidText = Start-WatchThing -Kind "cycle" -Name $CycleTaskName
            [void]$actions.Add("zamanlanmis gorev '$CycleTaskName' baslatildi$pidText")
        }
        elseif ($a -eq "start-round") {
            $round = "t-w" + (Get-Date).ToString("MMddHHmm")
            $pidText = Start-WatchThing -Kind "round" -Name $round
            [void]$actions.Add("test turu $round baslatildi$pidText")
        }
    }

    foreach ($h in @(@{ n = "Cloud Core"; u = $CoreHealthUrl }, @{ n = "staging"; u = $StagingHealthUrl })) {
        if (-not $h.u -or $h.u -eq "none") { continue }
        try { $r = Invoke-WebRequest -UseBasicParsing -Uri $h.u -TimeoutSec 15; [void]$lines.Add(("{0}: {1}" -f $h.n, $r.StatusCode)) }
        catch {
            $text = ("{0} saglik cevabi yok: {1}" -f $h.n, ($_.Exception.Message -replace '\s+', ' '))
            [void]$findings.Add([pscustomobject]@{ key = ("down-" + $h.n); text = $text }); [void]$lines.Add("SORUN: " + $text)
        }
    }
}
catch {
    $text = "kontrol tamamlanamadi: " + ($_.Exception.Message -replace '\s+', ' ')
    [void]$findings.Add([pscustomobject]@{ key = "watch-error"; text = $text }); [void]$lines.Add("SORUN: " + $text)
}
foreach ($a in $actions) { [void]$lines.Add("YAPILDI: " + $a) }

# NEW findings -> one Danisman run drafts cards; those the queue accepts are queued approved.
$fresh = @(Select-TeamWatchFresh -Findings @($findings.ToArray()) -State $state -Now $now)
if ($fresh.Count -gt 0 -and -not $NoModel -and $null -ne $store) {
    try {
        $draft = Join-Path $OutDir ("watch-cards-" + (Get-Date).ToString("MMddHHmm") + ".json")
        $open = @($tasks | Where-Object { @("done", "released") -notcontains [string]$_.state } | ForEach-Object { [string]$_.id })
        $prompt = "Gece nobeti $stamp. Bulgular:`n" + ((@($fresh | ForEach-Object { "- " + $_.text })) -join "`n") +
            "`n`nYapilan: " + $(if ($actions.Count) { $actions -join "; " } else { "yok" }) +
            "`n`nKuyruktaki acik kartlar (id): " + ($open -join ", ") +
            "`n`nKart taslaklarini (en fazla 3, ayni sorunu zaten kapsayan acik kart varsa YAZMA) su dosyaya yaz:`nDRAFT_FILE: $draft`n"
        if ($ModelScript) {
            $promptFile = Join-Path $OutDir "watch-prompt.txt"
            [System.IO.File]::WriteAllText($promptFile, $prompt, $utf8)
            $said = (& $powershell -NoProfile -ExecutionPolicy Bypass -File $ModelScript -PromptFile $promptFile | Out-String)
        }
        else {
            # The account the team's wrappers use (team-account.txt beside the output).
            $accountFile = Join-Path $OutDir "team-account.txt"
            $name = if (Test-Path -LiteralPath $accountFile) { ([System.IO.File]::ReadAllText($accountFile)).Trim() } else { "" }
            if ($name -and $name -ne "varsayilan" -and (Test-Path -LiteralPath (Join-Path (Join-Path $env:USERPROFILE $name) ".credentials.json"))) { $env:CLAUDE_CONFIG_DIR = Join-Path $env:USERPROFILE $name }
            if (-not $RoleFile) {
                $RoleFile = Join-Path $repoRoot ".claude\agents\danisman-watch.md"
                if (-not (Test-Path -LiteralPath $RoleFile)) { $RoleFile = Join-Path $OutDir "danisman-watch.md" }
            }
            $arguments = Get-TeamRunArguments -RoleFile $RoleFile -Model $Model
            $run = Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $prompt -WorkingDirectory $repoRoot
            $finished = Wait-TeamRun -Run $run -Deadline ([datetime]::UtcNow.AddMinutes($ModelMinutes))
            $said = [string](Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr -Model $Model).Text
        }
        $said = ([string]$said -replace '\s+', ' ').Trim()
        [void]$lines.Add("Danisman teshisi: " + $said.Substring(0, [Math]::Min(600, $said.Length)))
        if (Test-Path -LiteralPath $draft) {
            $drafts = @(ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($draft, [System.Text.Encoding]::UTF8)) | ForEach-Object { $_ })
            $queue = Read-TeamQueueApi -Store $store
            $made = ConvertTo-TeamWatchCards -Drafts $drafts -Queue $queue -Now ([datetime]::UtcNow)
            foreach ($s in $made.Skipped) { [void]$lines.Add("kart atlandi: " + $s) }
            foreach ($card in $made.Cards) {
                [void](Invoke-TeamApi -Store $store -Method PUT -Path "/v1/team/queue/tasks/$($card.id)" -Body ([ordered]@{ task = $card; expected_updated_at = $null }))
                [void]$lines.Add("YAPILDI: kart yazildi (onayli, bir sonraki yayina): $($card.id) - $($card.title)")
                [void]$actions.Add("kart $($card.id)")
            }
        }
        else { [void]$lines.Add("Danisman yeni kart yazmadi (mevcut kartlar kapsiyor ya da gecici)") }
    }
    catch { [void]$lines.Add("Danisman kosusu basarisiz: " + ($_.Exception.Message -replace '\s+', ' ')) }
}

$head = if ($findings.Count -eq 0) { "$stamp kontrol: sorun yok" } else { "$stamp kontrol: $($findings.Count) bulgu, $($actions.Count) eylem" }
$text = $head + "`r`n" + ((@($lines | ForEach-Object { "  " + $_ })) -join "`r`n") + "`r`n"
[System.IO.File]::WriteAllText($latest, $text, $utf8)
[System.IO.File]::AppendAllText($log, $text, $utf8)
Write-TeamWatchState -Path $stateFile -State $state
Write-Host $text
exit 0
