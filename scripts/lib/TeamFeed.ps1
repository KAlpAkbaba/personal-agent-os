<#
.SYNOPSIS
    The roadmap feeder's decisions (scripts/team/feed.ps1, ADR-0214 addendum 8): what counts
    as runnable, which rows the roadmap has, what a lead's feed file must be, what an edit of
    the roadmap's "Approved ideas" table may be.

.DESCRIPTION
    Dot-sourced after `NativeProcess.ps1`, `TeamQueue.ps1` and `TeamRun.ps1`. As in
    `TeamQueue.ps1`, every decision is a function that takes its inputs and returns its
    answer, so the tests drive them without a model, a repository or a clock:

      * a task is RUNNABLE when the cycle would work on it now: one of the five states in
        work, with every dependency on main (Get-TeamUnmetDependencies);
      * a roadmap ROW is read from docs/ROADMAP.md: a row of the JARVIS table, an item of
        "The order", a heading of the target section, an approved idea. A card names one of
        them exactly; a short note in brackets may follow it;
      * a FEED FILE is judged by the judge of the lead's split (Test-TeamSplit - one judge,
        not two) plus what only the feeder knows: the row exists, the count that was asked
        for, a title nobody has, and the items the lead marked `needs_owner`, which become
        ideas awaiting the owner and never tasks;
      * an EDIT of the roadmap is new rows inside the "Approved ideas" table and nothing
        else, one row per approved idea, each naming that idea's proposal.

    Get-TeamFeedSnapshot only reads: the checkout's `git status` with a hash of each file it
    lists, so that what a lead run changed can be told from what was already there.

    The functions of the path BESIDE A RUNNING CYCLE (at the end) do touch things, and say
    so: the feeder's own lock file, a throwaway worktree, and create-only writes to the store.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# The states the cycle works on (Get-TeamNextRole: run or move). A task at one of the owner's
# gates, merged, released, done or stopped fills no seat.
$script:TeamFeedRunnableStates = @("approved", "assigned", "returned", "in_progress", "inspecting")
# How the lead records the owner's approval of an idea in a task's reason.
$script:TeamFeedApprovedPrefix = "sahip onayladı"
$script:TeamFeedRoadmapPath = "docs/ROADMAP.md"
$script:TeamFeedOwnerSentenceMax = 400
# Headings of the target section that organise it; they are not rows a task can serve.
$script:TeamFeedStructuralHeadings = @("What JARVIS does", "The limits", "The order", "Approved ideas")

function Get-TeamRunnableTasks {
    <#
    .SYNOPSIS
        The tasks the cycle would work on now: in work by state, and waiting for nothing.
    #>
    param($Queue)
    $found = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamTasks -Queue $Queue)) {
        $state = [string](Get-TeamProperty -InputObject $task -Name "state" -Default "")
        if ($script:TeamFeedRunnableStates -notcontains $state) { continue }
        if (@(Get-TeamUnmetDependencies -Task $task -Queue $Queue).Count -gt 0) { continue }
        [void]$found.Add($task)
    }
    return @($found.ToArray())
}

# ------------------------------------------------------------------ the roadmap's rows

function Get-TeamFeedLines {
    <# A text as lines, whatever its line ends. #>
    param([string]$Text)
    return @((([string]$Text) -replace "`r`n", "`n") -split "`n")
}

function Get-TeamTableCells {
    <# The cells of a markdown table row; none when the line is not one. #>
    param([string]$Line)
    $text = ([string]$Line).Trim()
    if ($text.Length -lt 2 -or -not $text.StartsWith("|") -or -not $text.EndsWith("|")) { return @() }
    $inner = $text.Substring(1, $text.Length - 2)
    return @([regex]::Split($inner, '(?<!\\)\|') | ForEach-Object { $_.Trim() })
}

function Get-TeamRoadmapSection {
    <#
    .SYNOPSIS
        The text under a heading of the roadmap, up to the next heading. The heading is named
        by how it starts ("The order" finds "### The order (binding until ...)").
    #>
    param([string]$Text, [Parameter(Mandatory = $true)][string]$Heading)
    $kept = New-Object System.Collections.ArrayList
    $inside = $false
    foreach ($line in @(Get-TeamFeedLines -Text $Text)) {
        if ($line -match '^#{1,6}\s+(.*)$') {
            if ($inside) { break }
            if ($Matches[1].Trim().StartsWith($Heading, [System.StringComparison]::Ordinal)) { $inside = $true }
            continue
        }
        if ($inside) { [void]$kept.Add($line) }
    }
    return ((@($kept.ToArray()) -join "`n").Trim())
}

function Add-TeamRoadmapRowName {
    <# A row's name as it is compared: no bold marks, single spaces - and, beside it, the same
       name without a note in brackets at its end. #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.ArrayList]$Rows, [string]$Name)
    $clean = ((([string]$Name) -replace '\*\*', '') -replace '\s+', ' ').Trim()
    if (-not $clean) { return }
    if (@($Rows.ToArray()) -cnotcontains $clean) { [void]$Rows.Add($clean) }
    if ($clean -match '^(.*\S)\s*\([^()]*\)$') {
        $short = $Matches[1].Trim()
        if ($short -and @($Rows.ToArray()) -cnotcontains $short) { [void]$Rows.Add($short) }
    }
}

function Get-TeamRoadmapRows {
    <#
    .SYNOPSIS
        The rows of docs/ROADMAP.md a task can serve, read from its target section ("## The
        JARVIS target"): the first cell of each row of the JARVIS table (and its bold part;
        not a row whose state says NEVER),
        the bold title of each item of "The order", the section's other headings, and the
        bold name of each approved idea.
    #>
    param([string]$Text)
    $rows = New-Object System.Collections.ArrayList
    $inTarget = $false
    $section = ""
    foreach ($line in @(Get-TeamFeedLines -Text $Text)) {
        if ($line -match '^##\s+(.*)$') {
            $inTarget = $Matches[1].Trim().StartsWith("The JARVIS target", [System.StringComparison]::Ordinal)
            $section = ""
            continue
        }
        if (-not $inTarget) { continue }
        if ($line -match '^###\s+(.*)$') {
            $section = $Matches[1].Trim()
            $structural = $false
            foreach ($start in $script:TeamFeedStructuralHeadings) {
                if ($section.StartsWith($start, [System.StringComparison]::Ordinal)) { $structural = $true }
            }
            if (-not $structural) { Add-TeamRoadmapRowName -Rows $rows -Name $section }
            continue
        }
        if ($section.StartsWith("What JARVIS does", [System.StringComparison]::Ordinal)) {
            $cells = @(Get-TeamTableCells -Line $line)
            if (@($cells).Count -lt 2) { continue }
            $first = [string]$cells[0]
            if ($first -ceq "JARVIS" -or $first -match '^:?-{3,}:?$') { continue }
            # A row the roadmap marks NEVER ("Breaks into any system") is a limit, not work.
            if (([string]$cells[@($cells).Count - 1]) -cmatch 'NEVER') { continue }
            Add-TeamRoadmapRowName -Rows $rows -Name $first
            if ($first -match '^\*\*(.+?)\*\*') { Add-TeamRoadmapRowName -Rows $rows -Name $Matches[1] }
        }
        elseif ($section.StartsWith("The order", [System.StringComparison]::Ordinal)) {
            if ($line -match '^\s*(?:-\s+)?\d+[a-z]?\.\s+\*\*(.+?)\*\*') { Add-TeamRoadmapRowName -Rows $rows -Name $Matches[1] }
        }
        elseif ($section.StartsWith("Approved ideas", [System.StringComparison]::Ordinal)) {
            $cells = @(Get-TeamTableCells -Line $line)
            if (@($cells).Count -ge 2 -and ([string]$cells[1]) -match '^\*\*(.+?)\*\*') { Add-TeamRoadmapRowName -Rows $rows -Name $Matches[1] }
        }
    }
    return @($rows.ToArray())
}

function Test-TeamRoadmapRow {
    <#
    .SYNOPSIS
        Whether a card's roadmap_row is a row of the roadmap: the row quoted exactly, or the
        row with ONE note in brackets after it ("browser-use, anywhere (order 2b, ADR-0213)").
    #>
    param([string]$Row, [string[]]$Rows = @())
    $text = ((([string]$Row) -replace '\s+', ' ')).Trim()
    if (-not $text) { return $false }
    foreach ($known in @($Rows)) {
        $name = [string]$known
        if (-not $name) { continue }
        if ($text -ceq $name) { return $true }
        if ($text.Length -gt ($name.Length + 3) -and $text.StartsWith($name + " (", [System.StringComparison]::Ordinal) -and $text.EndsWith(")")) {
            $note = $text.Substring($name.Length + 2, $text.Length - $name.Length - 3)
            if ($note.Trim() -and $note -notmatch '[()]') { return $true }
        }
    }
    return $false
}

# ------------------------------------------------------------------ approved ideas

function Get-TeamApprovedIdeasMissing {
    <#
    .SYNOPSIS
        The ideas the owner approved that the roadmap does not name yet: a task that is `done`,
        carries a `proposal`, whose reason starts with "sahip onayladı", and whose proposal
        path (or id, in backticks) is nowhere in docs/ROADMAP.md.
    #>
    param($Queue, [string]$RoadmapText)
    $text = [string]$RoadmapText
    $found = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamTasks -Queue $Queue)) {
        if ([string](Get-TeamProperty -InputObject $task -Name "state" -Default "") -ne "done") { continue }
        $proposal = ([string](Get-TeamProperty -InputObject $task -Name "proposal" -Default "")).Trim()
        if (-not $proposal) { continue }
        $reason = ([string](Get-TeamProperty -InputObject $task -Name "reason" -Default "")).Trim()
        if (-not $reason.StartsWith($script:TeamFeedApprovedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        if ($text.IndexOf($proposal, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { continue }
        if ($text.IndexOf('`' + [string]$task.id + '`', [System.StringComparison]::Ordinal) -ge 0) { continue }
        [void]$found.Add($task)
    }
    return @($found.ToArray())
}

function Get-TeamIdeasTable {
    <# Where the "Approved ideas" table is in the roadmap's lines: its header, its first and
       its last row. Header is -1 when there is no such table. #>
    param([string[]]$Lines = @())
    $none = [pscustomobject]@{ Header = -1; First = -1; Last = -1 }
    $total = @($Lines).Count
    $heading = -1
    for ($i = 0; $i -lt $total; $i++) {
        if ($Lines[$i] -match '^#{2,6}\s+Approved ideas\b') { $heading = $i; break }
    }
    if ($heading -lt 0) { return $none }
    $header = -1
    for ($i = $heading + 1; $i -lt $total; $i++) {
        if ($Lines[$i] -match '^#{1,6}\s') { break }
        if (([string]$Lines[$i]).TrimStart().StartsWith("|")) { $header = $i; break }
    }
    if ($header -lt 0 -or ($header + 1) -ge $total -or $Lines[$header + 1] -notmatch '^\s*\|\s*:?-{3,}') { return $none }
    $last = $header + 1
    while (($last + 1) -lt $total -and ([string]$Lines[$last + 1]).TrimStart().StartsWith("|")) { $last++ }
    return [pscustomobject]@{ Header = $header; First = $header + 2; Last = $last }
}

function Test-TeamFeedRoadmapEdit {
    <#
    .SYNOPSIS
        What a lead run did to docs/ROADMAP.md, judged by the script: Problems (sentences;
        empty when the edit is sound), Added (the ideas that got their row), Rows (the rows).

    .DESCRIPTION
        Sound is: every line that was there is still there, in order; every new line is a row
        INSIDE the "Approved ideas" table - five cells, the approval date first, no cell
        empty; each new row names the proposal of exactly one idea that was asked for, and no
        idea has two. No edit at all is sound and adds nothing.
    #>
    param([string]$Before, [string]$After, [object[]]$Ideas = @())
    $problems = New-Object System.Collections.ArrayList
    $added = New-Object System.Collections.ArrayList
    $rows = New-Object System.Collections.ArrayList
    $old = @(([string]$Before) -split "`n")
    $new = @(([string]$After) -split "`n")
    $table = Get-TeamIdeasTable -Lines $old
    if ($table.Header -lt 0) {
        [void]$problems.Add("docs/ROADMAP.md has no 'Approved ideas' table to write a row into")
        return [pscustomobject]@{ Problems = @($problems.ToArray()); Added = @(); Rows = @() }
    }
    # After must be Before with lines put in: the old lines are matched in order, and whatever
    # is left over is new.
    $inserted = New-Object System.Collections.ArrayList
    $i = 0
    $oldTotal = @($old).Count
    foreach ($line in $new) {
        if ($i -lt $oldTotal -and $old[$i] -ceq $line) { $i++; continue }
        [void]$inserted.Add([pscustomobject]@{ At = $i; Text = [string]$line })
    }
    if ($i -lt $oldTotal) {
        [void]$problems.Add("the edit removes or rewrites $($oldTotal - $i) existing line(s) of docs/ROADMAP.md (from line $($i + 1)); only new rows in the 'Approved ideas' table are allowed")
        return [pscustomobject]@{ Problems = @($problems.ToArray()); Added = @(); Rows = @() }
    }
    foreach ($entry in @($inserted.ToArray())) {
        $text = [string]$entry.Text
        $shown = if ($text.Length -gt 80) { $text.Substring(0, 80) + "..." } else { $text }
        $cells = @(Get-TeamTableCells -Line $text)
        if ($entry.At -lt $table.First -or $entry.At -gt ($table.Last + 1) -or @($cells).Count -eq 0) {
            [void]$problems.Add("the edit adds a line outside the 'Approved ideas' table (at line $($entry.At + 1)): '$shown'")
            continue
        }
        if (@($cells).Count -ne 5) {
            [void]$problems.Add("an 'Approved ideas' row has five cells, this one has $(@($cells).Count) (a '|' inside a cell splits the row): '$shown'")
            continue
        }
        if (([string]$cells[0]) -notmatch '^\d{4}-\d{2}-\d{2}$') {
            [void]$problems.Add("an 'Approved ideas' row starts with the date the owner approved (YYYY-MM-DD): '$shown'")
        }
        if (@($cells | Where-Object { -not ([string]$_).Trim() }).Count -gt 0) {
            [void]$problems.Add("an 'Approved ideas' row has an empty cell: '$shown'")
        }
        $named = @(@($Ideas) | Where-Object {
                $proposal = ([string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "")).Trim()
                $proposal -and $text.IndexOf($proposal, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            })
        if (@($named).Count -eq 0) {
            [void]$problems.Add("the row names no approved idea that is missing from the roadmap (its proposal path must be in the row): '$shown'")
            continue
        }
        if (@($named).Count -gt 1) {
            [void]$problems.Add("the row names more than one idea ($((@($named) | ForEach-Object { [string]$_.id }) -join ', ')); one row per idea: '$shown'")
            continue
        }
        $id = [string]$named[0].id
        if (@($added.ToArray()) -contains $id) {
            [void]$problems.Add("more than one row was written for the idea ${id}; exactly one is allowed")
            continue
        }
        [void]$added.Add($id)
        [void]$rows.Add($text)
    }
    return [pscustomobject]@{ Problems = @($problems.ToArray()); Added = @($added.ToArray()); Rows = @($rows.ToArray()) }
}

# ------------------------------------------------------------------ the lead's feed file

function Test-TeamFeedOwnerItem {
    <# Whether the lead marked an item of its feed file as needing the owner. Anything but an
       absent, null, false or blank `needs_owner` is the mark - a card is never made of it. #>
    param($Item)
    if ($null -eq $Item -or $Item -isnot [System.Management.Automation.PSCustomObject]) { return $false }
    $property = $Item.PSObject.Properties["needs_owner"]
    if ($null -eq $property -or $null -eq $property.Value) { return $false }
    $value = $property.Value
    if ($value -is [bool]) { return [bool]$value }
    if ($value -is [string]) { return ($value.Trim().Length -gt 0) }
    return $true
}

function Test-TeamFeed {
    <#
    .SYNOPSIS
        Every reason a lead's feed file is refused, as sentences; empty when it is sound. THE
        SCRIPT judges, not the model, and the file is taken whole or refused whole.

    .DESCRIPTION
        The cards go through the judge of the lead's split (Test-TeamSplit: the fields, a free
        id, areas inside the repository, no shared file, no area of a task in work, no main,
        dependencies that exist). The feeder adds what only it knows: at most -MaxNew cards;
        a roadmap_row that IS a row of docs/ROADMAP.md (a new row is the owner's to open); a
        title no task already has. An item marked `needs_owner` is not a card: it needs a
        free id, a title and ONE sentence. An empty list is sound - "nothing can be cut".
    #>
    param($Feed, [Parameter(Mandatory = $true)]$Queue, [string[]]$RoadmapRows = @(), [int]$MaxNew = 3)
    $problems = New-Object System.Collections.ArrayList
    $cards = @(@($Feed) | Where-Object { -not (Test-TeamFeedOwnerItem -Item $_) })
    $owner = @(@($Feed) | Where-Object { Test-TeamFeedOwnerItem -Item $_ })
    if (@($cards).Count -gt $MaxNew) {
        [void]$problems.Add("the feed holds $(@($cards).Count) cards; at most $MaxNew were asked for")
    }
    if (@($owner).Count -gt $MaxNew) {
        [void]$problems.Add("the feed holds $(@($owner).Count) items for the owner; at most $MaxNew are taken in one run")
    }
    if (@($cards).Count -gt 0) {
        foreach ($problem in @(Test-TeamSplit -Split $cards -Queue $Queue)) { [void]$problems.Add([string]$problem) }
    }
    $existing = @(Get-TeamTasks -Queue $Queue)
    $titles = @{}
    foreach ($task in $existing) {
        $title = ([string](Get-TeamProperty -InputObject $task -Name "title" -Default "")).Trim().ToLowerInvariant()
        if ($title) { $titles[$title] = [string]$task.id }
    }
    $seen = @{}
    foreach ($item in @($cards + $owner)) {
        if ($null -eq $item -or $item -isnot [System.Management.Automation.PSCustomObject]) { continue }
        $id = [string](Get-TeamProperty -InputObject $item -Name "id" -Default "")
        $label = if ($id) { $id } else { "(an item without an id)" }
        $isOwner = Test-TeamFeedOwnerItem -Item $item
        $titleValue = Get-TeamProperty -InputObject $item -Name "title"
        $title = if ($titleValue -is [string]) { $titleValue.Trim().ToLowerInvariant() } else { "" }
        if ($title -and $titles.ContainsKey($title)) {
            [void]$problems.Add("${label}: '$($titleValue.Trim())' is already the title of $($titles[$title]) - the queue has this work")
        }
        if ($title) { $titles[$title] = $label }
        if (-not $isOwner) {
            # The row is checked HERE and nowhere else: a card that serves no row of the roadmap
            # is a new row, and a new row is the owner's to open.
            $row = Get-TeamProperty -InputObject $item -Name "roadmap_row"
            if ($row -is [string] -and $row.Trim() -and -not (Test-TeamRoadmapRow -Row $row -Rows $RoadmapRows)) {
                [void]$problems.Add("${label}: the roadmap_row '$($row.Trim())' is not a row of docs/ROADMAP.md (a new row is the owner's: mark the item needs_owner)")
            }
            if ($id) { $seen[$id] = $true }
            continue
        }
        if ($id -cnotmatch '^[a-z0-9][a-z0-9-]{2,63}$') {
            [void]$problems.Add("${label}: an id is 3-64 characters of a-z, 0-9 and '-'")
        }
        elseif (@($existing | Where-Object { [string]$_.id -eq $id }).Count -gt 0) {
            [void]$problems.Add("${label}: the id is already in the queue")
        }
        elseif ($seen.ContainsKey($id)) {
            [void]$problems.Add("${label}: the id is used twice in the feed")
        }
        if ($id) { $seen[$id] = $true }
        if (-not $title) { [void]$problems.Add("${label}: the field 'title' is missing") }
        $sentence = $item.PSObject.Properties["needs_owner"].Value
        # The refusal says what it measured, so the lead's report and the next run know what to fix.
        $rule = "${label}: needs_owner is one sentence (a string on one line, at most $script:TeamFeedOwnerSentenceMax characters)"
        if ($sentence -isnot [string]) {
            [void]$problems.Add("$rule; this one is not a string")
        }
        elseif ($sentence.Trim() -match "[`r`n]") {
            [void]$problems.Add("$rule; this one spans $(@($sentence.Trim() -split '\r\n|\r|\n').Count) lines")
        }
        elseif ($sentence.Trim().Length -gt $script:TeamFeedOwnerSentenceMax) {
            [void]$problems.Add("$rule; this one has $($sentence.Trim().Length) characters")
        }
    }
    return @($problems.ToArray())
}

function ConvertTo-TeamFeedTasks {
    <#
    .SYNOPSIS
        A sound feed as queue tasks, in the lead's order. Each answer has Task, Kind (card |
        owner) and, for an owner item, the proposal file to write (ProposalPath, ProposalText).

    .DESCRIPTION
        A card becomes an `approved` task (ConvertTo-TeamSplitTasks: only the fields the script
        knows; no branch, no worktree) with the reason "roadmap: <row>; fed by the lead run
        <date>" and no `proposal` - it is roadmap work, not an idea.

        An item the lead marked `needs_owner` becomes an idea `awaiting_owner`: no area, so no
        worker can be given it, and a `proposal` file - so that the day the owner approves it,
        it is the lead's to split (Test-TeamSplitCandidate). An approved task with neither an
        area nor a proposal would be moved to `assigned` and break the queue for every cycle.

        The queue is ordered by `created_at`; each task is stamped one second after the one
        before it, so the lead's order is the order of work.
    #>
    param(
        $Feed, [string[]]$RoadmapRows = @(), [Parameter(Mandatory = $true)][string]$Date,
        [double]$MaxUsd = 0, [datetime]$Now = [datetime]::UtcNow
    )
    $made = New-Object System.Collections.ArrayList
    $index = 0
    foreach ($item in @($Feed)) {
        $at = $Now.AddSeconds($index)
        $index++
        if (-not (Test-TeamFeedOwnerItem -Item $item)) {
            $task = @(ConvertTo-TeamSplitTasks -Split @($item) -Proposal ([pscustomobject]@{}) -MaxUsd $MaxUsd -Now $at)[0]
            $task.PSObject.Properties.Remove("proposal")
            Set-TeamProperty -InputObject $task -Name "reason" -Value "roadmap: $([string]$task.roadmap_row); fed by the lead run $Date"
            [void]$made.Add([pscustomobject]@{ Task = $task; Kind = "card"; ProposalPath = ""; ProposalText = "" })
            continue
        }
        $stamp = Get-TeamTimestamp -Now $at
        $id = [string]$item.id
        $title = ([string]$item.title).Trim()
        $sentence = ((([string]$item.needs_owner) -replace '\s+', ' ')).Trim()
        $row = ([string](Get-TeamProperty -InputObject $item -Name "roadmap_row" -Default "")).Trim()
        $isRow = Test-TeamRoadmapRow -Row $row -Rows $RoadmapRows
        $goal = ([string](Get-TeamProperty -InputObject $item -Name "goal" -Default "")).Trim()
        $acceptance = ([string](Get-TeamProperty -InputObject $item -Name "acceptance" -Default "")).Trim()
        $path = "team/proposals/$Date-feed-$id.md"
        $task = [ordered]@{
            id = $id; title = $title; roadmap_row = $(if ($isRow) { $row } else { "" }); state = "awaiting_owner"
            area = @(); branch = ""; worktree = ""; assignee = "lead"; reports = @()
            budget = [pscustomobject]@{ max_usd = $MaxUsd }; created_at = $stamp; updated_at = $stamp
            proposal = $path; reason = "sahibe sorulacak (lead koşusu ${Date}): $sentence"
        }
        if ($goal) { $task["goal"] = $goal }
        if ($acceptance) { $task["acceptance"] = $acceptance }
        $lines = New-Object System.Collections.ArrayList
        [void]$lines.Add("# $title")
        [void]$lines.Add("")
        [void]$lines.Add("Kaynak: lead koşusu (roadmap beslemesi), $Date. Bu bir iş kartı DEĞİL: sahibin kararını bekleyen bir fikir.")
        [void]$lines.Add("")
        # The researcher's shape (apps/web/tests/approvals/proposal-shapes.test.ts reads every
        # file under team/proposals): "## Ne" holds what the owner is asked, every section is
        # written even when empty, and the fixture FEED_SHAPE there is this text byte for byte.
        [void]$lines.Add("## Ne")
        [void]$lines.Add("")
        [void]$lines.Add($sentence)
        [void]$lines.Add("")
        [void]$lines.Add("## Roadmap satırı")
        [void]$lines.Add("")
        if ($isRow) { [void]$lines.Add($row) }
        elseif ($row) { [void]$lines.Add("Roadmap'te yok; önerilen yeni satır: $row") }
        else { [void]$lines.Add("Belirtilmedi.") }
        [void]$lines.Add(""); [void]$lines.Add("## Hedef"); [void]$lines.Add("")
        [void]$lines.Add($(if ($goal) { $goal } else { "Belirtilmedi." }))
        [void]$lines.Add(""); [void]$lines.Add("## Kabul"); [void]$lines.Add("")
        [void]$lines.Add($(if ($acceptance) { $acceptance } else { "Belirtilmedi." }))
        [void]$lines.Add(""); [void]$lines.Add("## Karar"); [void]$lines.Add("")
        [void]$lines.Add("Sahip: evet / hayır / ertele.")
        [void]$made.Add([pscustomobject]@{
                Task = [pscustomobject]$task; Kind = "owner"; ProposalPath = $path
                ProposalText = ((@($lines.ToArray()) -join "`n") + "`n")
            })
    }
    return @($made.ToArray())
}

# ------------------------------------------------------------------ the prompt

function New-TeamFeedCard {
    <#
    .SYNOPSIS
        The prompt of the lead's feed run: what to cut, into which file, from which rows, within
        which limits, beside which tasks - and the idea rows to write, when there are any.
    #>
    param(
        [Parameter(Mandatory = $true)]$Queue,
        [string]$RoadmapText,
        [Parameter(Mandatory = $true)][string]$FeedFile,
        [int]$MaxNew = 3,
        [Parameter(Mandatory = $true)][string]$Date,
        [object[]]$Ideas = @()
    )
    $tasks = @(Get-TeamTasks -Queue $Queue)
    $asked = @($Ideas)
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Feed run (lead, feed-$Date)")
    [void]$lines.Add("")
    [void]$lines.Add("This run does ONE thing: keep the worker seats full (ADR-0214 addendum 8). The queue has fewer runnable")
    [void]$lines.Add("tasks than there are worker seats, so you cut the NEXT unfinished items of the roadmap into task cards.")
    [void]$lines.Add("You run no command and dispatch no agent. You write exactly ONE file, the one named in feed_file" + $(if (@($asked).Count -gt 0) { "," } else { "." }))
    if (@($asked).Count -gt 0) { [void]$lines.Add("and you add the rows named at the end of this card to the 'Approved ideas' table of docs/ROADMAP.md.") }
    [void]$lines.Add("Everything else is read-only for you: the script that started you compares the checkout before and after,")
    [void]$lines.Add("and a run that changed any other file is refused whole - nothing it wrote is used.")
    [void]$lines.Add("")
    [void]$lines.Add("- feed_file: $FeedFile")
    [void]$lines.Add("- max_new: $MaxNew")
    [void]$lines.Add("- date: $Date")
    [void]$lines.Add("")
    [void]$lines.Add("## What to read")
    [void]$lines.Add("docs/ROADMAP.md ('The order', 'Approved ideas', the JARVIS table), the v1 master checklist and the feature")
    [void]$lines.Add("matrix under docs/product/ (PERSONALAGENTOS_V1_MASTER_CHECKLIST.md, PERSONALAGENTOS_V1_FEATURE_MATRIX.md),")
    [void]$lines.Add("docs/TEAM_PROTOCOL.md (sections 3a and 4), and the code of every area you are about to name.")
    [void]$lines.Add("")
    [void]$lines.Add("## What to write")
    [void]$lines.Add("feed_file is a JSON list of at most max_new cards and at most max_new owner items, in the order they should")
    [void]$lines.Add("be worked on. An empty list [] is an honest answer when the next item cannot be cut. An item is a task card:")
    [void]$lines.Add("id (a-z, 0-9, '-'; 3-64; not in the queue), title, roadmap_row (one of the rows listed below, quoted exactly;")
    [void]$lines.Add("one short note in brackets may follow it, with no brackets inside the note), area (a list of")
    [void]$lines.Add("repository-relative paths, at most $script:TeamMaxAreaEntries entries), goal, acceptance,")
    [void]$lines.Add("evidence_expected; optionally depends_on (ids) and needs_integration (true when existing code or a library")
    [void]$lines.Add("may already solve it). The area passes your role file's checklist: the directory that package's TESTS live")
    [void]$lines.Add("in, a new Python package's __init__.py, every file the goal or the acceptance names, the file an inspector")
    [void]$lines.Add("will plainly send the worker to.")
    [void]$lines.Add("Take the NEXT unfinished items: the first item of 'The order' that is not done and not already in the queue")
    [void]$lines.Add("below, then the master checklist's next open items. Never restate a task the queue has, in any state.")
    [void]$lines.Add("")
    [void]$lines.Add("## What needs the owner is not a card")
    [void]$lines.Add("An item that needs a new external dependency or account, a paid service, an irreversible or production")
    [void]$lines.Add("action (a release, a change on the live host, a deletion), or a roadmap row that does not exist, is NOT a")
    [void]$lines.Add("card. Write it as an item with id, title and needs_owner: ONE sentence on ONE line, at most")
    [void]$lines.Add("$script:TeamFeedOwnerSentenceMax characters, saying what the owner must decide (put the detail in goal; roadmap_row,")
    [void]$lines.Add("goal and acceptance if you have them; no area). A longer needs_owner, or one with a line break, refuses")
    [void]$lines.Add("the whole file. The script queues it as an idea awaiting the owner, never as work.")
    [void]$lines.Add("")
    [void]$lines.Add("## The script judges, not you")
    [void]$lines.Add("The file is taken WHOLE or refused whole. Each of these refuses everything: a missing field; an id or a")
    [void]$lines.Add("title the queue already has; a roadmap_row that is not one of the rows below; an area inside the area of a")
    [void]$lines.Add("task in work (listed below); a shared file (docs/HANDOFF.md, docs/DECISIONS.md, state/BUILD_STATE.json,")
    [void]$lines.Add("docs/THIRD_PARTY_COMPONENTS.md, team/queue.json) or a directory holding one; an area outside the")
    [void]$lines.Add("repository; main or hand-gestures; more than max_new cards; more than max_new owner items (the two are")
    [void]$lines.Add("counted apart); a title that is already in the queue or in this file, even one that")
    [void]$lines.Add("differs only in upper or lower case; a roadmap_row note that has brackets inside it; an area of more than $script:TeamMaxAreaEntries entries; a")
    [void]$lines.Add("needs_owner that is not one line of at most $script:TeamFeedOwnerSentenceMax characters; a depends_on id that does not exist -")
    [void]$lines.Add("every id in depends_on is in the queue or in this file. Two cards of yours may share an area only when")
    [void]$lines.Add("one lists the other in depends_on.")
    [void]$lines.Add("")
    [void]$lines.Add("## The roadmap's rows (roadmap_row is one of these)")
    $rows = @(Get-TeamRoadmapRows -Text $RoadmapText)
    if (@($rows).Count -eq 0) { [void]$lines.Add("(none could be read from docs/ROADMAP.md - write an empty list)") }
    foreach ($row in $rows) { [void]$lines.Add("- $row") }
    [void]$lines.Add("")
    [void]$lines.Add("## The roadmap's limits (propose nothing they forbid)")
    [void]$lines.Add((Get-TeamRoadmapSection -Text $RoadmapText -Heading "The limits"))
    [void]$lines.Add("")
    [void]$lines.Add("## The order")
    [void]$lines.Add((Get-TeamRoadmapSection -Text $RoadmapText -Heading "The order"))
    [void]$lines.Add("")
    [void]$lines.Add("## The queue now (do not duplicate)")
    if (@($tasks).Count -eq 0) { [void]$lines.Add("- empty") }
    foreach ($task in $tasks) {
        [void]$lines.Add("- $([string]$task.id) [$([string](Get-TeamProperty -InputObject $task -Name 'state' -Default ''))]: $([string](Get-TeamProperty -InputObject $task -Name 'title' -Default ''))")
    }
    [void]$lines.Add("")
    [void]$lines.Add("## Areas that are taken (do not overlap)")
    $taken = 0
    foreach ($task in $tasks) {
        $state = [string](Get-TeamProperty -InputObject $task -Name "state" -Default "")
        if ($script:TeamFeedRunnableStates -notcontains $state) { continue }
        $area = @(Get-TeamProperty -InputObject $task -Name "area" -Default @())
        if (@($area).Count -eq 0) { continue }
        [void]$lines.Add("* $([string]$task.id): " + (($area | ForEach-Object { [string]$_ }) -join ", "))
        $taken++
    }
    if ($taken -eq 0) { [void]$lines.Add("* none") }
    [void]$lines.Add("")
    [void]$lines.Add("## Approved ideas to write into docs/ROADMAP.md")
    if (@($asked).Count -eq 0) {
        [void]$lines.Add("Do NOT edit docs/ROADMAP.md: there is no approved idea to write, and an edit of it refuses this run.")
    }
    else {
        [void]$lines.Add("For EACH idea below add exactly ONE row at the end of the table under '### Approved ideas', and change")
        [void]$lines.Add("nothing else in that file - not a space. The table is:")
        [void]$lines.Add("| Approved | Idea | Serves | Tasks | State |")
        [void]$lines.Add("and a row is: the date the owner approved (YYYY-MM-DD), the idea in bold with one sentence and its")
        [void]$lines.Add("proposal path in backticks, the row or order item it serves, the task ids in backticks, its state.")
        [void]$lines.Add("Five cells; a '|' inside a cell splits the row, so write ' / ' instead. The proposal path must be in the")
        [void]$lines.Add("row exactly as given here: it is how the script knows the idea is written.")
        foreach ($idea in $asked) {
            $proposal = [string](Get-TeamProperty -InputObject $idea -Name "proposal" -Default "")
            [void]$lines.Add("- idea: $([string]$idea.id)")
            [void]$lines.Add("  title: $([string](Get-TeamProperty -InputObject $idea -Name 'title' -Default ''))")
            [void]$lines.Add("  proposal: $proposal")
            [void]$lines.Add("  the owner's word: $([string](Get-TeamProperty -InputObject $idea -Name 'reason' -Default ''))")
            $children = @($tasks | Where-Object {
                    [string]$_.id -ne [string]$idea.id -and [string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "") -eq $proposal
                } | ForEach-Object { "$([string]$_.id) [$([string](Get-TeamProperty -InputObject $_ -Name 'state' -Default ''))]" })
            [void]$lines.Add("  tasks: " + $(if (@($children).Count -gt 0) { $children -join ", " } else { "none traced to it in the queue - read the owner's word above" }))
        }
    }
    [void]$lines.Add("")
    [void]$lines.Add("Return your report as your final message, at most 40 lines: which cards, cut from which roadmap items, and")
    [void]$lines.Add("what you left for the owner.")
    return ((@($lines.ToArray())) -join "`n")
}

# ------------------------------------------------------------------ what a run changed

function ConvertFrom-TeamFeedStatus {
    <# `git status --porcelain -z` as a table: path -> the two status letters. #>
    param([string]$Text)
    $found = @{}
    $parts = @(([string]$Text).Split([char]0))
    $total = @($parts).Count
    for ($i = 0; $i -lt $total; $i++) {
        $entry = [string]$parts[$i]
        if ($entry.Length -lt 4) { continue }
        $code = $entry.Substring(0, 2)
        $found[$entry.Substring(3)] = $code
        # A rename or a copy is followed by the path it came from.
        if ($code -match '[RC]') { $i++ }
    }
    return $found
}

function Get-TeamFeedSnapshot {
    <#
    .SYNOPSIS
        What is not committed in a checkout, as a table: path -> "status:sha256". It reads
        and changes nothing.

    .DESCRIPTION
        The main checkout is rarely clean (the lead's session works there), so "what did the
        run change" cannot be "what is dirty": it is the difference of two of these. The hash
        is what tells a file that was already dirty and was written AGAIN from one that was
        left alone. An ignored path is not seen.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    # Read as UTF-8: with -z git prints a path's own bytes, and Invoke-NativeProcess reads a
    # tool's output in the console's code page - a Turkish file name would come back as
    # another name, hash as "gone" before and after, and a write to it would not be seen.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-TeamGit)
    $psi.Arguments = (ConvertTo-NativeArgumentLine -Arguments @("status", "--porcelain", "-z", "--untracked-files=all"))
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.WorkingDirectory = (Resolve-Path -LiteralPath $RepoRoot).Path
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(300000)) {
        try { $process.Kill() } catch { }
        throw "git status did not end in ${RepoRoot}"
    }
    $listed = [string]$stdout.GetAwaiter().GetResult()
    $complaint = [string]$stderr.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0) { throw "git status failed in ${RepoRoot}: $($complaint.Trim())" }
    $snapshot = @{}
    $entries = ConvertFrom-TeamFeedStatus -Text $listed
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($path in @($entries.Keys)) {
            $full = Join-Path $RepoRoot (([string]$path) -replace '/', '\')
            $hash = "gone"
            if (Test-Path -LiteralPath $full -PathType Leaf) {
                try { $hash = [System.BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($full))) -replace '-', '' }
                catch { $hash = "unreadable" }
            }
            $snapshot[[string]$path] = "$($entries[$path]):$hash"
        }
    }
    finally { $sha.Dispose() }
    return $snapshot
}

function Compare-TeamFeedSnapshot {
    <#
    .SYNOPSIS
        The paths that differ between two snapshots and are not among the ones the run was
        allowed to write. Empty when the run kept to its file.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][hashtable]$Before,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][hashtable]$After,
        [string[]]$Allowed = @()
    )
    $allowedKeys = @(@($Allowed) | ForEach-Object { (([string]$_) -replace '\\', '/').Trim().ToLowerInvariant() })
    $stray = New-Object System.Collections.ArrayList
    foreach ($path in @(@($Before.Keys) + @($After.Keys) | Sort-Object -Unique)) {
        $was = if ($Before.ContainsKey($path)) { [string]$Before[$path] } else { "" }
        $is = if ($After.ContainsKey($path)) { [string]$After[$path] } else { "" }
        if ($was -ceq $is) { continue }
        $key = (([string]$path) -replace '\\', '/').Trim().ToLowerInvariant()
        if ($allowedKeys -contains $key) { continue }
        [void]$stray.Add([string]$path)
    }
    return @($stray.ToArray())
}

# ------------------------------------------------------------------ beside a running cycle
#
# A live cycle of THIS machine holds the team's lock for most of the day (API mode). The feeder
# then does not take that lock: it takes its own (a machine-local file), lets the lead work in
# a throwaway worktree, reads the store again before it writes, and writes only NEW tasks, each
# as a conditional create. Nothing here touches the cycle's lock.

# A feeder's own lock older than this is somebody's that died without its finally.
$script:TeamFeedOwnLockStaleHours = 6
# A git command that met another git's lock file (the cycle uses git in the same checkout) is
# tried again this many times, a second apart; then it is a failure, said as one.
$script:TeamFeedGitLockTries = 15

function Read-TeamFeederLock {
    <# The feeder lock's document ({pid, machine, acquired_at}), read while its holder keeps the
       file open; $null when it cannot be read or is not that document. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
        try {
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $text = $reader.ReadToEnd()
        }
        finally { $stream.Dispose() }
        $document = ConvertFrom-Json -InputObject $text
        if ($null -eq $document -or $null -eq $document.PSObject.Properties["pid"]) { return $null }
        return $document
    }
    catch { return $null }
}

function Get-TeamFeederLockVerdict {
    <#
    .SYNOPSIS
        Whether a feeder lock that is there is somebody's: held (a live pid, younger than the
        bound) | dead (its pid is gone) | stale (older than the bound) | unreadable.
    #>
    param($Held, [datetime]$Now = [datetime]::UtcNow, [datetime]$FileTime = [datetime]::UtcNow)
    if ($null -eq $Held) {
        # Being written this very moment, or broken: the file's own age decides.
        if (($Now - $FileTime).TotalHours -ge $script:TeamFeedOwnLockStaleHours) { return [pscustomobject]@{ Kind = "stale"; Why = "okunamayan, $script:TeamFeedOwnLockStaleHours saatten eski" } }
        return [pscustomobject]@{ Kind = "held"; Why = "okunamıyor (yazılıyor olabilir)" }
    }
    $holderPid = 0
    [void][int]::TryParse([string]$Held.pid, [ref]$holderPid)
    $since = [string](Get-TeamProperty -InputObject $Held -Name "acquired_at" -Default "")
    $at = ConvertFrom-TeamTimestamp -Text $since
    if ($null -eq $at -or ($Now.ToUniversalTime() - $at).TotalHours -ge $script:TeamFeedOwnLockStaleHours) {
        return [pscustomobject]@{ Kind = "stale"; Why = "pid $holderPid, $since - $script:TeamFeedOwnLockStaleHours saatten eski" }
    }
    $alive = ($holderPid -gt 0 -and $null -ne (Get-Process -Id $holderPid -ErrorAction SilentlyContinue))
    if (-not $alive) { return [pscustomobject]@{ Kind = "dead"; Why = "pid $holderPid artık yok ($since)" } }
    return [pscustomobject]@{ Kind = "held"; Why = "pid $holderPid, $since" }
}

function Enter-TeamFeederLock {
    <#
    .SYNOPSIS
        Takes the feeder's own lock: a file created exclusively and KEPT OPEN (others may read
        it, nobody may delete it) until Exit-TeamFeederLock. Acquired, Why (when not), TookOver
        (the verdict on a lock that was taken over, else empty).
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Machine, [datetime]$Now = [datetime]::UtcNow)
    $folder = Split-Path -Parent $Path
    if ($folder -and -not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    $tookOver = ""
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $stream = $null
        try { $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read) }
        catch {
            if (-not (Test-Path -LiteralPath $Path)) { throw }
            $fileTime = (Get-Item -LiteralPath $Path).LastWriteTimeUtc
            $verdict = Get-TeamFeederLockVerdict -Held (Read-TeamFeederLock -Path $Path) -Now $Now -FileTime $fileTime
            if ($verdict.Kind -eq "held") { return [pscustomobject]@{ Acquired = $false; Why = $verdict.Why; TookOver = ""; Stream = $null; Path = $Path } }
            # A holder that is gone closed its handle with it: the file can go. One that still
            # has it open cannot be deleted - then it is held, whatever its document says.
            try { [System.IO.File]::Delete($Path) }
            catch { return [pscustomobject]@{ Acquired = $false; Why = "$($verdict.Why); dosya hâlâ açık"; TookOver = ""; Stream = $null; Path = $Path } }
            $tookOver = $verdict.Why
            continue
        }
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes((ConvertTo-Json -InputObject ([ordered]@{ pid = $PID; machine = $Machine; acquired_at = (Get-TeamTimestamp -Now $Now) }) -Compress))
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        return [pscustomobject]@{ Acquired = $true; Why = ""; TookOver = $tookOver; Stream = $stream; Path = $Path }
    }
    return [pscustomobject]@{ Acquired = $false; Why = "another feeder took it at the same moment"; TookOver = ""; Stream = $null; Path = $Path }
}

function Exit-TeamFeederLock {
    <# Closes and removes the feeder's own lock. Said, not thrown, when the file stays. #>
    param($Lock)
    if ($null -eq $Lock -or -not $Lock.Acquired) { return "" }
    try { $Lock.Stream.Dispose() } catch { }
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        try { if (Test-Path -LiteralPath $Lock.Path) { [System.IO.File]::Delete($Lock.Path) }; return "" }
        catch { Start-Sleep -Milliseconds 300 }
    }
    return "the feeder's own lock file could not be removed: $($Lock.Path)"
}

function Invoke-TeamFeedGit {
    <# A git command in the checkout a running cycle also uses: another git's lock file is
       waited out (tried again), anything else - and a lock that does not go - is a failure. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = $null
    for ($attempt = 1; $attempt -le $script:TeamFeedGitLockTries; $attempt++) {
        $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments $Arguments
        if ($result.Success) { return $result }
        if (([string]$result.StdErr) -notmatch '(?i)\.lock\b|unable to create .*lock|another git process') { break }
        Start-Sleep -Seconds 1
    }
    throw "git $($Arguments[0..1] -join ' ') failed: $(([string]$result.StdErr).Trim())"
}

function New-TeamFeedWorktree {
    <# A throwaway worktree of the checkout's HEAD (detached: no branch is made) for one lead
       run, under .claude/worktrees/feed, which git ignores. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Name)
    if ($Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$') { throw "'$Name' cannot name a worktree" }
    $path = Join-Path (Join-Path $RepoRoot ".claude\worktrees\feed") $Name
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    [void](Invoke-TeamFeedGit -RepoRoot $RepoRoot -Arguments @("worktree", "add", "--detach", $path, "HEAD"))
    return $path
}

function Remove-TeamFeedWorktree {
    <# Removes the throwaway worktree, whatever the run left in it (it was judged already).
       Returns what could not be done, as a sentence; empty when it is gone. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return "" }
    try { [void](Invoke-TeamFeedGit -RepoRoot $RepoRoot -Arguments @("worktree", "remove", "--force", $Path)) }
    catch { return "the throwaway worktree was not removed: $($_.Exception.Message)" }
    return ""
}

function Select-TeamFeedFresh {
    <#
    .SYNOPSIS
        The feed against the queue as it is NOW: a card whose id somebody created since the
        first read is not ours to write, and neither is any card that waits for it. Feed (what
        is kept, in order) and Dropped ({Id, Why}).
    #>
    param($Feed, [Parameter(Mandatory = $true)]$Before, [Parameter(Mandatory = $true)]$Fresh)
    $old = @{}
    foreach ($task in @(Get-TeamTasks -Queue $Before)) { $old[[string]$task.id] = $true }
    $now = @{}
    foreach ($task in @(Get-TeamTasks -Queue $Fresh)) { $now[[string]$task.id] = $task }
    $dropped = New-Object System.Collections.ArrayList
    $gone = @{}
    foreach ($item in @($Feed)) {
        $id = [string](Get-TeamProperty -InputObject $item -Name "id" -Default "")
        if ($id -and $now.ContainsKey($id) -and -not $old.ContainsKey($id)) {
            $gone[$id] = $true
            [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "bu id lead koşusu sürerken başka bir yazar tarafından oluşturuldu ('$([string]$now[$id].title)')" })
        }
    }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in @($Feed)) {
            $id = [string](Get-TeamProperty -InputObject $item -Name "id" -Default "")
            if (-not $id -or $gone.ContainsKey($id)) { continue }
            $waits = @(@(Get-TeamProperty -InputObject $item -Name "depends_on" -Default @()) | Where-Object { $gone.ContainsKey([string]$_) })
            if (@($waits).Count -gt 0) {
                $gone[$id] = $true
                $changed = $true
                [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "yazılmayan $([string]$waits[0]) kartına bağlı" })
            }
        }
    }
    $kept = @(@($Feed) | Where-Object { -not $gone.ContainsKey([string](Get-TeamProperty -InputObject $_ -Name "id" -Default "")) })
    return [pscustomobject]@{ Feed = $kept; Dropped = @($dropped.ToArray()) }
}

function Get-TeamFeedCreateOrder {
    <# The tasks in the lead's order, except that a task comes after every task of the same list
       it depends on. #>
    param([object[]]$Tasks = @())
    $byId = [ordered]@{}
    foreach ($task in @($Tasks)) { $byId[[string]$task.id] = $task }
    $placed = @{}
    $order = New-Object System.Collections.ArrayList
    $visiting = @{}
    $place = $null
    $place = {
        param([string]$Id)
        if ($placed.ContainsKey($Id) -or $visiting.ContainsKey($Id)) { return }
        $visiting[$Id] = $true
        foreach ($dependency in @(Get-TeamProperty -InputObject $byId[$Id] -Name "depends_on" -Default @())) {
            if ($byId.Contains([string]$dependency)) { & $place ([string]$dependency) }
        }
        $placed[$Id] = $true
        [void]$order.Add($byId[$Id])
    }
    foreach ($id in @($byId.Keys)) { & $place ([string]$id) }
    return @($order.ToArray())
}

function Save-TeamFeedCreates {
    <#
    .SYNOPSIS
        Writes NEW tasks to the store, each as its own conditional create (a PUT whose
        expected_updated_at is null: the store answers 409 when the id exists). Nothing else
        is ever sent - no task the store has, whatever its state.

    .DESCRIPTION
        In dependency order. A 409 means somebody made that id first: that task, and every task
        that depends on it, is dropped and named; what was written stays; nothing is retried or
        overwritten. Any other failure stops the writing: Failed says why, and the tasks not
        reached are named as not written.
        Written (ids), Dropped ({Id, Why}), Failed (empty when none).
    #>
    param([Parameter(Mandatory = $true)]$Store, [object[]]$Tasks = @())
    $written = New-Object System.Collections.ArrayList
    $dropped = New-Object System.Collections.ArrayList
    $gone = @{}
    $failed = ""
    foreach ($task in @(Get-TeamFeedCreateOrder -Tasks $Tasks)) {
        $id = [string]$task.id
        if ($failed) { [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "depo yazmayı kesti" }); continue }
        $waits = @(@(Get-TeamProperty -InputObject $task -Name "depends_on" -Default @()) | Where-Object { $gone.ContainsKey([string]$_) })
        if (@($waits).Count -gt 0) {
            $gone[$id] = $true
            [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "yazılmayan $([string]$waits[0]) kartına bağlı" })
            continue
        }
        $body = [ordered]@{ task = $task; expected_updated_at = $null }
        try { [void](Invoke-TeamApi -Store $Store -Method "PUT" -Path "/v1/team/queue/tasks/$id" -Body $body) }
        catch {
            if (([string]$_.Exception.Message) -match '^HTTP 409 ') {
                $gone[$id] = $true
                [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "depo 409 dedi: bu id'yi başkası önce oluşturdu" })
                continue
            }
            $failed = ([string]$_.Exception.Message -replace '\s+', ' ').Trim()
            [void]$dropped.Add([pscustomobject]@{ Id = $id; Why = "depo yanıt vermedi" })
            continue
        }
        [void]$written.Add($id)
    }
    return [pscustomobject]@{ Written = @($written.ToArray()); Dropped = @($dropped.ToArray()); Failed = $failed }
}
