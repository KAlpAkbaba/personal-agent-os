<#
.SYNOPSIS
    The Proje Yöneticisi's duty action `resolve_integration`: an approved task stopped with
    "entegrasyon dalında çakışma" is merged into integrate/<cycle> by the Proje Yöneticisi
    itself; the Danışman gets it only when the resolution is not mechanical.

.DESCRIPTION
    The owner, 2026-10-06: "çalışan 2'nin direk sana değil proje yöneticisine gitmeli". Six
    approved tasks of 2026-10-05/06 sat "Danışman'da" for hours, and every one was a mechanical
    union: both sides' lines kept, an alembic down_revision re-pointed at the chain tip, a
    guards.json array entry kept twice. This file does that union, deterministically:

      * the task branch is merged into a SCRATCH worktree detached at the integration tip
        (diff3 conflict style), never into the integration worktree itself;
      * a conflict hunk is resolved only when it is ADDITIVE: both sides keep the base lines
        and only insert at the same place (Resolve-TeamDutyHunk); in a .json file a trailing
        comma is not a change, and the comma between the two insertions is written;
      * a new alembic migration of the task that revises the integration side's old head is
        renamed, its revision and down_revision re-pointed after the current head, and the
        chain proven to have one head;
      * the guards of team/guards.json plus the task's own test files run on the merged tree;
      * only then is integrate/<cycle> fast-forwarded to the merge, and the task set merged.

    The Danışman decides (the reason line names which case it was):
      * korunan dosya     - a lead-protected path (Get-TeamAreaProtection) is in the conflict;
      * ekleme değil      - a resolution would delete or rewrite the other side's lines;
      * koruyucu kırmızı  - a guard or the task's own test is red on the merged tree;
      * iki çözüm denemesi başarısız - the resolution failed (git, an unparsable JSON, a chain
                            that cannot be renumbered) twice in a row; the first failure is
                            counted in the reason "(1/2)" and the task stays the PM's.
    In every case but `merged` the integration branch is left exactly as it was.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")
. (Join-Path $PSScriptRoot "HttpJson.ps1")
. (Join-Path $PSScriptRoot "TeamQueue.ps1")
. (Join-Path $PSScriptRoot "TeamRun.ps1")
. (Join-Path $PSScriptRoot "TeamArea.ps1")
. (Join-Path $PSScriptRoot "TeamGuards.ps1")

# The reason the cycle writes for a merge that conflicted (scripts/team/cycle.ps1).
$script:TeamDutyConflictReason = "entegrasyon dalında çakışma"
# While the duty works on it: the Ofis page reads this as "Proje Yöneticisi çözüyor".
$script:TeamDutyResolving = "Proje Yöneticisi çözüyor: "
# The first failed resolution, counted: the second in a row goes to the Danışman.
$script:TeamDutyResolveFailedOnce = "entegrasyon dalında çakışma - Proje Yöneticisi'nin çözümü başarısız (1/2): "
$script:TeamDutyAlembicVersions = "services/api/alembic/versions"
$script:TeamDutyMaxWhy = 1500

function Get-TeamDutyResolveCandidates {
    <# The stopped tasks whose stop is an integration conflict the PM resolves (queue order). #>
    param([Parameter(Mandatory = $true)]$Queue)
    $found = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamTasks -Queue $Queue)) {
        if ([string](Get-TeamProperty -InputObject $task -Name "state" -Default "") -ne "stopped") { continue }
        if (-not [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")) { continue }
        $reason = [string](Get-TeamProperty -InputObject $task -Name "reason" -Default "")
        if ($reason.StartsWith($script:TeamDutyConflictReason, [System.StringComparison]::Ordinal) -or
            $reason.StartsWith($script:TeamDutyResolving, [System.StringComparison]::Ordinal)) {
            [void]$found.Add($task)
        }
    }
    return @($found.ToArray())
}

function Get-TeamDutyResolveAttempt {
    <# 2 when the task's last resolution already failed once, else 1. #>
    param([AllowEmptyString()][string]$Reason)
    if ($Reason.StartsWith($script:TeamDutyResolveFailedOnce, [System.StringComparison]::Ordinal)) { return 2 }
    if ($Reason.StartsWith($script:TeamDutyResolving, [System.StringComparison]::Ordinal) -and $Reason.Contains("(deneme 2/2)")) { return 2 }
    return 1
}

function Test-TeamDutyLineSame {
    param([string]$First, [string]$Second, [bool]$Json)
    if ($Json) { return ($First.TrimEnd().TrimEnd(",").TrimEnd() -ceq $Second.TrimEnd().TrimEnd(",").TrimEnd()) }
    return ($First -ceq $Second)
}

function Test-TeamDutyLinesSame {
    param([string[]]$First, [string[]]$Second, [bool]$Json)
    if (@($First).Count -ne @($Second).Count) { return $false }
    for ($i = 0; $i -lt @($First).Count; $i++) {
        if (-not (Test-TeamDutyLineSame -First $First[$i] -Second $Second[$i] -Json $Json)) { return $false }
    }
    return $true
}

function Resolve-TeamDutyHunk {
    <#
    .SYNOPSIS
        One diff3 hunk: { Ok, Lines }. Ok only when the union is additive.

    .DESCRIPTION
        Additive: for one split point k of the base lines B, each side is B[0..k) + its own
        insertion + B[k..). The result is B[0..k) (as ours writes it) + ours' insertion +
        theirs' insertion (left out when it is the same) + B[k..). Anything else - a base line
        changed or deleted by either side - is not additive.
    #>
    param([string[]]$Base = @(), [string[]]$Ours = @(), [string[]]$Theirs = @(), [bool]$Json = $false)
    $Base = @($Base); $Ours = @($Ours); $Theirs = @($Theirs)
    if (Test-TeamDutyLinesSame -First $Ours -Second $Theirs -Json $false) { return [pscustomobject]@{ Ok = $true; Lines = $Ours } }
    $b = @($Base).Count
    if (@($Ours).Count -lt $b -or @($Theirs).Count -lt $b) { return [pscustomobject]@{ Ok = $false; Lines = @() } }
    # The latest split first: insertions after the base lines. With k = 0 first, a JSON entry
    # appended after "    }" matched with its own closing brace as the base, and the union
    # interleaved the two entries into invalid JSON.
    for ($k = $b; $k -ge 0; $k--) {
        $head = if ($k -gt 0) { $Base[0..($k - 1)] } else { @() }
        $tail = if ($k -lt $b) { $Base[$k..($b - 1)] } else { @() }
        $fits = $true
        $middles = @()
        foreach ($side in @(, $Ours) + @(, $Theirs)) {
            $sideHead = if ($k -gt 0) { $side[0..($k - 1)] } else { @() }
            $tailCount = $b - $k
            $sideTail = if ($tailCount -gt 0) { $side[(@($side).Count - $tailCount)..(@($side).Count - 1)] } else { @() }
            if (-not (Test-TeamDutyLinesSame -First @($sideHead) -Second @($head) -Json $Json) -or
                -not (Test-TeamDutyLinesSame -First @($sideTail) -Second @($tail) -Json $Json)) { $fits = $false; break }
            $middleCount = @($side).Count - $b
            $middles += , @($(if ($middleCount -gt 0) { $side[$k..($k + $middleCount - 1)] } else { @() }))
        }
        if (-not $fits) { continue }
        $x = @($middles[0]); $y = @($middles[1])
        $lines = New-Object System.Collections.Generic.List[string]
        if ($k -gt 0) { foreach ($line in $Ours[0..($k - 1)]) { $lines.Add($line) } }
        foreach ($line in $x) { $lines.Add($line) }
        if (-not (Test-TeamDutyLinesSame -First $x -Second $y -Json $false)) {
            if ($Json -and @($x).Count -gt 0 -and @($y).Count -gt 0) {
                $last = $lines[@($lines).Count - 1]
                $trimmed = $last.TrimEnd()
                if (-not ($trimmed.EndsWith(",") -or $trimmed.EndsWith("{") -or $trimmed.EndsWith("["))) { $lines[@($lines).Count - 1] = $trimmed + "," }
            }
            foreach ($line in $y) { $lines.Add($line) }
        }
        $tailStart = @($Ours).Count - ($b - $k)
        if ($b - $k -gt 0) { foreach ($line in $Ours[$tailStart..(@($Ours).Count - 1)]) { $lines.Add($line) } }
        return [pscustomobject]@{ Ok = $true; Lines = $lines.ToArray() }
    }
    return [pscustomobject]@{ Ok = $false; Lines = @() }
}

function Resolve-TeamDutyConflictText {
    <# A file with diff3 markers: { Ok, Text }. Ok is false when a hunk is not additive. #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text, [bool]$Json = $false)
    $crlf = $Text.Contains("`r`n")
    $lines = @(($Text -replace "`r`n", "`n") -split "`n", -1)
    $out = New-Object System.Collections.Generic.List[string]
    $i = 0
    while ($i -lt @($lines).Count) {
        if (-not $lines[$i].StartsWith("<<<<<<< ")) { $out.Add($lines[$i]); $i++; continue }
        $ours = New-Object System.Collections.Generic.List[string]
        $base = New-Object System.Collections.Generic.List[string]
        $theirs = New-Object System.Collections.Generic.List[string]
        $part = $ours
        $i++
        $closed = $false
        while ($i -lt @($lines).Count) {
            $line = $lines[$i]
            if ($line.StartsWith("||||||| ") -or $line -eq "|||||||") { $part = $base }
            elseif ($line -eq "=======") { $part = $theirs }
            elseif ($line.StartsWith(">>>>>>> ")) { $closed = $true; $i++; break }
            else { $part.Add($line) }
            $i++
        }
        if (-not $closed) { return [pscustomobject]@{ Ok = $false; Text = "" } }
        $hunk = Resolve-TeamDutyHunk -Base $base.ToArray() -Ours $ours.ToArray() -Theirs $theirs.ToArray() -Json $Json
        if (-not $hunk.Ok) { return [pscustomobject]@{ Ok = $false; Text = "" } }
        foreach ($line in @($hunk.Lines)) { $out.Add($line) }
    }
    $joined = [string]::Join("`n", $out.ToArray())
    if ($crlf) { $joined = $joined -replace "`n", "`r`n" }
    return [pscustomobject]@{ Ok = $true; Text = $joined }
}

function Invoke-TeamDutyGit {
    <# git in a tree; throws with git's words when it says no. #>
    param([Parameter(Mandatory = $true)][string]$Tree, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $ran = Invoke-TeamGit -WorkingDirectory $Tree -Arguments $Arguments
    if (-not $ran.Success) { throw ("git " + ($Arguments -join " ") + ": " + (($ran.StdOut + " " + $ran.StdErr) -replace '\s+', ' ').Trim()) }
    return ([string]$ran.StdOut).Trim()
}

function Get-TeamDutyLines {
    param([AllowEmptyString()][string]$Text)
    return @($Text -split "`r?`n" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
}

function Get-TeamDutyMigrations {
    <# The alembic migrations of a revision: revision id -> { File, Downs }. #>
    param([Parameter(Mandatory = $true)][string]$Tree, [Parameter(Mandatory = $true)][string]$Revision)
    $map = @{}
    $ran = Invoke-TeamGit -WorkingDirectory $Tree -Arguments @("ls-tree", "--name-only", $Revision, "$($script:TeamDutyAlembicVersions)/")
    if (-not $ran.Success) { return $map }
    foreach ($file in @(Get-TeamDutyLines -Text $ran.StdOut | Where-Object { $_ -like "*.py" })) {
        $text = Invoke-TeamDutyGit -Tree $Tree -Arguments @("show", "${Revision}:$file")
        $found = [regex]::Match($text, '(?m)^revision\b[^=\r\n]*=\s*[''"]([^''"]+)[''"]')
        if (-not $found.Success) { continue }
        $downLine = [regex]::Match($text, '(?m)^down_revision\b[^=\r\n]*=\s*(.+)$').Groups[1].Value
        $downs = @([regex]::Matches($downLine, '[''"]([^''"]+)[''"]') | ForEach-Object { $_.Groups[1].Value })
        $map[$found.Groups[1].Value] = [pscustomobject]@{ File = $file; Downs = $downs }
    }
    return $map
}

function Get-TeamDutyHeads {
    param([Parameter(Mandatory = $true)][hashtable]$Migrations)
    $named = @{}
    foreach ($entry in $Migrations.Values) { foreach ($down in @($entry.Downs)) { $named[$down] = $true } }
    return @($Migrations.Keys | Where-Object { -not $named.ContainsKey($_) } | Sort-Object)
}

function Update-TeamDutyMigrationChain {
    <#
    .SYNOPSIS
        After the merge commit in -Tree: when the chain has more than one head, the task's new
        migrations are renamed and re-pointed after the integration side's head. Returns what
        was done, as text ("" when nothing was needed); throws when it cannot be done.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Tree,
        [Parameter(Mandatory = $true)][string]$Before,
        [Parameter(Mandatory = $true)][string]$TaskTip
    )
    $merged = Get-TeamDutyMigrations -Tree $Tree -Revision "HEAD"
    if (@($merged.Keys).Count -eq 0 -or @(Get-TeamDutyHeads -Migrations $merged).Count -le 1) { return "" }
    $integration = Get-TeamDutyMigrations -Tree $Tree -Revision $Before
    $integrationHeads = @(Get-TeamDutyHeads -Migrations $integration)
    if (@($integrationHeads).Count -ne 1) { throw "entegrasyon dalının göç zincirinde $(@($integrationHeads).Count) baş var; yeniden zincirlenemez" }
    $head = $integrationHeads[0]
    $fork = Invoke-TeamDutyGit -Tree $Tree -Arguments @("merge-base", $Before, $TaskTip)
    $added = @(Get-TeamDutyLines -Text (Invoke-TeamDutyGit -Tree $Tree -Arguments @("diff", "--name-only", "--diff-filter=A", $fork, $TaskTip, "--", "$($script:TeamDutyAlembicVersions)/")))
    $mine = @{}
    foreach ($id in @($merged.Keys)) {
        if (@($added) -contains [string]$merged[$id].File -and -not $integration.ContainsKey($id)) { $mine[$id] = $merged[$id] }
    }
    $roots = @($mine.Keys | Where-Object { @(@($mine[$_].Downs) | Where-Object { $mine.ContainsKey($_) }).Count -eq 0 })
    if (@($mine.Keys).Count -eq 0 -or @($roots).Count -ne 1) { throw "işin yeni göçleri tek bir zincir değil ($(@($mine.Keys).Count) göç, $(@($roots).Count) kök)" }
    $chain = New-Object System.Collections.ArrayList
    $current = $roots[0]
    while ($null -ne $current) {
        [void]$chain.Add($current)
        $next = @($mine.Keys | Where-Object { @($mine[$_].Downs) -contains $current })
        if (@($next).Count -gt 1) { throw "işin göç zinciri dallanıyor ($current)" }
        $current = if (@($next).Count -eq 1) { $next[0] } else { $null }
    }
    if (@($chain).Count -ne @($mine.Keys).Count) { throw "işin göçleri tek bir zincir değil" }
    $headNumber = [regex]::Match($head, '^(\d{4})_')
    if (-not $headNumber.Success) { throw "baş göçün kimliği NNNN_ ile başlamıyor: $head" }
    $number = [int]$headNumber.Groups[1].Value
    $renames = [ordered]@{}
    foreach ($id in $chain) {
        $parts = [regex]::Match([string]$id, '^\d{4}_(.+)$')
        if (-not $parts.Success) { throw "göç kimliği NNNN_ ile başlamıyor, yeniden numaralanamaz: $id" }
        $number++
        $renames[[string]$id] = ("{0:D4}_{1}" -f $number, $parts.Groups[1].Value)
    }
    $rootId = [string]$chain[0]
    $oldDown = @($mine[$rootId].Downs)
    $pattern = '(?<![A-Za-z0-9_])(' + ((@($renames.Keys) | ForEach-Object { [regex]::Escape($_) }) -join "|") + ')(?![A-Za-z0-9_])'
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] { param($m) return [string]$renames[$m.Value] }
    $changed = @(Get-TeamDutyLines -Text (Invoke-TeamDutyGit -Tree $Tree -Arguments @("diff", "--name-only", "--diff-filter=AM", $fork, $TaskTip)))
    $encoding = New-Object System.Text.UTF8Encoding($false)
    foreach ($file in $changed) {
        $full = Join-Path $Tree ($file -replace '/', '\')
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $bytes = [System.IO.File]::ReadAllBytes($full)
        if ([Array]::IndexOf($bytes, [byte]0) -ge 0) { continue }
        $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $text = $encoding.GetString($bytes, $(if ($bom) { 3 } else { 0 }), $bytes.Length - $(if ($bom) { 3 } else { 0 }))
        $new = [regex]::Replace($text, $pattern, $evaluator)
        if ($file -ceq [string]$mine[$rootId].File) {
            $quotedDown = '"' + $head + '"'
            $new = [regex]::Replace($new, '(?m)^(down_revision\b[^=\r\n]*=\s*).*?(\r?)$', { param($m) $m.Groups[1].Value + $quotedDown + $m.Groups[2].Value })
            $new = [regex]::Replace($new, '(?m)^(Revises:[ \t]*).*?(\r?)$', { param($m) $m.Groups[1].Value + $head + $m.Groups[2].Value })
        }
        if ($new -cne $text) {
            $out = $encoding.GetBytes($new)
            if ($bom) { $out = [byte[]](@(0xEF, 0xBB, 0xBF) + $out) }
            [System.IO.File]::WriteAllBytes($full, $out)
        }
    }
    foreach ($id in @($renames.Keys)) {
        $file = [string]$mine[$id].File
        $leaf = Split-Path -Leaf $file
        if (-not $leaf.Contains($id)) { continue }
        $target = ($file.Substring(0, $file.Length - $leaf.Length)) + $leaf.Replace($id, [string]$renames[$id])
        [void](Invoke-TeamDutyGit -Tree $Tree -Arguments @("mv", $file, $target))
    }
    [void](Invoke-TeamDutyGit -Tree $Tree -Arguments @("add", "-A"))
    [void](Invoke-TeamDutyGit -Tree $Tree -Arguments @("commit", "-q", "--amend", "--no-edit"))
    $after = @(Get-TeamDutyHeads -Migrations (Get-TeamDutyMigrations -Tree $Tree -Revision "HEAD"))
    if (@($after).Count -ne 1) { throw "yeniden zincirden sonra $(@($after).Count) baş var: $($after -join ', ')" }
    $said = @($renames.Keys | ForEach-Object { "$_ -> $($renames[$_])" }) -join ", "
    return "göç yeniden zincirlendi ($said; $rootId artık $head sonrası, tek baş $($after[0]))"
}

function Get-TeamDutyTaskTests {
    <# The task's own test files, as guard entries (the guard runner runs them the same way). #>
    param([string[]]$Files = @())
    $tests = New-Object System.Collections.ArrayList
    foreach ($file in @($Files)) {
        $id = "task-" + (([string]$file).ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim("-")
        if ($file -cmatch '^scripts/tests/[^/]+\.tests\.ps1$') {
            [void]$tests.Add([pscustomobject]@{ id = $id; kind = "powershell"; path = $file; label = "işin kendi testi kırmızı ($file)" })
        }
        elseif ($file -cmatch '^services/api/tests/(.+/)?test_[^/]+\.py$') {
            [void]$tests.Add([pscustomobject]@{ id = $id; kind = "pytest"; path = $file; label = "işin kendi testi kırmızı ($file)" })
        }
    }
    return @($tests.ToArray())
}

function New-TeamDutyOutcome {
    param([string]$Outcome, [string]$Kind = "", [string]$Why = "", [string]$Sha = "")
    if ($Why.Length -gt $script:TeamDutyMaxWhy) { $Why = $Why.Substring(0, $script:TeamDutyMaxWhy) + " [...]" }
    return [pscustomobject]@{ Outcome = $Outcome; Kind = $Kind; Why = $Why; Sha = $Sha }
}

function Resolve-TeamDutyIntegrationTask {
    <#
    .SYNOPSIS
        Merge one task branch into integrate/<cycle>, resolving what is additive. Returns
        { Outcome: merged | escalate | failed, Kind, Why, Sha }. Only `merged` moves the
        integration branch.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main",
        [string]$Python = ""
    )
    $integration = "integrate/$CycleId"
    $tree = (New-TeamWorktree -RepoRoot $RepoRoot -Branch $integration -Base $Base).Path
    if (Invoke-TeamDutyGit -Tree $tree -Arguments @("status", "--porcelain")) {
        return (New-TeamDutyOutcome -Outcome "failed" -Why "entegrasyon çalışma ağacı temiz değil ($tree)")
    }
    $before = Invoke-TeamDutyGit -Tree $tree -Arguments @("rev-parse", "HEAD")
    $tipRun = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/$Branch")
    if (-not $tipRun.Success) { return (New-TeamDutyOutcome -Outcome "failed" -Why "iş dalı yok: $Branch") }
    $tip = $tipRun.StdOut.Trim()
    if ((Invoke-TeamGit -WorkingDirectory $tree -Arguments @("merge-base", "--is-ancestor", $tip, "HEAD")).ExitCode -eq 0) {
        return (New-TeamDutyOutcome -Outcome "merged" -Why "zaten birleşmiş" -Sha $before)
    }

    $scratch = Join-Path $RepoRoot (".claude\worktrees\duty-resolve\" + ($CycleId -replace '[^A-Za-z0-9.-]', '-') + "-" + (($Branch -split '/')[-1] -replace '[^A-Za-z0-9.-]', '-'))
    if (Test-Path -LiteralPath $scratch) {
        [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "remove", "--force", $scratch))
        if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
        [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "prune"))
    }
    [void](Invoke-TeamDutyGit -Tree $RepoRoot -Arguments @("worktree", "add", "--detach", $scratch, $before))
    try {
        $message = "merge: $Branch into $integration (Proje Yöneticisi çözdü)"
        $merge = Invoke-TeamGit -WorkingDirectory $scratch -Arguments @("-c", "merge.conflictstyle=diff3", "merge", "--no-ff", "--no-commit", $Branch)
        $conflicted = @(Get-TeamDutyLines -Text (Invoke-TeamDutyGit -Tree $scratch -Arguments @("diff", "--name-only", "--diff-filter=U")))
        if (-not $merge.Success -and @($conflicted).Count -eq 0) {
            return (New-TeamDutyOutcome -Outcome "failed" -Why ("git merge: " + (($merge.StdOut + " " + $merge.StdErr) -replace '\s+', ' ').Trim()))
        }
        $protected = @($conflicted | Where-Object { $null -ne (Get-TeamAreaProtection -Path $_) })
        if (@($protected).Count -gt 0) {
            return (New-TeamDutyOutcome -Outcome "escalate" -Kind "korunan dosya" -Why ("çakışmada korunan dosya var: " + ($protected -join ", ")))
        }
        $notAdditive = New-Object System.Collections.ArrayList
        $encoding = New-Object System.Text.UTF8Encoding($false)
        foreach ($file in $conflicted) {
            $stages = @(Get-TeamDutyLines -Text (Invoke-TeamDutyGit -Tree $scratch -Arguments @("ls-files", "-u", "--", $file)) | ForEach-Object { ($_ -split '\s+')[2] })
            if (@($stages) -notcontains "2" -or @($stages) -notcontains "3") { [void]$notAdditive.Add("$file (bir taraf siliyor)"); continue }
            $full = Join-Path $scratch ($file -replace '/', '\')
            $bytes = [System.IO.File]::ReadAllBytes($full)
            if ([Array]::IndexOf($bytes, [byte]0) -ge 0) { [void]$notAdditive.Add("$file (ikili dosya)"); continue }
            $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
            $text = $encoding.GetString($bytes, $(if ($bom) { 3 } else { 0 }), $bytes.Length - $(if ($bom) { 3 } else { 0 }))
            $json = $file.ToLowerInvariant().EndsWith(".json")
            $resolved = Resolve-TeamDutyConflictText -Text $text -Json $json
            if (-not $resolved.Ok) { [void]$notAdditive.Add($file); continue }
            if ($json) {
                try { [void](ConvertFrom-Json -InputObject $resolved.Text) }
                catch { return (New-TeamDutyOutcome -Outcome "failed" -Why "birleşim sonrası $file geçerli JSON değil") }
            }
            $out = $encoding.GetBytes($resolved.Text)
            if ($bom) { $out = [byte[]](@(0xEF, 0xBB, 0xBF) + $out) }
            [System.IO.File]::WriteAllBytes($full, $out)
            [void](Invoke-TeamDutyGit -Tree $scratch -Arguments @("add", "--", $file))
        }
        if (@($notAdditive).Count -gt 0) {
            return (New-TeamDutyOutcome -Outcome "escalate" -Kind "ekleme değil" -Why ("çözüm öteki tarafın satırlarını silecek ya da değiştirecekti: " + (@($notAdditive.ToArray()) -join ", ")))
        }
        [void](Invoke-TeamDutyGit -Tree $scratch -Arguments @("commit", "-q", "-m", $message))
        $done = New-Object System.Collections.ArrayList
        if (@($conflicted).Count -gt 0) { [void]$done.Add("eklemeli birleşim: " + ($conflicted -join ", ")) }
        $chain = Update-TeamDutyMigrationChain -Tree $scratch -Before $before -TaskTip $tip
        if ($chain) { [void]$done.Add($chain) }

        $list = Read-TeamGuardList -Path (Join-Path $scratch "team\guards.json")
        if ($list.refused) { return (New-TeamDutyOutcome -Outcome "failed" -Why ("koruyucu listesi okunamadı: " + $list.reason)) }
        $fork = Invoke-TeamDutyGit -Tree $scratch -Arguments @("merge-base", $before, $tip)
        $taskFiles = @(Get-TeamDutyLines -Text (Invoke-TeamDutyGit -Tree $scratch -Arguments @("diff", "--name-only", "--diff-filter=AM", $fork, "HEAD^2")))
        $guards = @($list.guards)
        foreach ($test in @(Get-TeamDutyTaskTests -Files $taskFiles)) {
            if (@($guards | Where-Object { $_.path -ceq $test.path }).Count -eq 0) { $guards += $test }
        }
        $run = Invoke-TeamGuards -Worktree $scratch -List $guards -Python $Python
        if ($run.status -ne "green") {
            $lines = @($run.rows | Where-Object { $_.outcome -ne "green" } | ForEach-Object { (Get-TeamGuardLine -Row $_) + $(if ($_.detail) { " - " + (($_.detail -replace '\s+', ' ').Trim()) } else { "" }) })
            return (New-TeamDutyOutcome -Outcome "escalate" -Kind "koruyucu kırmızı" -Why ("birleşmiş ağaçta: " + ($lines -join "; ")))
        }
        $sha = Invoke-TeamDutyGit -Tree $scratch -Arguments @("rev-parse", "HEAD")
        $now = Invoke-TeamDutyGit -Tree $tree -Arguments @("rev-parse", "HEAD")
        if ($now -ne $before) { return (New-TeamDutyOutcome -Outcome "failed" -Why "entegrasyon dalı çözüm sırasında ilerledi ($before -> $now)") }
        [void](Invoke-TeamDutyGit -Tree $tree -Arguments @("merge", "--ff-only", "-q", $sha))
        [void]$done.Add("koruyucular yeşil ($(@($run.rows).Count))")
        return (New-TeamDutyOutcome -Outcome "merged" -Why ($done -join "; ") -Sha $sha)
    }
    catch { return (New-TeamDutyOutcome -Outcome "failed" -Why (([string]$_.Exception.Message) -replace '\s+', ' ')) }
    finally {
        [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "remove", "--force", $scratch))
        if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue }
        [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "prune"))
    }
}

function Invoke-TeamDutyResolveIntegration {
    <#
    .SYNOPSIS
        The duty action `resolve_integration` over the team's store: every task stopped with an
        integration conflict (or only -TaskIds) is resolved and written back. One row a task:
        { Task, Outcome: merged | escalated | failed, Reason }.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)]$Store,
        [string]$Base = "main",
        [string]$Python = "",
        [string[]]$TaskIds = @()
    )
    $queue = Read-TeamQueueApi -Store $Store
    $results = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamDutyResolveCandidates -Queue $queue)) {
        $id = [string]$task.id
        if (@($TaskIds).Count -gt 0 -and @($TaskIds) -cnotcontains $id) { continue }
        $attempt = Get-TeamDutyResolveAttempt -Reason ([string]$task.reason)
        Set-TeamProperty -InputObject $task -Name "reason" -Value ($script:TeamDutyResolving + "$($script:TeamDutyConflictReason) (deneme $attempt/2)")
        Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        if (@(Save-TeamQueueApi -Store $Store -Queue $queue -SkipStale) -contains $id) { continue }

        $outcome = $null
        try { $outcome = Resolve-TeamDutyIntegrationTask -RepoRoot $RepoRoot -CycleId $CycleId -Branch ([string]$task.branch) -Base $Base -Python $Python }
        catch { $outcome = New-TeamDutyOutcome -Outcome "failed" -Why (([string]$_.Exception.Message) -replace '\s+', ' ') }

        $said = "failed"
        if ($outcome.Outcome -eq "merged") {
            Set-TeamProperty -InputObject $task -Name "state" -Value "merged"
            Set-TeamProperty -InputObject $task -Name "integration_branch" -Value "integrate/$CycleId"
            Set-TeamProperty -InputObject $task -Name "reason" -Value ""
            $said = "merged"
        }
        elseif ($outcome.Outcome -eq "escalate") {
            Set-TeamProperty -InputObject $task -Name "reason" -Value ((Get-TeamDutyPrefix -Kind "escalated") + "entegrasyon çakışması, $($outcome.Kind): $($outcome.Why)")
            $said = "escalated"
        }
        elseif ($attempt -ge 2) {
            Set-TeamProperty -InputObject $task -Name "reason" -Value ((Get-TeamDutyPrefix -Kind "escalated") + "entegrasyon çakışması, iki çözüm denemesi başarısız: $($outcome.Why)")
            $said = "escalated"
        }
        else {
            Set-TeamProperty -InputObject $task -Name "reason" -Value ($script:TeamDutyResolveFailedOnce + $outcome.Why)
        }
        Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        [void](Save-TeamQueueApi -Store $Store -Queue $queue -SkipStale)
        [void]$results.Add([pscustomobject]@{ Task = $id; Outcome = $said; Reason = [string]$task.reason; Sha = [string]$outcome.Sha })
    }
    return @($results.ToArray())
}
