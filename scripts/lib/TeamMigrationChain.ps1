<#
.SYNOPSIS
    The rule that puts an approved branch's NEW alembic migrations on the integration branch's
    tip (card migration-rechain-on-merge, owner-approved proposal 2026-10-06-goc-numarasi-entegrasyonda).

.DESCRIPTION
    Two workers who start beside each other both build their migration on the same tip and both
    call it 0067. Each branch is right alone; side by side on the integration branch the chain has
    two heads and the work stopped as "entegrasyon dalında çakışma" for the Danışman to renumber by
    hand (ADR-0224 addendum). This file is that hand rule as a function.

    PURE: no git, no file is read or written. The caller (TeamRun.ps1's Merge-TeamBranch) gives the
    files as objects - Path (repository-relative, forward slashes) and Text - and applies the plan.

      Existing      the integration branch's version files; InMain = $true when main has it too.
      New           the version files the branch ADDED; InBase = $true when the base has the path
                    (a caller's mistake: a migration of main is never renumbered - the plan stops).
      ChangedTests  the branch's added/changed services/api/tests/** files.

    The revision and down_revision lines are read with the two patterns of
    services/api/tests/unit/test_migration_model_agreement.py (one rule, two readers).

    Windows PowerShell 5.1, StrictMode. UTF-8 with a byte-order mark: the reasons are Turkish.
#>

Set-StrictMode -Version Latest

$script:TeamMigrationRevisionPattern = '(?m)^revision:?\s*(?::\s*str\s*)?=\s*"([^"]+)"'
$script:TeamMigrationDownPattern = '(?m)^down_revision:?[^=\n]*=\s*(?:"([^"]+)"|None)'
$script:TeamMigrationTablePattern = 'op\.(?:create_table|drop_table|add_column|drop_column|alter_column|create_index|batch_alter_table)\(\s*["'']([^"'']+)["'']'
$script:TeamMigrationIdLimit = 32

function Test-TeamMigrationVersionPath {
    <# Whether a repository path is an alembic version file (the form of TeamRelease.ps1's Test-TeamMigrationPath). #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return ((($Path -replace '\\', '/')) -match '(^|/)alembic/versions/[^/]+\.py$')
}

function Read-TeamMigrationHeader {
    <# Revision, down_revision and whether the file is something this rule may not rechain. #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $revisions = @([regex]::Matches($Text, $script:TeamMigrationRevisionPattern) | ForEach-Object { $_.Groups[1].Value })
    $downLines = @([regex]::Matches($Text, '(?m)^down_revision\b.*$'))
    $downs = @([regex]::Matches($Text, $script:TeamMigrationDownPattern))
    $branched = (@($downLines).Count -ne 1) -or (@($downs).Count -ne 1)
    $down = $null
    if (@($downs).Count -eq 1 -and $downs[0].Groups[1].Success) { $down = $downs[0].Groups[1].Value }
    $labels = [regex]::Match($Text, '(?m)^branch_labels:?[^=\n]*=\s*(.+)$')
    if ($labels.Success -and $labels.Groups[1].Value.Trim() -ne "None") { $branched = $true }
    return [pscustomobject]@{
        Revision = if (@($revisions).Count -eq 1) { $revisions[0] } else { $null }
        RevisionCount = @($revisions).Count
        Down = $down
        Branched = $branched
    }
}

function Get-TeamMigrationHeads {
    <# The heads of a set of version files: revisions no file names as its down_revision. #>
    param([object[]]$Files = @())
    $revisions = New-Object 'System.Collections.Generic.List[string]'
    $parents = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($file in @($Files)) {
        $header = Read-TeamMigrationHeader -Text ([string]$file.Text)
        if ($header.Revision) { $revisions.Add($header.Revision) }
        if ($header.Down) { [void]$parents.Add($header.Down) }
    }
    return @($revisions | Where-Object { -not $parents.Contains($_) })
}

function Get-TeamMigrationTables {
    <# The tables a migration touches: the first string argument of the op.* calls that name one. #>
    param([AllowEmptyString()][string]$Text)
    return @([regex]::Matches($Text, $script:TeamMigrationTablePattern) | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
}

function Convert-TeamMigrationTokens {
    <#
        Every whole occurrence of a key replaced by its value, in ONE pass: a value that is itself
        another key (0068 -> 0069 beside 0067 -> 0068) is never replaced twice. Case-sensitive.
    #>
    param([AllowEmptyString()][string]$Text, [Parameter(Mandatory = $true)]$Map)
    if (@($Map.Keys).Count -eq 0 -or -not $Text) { return $Text }
    $keys = @($Map.Keys | Sort-Object -Property Length -Descending | ForEach-Object { [regex]::Escape($_) })
    $pattern = '(?<![A-Za-z0-9_])(?:' + ($keys -join '|') + ')(?![A-Za-z0-9_])'
    $lookup = $Map
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] { param($m) $lookup[$m.Value] }.GetNewClosure()
    return [regex]::Replace($Text, $pattern, $evaluator)
}

function Get-TeamMigrationLeaf {
    param([string]$Path)
    $normal = $Path -replace '\\', '/'
    $slash = $normal.LastIndexOf('/')
    return [pscustomobject]@{ Folder = $normal.Substring(0, $slash + 1); Leaf = $normal.Substring($slash + 1) }
}

function New-TeamMigrationPlan {
    param([string]$Action, [string]$Tip = "", [object[]]$Renames = @(), [object[]]$TestRewrites = @(), [object[]]$TestFiles = @(), [string]$Reason = "")
    return [pscustomobject]@{
        Action = $Action; Tip = $Tip; Renames = @($Renames); TestRewrites = @($TestRewrites); TestFiles = @($TestFiles); Reason = $Reason
    }
}

function Get-TeamMigrationChainPlan {
    <#
    .SYNOPSIS
        What to do with a branch's new migrations on the integration branch: none, rechain or stop.

    .DESCRIPTION
        Action 'none'    the new migrations already sit on the tip with the next numbers.
        Action 'rechain' Renames (OldPath, NewPath, OldRevision, NewRevision, OldDown, NewDown, Text =
                         the file's new content) and the test rewrites (TestRewrites: Path, OldToken,
                         NewToken; TestFiles: Path, Text) that put them there. Only the NNNN prefix of
                         the file name and of the revision changes; the rest of the name stays.
        Action 'stop'    Reason says why, in Turkish; the caller takes the merge back.

        The same-table check is made only when a rechain is needed: a branch whose migrations
        already chain onto the tip was written by a worker who saw the migrations below it.
    #>
    param(
        [object[]]$Existing = @(),
        [object[]]$New = @(),
        [object[]]$ChangedTests = @()
    )
    $New = @($New | Where-Object { $null -ne $_ })
    $Existing = @($Existing | Where-Object { $null -ne $_ })
    if (@($New).Count -eq 0) { return (New-TeamMigrationPlan -Action "none") }

    foreach ($file in $New) {
        if ($file.PSObject.Properties["InBase"] -and [bool]$file.InBase) {
            return (New-TeamMigrationPlan -Action "stop" -Reason ("main'deki göç yeniden numaralanmaz: " + $file.Path))
        }
    }
    $folder = (Get-TeamMigrationLeaf -Path ([string]$New[0].Path)).Folder
    foreach ($file in $New) {
        if ((Get-TeamMigrationLeaf -Path ([string]$file.Path)).Folder -ne $folder) {
            return (New-TeamMigrationPlan -Action "stop" -Reason "dalın göçleri iki ayrı klasörde")
        }
    }

    # The integration branch's own chain: exactly one head.
    $existingRevisions = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($file in $Existing) {
        $header = Read-TeamMigrationHeader -Text ([string]$file.Text)
        if ($header.Revision) { [void]$existingRevisions.Add($header.Revision) }
    }
    $heads = @(Get-TeamMigrationHeads -Files $Existing)
    if (@($heads).Count -gt 1) {
        return (New-TeamMigrationPlan -Action "stop" -Reason ("entegrasyon dalı zaten iki uçlu: " + (($heads | Sort-Object) -join ", ")))
    }
    if (@($heads).Count -eq 0) {
        return (New-TeamMigrationPlan -Action "stop" -Reason "entegrasyon dalında göç zinciri bulunamadı")
    }
    $tip = $heads[0]
    $tipNumber = [regex]::Match($tip, '^(\d{4})_')
    if (-not $tipNumber.Success) {
        return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason "uç NNNN_ biçiminde değil: $tip")
    }

    # The branch's own chain, in order: the one whose parent is outside the branch first.
    $parsed = @()
    foreach ($file in $New) {
        $header = Read-TeamMigrationHeader -Text ([string]$file.Text)
        if ($header.Branched) {
            return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason ("dallı göç yeniden zincirlenmez: " + $file.Path))
        }
        if ($header.RevisionCount -ne 1) {
            return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason ("göçün tek bir revision satırı yok: " + $file.Path))
        }
        $parsed += [pscustomobject]@{ File = $file; Revision = $header.Revision; Down = $header.Down }
    }
    $newRevisions = @($parsed | ForEach-Object { $_.Revision })
    $roots = @($parsed | Where-Object { $newRevisions -cnotcontains $_.Down })
    if (@($roots).Count -ne 1) {
        return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason "dalın göçleri tek bir zincir değil")
    }
    if (-not $roots[0].Down -or -not $existingRevisions.Contains([string]$roots[0].Down)) {
        return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason ("göç entegrasyon dalında olmayan bir ebeveyne bağlı: " + $roots[0].File.Path))
    }
    $ordered = @($roots[0])
    while (@($ordered).Count -lt @($parsed).Count) {
        $last = $ordered[-1].Revision
        $children = @($parsed | Where-Object { $_.Down -ceq $last })
        if (@($children).Count -ne 1) {
            return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason "dalın göçleri tek bir zincir değil")
        }
        $ordered += $children[0]
    }

    # The new names and numbers.
    $number = [int]$tipNumber.Groups[1].Value
    $previous = $tip
    $steps = @()
    $changed = $false
    foreach ($item in $ordered) {
        $number++
        $place = Get-TeamMigrationLeaf -Path ([string]$item.File.Path)
        # A name or id without the NNNN prefix (money_ledger.py) keeps it: only the parent decides the chain.
        $leaf = [regex]::Match($place.Leaf, '^((?:\d{8}_)?)(\d{4})(_.+\.py)$')
        $id = [regex]::Match($item.Revision, '^(\d{4})(_.+)$')
        $digits = $number.ToString("0000")
        $newRevision = if ($id.Success) { $digits + $id.Groups[2].Value } else { $item.Revision }
        if ($newRevision.Length -gt $script:TeamMigrationIdLimit) {
            return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason ("revision id $($script:TeamMigrationIdLimit) karakteri aşıyor: $newRevision"))
        }
        $newPath = if ($leaf.Success) { $place.Folder + $leaf.Groups[1].Value + $digits + $leaf.Groups[3].Value } else { [string]$item.File.Path -replace '\\', '/' }
        if ($newRevision -cne $item.Revision -or $newPath -cne ([string]$item.File.Path -replace '\\', '/') -or $previous -cne $item.Down) { $changed = $true }
        $steps += [pscustomobject]@{ Item = $item; OldPath = ([string]$item.File.Path -replace '\\', '/'); NewPath = $newPath; NewRevision = $newRevision; NewDown = $previous }
        $previous = $newRevision
    }
    if (-not $changed) { return (New-TeamMigrationPlan -Action "none" -Tip $tip) }

    # Two migrations the two workers did not see of each other must not touch one table.
    foreach ($step in $steps) {
        $mine = @(Get-TeamMigrationTables -Text ([string]$step.Item.File.Text))
        foreach ($other in $Existing) {
            if ($other.PSObject.Properties["InMain"] -and [bool]$other.InMain) { continue }
            foreach ($table in @(Get-TeamMigrationTables -Text ([string]$other.Text))) {
                if ($mine -ccontains $table) {
                    $names = (Get-TeamMigrationLeaf -Path $step.OldPath).Leaf + ", " + (Get-TeamMigrationLeaf -Path ([string]$other.Path)).Leaf
                    return (New-TeamMigrationPlan -Action "stop" -Tip $tip -Reason "aynı tablo: $table ($names)")
                }
            }
        }
    }

    # One token map for the migrations and the tests: revision ids, the old parent, the file stems.
    $map = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    foreach ($step in $steps) {
        if ($step.NewRevision -cne $step.Item.Revision) { $map[$step.Item.Revision] = $step.NewRevision }
        $oldStem = [System.IO.Path]::GetFileNameWithoutExtension($step.OldPath)
        $newStem = [System.IO.Path]::GetFileNameWithoutExtension($step.NewPath)
        if ($oldStem -cne $newStem) { $map[$oldStem] = $newStem }
    }
    $root = $steps[0]
    if ([string]$root.Item.Down -cne $root.NewDown) { $map[[string]$root.Item.Down] = $root.NewDown }

    $renames = @()
    foreach ($step in $steps) {
        $renames += [pscustomobject]@{
            OldPath = $step.OldPath; NewPath = $step.NewPath
            OldRevision = $step.Item.Revision; NewRevision = $step.NewRevision
            OldDown = $step.Item.Down; NewDown = $step.NewDown
            Text = (Convert-TeamMigrationTokens -Text ([string]$step.Item.File.Text) -Map $map)
        }
    }
    $rewrites = @()
    $testFiles = @()
    foreach ($test in @($ChangedTests | Where-Object { $null -ne $_ })) {
        $text = [string]$test.Text
        $path = [string]$test.Path -replace '\\', '/'
        $hits = @($map.Keys | Where-Object { [regex]::IsMatch($text, '(?<![A-Za-z0-9_])' + [regex]::Escape($_) + '(?![A-Za-z0-9_])') })
        if (@($hits).Count -eq 0) { continue }
        foreach ($key in $hits) { $rewrites += [pscustomobject]@{ Path = $path; OldToken = $key; NewToken = $map[$key] } }
        $testFiles += [pscustomobject]@{ Path = $path; Text = (Convert-TeamMigrationTokens -Text $text -Map $map) }
    }
    return (New-TeamMigrationPlan -Action "rechain" -Tip $tip -Renames $renames -TestRewrites $rewrites -TestFiles $testFiles)
}
