<#
.SYNOPSIS
    The automatic release of the agent team (scripts/team/release.ps1, ADR-0214 addendum 9):
    gated roadmap work that reached main is released blue/green, pinned and verified - or the
    step stops, says why, and leaves it to the owner.

.DESCRIPTION
    Two halves, as in team-integrate.tests.ps1.

    The decisions (scripts/lib/TeamRelease.ps1) are driven as functions: which migration is
    expand-only, what the host probe says, when a release stops, what the verification wants.

    The step itself is run for real, in a git repository made for the test (with a bare
    "origin"), with scripts/tests/lib/fake-release.ps1 in place of BOTH the release script and
    ssh.exe. The host is a folder; the fake writes every call it gets into it. Nothing here
    reaches a host, and this repository's own branches, worktrees and team/ files are not
    written to.

    Run: powershell -NoProfile -File scripts\tests\team-release.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")
$releaseLib = Join-Path $repoRoot "scripts\lib\TeamRelease.ps1"
$releaseScript = Join-Path $repoRoot "scripts\team\release.ps1"
$fakeRelease = Join-Path $repoRoot "scripts\tests\lib\fake-release.ps1"
if (Test-Path -LiteralPath $releaseLib) { . $releaseLib }

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

$shaA = "a" * 40
$shaB = "b" * 40
$shaC = "c" * 40

function New-Probe {
    param(
        [string]$Release = $shaA, [string]$Pin = $shaA, [string]$Lkg = $shaC, [string]$Colour = "blue",
        [string]$Marker = "no", [string]$InSeconds = "none", [string]$Health = "ok", [string]$HealthRelease = $shaA,
        [string]$Reconcile = "", [int]$ExitCode = 0
    )
    if (-not $Reconcile) { $Reconcile = "RECONCILE OK: api-$Colour is canonical (release $Release); markers, upstreams and containers agree" }
    $body = ConvertTo-Json -Compress -InputObject ([ordered]@{ status = $Health; release = [ordered]@{ version = $HealthRelease } })
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($body))
    $text = @("release=$Release", "app_release=$Release", "pin=$Pin", "lkg=$Lkg", "colour=$Colour", "maintenance_marker=$Marker",
        "maintenance_in_s=$InSeconds", "health_b64=$b64", "reconcile=$Reconcile") -join "`n"
    return (Read-TeamHostProbe -Text $text -ExitCode $ExitCode)
}

function New-Facts {
    <# Every fact of a release that may go: a test changes one. #>
    param([hashtable]$Change = @{})
    $facts = [ordered]@{
        Sha     = $shaB
        MainTip = $shaB
        Gate    = [pscustomobject]@{ Found = $true; Pass = $true; Why = "" }
        Blocked = ""
        Lock    = [pscustomobject]@{ MayRun = $true; Kind = "free"; Holder = ""; Since = "" }
        Host    = (New-Probe)
        Diff    = [pscustomobject]@{ Readable = $true; Why = ""; Files = @(); Migrations = @() }
    }
    foreach ($name in @($Change.Keys)) { $facts[$name] = $Change[$name] }
    return [pscustomobject]$facts
}

function Get-StopCodes {
    param($Decision)
    return ((@($Decision.Reasons) | ForEach-Object { [string]$_.Code }) -join ",")
}

$expandOnly = @'
"""Expand-only: a nullable column, a table and an index.

The downgrade drops them again.
"""
import sqlalchemy as sa
from alembic import op

revision = "0066_expand"
down_revision = "0065_misheard_utterances"


def upgrade() -> None:
    op.add_column("notes", sa.Column("colour", sa.String(length=16), nullable=True))
    op.create_table("labels", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("name", sa.String(64), nullable=False))
    op.create_index("ix_labels_name", "labels", ["name"])


def downgrade() -> None:
    op.drop_index("ix_labels_name", table_name="labels")
    op.drop_table("labels")
    op.drop_column("notes", "colour")
'@

$dropColumn = @'
import sqlalchemy as sa
from alembic import op

revision = "0066_contract"
down_revision = "0065_misheard_utterances"


def upgrade() -> None:
    op.drop_column("notes", "colour")


def downgrade() -> None:
    op.add_column("notes", sa.Column("colour", sa.String(length=16), nullable=True))
'@

# ============================================================================ the decisions

Write-Host ""
Write-Host "which migration is expand-only (read conservatively: an unreadable one stops)"

Test-Case "add_column nullable, create_table and create_index are expand-only; what the DOWNGRADE drops is not read" {
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $expandOnly
    Assert-True -Condition $verdict.ExpandOnly -Because "an expand-only migration: $($verdict.Why)"
}

Test-Case "drop_column, drop_table, alter_column with a type or nullable change, a rename, and DELETE/UPDATE in op.execute are not" {
    $cases = @{
        "drop_column"   = 'op.drop_column("notes", "colour")'
        "drop_table"    = 'op.drop_table("notes")'
        "batch drop"    = "with op.batch_alter_table(`"notes`") as batch:`n        batch.drop_column(`"colour`")"
        "alter type"    = "op.alter_column(`n        `"notes`", `"colour`",`n        type_=sa.String(32),`n    )"
        "alter null"    = 'op.alter_column("notes", "colour", existing_type=sa.String(16), nullable=False)'
        "rename column" = 'op.alter_column("notes", "colour", new_column_name="hue")'
        "rename table"  = 'op.rename_table("notes", "memos")'
        "delete"        = 'op.execute("DELETE FROM notes WHERE colour IS NULL")'
        "update"        = "op.execute(sa.text(`"update notes set colour = 'red'`"))"
        "not null add"  = 'op.add_column("notes", sa.Column("colour", sa.String(16), nullable=False))'
    }
    foreach ($name in @($cases.Keys)) {
        $text = "from alembic import op`nimport sqlalchemy as sa`n`n`ndef upgrade() -> None:`n    " + $cases[$name] + "`n`n`ndef downgrade() -> None:`n    pass`n"
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "$name is not expand-only"
        Assert-True -Condition ([bool]$verdict.Why) -Because "$name says why"
    }
}

Test-Case "an alter_column that changes only a comment or a server default, and an UPDATE inside a column NAME, stay expand-only" {
    $text = "from alembic import op`n`n`ndef upgrade() -> None:`n    op.alter_column(`"notes`", `"colour`", comment=`"the colour`")`n    op.add_column(`"notes`", sa.Column(`"updated_at`", sa.DateTime(), nullable=True))`n`n`ndef downgrade() -> None:`n    pass`n"
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
    Assert-True -Condition $verdict.ExpandOnly -Because "a comment and a nullable column: $($verdict.Why)"
}

Test-Case "an unreadable migration, one without upgrade(), and a CHANGED or DELETED existing migration stop" {
    foreach ($text in @("", $null)) {
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text $text
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "no text is not expand-only"
    }
    $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status "A" -Text "revision = 'x'`n"
    Assert-True -Condition (-not $verdict.ExpandOnly) -Because "no upgrade() cannot be read"
    foreach ($status in @("M", "D", "R100", "T")) {
        $verdict = Get-TeamMigrationVerdict -Path "services/api/alembic/versions/x.py" -Status $status -Text $expandOnly
        Assert-True -Condition (-not $verdict.ExpandOnly) -Because "status $status of an existing migration stops"
    }
}

Test-Case "only alembic versions are migrations" {
    Assert-True -Condition (Test-TeamMigrationPath -Path "services/api/alembic/versions/20261003_0066_x.py") -Because "a version file"
    Assert-True -Condition (-not (Test-TeamMigrationPath -Path "services/api/alembic/env.py")) -Because "env.py is not a version"
    Assert-True -Condition (-not (Test-TeamMigrationPath -Path "services/api/app/models.py")) -Because "a model is not a migration"
}

Write-Host ""
Write-Host "what the host probe says"

Test-Case "the probe is parsed: markers, the colour, the maintenance marker and window, the health body and the reconcile line" {
    $probe = New-Probe -Release $shaA -Pin $shaB -Colour "green" -Marker "yes" -InSeconds "1200" -Health "degraded" -HealthRelease $shaA
    Assert-True -Condition $probe.Ok -Because "a probe that answered"
    Assert-Equal -Expected $shaA -Actual $probe.Release -Because "RELEASE"
    Assert-Equal -Expected $shaB -Actual $probe.Pin -Because "APPROVED_SHA"
    Assert-Equal -Expected "green" -Actual $probe.Colour -Because "the colour"
    Assert-True -Condition $probe.MaintenanceMarker -Because "the marker"
    Assert-Equal -Expected "1200" -Actual $probe.MaintenanceInSeconds -Because "seconds to the window (the HOST's clock)"
    Assert-Equal -Expected "degraded" -Actual $probe.HealthStatus -Because "the health status"
    Assert-Equal -Expected $shaA -Actual $probe.HealthRelease -Because "the served release"
    $failed = Read-TeamHostProbe -Text "" -ExitCode 255
    Assert-True -Condition (-not $failed.Ok) -Because "ssh that did not answer is not a probe"
}

Test-Case "the probe command reads only: no redirection into a file, no systemctl start/stop, no rm" {
    $command = Get-TeamHostProbeCommand -HostBase "/opt/pagentos"
    Assert-True -Condition ($command -notmatch '(^|[^2])>\s*[/$]' -and $command -notmatch '\brm\b' -and $command -notmatch 'systemctl (start|stop|restart|enable|disable)') -Because $command
    Assert-True -Condition ($command -match 'pagentos-maintenance-window\.timer' -and $command -match 'MAINTENANCE_MARKER' -and $command -match 'APPROVED_SHA') -Because "it reads what the decision needs: $command"
    Assert-True -Condition ($command -notmatch '2>&1') -Because "no merged streams"
}

Write-Host ""
Write-Host "when the release goes and when it stops (Get-TeamReleaseDecision)"

Test-Case "everything in order: release" {
    $decision = Get-TeamReleaseDecision -Facts (New-Facts)
    Assert-Equal -Expected "release" -Actual $decision.Action -Because (Get-StopCodes $decision)
}

Test-Case "each rule stops on its own, with its own reason" {
    $marker = New-Probe -Marker "yes"
    $soon = New-Probe -InSeconds "1500"
    $unknownWindow = New-Probe -InSeconds "unknown"
    $sick = New-Probe -Health "degraded"
    $unread = Read-TeamHostProbe -Text "" -ExitCode 255
    $migration = [pscustomobject]@{ Path = "services/api/alembic/versions/x.py"; ExpandOnly = $false; Why = "drop_column" }
    $cases = [ordered]@{
        "not_tip"            = @{ MainTip = $shaC }
        "no_gate"            = @{ Gate = [pscustomobject]@{ Found = $false; Pass = $false; Why = "kapı kaydı yok" } }
        "gate_not_pass"      = @{ Gate = [pscustomobject]@{ Found = $true; Pass = $false; Why = "FAIL" } }
        "blocked"            = @{ Blocked = "team/release-blocked.json" }
        "lock"               = @{ Lock = [pscustomobject]@{ MayRun = $false; Kind = "held"; Holder = "OTHER"; Since = "2026-10-03T10:00:00Z" } }
        "host_unread"        = @{ Host = $unread }
        "health"             = @{ Host = $sick }
        "maintenance_marker" = @{ Host = $marker }
        "maintenance_window" = @{ Host = $soon }
        "maintenance_unknown" = @{ Host = $unknownWindow }
        "diff_unreadable"    = @{ Diff = [pscustomobject]@{ Readable = $false; Why = "git diff failed"; Files = @(); Migrations = @() } }
        "migration"          = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("services/api/alembic/versions/x.py"); Migrations = @($migration) } }
        "compose"            = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("infra/docker/docker-compose.prod.yml"); Migrations = @() } }
        "edge"               = @{ Diff = [pscustomobject]@{ Readable = $true; Why = ""; Files = @("infra/docker/edge/nginx.conf"); Migrations = @() } }
    }
    foreach ($code in @($cases.Keys)) {
        $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change $cases[$code])
        Assert-Equal -Expected "stop" -Actual $decision.Action -Because "$code stops"
        Assert-Equal -Expected $code -Actual (Get-StopCodes $decision) -Because "only $code is named"
        Assert-True -Condition ([bool]$decision.Reason) -Because "$code says why"
    }
}

Test-Case "a window more than 30 minutes away, or one that has passed, does not stop it; 30 minutes exactly does" {
    foreach ($seconds in @("1801", "-60", "none")) {
        Assert-Equal -Expected "release" -Actual (Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = (New-Probe -InSeconds $seconds) })).Action -Because "$seconds s"
    }
    Assert-Equal -Expected "stop" -Actual (Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = (New-Probe -InSeconds "1800") })).Action -Because "1800 s"
}

Test-Case "-LocalOnly judges what is known before the host is read; the host's rules are not asked" {
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null }) -LocalOnly
    Assert-Equal -Expected "release" -Actual $decision.Action -Because (Get-StopCodes $decision)
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null; Gate = [pscustomobject]@{ Found = $false; Pass = $false; Why = "x" } }) -LocalOnly
    Assert-Equal -Expected "no_gate" -Actual (Get-StopCodes $decision) -Because "a local rule still stops"
    $decision = Get-TeamReleaseDecision -Facts (New-Facts -Change @{ Host = $null; Diff = $null })
    Assert-Equal -Expected "stop" -Actual $decision.Action -Because "without -LocalOnly a host that was never read is not 'in order'"
}

Test-Case "the verification wants RELEASE, APPROVED_SHA, the reconcile's last line and the edge's health on the sha" {
    $good = New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Colour "green"
    Assert-True -Condition (Test-TeamReleaseVerified -Probe $good -Sha $shaB).Ok -Because "all four agree"
    $checks = @{
        "RELEASE"      = (New-Probe -Release $shaA -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE OK: api-green is canonical (release $shaB); x")
        "APPROVED_SHA" = (New-Probe -Release $shaB -Pin $shaA -HealthRelease $shaB)
        "RECONCILE"    = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE DEGRADED: api-green ($shaB) stays canonical")
        "health"       = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Health "degraded")
        "edge"         = (New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaA)
    }
    foreach ($name in @($checks.Keys)) {
        $verified = Test-TeamReleaseVerified -Probe $checks[$name] -Sha $shaB
        Assert-True -Condition (-not $verified.Ok) -Because "$name wrong is not verified"
    }
    $old = New-Probe -Release $shaB -Pin $shaB -HealthRelease $shaB -Reconcile "RECONCILE OK: api-blue is canonical (release $shaA); x"
    Assert-True -Condition (-not (Test-TeamReleaseVerified -Probe $old -Sha $shaB).Ok) -Because "a RECONCILE OK for ANOTHER sha is not the reconcile of this release"
}

# ============================================================================ the step

Write-Host ""
Write-Host "the step, in a repository of its own, with the fake release script and the fake ssh"

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandboxes = New-Object System.Collections.ArrayList
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

$greenLog = "=== Required files ===`nall present`n`n=== Quality gate summary ===`n`nStep           Result Seconds`nRequired files PASS       0.1`n`nQUALITY GATE: PASS`n"
$redLog = "=== API unit tests ===`nFAILED tests/unit/test_x.py::test_y - AssertionError`nFAILED: pytest (unit) exited with code 1`n`n=== Quality gate summary ===`n`nQUALITY GATE: FAIL`n"

function New-Sandbox {
    <#
        A repository whose first commit is what the host serves, and whose second - main's tip,
        pushed to a bare origin beside it - is what waits for the release, with -Files in it.
        team/reports/c1/gate-1.json + .log as the integrate step leaves them (-Gate).
        The host is <root>-host\state.json.
    #>
    param(
        [hashtable]$Files = @{}, [string]$Gate = "pass", $Lock = $null, [switch]$Blocked,
        [hashtable]$HostChange = @{}
    )
    $root = Join-Path $env:TEMP ("pagentos-rel-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $hostDir = "$root-host"
    [void]$sandboxes.Add($root); [void]$sandboxes.Add($hostDir); [void]$sandboxes.Add("$root-origin.git")
    foreach ($folder in @("scripts\lib", "scripts\team", "team", "src")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder)) }
    [void](New-Item -ItemType Directory -Force -Path $hostDir)
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamIntegrate.ps1", "TeamRelease.ps1")) {
        $from = Join-Path $repoRoot "scripts\lib\$name"
        if (Test-Path -LiteralPath $from) { Copy-Item -LiteralPath $from -Destination (Join-Path $root "scripts\lib\$name") }
    }
    if (Test-Path -LiteralPath $releaseScript) { Copy-Item -LiteralPath $releaseScript -Destination (Join-Path $root "scripts\team\release.ps1") }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/`nteam/reports/`nteam/release-blocked.json`nteam/queue.json`nteam/lock.json" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\app.txt") -Value "served" -Encoding ASCII
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "what the host serves"))
    $served = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "HEAD")
    Add-Content -LiteralPath (Join-Path $root "src\app.txt") -Value "the gated change" -Encoding ASCII
    foreach ($relative in @($Files.Keys)) {
        $target = Join-Path $root ($relative -replace "/", "\")
        $folder = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        [System.IO.File]::WriteAllText($target, [string]$Files[$relative], $utf8)
    }
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "merge: integrate/c1 (gated) into main"))
    $tip = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "HEAD")
    [void](Invoke-TeamGit -WorkingDirectory $hostDir -Arguments @("init", "-q", "--bare", "$root-origin.git"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("remote", "add", "origin", "$root-origin.git"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("push", "-q", "origin", "main"))

    $task = [pscustomobject]@{
        id = "task-one"; title = "the task"; roadmap_row = "row"; state = "awaiting_release"; area = @("src")
        branch = "team/c1/worker-task-one"; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-01T00:00:00Z"; updated_at = "2026-10-01T00:00:00Z"; sha = $tip; integration_branch = "integrate/c1"
        reason = "kapı yeşil: $tip; main $tip; kayıt: team/reports/c1/gate-1.log"
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @($task) })
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document $(if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased })
    if ($Blocked) { Write-TeamJson -Path (Join-Path $root "team\release-blocked.json") -Document ([pscustomobject]@{ sha = $served; why = "geri alındı" }) }

    $reports = Join-Path $root "team\reports\c1"
    [void](New-Item -ItemType Directory -Force -Path $reports)
    if ($Gate -ne "none") {
        $gatedMain = if ($Gate -eq "other-sha") { $served } else { $tip }
        Write-TeamJson -Path (Join-Path $reports "gate-1.json") -Document ([pscustomobject]@{
                n = 1; branch = "integrate/c1"; at = "2026-10-03T10:00:00Z"; result = "green"; sha = $gatedMain; main = $gatedMain; log = "team/reports/c1/gate-1.log" })
        [System.IO.File]::WriteAllText((Join-Path $reports "gate-1.log"), $(if ($Gate -eq "fail") { $redLog } else { $greenLog }), $utf8)
    }

    $state = [ordered]@{
        release = $served; app_release = $served; pin = $served; lkg = $shaC; colour = "blue"
        maintenance_marker = "no"; maintenance_in_s = "none"; health_status = "ok"; health_release = $served
        reconcile = "RECONCILE OK: api-blue is canonical (release $served); markers, upstreams and containers agree"; probe_exit = 0
    }
    foreach ($name in @($HostChange.Keys)) { $state[$name] = $HostChange[$name] }
    [System.IO.File]::WriteAllText((Join-Path $hostDir "state.json"), (ConvertTo-Json -InputObject ([pscustomobject]$state)), $utf8)
    return [pscustomobject]@{ Root = $root; Host = $hostDir; Served = $served; Tip = $tip }
}

function Invoke-Release {
    param($Box, [string]$Scenario = "ok", [string]$Extra = "")
    $set = @{ PAGENTOS_FAKE_HOST = $Box.Host; PAGENTOS_FAKE_RELEASE_SCENARIO = $Scenario }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    try {
        $command = "& '" + (Join-Path $Box.Root "scripts\team\release.ps1") + "' -Machine 'MAIL'" +
        " -ReleaseScript '$fakeRelease' -SshPath '$powershell'" +
        " -SshPrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','$fakeRelease','ssh'" +
        " -VerifyWaitSeconds 0" + $(if ($Extra) { " " + $Extra } else { "" })
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + "; exit `$LASTEXITCODE")) `
            -WorkingDirectory $Box.Root -TimeoutSeconds 300
    }
    finally { foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    $callsPath = Join-Path $Box.Host "calls.log"
    $calls = if (Test-Path -LiteralPath $callsPath) { @([System.IO.File]::ReadAllLines($callsPath, [System.Text.Encoding]::UTF8) | Where-Object { $_.Trim() }) } else { @() }
    $reports = Join-Path $Box.Root "team\reports\c1"
    $report = @(Get-ChildItem -LiteralPath $reports -Filter "release-*.md" -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1)
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Task     = @((Read-TeamJson -Path (Join-Path $Box.Root "team\queue.json")).tasks)[0]
        Calls    = @($calls)
        HostState = (ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText((Join-Path $Box.Host "state.json"), $utf8)))
        Report   = $(if (@($report).Count -gt 0) { [System.IO.File]::ReadAllText($report[0].FullName, [System.Text.Encoding]::UTF8) } else { "" })
        Files    = @(Get-ChildItem -LiteralPath $reports -Filter "release-*" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
        Blocked  = (Test-Path -LiteralPath (Join-Path $Box.Root "team\release-blocked.json"))
        Lock     = (Read-TeamJson -Path (Join-Path $Box.Root "team\lock.json"))
        Reports  = $reports
    }
}

function Assert-Stopped {
    <# The step stopped: no release command, no pin; the task waits with the reason; the report says it. #>
    param($Run, [string]$Words, [int]$ExitCode = 5)
    Assert-Equal -Expected $ExitCode -Actual $Run.ExitCode -Because $Run.Output
    $acted = @($Run.Calls | Where-Object { $_ -match '^release\|' -or $_ -match '^ssh\|pin' })
    Assert-Equal -Expected 0 -Actual @($acted).Count -Because "no release command was issued: $($Run.Calls -join ' / ')"
    Assert-Equal -Expected "awaiting_release" -Actual $Run.Task.state -Because "the task waits"
    Assert-True -Condition ([string]$Run.Task.reason -match [regex]::Escape($Words)) -Because "the task carries the reason '$Words': $($Run.Task.reason)"
    Assert-True -Condition ([string]$Run.Task.reason -match "Onay Merkezi") -Because "and says it waits for the owner: $($Run.Task.reason)"
    Assert-True -Condition ($Run.Report -match "## Yayın" -and $Run.Report -match [regex]::Escape($Words)) -Because "the report says why: $($Run.Report)"
    Assert-Equal -Expected $false -Actual ([bool](Get-TeamProperty -InputObject $Run.Task -Name "release_approved" -Default $false)) -Because "nothing was approved"
}

try {
    Test-Case "a gated main with a PASS log is released from a clean worktree at the sha, pinned with the full sha, verified, and the task is 'released' by the standing rule" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $releases = @($run.Calls | Where-Object { $_ -match '^release\|' })
        Assert-Equal -Expected 2 -Actual @($releases).Count -Because "preflight, then release: $($run.Calls -join ' / ')"
        Assert-True -Condition ($releases[0] -match '^release\|preflight\|bluegreen=True\|' -and $releases[1] -match '^release\|release\|bluegreen=True\|') -Because "preflight first, both -BlueGreen: $($releases -join ' / ')"
        foreach ($line in $releases) {
            $parts = $line.Split("|")
            Assert-Equal -Expected $box.Tip -Actual $parts[4] -Because "the release ran on the sha: $line"
            Assert-Equal -Expected "dirty=False" -Actual $parts[5] -Because "from a clean tree: $line"
            Assert-True -Condition ($parts[3] -ne $box.Root -and $parts[3] -match 'worktrees') -Because "never the main checkout: $line"
        }
        Assert-True -Condition (@($run.Calls | Where-Object { $_ -eq "ssh|pin|$($box.Tip)" }).Count -eq 1) -Because "pinned with the FULL sha: $($run.Calls -join ' / ')"
        Assert-Equal -Expected $box.Tip -Actual $run.HostState.pin -Because "APPROVED_SHA is the sha"
        Assert-Equal -Expected "released" -Actual $run.Task.state -Because "released"
        Assert-Equal -Expected $true -Actual $run.Task.release_approved -Because "approved"
        Assert-Equal -Expected "standing_rule" -Actual $run.Task.release_approved_by -Because "by the owner's standing rule (ADR-0214 addendum 9)"
        Assert-True -Condition ([string]$run.Task.release_approved_at -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') -Because "when"
        Assert-True -Condition ([string]$run.Task.reason -match "^yayinlandi \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z, main $($box.Tip) \(green\)$") -Because $run.Task.reason
        Assert-True -Condition ($run.Report -match "## Yayın" -and $run.Report.Contains($box.Tip) -and $run.Report -match "green" -and $run.Report.Contains($box.Served)) -Because "the report names the sha, the colour and the last known good: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one") -Because "and what was released"
        Assert-Equal -Expected $false -Actual $run.Blocked -Because "no block marker"
        Assert-Equal -Expected $false -Actual ([bool](Get-TeamProperty -InputObject $run.Lock -Name "held" -Default $false)) -Because "the lock is given back"
        $wt = @((Invoke-SandboxGit -Root $box.Root -Arguments @("worktree", "list", "--porcelain")) -split "`n" | Where-Object { $_ -match '^worktree ' })
        Assert-Equal -Expected 1 -Actual @($wt).Count -Because "the release worktree is removed afterwards: $($wt -join ' / ')"
    }

    Test-Case "each command's stdout and stderr land in SEPARATE files under team/reports/<cycle>/release-<n>.*" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        foreach ($step in @("preflight", "release", "pin")) {
            $out = @($run.Files | Where-Object { $_ -match "^release-1\.$step\.out$" })
            $err = @($run.Files | Where-Object { $_ -match "^release-1\.$step\.err$" })
            Assert-True -Condition (@($out).Count -eq 1 -and @($err).Count -eq 1) -Because "$step has an .out and an .err: $($run.Files -join ', ')"
        }
        $releaseOut = [System.IO.File]::ReadAllText((Join-Path $run.Reports "release-1.release.out"), [System.Text.Encoding]::UTF8)
        $releaseErr = [System.IO.File]::ReadAllText((Join-Path $run.Reports "release-1.release.err"), [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($releaseOut -match "RELEASE OK" -and $releaseOut -notmatch "nginx") -Because "stdout only in .out: $releaseOut"
        Assert-True -Condition ($releaseErr -match "nginx: \[notice\]" -and $releaseErr -notmatch "RELEASE OK") -Because "stderr only in .err: $releaseErr"
    }

    Test-Case "the step's source never merges stderr into stdout (2>&1) - least of all where it runs the release or ssh" {
        foreach ($path in @($releaseScript, $releaseLib)) {
            Assert-True -Condition (Test-Path -LiteralPath $path) -Because "$path exists"
            $lines = @([System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8))
            $merged = @($lines | Where-Object { $_ -match '2>&1' -or $_ -match '\*>&1' })
            Assert-Equal -Expected 0 -Actual @($merged).Count -Because "no line of $path merges the streams: $($merged -join ' / ')"
        }
        $text = [System.IO.File]::ReadAllText($releaseScript, [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($text -match 'ReleaseScript' -and $text -match 'SshPath') -Because "the release and ssh are run by this script"
    }

    Test-Case "no gate record or log: stop, and nothing is run - not even a look at the host" {
        $box = New-Sandbox -Gate "none"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı kaydı yok"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a FAIL gate log: stop, nothing run" {
        $box = New-Sandbox -Gate "fail"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı PASS demiyor"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a gate log for ANOTHER sha: stop, nothing run" {
        $box = New-Sandbox -Gate "other-sha"
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kapı kaydı yok"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "a migration with drop_column in the diff: stop, no release command" {
        $box = New-Sandbox -Files @{ "services/api/alembic/versions/20261003_0066_contract.py" = $dropColumn }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "genişletme dışı göç"
        Assert-True -Condition ($run.Task.reason -match "0066_contract") -Because "names the migration: $($run.Task.reason)"
    }

    Test-Case "an expand-only migration (add_column nullable, create_table, create_index) is released" {
        $box = New-Sandbox -Files @{ "services/api/alembic/versions/20261003_0066_expand.py" = $expandOnly }
        $run = Invoke-Release -Box $box
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "released" -Actual $run.Task.state -Because $run.Output
    }

    Test-Case "a changed prod compose file: stop, no release command" {
        $box = New-Sandbox -Files @{ "infra/docker/docker-compose.prod.yml" = "services: {}`n" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "docker-compose.prod.yml değişti"
    }

    Test-Case "a changed edge: stop, no release command" {
        $box = New-Sandbox -Files @{ "infra/docker/edge/nginx.conf" = "events {}`n" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "edge değişti"
    }

    Test-Case "production health not ok before the release: stop, no release command" {
        $box = New-Sandbox -HostChange @{ health_status = "degraded" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "sağlık ok değil"
    }

    Test-Case "a maintenance window within 30 minutes: stop, no release command" {
        $box = New-Sandbox -HostChange @{ maintenance_in_s = "900" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "bakım penceresi"
    }

    Test-Case "a maintenance marker on the host: stop, no release command" {
        $box = New-Sandbox -HostChange @{ maintenance_marker = "yes" }
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "bakım işareti"
    }

    Test-Case "a lock another machine holds: stop, nothing run, the lock stays theirs" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $box = New-Sandbox -Lock $held
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "kilit" -ExitCode 3
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $run.Lock.machine -Because "the lock is still theirs"
    }

    Test-Case "team/release-blocked.json (an automatic release was rolled back and nobody cleared it): stop, nothing run" {
        $box = New-Sandbox -Blocked
        $run = Invoke-Release -Box $box
        Assert-Stopped -Run $run -Words "release-blocked.json"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no command at all: $($run.Calls -join ' / ')"
    }

    Test-Case "the release script rolls back: the task stays awaiting_release, 'geri alındı' is recorded and the block marker is set" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "rollback"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "not released"
        Assert-True -Condition ([string]$run.Task.reason -match "geri alındı") -Because $run.Task.reason
        Assert-True -Condition $run.Blocked -Because "the marker stops the next run until the lead removes it"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -match '^ssh\|pin' }).Count -Because "no pin after a failed release"
        Assert-Equal -Expected $box.Served -Actual $run.HostState.release -Because "the host is what the release script's own rollback left"
        Assert-True -Condition ($run.Report -match "geri alındı") -Because $run.Report
        $again = Invoke-Release -Box $box
        Assert-Stopped -Run $again -Words "release-blocked.json"
    }

    Test-Case "a verification that finds APPROVED_SHA != sha is a failed release: the marker is set and the task is not 'released'" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "pin-wrong"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "not released"
        Assert-True -Condition $run.Blocked -Because "the marker is set"
        Assert-True -Condition ($run.Report -match "APPROVED_SHA") -Because "the report names what failed: $($run.Report)"
    }

    Test-Case "a preflight that fails changes nothing: the task waits, no release, no marker" {
        $box = New-Sandbox
        $run = Invoke-Release -Box $box -Scenario "preflight-fail"
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -match '^release\|release\|' }).Count -Because "no release after a failed preflight"
        Assert-Equal -Expected "awaiting_release" -Actual $run.Task.state -Because "waits"
        Assert-Equal -Expected $false -Actual $run.Blocked -Because "nothing changed on the host: no marker"
    }

    Test-Case "-DryRun prints the decision and changes nothing" {
        $box = New-Sandbox
        $queueBefore = (Get-FileHash -LiteralPath (Join-Path $box.Root "team\queue.json") -Algorithm SHA256).Hash
        $lockBefore = (Get-FileHash -LiteralPath (Join-Path $box.Root "team\lock.json") -Algorithm SHA256).Hash
        $run = Invoke-Release -Box $box -Extra "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-True -Condition ($run.Output -match "DRY RUN" -and $run.Output -match "release") -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_ -notmatch '^ssh\|probe$' }).Count -Because "only reads: $($run.Calls -join ' / ')"
        Assert-Equal -Expected $queueBefore -Actual (Get-FileHash -LiteralPath (Join-Path $box.Root "team\queue.json") -Algorithm SHA256).Hash -Because "the queue is untouched"
        Assert-Equal -Expected $lockBefore -Actual (Get-FileHash -LiteralPath (Join-Path $box.Root "team\lock.json") -Algorithm SHA256).Hash -Because "the lock is untouched"
        Assert-Equal -Expected 0 -Actual @($run.Files).Count -Because "no report, no log: $($run.Files -join ', ')"
        Assert-Equal -Expected $false -Actual (Test-Path -LiteralPath (Join-Path $box.Root ".claude\worktrees")) -Because "no worktree"
    }
}
finally {
    foreach ($root in $sandboxes) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if (Test-Path -LiteralPath (Join-Path $root ".git")) { try { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("worktree", "prune")) } catch { } }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-release tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
