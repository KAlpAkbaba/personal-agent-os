<#
.SYNOPSIS
    After a release, the JARVIS table in docs/ROADMAP.md follows the queue (card
    release-moves-roadmap-rows, team/plans/release-moves-roadmap-rows-adr.md): a MISSING row with
    a released card becomes PARTIAL, a PARTIAL row names its released and its open cards, and a
    row that looks finished is only PROPOSED for HAVE - the Proje Yöneticisi writes that.

.DESCRIPTION
    The owner, 2026-10-07: "her iş yarım yapılmış neden?" - the table was edited by hand and the
    releases did not move it ('Knows his money' read MISSING the morning after money-ledger was
    released). The lead runs this after every release, on the lead branch:

      1. Every card of the queue is matched to a JARVIS row by its roadmap_row (the same rule as
         the İlerleme strip, services/api/app/team/progress.py: the same words, the same name
         before a remark, or one the other's leading words - at least three - and its aliases).
         A card no row names is left out and listed as "satır dışı".
      2. A card is RELEASED when its state is released or awaiting_real_evidence, or done with a
         40-hex sha; it is OPEN in every state before that and when stopped; done without a sha
         (merged into another card) is neither.
      3. A MISSING row with a released card becomes PARTIAL. A PARTIAL row with a card gets one
         segment at the end of its state cell, the script's own and rewritten whole every run:
           [kartlar: yayında <id> (<sha 8>), ...; kalan: <id>, ... | yok]
         A HAVE row, and a MISSING row with no released card, are not touched.
      4. A PARTIAL row with a released card, no open card and the strip's staging_proven true is
         printed as "ÖNERİ VAR:" - the script never writes HAVE (the lead decides and writes it).

    The cell is a function of the queue alone: a second run on the same queue changes nothing.
    With -Commit the change (if any) is committed alone - docs/ROADMAP.md only - with the release
    sha in the message; on main it refuses.

    Exit codes: 0 done (changed or not); 2 a parameter or an input is wrong (nothing written);
    12 the commit failed.

.PARAMETER Sha
    The release's 40-hex sha (required with -Commit; named in the message).

.PARAMETER StripFile
    The İlerleme strip as JSON (GET /v1/team/office, its `progress`, or its `progress.proof`).
    Without it, and without -QueueUrl, no HAVE is proposed.

.EXAMPLE
    .\scripts\team\roadmap-rows.ps1 -QueueUrl https://core.example/ -QueueToken C:\path\token.txt -Sha <40-hex> -Commit
    .\scripts\team\roadmap-rows.ps1 -QueuePath team\queue.json -StripFile office.json
#>
[CmdletBinding()]
param(
    [string]$Worktree = "",
    [string]$Roadmap = "",
    [string]$QueuePath = "",
    [string]$StripFile = "",
    [string]$QueueUrl = "",
    # A PATH to the file holding the owner-session token; a token is never a parameter.
    [string]$QueueToken = "",
    [string]$Sha = "",
    [switch]$Commit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

$tree = if ($Worktree) { $Worktree } else { $repoRoot }
$roadmapPath = if ($Roadmap) { $Roadmap } else { Join-Path $tree "docs\ROADMAP.md" }
$utf8 = New-Object System.Text.UTF8Encoding($false)

$jarvisHeading = "### What JARVIS does"
$segmentPattern = '\s*\[kartlar: [^\]]*\]'
$releasedStates = @("released", "awaiting_real_evidence")
$openStates = @("proposed", "awaiting_owner", "approved", "assigned", "in_progress", "inspecting", "returned", "merged", "awaiting_release", "stopped")
# progress.py ROW_ALIASES: the queue's wordings that name a JARVIS row in other words.
$rowAliases = [ordered]@{
    "browser-use, anywhere" = "Researches anything, reads the world's data"
    "records everything"    = "Records everything and tells him, whenever he asks"
    "repairs"               = "Repairs and improves itself"
}

if ($Sha -and $Sha -cnotmatch '^[0-9a-f]{40}$') { Write-Host "-Sha is a 40-hex sha; nothing was done"; exit 2 }
if ($Commit -and -not $Sha) { Write-Host "-Commit needs -Sha (the release); nothing was done"; exit 2 }
if ($QueueUrl -and -not $QueueToken) { Write-Host "-QueueUrl needs -QueueToken: the path of a file holding the token; nothing was done"; exit 2 }
if (-not (Test-Path -LiteralPath $roadmapPath)) { Write-Host "no roadmap at $roadmapPath; nothing was done"; exit 2 }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

# The branch is checked BEFORE anything is written: a refusal on main leaves the file as it was.
$branchName = ""
if ($Commit) {
    $branch = Invoke-TeamGit -WorkingDirectory $tree -Arguments @("rev-parse", "--abbrev-ref", "HEAD") -TimeoutSeconds 60
    $branchName = ([string]$branch.StdOut).Trim()
    if (-not $branch.Success -or -not $branchName -or $branchName -in @("main", "master", "HEAD")) {
        Write-Host "refused: -Commit runs on the lead branch, not on '$branchName'; nothing was done"
        exit 12
    }
}

# ------------------------------------------------------------------ the row names (progress.py's rule)

function Get-PlainText { param([string]$Text) return ((($Text -replace '\*\*', '') -split '\s+') -join ' ').Trim() }
function Get-NormText { param([string]$Text) return (Get-PlainText $Text).ToLowerInvariant().Trim(" .:;-$([char]0x2014)".ToCharArray()) }
function Get-BaseText {
    param([string]$Text)
    $t = Get-PlainText $Text
    while ($true) {
        $shorter = $t -replace '\s*\([^()]*\)\s*$', ''
        if ($shorter -eq $t) { return (Get-NormText $t) }
        $t = $shorter
    }
}
function Get-HeadText {
    param([string]$Text)
    return (($Text -split "\s+[-$([char]0x2014)$([char]0x2013):]\s+|:\s+", 2)[0]).Trim(" .:;-$([char]0x2014)".ToCharArray())
}
function Test-Starts {
    param([string]$Long, [string]$Short)
    if ($Long -ceq $Short) { return $true }
    return (@($Short -split ' ').Count -ge 3 -and $Long.StartsWith($Short, [System.StringComparison]::Ordinal) -and $Long.Length -gt $Short.Length -and -not [char]::IsLetterOrDigit($Long[$Short.Length]))
}
function Test-NamesRow {
    param([string]$Row, [string]$Ref, [switch]$NoBase)
    $a = Get-NormText $Row
    $b = if ($NoBase) { Get-NormText $Ref } else { Get-BaseText $Ref }
    if (-not $a -or -not $b) { return $false }
    if ($a.Length -le $b.Length) { $short = $a; $long = $b } else { $short = $b; $long = $a }
    return ((Test-Starts -Long $long -Short $short) -or ((Get-HeadText $a) -ceq (Get-HeadText $b)))
}
function Resolve-Row {
    param([string]$Ref, [string[]]$Names)
    foreach ($name in $Names) { if (Test-NamesRow -Row $name -Ref $Ref) { return $name } }
    $base = Get-BaseText $Ref
    foreach ($key in $rowAliases.Keys) {
        if ($base -ceq $key -or ($base.StartsWith($key, [System.StringComparison]::Ordinal) -and -not ($base.Length -gt $key.Length -and [char]::IsLetterOrDigit($base[$key.Length])))) {
            foreach ($name in $Names) { if (Test-NamesRow -Row $name -Ref $rowAliases[$key]) { return $name } }
        }
    }
    return $null
}

# ------------------------------------------------------------------ the table

$text = [System.IO.File]::ReadAllText($roadmapPath, $utf8)
$newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
$lines = [System.Collections.ArrayList]@($text -split "\r?\n", -1)
$start = -1
for ($i = 0; $i -lt $lines.Count; $i++) { if ([string]$lines[$i] -clike "$jarvisHeading*") { $start = $i; break } }
if ($start -lt 0) { Write-Host "no '$jarvisHeading' table in $roadmapPath; nothing was done"; exit 2 }
$rows = New-Object System.Collections.ArrayList
$tableLines = 0
for ($i = $start + 1; $i -lt $lines.Count; $i++) {
    $line = [string]$lines[$i]
    if ($line.StartsWith("#")) { break }
    if (-not $line.TrimStart().StartsWith("|")) { continue }
    $tableLines++
    if ($tableLines -le 2) { continue }  # the header and its separator
    $parts = $line -split '\|', -1
    if ($parts.Count -lt 4) { continue }
    $name = Get-PlainText $parts[1]
    $cell = $parts[$parts.Count - 2].Trim()
    $first = [regex]::Match($cell, '\*\*(.+?)\*\*')
    $word = if ($first.Success) { $first.Groups[1].Value.Trim().ToUpperInvariant() } else { "" }
    $state = if ($word.StartsWith("NEVER")) { "never" } elseif ($word -ceq "HAVE") { "have" } elseif ($word -ceq "PARTIAL") { "partial" } elseif ($word -ceq "MISSING") { "missing" } else { "unknown" }
    [void]$rows.Add([pscustomobject]@{ Index = $i; Name = $name; State = $state; Parts = $parts; Released = (New-Object System.Collections.ArrayList); Open = (New-Object System.Collections.ArrayList) })
}
if ($rows.Count -eq 0) { Write-Host "the JARVIS table has no rows; nothing was done"; exit 2 }
$names = @($rows | Where-Object { $_.State -ne "never" } | ForEach-Object { $_.Name })

# ------------------------------------------------------------------ the queue and the strip

$store = $null
if ($QueueUrl) { $store = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }
try {
    $queue = if ($store) { Get-TeamQueueApi -Store $store } else { Read-TeamJson -Path $(if ($QueuePath) { $QueuePath } else { Join-Path $tree "team\queue.json" }) }
}
catch { Write-Host "the queue could not be read ($($_.Exception.Message)); nothing was done"; exit 2 }

$strip = $null
try {
    if ($StripFile) { $strip = Read-TeamJson -Path $StripFile }
    elseif ($store) { $strip = Invoke-TeamApi -Store $store -Method "GET" -Path "/v1/team/office" }
}
catch { Write-Host "risk: the strip could not be read ($($_.Exception.Message)); no HAVE is proposed" }
$proofRows = @()
if ($null -ne $strip) {
    $node = $strip
    foreach ($step in @("progress", "proof")) { $inner = Get-TeamProperty -InputObject $node -Name $step -Default $null; if ($null -ne $inner) { $node = $inner } }
    $proofRows = @(Get-TeamProperty -InputObject $node -Name "rows" -Default @())
}

$outside = New-Object System.Collections.ArrayList
foreach ($task in (Get-TeamTasks -Queue $queue)) {
    $id = [string]$task.id
    $taskState = [string](Get-TeamProperty -InputObject $task -Name "state" -Default "")
    $taskSha = [string](Get-TeamProperty -InputObject $task -Name "sha" -Default "")
    $released = ($releasedStates -contains $taskState) -or ($taskState -eq "done" -and $taskSha -cmatch '^[0-9a-f]{40}$')
    $open = $openStates -contains $taskState
    if (-not $released -and -not $open) { continue }
    $rowName = Resolve-Row -Ref ([string](Get-TeamProperty -InputObject $task -Name "roadmap_row" -Default "")) -Names $names
    if ($null -eq $rowName) { [void]$outside.Add($id); continue }
    $row = @($rows | Where-Object { $_.Name -ceq $rowName })[0]
    if ($released) {
        $short = if ($taskSha -cmatch '^[0-9a-f]{40}$') { $taskSha.Substring(0, 8) } else { "sha yok" }
        [void]$row.Released.Add("$id ($short)")
    }
    else { [void]$row.Open.Add($id) }
}

# ------------------------------------------------------------------ the cells

$changed = 0
foreach ($row in $rows) {
    if ($row.State -ne "missing" -and $row.State -ne "partial") { continue }
    $released = @($row.Released | Sort-Object)
    $open = @($row.Open | Sort-Object)
    if ($row.State -eq "missing" -and $released.Count -eq 0) { continue }
    if ($released.Count + $open.Count -eq 0) { continue }
    $old = [string]$row.Parts[$row.Parts.Count - 2]
    $cell = ($old.Trim() -replace $segmentPattern, '')
    if ($row.State -eq "missing") {
        $cell = ([regex]'\*\*MISSING\*\*').Replace($cell, "**PARTIAL**", 1)
        Write-Host "YARIM: $($row.Name) <- $($released -join ', ')"
    }
    $left = if ($open.Count) { $open -join ', ' } else { "yok" }
    $done = if ($released.Count) { "yayında $($released -join ', ')" } else { "yayında yok" }
    $cell = "$cell [kartlar: $done; kalan: $left]"
    $parts = @($row.Parts)
    $parts[$parts.Count - 2] = " $cell "
    $line = $parts -join '|'
    if ($line -cne [string]$lines[$row.Index]) { $lines[$row.Index] = $line; $changed++ }

    $proven = $false
    foreach ($proofRow in $proofRows) {
        $proofName = [string](Get-TeamProperty -InputObject $proofRow -Name "name" -Default "")
        if ($proofName -and (Test-NamesRow -Row $row.Name -Ref $proofName -NoBase) -and [bool](Get-TeamProperty -InputObject $proofRow -Name "staging_proven" -Default $false)) { $proven = $true }
    }
    if ($released.Count -gt 0 -and $open.Count -eq 0 -and $proven) {
        Write-Host "ÖNERİ VAR: $($row.Name) - kartların hepsi yayında ve şerit staging'de kanıtlı; kararı Proje Yöneticisi yazar"
    }
}
if ($outside.Count) { Write-Host "satır dışı (JARVIS satırı bulunamadı): $(@($outside | Sort-Object) -join ', ')" }

if ($changed -eq 0) { Write-Host "değişiklik yok: the JARVIS table already follows the queue" }
else {
    [System.IO.File]::WriteAllText($roadmapPath, ($lines.ToArray() -join $newline), $utf8)
    Write-Host "$changed JARVIS row(s) written to $roadmapPath"
}
if (-not $Commit) { exit 0 }

# ------------------------------------------------------------------ one commit, on the lead branch
# Whenever the file differs from HEAD - also a change an earlier run wrote and did not commit.

$relative = [System.IO.Path]::GetFullPath($roadmapPath).Substring([System.IO.Path]::GetFullPath($tree).TrimEnd('\').Length + 1)
$status = Invoke-TeamGit -WorkingDirectory $tree -Arguments @("status", "--porcelain", "--", $relative) -TimeoutSeconds 60
if ($status.Success -and -not ([string]$status.StdOut).Trim()) { Write-Host "nothing to commit"; exit 0 }
$added = Invoke-TeamGit -WorkingDirectory $tree -Arguments @("add", "--", $relative) -TimeoutSeconds 60
$made = $null
if ($added.Success) {
    $made = Invoke-TeamGit -WorkingDirectory $tree -Arguments @("commit", "--quiet", "-m", "roadmap: the JARVIS rows after the release $Sha (scripts/team/roadmap-rows.ps1)", "--", $relative) -TimeoutSeconds 120
}
if (-not $added.Success -or -not $made.Success) {
    $err = if ($made) { $made.StdErr } else { $added.StdErr }
    Write-Host "the commit failed: $((([string]$err) -replace '\s+', ' ').Trim())"
    exit 12
}
Write-Host "committed on $branchName for the release $Sha"
exit 0
