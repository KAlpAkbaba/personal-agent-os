<#
.SYNOPSIS
    What the automatic release step decides (scripts/team/release.ps1, ADR-0214 addendum 9):
    whether gated roadmap work on main may be released without asking the owner, and whether
    a release that ran is what production now serves.

.DESCRIPTION
    Dot-sourced after `NativeProcess.ps1`, `TeamQueue.ps1`, `TeamRun.ps1` and `TeamIntegrate.ps1`.
    The first half is decisions - functions that take their inputs and return their answer, so
    the tests drive them without a repository or a host:

      * Get-TeamMigrationVerdict: whether an alembic version is expand-only, read conservatively
        (an unreadable one, a changed or deleted existing one, is not);
      * Read-TeamHostProbe / Get-TeamHostProbeCommand: the ONE read-only look at the host;
      * Get-TeamReleaseDecision: 'release' or 'stop', with every reason that stops it;
      * Test-TeamReleaseVerified: RELEASE, APPROVED_SHA, the reconcile's last line and the
        health through the edge all name the sha.

    The second half reads the repository and the gate's records (git only; nothing here writes
    to a branch, a worktree or the host).

    What does NOT become automatic (the addendum's own list) is exactly what stops here: a
    migration that is not expand-only, the host's compose or edge, anything the gate did not
    pass, a maintenance window within 30 minutes, health that is not ok - and an earlier
    automatic release that was rolled back (team/release-blocked.json) until the lead looked.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

$script:TeamReleaseMaintenanceSeconds = 1800
$script:TeamReleaseApprover = "standing_rule"
$script:TeamReleaseBlockedName = "release-blocked.json"
$script:TeamReleaseProbeKeys = @("release", "app_release", "pin", "lkg", "colour", "maintenance_marker", "maintenance_in_s", "health_b64", "reconcile")
# A changed file among these is the host's own configuration beyond the image: the owner's.
$script:TeamReleaseHostFiles = @("infra/docker/docker-compose.prod.yml")
$script:TeamReleaseHostPrefixes = @("infra/docker/edge/")

# ---------------------------------------------------------------------------- migrations

function Test-TeamMigrationPath {
    <# Whether a repository path is an alembic version file. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return ((($Path -replace '\\', '/')) -match '(^|/)alembic/versions/[^/]+\.py$')
}

function Get-TeamCallTexts {
    <# The text of every call of a function whose name matches -Name: from the name to its closing parenthesis. #>
    param([string]$Text, [Parameter(Mandatory = $true)][string]$Name)
    $calls = New-Object System.Collections.ArrayList
    foreach ($match in [regex]::Matches([string]$Text, "\b(?:$Name)\s*\(")) {
        $depth = 0
        $end = -1
        for ($i = $match.Index + $match.Length - 1; $i -lt $Text.Length; $i++) {
            $c = $Text[$i]
            if ($c -eq '(') { $depth++ }
            elseif ($c -eq ')') { $depth--; if ($depth -eq 0) { $end = $i; break } }
        }
        # An unclosed call is read to the end: more text is more to object to, never less.
        if ($end -lt 0) { $end = $Text.Length - 1 }
        [void]$calls.Add($Text.Substring($match.Index, $end - $match.Index + 1))
    }
    return @($calls.ToArray())
}

function Get-TeamMigrationVerdict {
    <#
    .SYNOPSIS
        Whether one alembic version in the diff may go out without the owner: ExpandOnly, and
        Why when it may not.

    .DESCRIPTION
        Expand-only means both colours of a blue-green release can serve beside the new schema:
        new tables, new nullable columns, new indexes. Read conservatively:
          * only an ADDED file is judged (status 'A'): a changed, deleted or renamed existing
            version is not expand-only, whatever it holds;
          * no text, or no `def upgrade(`, is unreadable - and an unreadable migration stops;
          * the module is read WITHOUT its downgrade() (the downgrade of an expand-only
            migration drops what the upgrade added), but with everything else - a helper the
            upgrade calls is read too;
          * a drop_* call (op.drop_column, batch.drop_table, ...), rename_table, an alter_column
            that changes a type, nullability or a name, an add_column that is NOT NULL without a
            server default, and SQL that deletes, updates, truncates, drops or renames.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [string]$Status = "A", [AllowNull()][AllowEmptyString()][string]$Text = "")
    $verdict = { param([bool]$Ok, [string]$Why) [pscustomobject]@{ Path = $Path; ExpandOnly = $Ok; Why = $Why } }
    if ($Status -ne "A") { return (& $verdict $false "var olan bir göç dosyası değişti ya da silindi (git $Status)") }
    if ([string]::IsNullOrWhiteSpace($Text)) { return (& $verdict $false "göç okunamadı") }
    $lines = @(([string]$Text) -split "`r?`n")
    if (@($lines | Where-Object { $_ -match '^def\s+upgrade\s*\(' }).Count -eq 0) { return (& $verdict $false "göçte upgrade() okunamadı") }
    $kept = New-Object System.Collections.ArrayList
    $inDowngrade = $false
    foreach ($line in $lines) {
        if ($line -match '^def\s+downgrade\s*\(') { $inDowngrade = $true; continue }
        # The downgrade ends at the next top-level statement (a closing ')' of its own signature is not one).
        if ($inDowngrade -and $line -match '^[^\s#)]') { $inDowngrade = $false }
        if (-not $inDowngrade) { [void]$kept.Add($line) }
    }
    $body = ($kept.ToArray() -join "`n")

    $drop = [regex]::Match($body, '\bdrop_\w+\s*\(')
    if ($drop.Success) { return (& $verdict $false (($drop.Value -replace '[\s(]+$', ''))) }
    if ($body -match '\brename_table\s*\(') { return (& $verdict $false "rename_table") }
    foreach ($call in @(Get-TeamCallTexts -Text $body -Name "alter_column")) {
        if ($call -match '\b(type_|nullable|new_column_name)\s*=') { return (& $verdict $false "alter_column ($($Matches[1]))") }
    }
    foreach ($call in @(Get-TeamCallTexts -Text $body -Name "add_column")) {
        if ($call -match '\bnullable\s*=\s*False\b' -and $call -notmatch '\bserver_default\s*=') { return (& $verdict $false "add_column NOT NULL, varsayılansız") }
    }
    $sql = @(
        '(?i)\bdelete\s+from\b', '(?i)\bupdate\s+\S+\s+set\b', '(?i)\btruncate\b',
        '(?i)\bdrop\s+(table|column|index|constraint|schema|type|view)\b', '(?i)\brename\s+(to|column)\b', '(?i)\balter\s+column\b'
    )
    foreach ($pattern in $sql) {
        $found = [regex]::Match($body, $pattern)
        if ($found.Success) { return (& $verdict $false "SQL: $($found.Value)") }
    }
    return (& $verdict $true "")
}

# ---------------------------------------------------------------------------- the host

function Get-TeamHostProbeCommand {
    <#
    .SYNOPSIS
        The ONE look at the host, as a bash command for ssh: it reads and prints key=value lines
        and changes nothing. The seconds to the maintenance window are counted on the HOST's
        clock (one clock for one decision).
    #>
    param(
        [string]$HostBase = "/opt/pagentos",
        [string]$RecoveryRoot = "/opt/pagentos-recovery",
        [string]$EdgeDir = "/mnt/pagentos-data/edge",
        [string]$HealthUrl = "http://127.0.0.1:8001/v1/system/health"
    )
    foreach ($p in @($HostBase, $RecoveryRoot, $EdgeDir)) {
        if ($p -cnotmatch '^/[A-Za-z0-9_./-]+$') { throw "unsafe remote path '$p'" }
    }
    if ($HealthUrl -cnotmatch '^http://[A-Za-z0-9.:-]+/[A-Za-z0-9_./-]*$') { throw "unsafe health url '$HealthUrl'" }
    $lines = @(
        "b='$HostBase'; r='$RecoveryRoot'; e='$EdgeDir'",
        'm() { if [ -f "$1" ]; then cat "$1" | tr -d "[:space:]"; fi; }',
        'echo "release=$(m "$b/RELEASE")"',
        'echo "app_release=$(m "$b/app/RELEASE")"',
        'echo "pin=$(m "$r/APPROVED_SHA")"',
        'echo "lkg=$(m "$b/LAST_KNOWN_GOOD")"',
        'echo "colour=$(m "$e/active.txt" | tr "[:upper:]" "[:lower:]")"',
        'if [ -e "$b/MAINTENANCE_MARKER" ]; then echo maintenance_marker=yes; else echo maintenance_marker=no; fi',
        'n=$(systemctl show pagentos-maintenance-window.timer -p NextElapseUSecRealtime --value 2>/dev/null || true)',
        'if [ -z "$n" ] || [ "$n" = "n/a" ]; then echo maintenance_in_s=none; elif t=$(date -d "$n" +%s 2>/dev/null); then echo "maintenance_in_s=$((t - $(date +%s)))"; else echo maintenance_in_s=unknown; fi',
        "echo `"health_b64=`$(curl -fsS --max-time 10 '$HealthUrl' 2>/dev/null | base64 -w0)`"",
        'echo "reconcile=$(journalctl -u pagentos-bluegreen-reconcile.service -n 200 --no-pager -o cat 2>/dev/null | grep -E "^RECONCILE" | tail -n 1)"'
    )
    return ($lines -join "; ")
}

function Read-TeamHostProbe {
    <#
    .SYNOPSIS
        What the probe printed. Ok only when ssh exited 0 AND every key is there: a probe that
        said half is not a probe. Health is the top-level status and release.version of the body.
    #>
    param([AllowEmptyString()][string]$Text = "", [int]$ExitCode = 0)
    $values = @{}
    foreach ($line in @(([string]$Text) -split "`r?`n")) {
        $at = $line.IndexOf("=")
        if ($at -lt 1) { continue }
        $key = $line.Substring(0, $at).Trim()
        if ($script:TeamReleaseProbeKeys -contains $key -and -not $values.ContainsKey($key)) { $values[$key] = $line.Substring($at + 1).Trim() }
    }
    $missing = @($script:TeamReleaseProbeKeys | Where-Object { -not $values.ContainsKey($_) })
    $value = { param([string]$Key, [string]$Default = "") if ($values.ContainsKey($Key)) { [string]$values[$Key] } else { $Default } }
    $status = ""
    $served = ""
    $encoded = & $value "health_b64"
    if ($encoded) {
        try {
            $document = ConvertFrom-Json -InputObject ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)))
            $status = [string](Get-TeamProperty -InputObject $document -Name "status" -Default "")
            $served = [string](Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $document -Name "release" -Default $null) -Name "version" -Default "")
        }
        catch { $status = "" }
    }
    return [pscustomobject]@{
        Ok                   = ($ExitCode -eq 0 -and @($missing).Count -eq 0)
        ExitCode             = $ExitCode
        Missing              = @($missing)
        Release              = (& $value "release")
        AppRelease           = (& $value "app_release")
        Pin                  = (& $value "pin")
        Lkg                  = (& $value "lkg")
        Colour               = (& $value "colour")
        MaintenanceMarker    = ((& $value "maintenance_marker" "yes") -ne "no")
        MaintenanceInSeconds = (& $value "maintenance_in_s" "unknown")
        HealthStatus         = $status
        HealthRelease        = $served
        Reconcile            = (& $value "reconcile")
    }
}

# ---------------------------------------------------------------------------- the decision

function Get-TeamReleaseDecision {
    <#
    .SYNOPSIS
        'release' or 'stop', and every reason that stops it (Reasons: Code + Text; Reason: the
        texts in one line).

    .DESCRIPTION
        -Facts carries: Sha (what would go), MainTip (origin/main's tip), Gate (Found, Pass, Why:
        the integrate step's record and log for that sha), Blocked (the block marker's name, or
        ""), Lock (Get-TeamLockDecision's answer), Host (Read-TeamHostProbe's answer) and Diff
        (Readable, Why, Files, Migrations: the verdicts of Get-TeamMigrationVerdict).

        -LocalOnly judges what is known before the host is read (the sha, the gate, the marker,
        the lock): a release whose own evidence is missing never looks at production.
    #>
    param([Parameter(Mandatory = $true)]$Facts, [switch]$LocalOnly)
    $reasons = New-Object System.Collections.ArrayList
    $add = { param([string]$Code, [string]$Text) [void]$reasons.Add([pscustomobject]@{ Code = $Code; Text = $Text }) }

    $sha = [string]$Facts.Sha
    $tip = [string]$Facts.MainTip
    if ($sha -cnotmatch '^[0-9a-f]{40}$' -or $sha -ne $tip) {
        & $add "not_tip" $(if ($tip) { "iş $sha, origin/main'in ucu $tip değil" } else { "origin/main okunamadı" })
    }
    $gate = $Facts.Gate
    if ($null -eq $gate -or -not [bool]$gate.Found) {
        & $add "no_gate" ("kapı kaydı yok: " + $(if ($null -ne $gate -and $gate.Why) { [string]$gate.Why } else { "$sha için yeşil kapı kaydı bulunamadı" }))
    }
    elseif (-not [bool]$gate.Pass) { & $add "gate_not_pass" "kapı PASS demiyor: $([string]$gate.Why)" }
    if ([string]$Facts.Blocked) {
        & $add "blocked" "önceki otomatik yayın geri alındı ya da doğrulanamadı ($([string]$Facts.Blocked)); lead kaldırana kadar otomatik yayın durur"
    }
    $lock = $Facts.Lock
    if ($null -ne $lock -and -not [bool]$lock.MayRun) { & $add "lock" "kilit $([string]$lock.Holder) makinesinde ($([string]$lock.Since)); başka bir yayın ya da döngü sürüyor" }

    if (-not $LocalOnly) {
        $probe = $Facts.Host
        if ($null -eq $probe -or -not [bool]$probe.Ok) {
            $said = if ($null -eq $probe) { "okunmadı" } else { "ssh çıkış kodu $($probe.ExitCode)" + $(if (@($probe.Missing).Count -gt 0) { ", eksik: " + (@($probe.Missing) -join ", ") } else { "" }) }
            & $add "host_unread" "üretim okunamadı ($said)"
        }
        else {
            if ([string]$probe.HealthStatus -ne "ok") { & $add "health" "üretimde sağlık ok değil ('$([string]$probe.HealthStatus)'); yayından önce ok olmalı" }
            if ([bool]$probe.MaintenanceMarker) { & $add "maintenance_marker" "sunucuda bakım işareti var (MAINTENANCE_MARKER): bakım sürüyor" }
            $window = [string]$probe.MaintenanceInSeconds
            $seconds = 0
            if ($window -ne "none") {
                if ([int]::TryParse($window, [ref]$seconds)) {
                    if ($seconds -ge 0 -and $seconds -le $script:TeamReleaseMaintenanceSeconds) {
                        & $add "maintenance_window" "bakım penceresi $([Math]::Ceiling($seconds / 60)) dk içinde (30 dk kuralı)"
                    }
                }
                else { & $add "maintenance_unknown" "bakım penceresinin zamanı okunamadı ('$window')" }
            }
        }
        $diff = $Facts.Diff
        if ($null -eq $diff -or -not [bool]$diff.Readable) {
            & $add "diff_unreadable" ("yayındakiyle fark okunamadı: " + $(if ($null -ne $diff) { [string]$diff.Why } else { "okunmadı" }))
        }
        else {
            $bad = @(@($diff.Migrations) | Where-Object { $null -ne $_ -and -not [bool]$_.ExpandOnly })
            if (@($bad).Count -gt 0) {
                & $add "migration" ("genişletme dışı göç: " + ((@($bad) | ForEach-Object { "$($_.Path) ($($_.Why))" }) -join ", "))
            }
            $files = @(@($diff.Files) | ForEach-Object { ([string]$_) -replace '\\', '/' })
            foreach ($file in $script:TeamReleaseHostFiles) {
                if ($files -contains $file) { & $add "compose" "$file değişti: sunucunun compose'u imajın ötesinde değişiyor" }
            }
            $edge = @($files | Where-Object { $path = $_; @($script:TeamReleaseHostPrefixes | Where-Object { $path.StartsWith($_) }).Count -gt 0 })
            if (@($edge).Count -gt 0) { & $add "edge" ("infra/docker/edge değişti: " + (@($edge) -join ", ")) }
        }
    }
    $all = @($reasons.ToArray())
    return [pscustomobject]@{
        Action  = $(if (@($all).Count -eq 0) { "release" } else { "stop" })
        Reasons = $all
        Reason  = ((@($all) | ForEach-Object { $_.Text }) -join "; ")
    }
}

function Test-TeamReleaseVerified {
    <#
    .SYNOPSIS
        After the release and the pin: RELEASE == sha, APPROVED_SHA == sha, the reconcile's
        last line is RECONCILE OK for THAT sha, and health through the edge is ok serving it.
    #>
    param([Parameter(Mandatory = $true)]$Probe, [Parameter(Mandatory = $true)][string]$Sha)
    $problems = New-Object System.Collections.ArrayList
    if (-not [bool]$Probe.Ok) { [void]$problems.Add("üretim okunamadı (ssh çıkış kodu $($Probe.ExitCode))") }
    else {
        if ([string]$Probe.Release -ne $Sha) { [void]$problems.Add("RELEASE '$($Probe.Release)', beklenen $Sha") }
        if ([string]$Probe.Pin -ne $Sha) { [void]$problems.Add("APPROVED_SHA '$($Probe.Pin)', beklenen $Sha") }
        if ([string]$Probe.Reconcile -notmatch ('^RECONCILE OK: .*\(release ' + [regex]::Escape($Sha) + '\)')) {
            [void]$problems.Add("uzlaştırmanın son satırı $Sha için RECONCILE OK değil: '$($Probe.Reconcile)'")
        }
        if ([string]$Probe.HealthStatus -ne "ok" -or [string]$Probe.HealthRelease -ne $Sha) {
            [void]$problems.Add("edge üzerinden sağlık '$($Probe.HealthStatus)', sunulan sürüm '$($Probe.HealthRelease)'; beklenen ok / $Sha")
        }
    }
    $all = @($problems.ToArray())
    return [pscustomobject]@{ Ok = (@($all).Count -eq 0); Problems = $all }
}

# ---------------------------------------------------------------------------- the repository and the records

function Find-TeamReleaseGate {
    <#
    .SYNOPSIS
        The integrate step's evidence for a sha on main: the newest green record
        (team/reports/<cycle>/gate-<n>.json) whose `main` IS the sha, and its log beside it,
        which must say QUALITY GATE: PASS (Read-TeamGateLog, the integrate step's own reader).
        No record for that sha, or no log: Found is false.
    #>
    param([Parameter(Mandatory = $true)][string]$ReportsRoot, [Parameter(Mandatory = $true)][string]$Sha)
    $none = { param([string]$Why) [pscustomobject]@{ Found = $false; Pass = $false; Why = $Why; Directory = ""; CycleId = ""; Log = "" } }
    if (-not (Test-Path -LiteralPath $ReportsRoot)) { return (& $none "team/reports yok") }
    $best = $null
    foreach ($folder in @(Get-ChildItem -LiteralPath $ReportsRoot -Directory)) {
        foreach ($file in @(Get-ChildItem -LiteralPath $folder.FullName -Filter "gate-*.json" -File)) {
            if ($file.Name -notmatch '^gate-(\d+)\.json$') { continue }
            try { $record = Read-TeamJson -Path $file.FullName } catch { continue }
            if ([string](Get-TeamProperty -InputObject $record -Name "result" -Default "") -ne "green") { continue }
            if ([string](Get-TeamProperty -InputObject $record -Name "main" -Default "") -ne $Sha) { continue }
            $at = [string](Get-TeamProperty -InputObject $record -Name "at" -Default "")
            if ($null -eq $best -or $at -gt $best.At) { $best = [pscustomobject]@{ Record = $record; Folder = $folder; At = $at } }
        }
    }
    if ($null -eq $best) { return (& $none "main $Sha için yeşil kapı kaydı (team/reports/<döngü>/gate-<n>.json) bulunamadı") }
    $logName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $best.Record -Name "log" -Default ""))
    $relative = "team/reports/$($best.Folder.Name)/$logName"
    $logPath = if ($logName) { Join-Path $best.Folder.FullName $logName } else { "" }
    if (-not $logPath -or -not (Test-Path -LiteralPath $logPath)) {
        $missing = & $none "kapı günlüğü yok ($relative)"
        $missing.Directory = $best.Folder.FullName
        $missing.CycleId = $best.Folder.Name
        return $missing
    }
    $gate = Read-TeamGateLog -Text ([System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8)) -ExitCode 0
    return [pscustomobject]@{
        Found = $true; Pass = [bool]$gate.Green; Why = $(if ($gate.Green) { "" } else { "$relative ($($gate.Why))" })
        Directory = $best.Folder.FullName; CycleId = $best.Folder.Name; Log = $relative
    }
}

function Get-TeamReleaseDiff {
    <#
    .SYNOPSIS
        What changes between what production serves (-From, the host's RELEASE) and -To: the
        files, and a verdict for every alembic version among them. Not readable when -From is
        not a sha this repository has.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string]$From = "", [Parameter(Mandatory = $true)][string]$To)
    $unread = { param([string]$Why) [pscustomobject]@{ Readable = $false; Why = $Why; Files = @(); Migrations = @() } }
    if ($From -cnotmatch '^[0-9a-f]{40}$') { return (& $unread "yayındaki sürüm okunamadı ('$From')") }
    if (-not (Get-TeamRevision -RepoRoot $RepoRoot -Revision $From)) { return (& $unread "yayındaki sürüm $From bu depoda yok") }
    $listed = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("diff", "--name-status", "--no-renames", $From, $To)
    if (-not $listed.Success) { return (& $unread "git diff başarısız: $(($listed.StdErr -replace '\s+', ' ').Trim())") }
    $files = New-Object System.Collections.ArrayList
    $migrations = New-Object System.Collections.ArrayList
    foreach ($line in @($listed.StdOut -split "`r?`n" | Where-Object { $_.Trim() })) {
        $parts = $line.Split("`t")
        if (@($parts).Count -lt 2) { return (& $unread "git diff satırı okunamadı: '$line'") }
        $status = $parts[0].Trim()
        $path = $parts[@($parts).Count - 1].Trim()
        [void]$files.Add($path)
        if (-not (Test-TeamMigrationPath -Path $path)) { continue }
        $text = $null
        if ($status -ne "D") {
            $shown = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("show", "${To}:$path")
            if ($shown.Success) { $text = $shown.StdOut }
        }
        [void]$migrations.Add((Get-TeamMigrationVerdict -Path $path -Status $status -Text $text))
    }
    return [pscustomobject]@{ Readable = $true; Why = ""; Files = @($files.ToArray()); Migrations = @($migrations.ToArray()) }
}

function Get-TeamReleaseNextNumber {
    <# One past the highest release-<n>.* in a cycle's report folder. #>
    param([Parameter(Mandatory = $true)][string]$Directory)
    $highest = 0
    if (Test-Path -LiteralPath $Directory) {
        foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter "release-*" -File)) {
            if ($file.Name -match '^release-(\d+)\.') { $highest = [Math]::Max($highest, [int]$Matches[1]) }
        }
    }
    return ($highest + 1)
}

function New-TeamReleaseReport {
    <#
    .SYNOPSIS
        The step's report, in Turkish: the section 'Yayın' names what, the sha, the colour and
        the last known good - or why it stopped and that it waits for the owner.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Machine,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$Result,
        [string]$Sha = "",
        [string]$Colour = "",
        [string]$LastKnownGood = "",
        [string[]]$Tasks = @(),
        [string[]]$Lines = @()
    )
    $out = New-Object System.Collections.ArrayList
    [void]$out.Add("# Otomatik yayın raporu")
    [void]$out.Add("")
    [void]$out.Add("Makine: $Machine · başladı $StartedAt · bitti $(Get-TeamTimestamp)")
    [void]$out.Add("")
    [void]$out.Add("Kural: kapıdan geçip main'e giren roadmap işi sahibe sorulmadan yayınlanır (ADR-0214 ek 9); kuralın saydığı durumlarda durur ve sahibe bırakır.")
    [void]$out.Add("")
    [void]$out.Add("## Yayın")
    [void]$out.Add("")
    [void]$out.Add("- sonuç: $Result")
    [void]$out.Add("- ne: " + $(if (@($Tasks).Count -gt 0) { $Tasks -join ", " } else { "yok" }))
    [void]$out.Add("- sha: " + $(if ($Sha) { $Sha } else { "-" }))
    [void]$out.Add("- renk: " + $(if ($Colour) { $Colour } else { "-" }))
    [void]$out.Add("- son bilinen iyi (LKG): " + $(if ($LastKnownGood) { $LastKnownGood } else { "-" }))
    foreach ($line in @($Lines)) { if ($line) { [void]$out.Add("- $line") } }
    [void]$out.Add("")
    return (($out.ToArray()) -join "`n")
}
