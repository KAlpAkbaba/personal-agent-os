<#
.SYNOPSIS
    The step after the integration (ADR-0214 addendum 9, "Kapı yeşilse otomatik yayınla"): gated
    roadmap work that reached main is released blue/green without asking the owner - pinned,
    verified and reported - or the step stops, says why, and leaves it to the owner.

.DESCRIPTION
    scripts/team/integrate.ps1 gates an integration branch, merges it into main and leaves its
    tasks 'awaiting_release' with main's sha. The scheduled task runs this after it:

      1. The tasks 'awaiting_release' whose 40-hex sha IS origin/main's tip (fetched now) are the
         release; awaiting tasks whose sha is an ancestor of it go with it. Nothing else acts.
      2. Before anything is run - not even a look at the host - the evidence: the integrate
         step's green record for that sha (team/reports/<cycle>/gate-<n>.json, `main` = sha) and
         its log beside it saying QUALITY GATE: PASS. No record, a FAIL log, a record of another
         sha: stop. team/release-blocked.json (an earlier automatic release was rolled back or
         could not be verified, and the lead has not removed it): stop. The team lock held by
         another release or cycle: stop.
      3. The lock is taken; the host is read ONCE over ssh (read-only: markers, the maintenance
         marker and window, health through the edge, the reconcile's last line) and the diff
         between what production serves and the sha is read. Get-TeamReleaseDecision stops on
         ANY changed migration (the Danışman releases those, 2026-10-04; the expand-only analyzer
         is only an information line), a changed prod compose or edge, health not ok, a
         maintenance marker or a window within 30 minutes (scripts/lib/TeamRelease.ps1).
         A stop writes the reason into the report and into every task, which stays
         'awaiting_release' - where the Onay Merkezi shows it to the owner.
      4. Otherwise, from a clean worktree at that sha (.claude/worktrees/release/<sha>, never the
         main checkout): the release script with -BlueGreen -Preflight, then -BlueGreen, then over
         ssh install-recovery-supervisor.sh <the full sha>, then the probe again until RELEASE,
         APPROVED_SHA, the reconcile's last line and health through the edge all name the sha.
         Every command's stdout and stderr go to SEPARATE files,
         team/reports/<cycle>/release-<n>.<step>.out / .err: nothing here merges the two streams
         (nginx's routine stderr, merged, once became a terminating error and half-promoted the
         host).
      5. Success: the tasks become 'released', release_approved by 'standing_rule'.
         A failed release (the release script's own rollback ran), a pin that failed or a
         verification that does not hold: 'geri alındı' / the failure is recorded, the tasks stay
         'awaiting_release' and team/release-blocked.json is written - every later run stops on
         it until the lead removes it. This step never improvises a rollback of its own.

    -DryRun reads (the gate records, the host probe) and prints the decision; it writes nothing,
    takes no lock and runs no release.

    Exit codes: 0 released, or nothing to release; 2 the queue or a parameter breaks the protocol;
    3 the lock is held; 5 stopped by a rule (the owner decides); 6 the release failed or could not
    be verified (team/release-blocked.json written); 7 the preflight failed (nothing changed);
    12 an unexpected error, or the queue could not be written.

.PARAMETER ReleaseScript
    The release script to run in the release worktree. Empty (the default) is the worktree's own
    scripts\cloud\release-cloud-core.ps1. The tests name scripts/tests/lib/fake-release.ps1.

.PARAMETER SshPrefixArguments
    Arguments put before ssh's own (the tests run the fake through powershell.exe -File).

.EXAMPLE
    .\scripts\team\release.ps1 -DryRun
    .\scripts\team\release.ps1 -QueueUrl https://core.example/ -QueueToken C:\path\token.txt
#>
[CmdletBinding()]
param(
    [string]$TeamRoot = "",
    [string]$Base = "main",
    [string]$Remote = "origin",
    [string]$BrokerHost = "pagentos-core",
    [string]$CloudUser = "root",
    [string]$HostBase = "/opt/pagentos",
    [string]$RecoveryRoot = "/opt/pagentos-recovery",
    [string]$EdgeDir = "/mnt/pagentos-data/edge",
    [string]$HostHealthUrl = "http://127.0.0.1:8001/v1/system/health",
    [string]$ReleaseScript = "",
    [string]$SshPath = (Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"),
    [string[]]$SshPrefixArguments = @(),
    [int]$SshConnectTimeoutSec = 20,
    # The release script's whole transaction (build, migrate, switch, its own health wait).
    [double]$ReleaseMinutes = 60,
    [int]$VerifyTries = 6,
    [int]$VerifyWaitSeconds = 20,
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$DryRun,
    [string]$QueueUrl = "",
    # A PATH to the file holding the owner-session token; a token is never a parameter.
    [string]$QueueToken = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRelease.ps1")

# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$teamDir = if ($TeamRoot) { $TeamRoot } else { Join-Path $repoRoot "team" }
$queuePath = Join-Path $teamDir "queue.json"
$lockPath = Join-Path $teamDir "lock.json"
$reportsRoot = Join-Path $teamDir "reports"
$blockedPath = Join-Path $teamDir $script:TeamReleaseBlockedName
$blockedName = "team/$($script:TeamReleaseBlockedName)"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)

if ($CloudUser -cnotmatch '^[A-Za-z_][A-Za-z0-9_-]{0,31}$') { Write-Host "unsafe -CloudUser '$CloudUser'; nothing was done"; exit 2 }
if ($BrokerHost -cnotmatch '^[A-Za-z0-9.-]+$') { Write-Host "unsafe -BrokerHost '$BrokerHost'; nothing was done"; exit 2 }
if ($HostBase -cnotmatch '^/[A-Za-z0-9_./-]+$') { Write-Host "unsafe -HostBase '$HostBase'; nothing was done"; exit 2 }
if ($ReleaseMinutes -le 0 -or $VerifyTries -lt 1 -or $VerifyWaitSeconds -lt 0) { Write-Host "-ReleaseMinutes > 0, -VerifyTries >= 1, -VerifyWaitSeconds >= 0; nothing was done"; exit 2 }
$probeCommand = Get-TeamHostProbeCommand -HostBase $HostBase -RecoveryRoot $RecoveryRoot -EdgeDir $EdgeDir -HealthUrl $HostHealthUrl

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was done:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$env:GIT_TERMINAL_PROMPT = "0"
$startedAt = Get-TeamTimestamp

# ------------------------------------------------------------------ 1. what waits, and main's tip

$awaiting = @(Get-TeamTasks -Queue $queue | Where-Object {
        [string]$_.state -eq "awaiting_release" -and [string](Get-TeamProperty -InputObject $_ -Name "sha" -Default "") -cmatch '^[0-9a-f]{40}$' })
if (@($awaiting).Count -eq 0) {
    Write-Host "nothing to release: no task is awaiting_release with a sha"
    exit 0
}
$fetched = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("fetch", "--quiet", $Remote, "refs/heads/${Base}:refs/remotes/$Remote/$Base") -TimeoutSeconds 300
$tip = if ($fetched.Success) { Get-TeamRevision -RepoRoot $repoRoot -Revision "refs/remotes/$Remote/$Base" } else { "" }
if (-not $tip) {
    Write-Host "$Remote/$Base could not be read ($((($fetched.StdErr) -replace '\s+', ' ').Trim())); nothing was done"
    exit 12
}
$atTip = @($awaiting | Where-Object { [string]$_.sha -eq $tip })
if (@($atTip).Count -eq 0) {
    Write-Host "nothing to release: no awaiting_release task is at $Remote/$Base's tip $tip (waiting: $((@($awaiting) | ForEach-Object { "$($_.id)@$($_.sha.Substring(0, 7))" }) -join ', '))"
    exit 0
}
$tasks = @($atTip) + @($awaiting | Where-Object { [string]$_.sha -ne $tip -and (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor ([string]$_.sha) -Of $tip) })
$ids = @($tasks | ForEach-Object { [string]$_.id })

# ------------------------------------------------------------------ 2. the evidence, before anything is run

$gate = Find-TeamReleaseGate -ReportsRoot $reportsRoot -Sha $tip
$cycleId = [string]$gate.CycleId
if (-not $cycleId) {
    foreach ($task in $atTip) {
        try { $cycleId = Get-TeamIntegrationCycleId -Branch ([string](Get-TeamProperty -InputObject $task -Name "integration_branch" -Default "")); break } catch { }
    }
}
if (-not $cycleId) { $cycleId = "release" }
$reportDir = Join-Path $reportsRoot $cycleId
$number = Get-TeamReleaseNextNumber -Directory $reportDir

$lock = $null
if ($useApi) { $lock = Get-TeamLockApi -Store $apiStore }
elseif (Test-Path -LiteralPath $lockPath) { $lock = Read-TeamJson -Path $lockPath }
$lockDecision = Get-TeamLockDecision -Lock $lock -Machine $Machine
if ($lockDecision.Kind -eq "ours") {
    $holderPid = [int](Get-TeamProperty -InputObject $lock -Name "pid" -Default 0)
    $alive = $false
    if ($holderPid -gt 0) { $alive = ($null -ne (Get-Process -Id $holderPid -ErrorAction SilentlyContinue)) }
    if (-not $alive) { $lockDecision = [pscustomobject]@{ MayRun = $true; Kind = "dead"; Holder = $lockDecision.Holder; Since = $lockDecision.Since } }
}

$facts = [pscustomobject]@{
    Sha = $tip; MainTip = $tip; Gate = $gate
    Blocked = $(if (Test-Path -LiteralPath $blockedPath) { $blockedName } else { "" })
    Lock = $lockDecision; Host = $null; Diff = $null
}

function Save-Queue {
    if ($useApi) { Save-TeamQueueApi -Store $apiStore -Queue $script:queue }
    else { Write-TeamJson -Path $queuePath -Document $script:queue }
}

function Set-TaskReason {
    param($Task, [string]$Reason)
    $text = if ($Reason.Length -gt 900) { $Reason.Substring(0, 897) + "..." } else { $Reason }
    Set-TeamProperty -InputObject $Task -Name "reason" -Value $text
    Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
}

function Save-Report {
    param([string]$Result, [string]$Colour = "", [string]$LastKnownGood = "", [string[]]$Lines = @())
    if (-not (Test-Path -LiteralPath $reportDir)) { [void](New-Item -ItemType Directory -Force -Path $reportDir) }
    $text = New-TeamReleaseReport -Machine $Machine -StartedAt $startedAt -Result $Result -Sha $tip -Colour $Colour -LastKnownGood $LastKnownGood -Tasks $ids -Lines $Lines
    $name = "release-$number.md"
    [System.IO.File]::WriteAllText((Join-Path $reportDir $name), $text + "`n", $utf8)
    if ($useApi) {
        try { Send-TeamReportApi -Store $apiStore -Name "$cycleId-$name" -Text $text }
        catch { Write-Host "the report was not posted to the queue store: $($_.Exception.Message)" }
    }
}

function Stop-Release {
    <# A rule stopped it: the reason goes into the report and into every task, which keeps waiting for the owner. #>
    param($Decision)
    $why = [string]$Decision.Reason
    $exit = if (@($Decision.Reasons | Where-Object { [string]$_.Code -ne "lock" }).Count -eq 0) { 3 } else { 5 }
    foreach ($task in $tasks) {
        Set-TaskReason -Task $task -Reason "otomatik yayın durdu ($(Get-TeamTimestamp)): $why | Onay Merkezi: sahibin kararı bekleniyor (ADR-0214 ek 9)"
    }
    $code = $exit
    try { Save-Queue } catch { Write-Host "the queue could not be written: $($_.Exception.Message)"; $code = 12 }
    Save-Report -Result "durdu - sahibe bırakıldı" -Lines (@(@($Decision.Reasons) | ForEach-Object { "durdu: $($_.Text)" }) + @(Get-TeamProperty -InputObject $Decision -Name "Notes" -Default @()))
    Write-Host "stopped: $why"
    exit $code
}

function Invoke-Logged {
    <#
        One command: its stdout and its stderr each to a file of their own
        (release-<n>.<name>.out / .err) - never merged. A command that cannot be started or
        times out is a failure with its words in .err.
    #>
    param([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory = "", [int]$TimeoutSeconds = 120, [switch]$NoFiles)
    try {
        if ($WorkingDirectory) { $ran = Invoke-NativeProcess -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds }
        else { $ran = Invoke-NativeProcess -FilePath $FilePath -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds }
    }
    catch { $ran = [pscustomobject]@{ ExitCode = -1; StdOut = ""; StdErr = [string]$_.Exception.Message; Success = $false } }
    if (-not $NoFiles) {
        if (-not (Test-Path -LiteralPath $reportDir)) { [void](New-Item -ItemType Directory -Force -Path $reportDir) }
        $stem = Join-Path $reportDir "release-$number.$Name"
        [System.IO.File]::WriteAllText("$stem.out", [string]$ran.StdOut, $utf8)
        [System.IO.File]::WriteAllText("$stem.err", [string]$ran.StdErr, $utf8)
    }
    return $ran
}

function Invoke-Ssh {
    param([string]$Name, [string]$Command, [int]$TimeoutSeconds = 120, [switch]$NoFiles)
    $arguments = @($SshPrefixArguments) + @("-n", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=$SshConnectTimeoutSec", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", "${CloudUser}@${BrokerHost}", $Command)
    return (Invoke-Logged -Name $Name -FilePath $SshPath -Arguments $arguments -TimeoutSeconds $TimeoutSeconds -NoFiles:$NoFiles)
}

function Read-HostState {
    param([string]$Name, [switch]$NoFiles)
    $ran = Invoke-Ssh -Name $Name -Command $probeCommand -NoFiles:$NoFiles
    return (Read-TeamHostProbe -Text ([string]$ran.StdOut) -ExitCode ([int]$ran.ExitCode))
}

$local = Get-TeamReleaseDecision -Facts $facts -LocalOnly
if ($DryRun) {
    Write-Host "DRY RUN: nothing is changed. Tasks: $($ids -join ', ') at $Remote/$Base $tip"
    if ($local.Action -eq "stop") { Write-Host "  would stop (nothing would be run): $($local.Reason)"; exit 0 }
    $facts.Host = Read-HostState -Name "probe" -NoFiles
    $facts.Diff = Get-TeamReleaseDiff -RepoRoot $repoRoot -From ([string]$facts.Host.Release) -To $tip
    $decision = Get-TeamReleaseDecision -Facts $facts
    if ($decision.Action -eq "stop") { Write-Host "  would stop: $($decision.Reason)" }
    else {
        Write-Host "  would release: preflight, release -BlueGreen, install-recovery-supervisor.sh $tip, verify (production serves $($facts.Host.Release) on $($facts.Host.Colour))"
    }
    exit 0
}
if ($local.Action -eq "stop") { Stop-Release -Decision $local }

# ------------------------------------------------------------------ 3. the lock, the host, the diff

$lockCycle = "release-" + $tip.Substring(0, 12)
if ($useApi) {
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle -TakeoverDead ($lockDecision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        $facts.Lock = [pscustomobject]@{ MayRun = $false; Kind = "held"; Holder = [string]$taken.holder; Since = [string]$taken.since }
        Stop-Release -Decision (Get-TeamReleaseDecision -Facts $facts -LocalOnly)
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $lockCycle) }

$worktree = Join-Path $repoRoot (".claude\worktrees\release\" + $tip.Substring(0, 12))
$exitCode = 12
try {
    $facts.Host = Read-HostState -Name "probe"
    $facts.Diff = Get-TeamReleaseDiff -RepoRoot $repoRoot -From ([string]$facts.Host.Release) -To $tip
    $decision = Get-TeamReleaseDecision -Facts $facts
    if ($decision.Action -eq "stop") { Stop-Release -Decision $decision }
    $before = $facts.Host
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("önce: üretim $($before.Release) ($($before.Colour)), sağlık $($before.HealthStatus); kapı kaydı $($gate.Log)")

    # ---- 4. a clean worktree at the sha - never the main checkout
    if (Test-Path -LiteralPath $worktree) { [void](Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("worktree", "remove", "--force", $worktree)) }
    [void](Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("worktree", "prune"))
    $added = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("worktree", "add", "--detach", "--quiet", $worktree, $tip)
    if (-not $added.Success) { throw "the release worktree could not be made: $((($added.StdErr) -replace '\s+', ' ').Trim())" }
    $releaseFile = if ($ReleaseScript) { $ReleaseScript } else { Join-Path $worktree "scripts\cloud\release-cloud-core.ps1" }
    $common = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $releaseFile, "-BlueGreen", "-RepoRoot", $worktree,
        "-BrokerHost", $BrokerHost, "-CloudUser", $CloudUser, "-HostBase", $HostBase)
    $releaseSeconds = [int][Math]::Ceiling($ReleaseMinutes * 60)

    $failure = ""
    $failed = ""
    $preflight = Invoke-Logged -Name "preflight" -FilePath $powershell -Arguments ($common + @("-Preflight")) -WorkingDirectory $worktree -TimeoutSeconds $releaseSeconds
    if (-not $preflight.Success) {
        $why = "ön kontrol başarısız (çıkış kodu $($preflight.ExitCode)); üretimde hiçbir şey değişmedi; kayıt team/reports/$cycleId/release-$number.preflight.out/.err"
        foreach ($task in $tasks) { Set-TaskReason -Task $task -Reason "otomatik yayın: $why | Onay Merkezi: sahibin kararı bekleniyor" }
        [void]$lines.Add($why)
        $exitCode = 7
        try { Save-Queue } catch { $exitCode = 12; [void]$lines.Add("kuyruk yazılamadı: $($_.Exception.Message)") }
        Save-Report -Result "ön kontrol başarısız - yayın yapılmadı" -Colour $before.Colour -LastKnownGood $before.Lkg -Lines @($lines.ToArray())
        Write-Host "preflight failed: $why"
        exit $exitCode
    }
    [void]$lines.Add("ön kontrol: tamam")

    $release = Invoke-Logged -Name "release" -FilePath $powershell -Arguments $common -WorkingDirectory $worktree -TimeoutSeconds $releaseSeconds
    if (-not $release.Success) {
        $failure = "geri alındı: yayın betiği çıkış kodu $($release.ExitCode) ile bitti, kendi geri alma yolu çalıştı (kayıt team/reports/$cycleId/release-$number.release.out/.err)"
        $failed = "geri alındı"
    }
    else {
        [void]$lines.Add("yayın betiği: tamam (-BlueGreen)")
        $pin = Invoke-Ssh -Name "pin" -Command "bash '$HostBase/app/scripts/cloud/install-recovery-supervisor.sh' $tip" -TimeoutSeconds 1800
        if (-not $pin.Success) {
            $failure = "kurtarma sabitlemesi kurulamadı: install-recovery-supervisor.sh çıkış kodu $($pin.ExitCode) (kayıt release-$number.pin.out/.err)"
            $failed = "doğrulanamadı"
        }
        else {
            [void]$lines.Add("kurtarma sabitlemesi: install-recovery-supervisor.sh $tip")
            $verified = $null
            $after = $null
            for ($try = 1; $try -le $VerifyTries; $try++) {
                $after = Read-HostState -Name "verify-$try"
                $verified = Test-TeamReleaseVerified -Probe $after -Sha $tip
                if ($verified.Ok) { break }
                if ($try -lt $VerifyTries -and $VerifyWaitSeconds -gt 0) { Start-Sleep -Seconds $VerifyWaitSeconds }
            }
            if (-not $verified.Ok) {
                $failure = "doğrulama tutmadı ($VerifyTries deneme): " + (@($verified.Problems) -join "; ")
                $failed = "doğrulanamadı"
            }
            else {
                # ---- 5. released
                $stamp = Get-TeamTimestamp
                foreach ($task in $tasks) {
                    Set-TeamProperty -InputObject $task -Name "state" -Value "released"
                    Set-TeamProperty -InputObject $task -Name "release_approved" -Value $true
                    Set-TeamProperty -InputObject $task -Name "release_approved_at" -Value $stamp
                    Set-TeamProperty -InputObject $task -Name "release_approved_by" -Value $script:TeamReleaseApprover
                    Set-TaskReason -Task $task -Reason "yayinlandi $stamp, main $tip ($($after.Colour))"
                }
                [void]$lines.Add("doğrulandı: RELEASE = APPROVED_SHA = $tip, $($after.Reconcile), edge sağlığı ok")
                $exitCode = 0
                try { Save-Queue } catch { $exitCode = 12; [void]$lines.Add("kuyruk yazılamadı: $($_.Exception.Message); bir sonraki adım doğrulayıp yazar") }
                Save-Report -Result "yayınlandı" -Colour $after.Colour -LastKnownGood $after.Lkg -Lines @($lines.ToArray())
                Write-Host "released: $tip on api-$($after.Colour) (last known good $($after.Lkg)); tasks: $($ids -join ', ')"
            }
        }
    }

    if ($failure) {
        # The release script's own path did (or did not need to do) the rollback; this step only
        # records it and blocks the next automatic release until the lead looked.
        $marker = [pscustomobject]@{ sha = $tip; at = (Get-TeamTimestamp); result = $failed; why = $failure; report = "team/reports/$cycleId/release-$number.md" }
        Write-TeamJson -Path $blockedPath -Document $marker
        foreach ($task in $tasks) {
            Set-TaskReason -Task $task -Reason "otomatik yayın $failed ($(Get-TeamTimestamp)): $failure | $blockedName konuldu: lead kaldırana kadar otomatik yayın durur | Onay Merkezi: sahibin kararı bekleniyor"
        }
        [void]$lines.Add($failure)
        [void]$lines.Add("$blockedName konuldu: lead bakıp kaldırana kadar otomatik yayın durur")
        $exitCode = 6
        try { Save-Queue } catch { [void]$lines.Add("kuyruk yazılamadı: $($_.Exception.Message)") }
        $now = Read-HostState -Name "after"
        Save-Report -Result "başarısız - $failed" -Colour $now.Colour -LastKnownGood $now.Lkg -Lines @($lines.ToArray())
        Write-Host "release failed ($failed): $failure"
    }
}
finally {
    if (Test-Path -LiteralPath $worktree) {
        [void](Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("worktree", "remove", "--force", $worktree))
        [void](Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("worktree", "prune"))
    }
    if ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
}
exit $exitCode
