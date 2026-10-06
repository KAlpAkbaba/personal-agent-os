<#
.SYNOPSIS
    The roadmap feeder (scripts/team/feed.ps1, ADR-0214 addendum 8): when the queue runs low a
    lead run cuts the roadmap's next items into cards, and THE SCRIPT judges what it wrote.

.DESCRIPTION
    Two halves, as in team-cycle.tests.ps1.

    The decisions (scripts/lib/TeamFeed.ps1) are driven as functions, with the near misses
    beside the hits; the roadmap's rows are read from THIS repository's docs/ROADMAP.md too,
    so a row the queue names today is a row the judge accepts.

    The feeder itself is run for real, in a git repository made for the test, with a fake in
    place of the model. The fake is written by this file into a folder OUTSIDE the sandbox (a
    log inside it would be a file the lead run "edited"); what it writes is the plan the test
    names: a feed file, lines in the roadmap's table, a file it has no business writing.
    What is asserted is what is on disk afterwards: the queue, the roadmap, the commits, the
    lock, the report - and the fake's log of how each run was started.

    No model is started and this repository's own branches and team/ files are not written to.

    Run: powershell -NoProfile -File scripts\tests\team-feed.tests.ps1
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
. (Join-Path $repoRoot "scripts\lib\TeamFeed.ps1")

$script:Failures = 0
$script:Passes = 0
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$feedDate = "2026-10-01"

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

function New-Task {
    param([string]$Id, [string]$State = "approved", [string[]]$Area = @("src/area"), [string[]]$DependsOn = @())
    $task = [pscustomobject]@{
        id = $Id; title = "the task $Id"; roadmap_row = "Secretary"; state = $State; area = @($Area)
        branch = ""; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-09-30T00:00:00Z"; updated_at = "2026-09-30T00:00:00Z"
    }
    if (@($DependsOn).Count -gt 0) { $task | Add-Member -NotePropertyName "depends_on" -NotePropertyValue @($DependsOn) }
    return $task
}

function New-Queue {
    param([object[]]$Tasks = @())
    return [pscustomobject]@{ version = 1; tasks = @($Tasks) }
}

function New-ApprovedIdea {
    <# An idea the owner approved and the lead split: done, traced to its proposal. #>
    param([string]$Id = "idea-one", [string]$Reason = "sahip onayladı (2026-10-01: 'evet'); işlere bölündü: idea-one-a")
    $task = New-Task -Id $Id -State "done" -Area @()
    $task | Add-Member -NotePropertyName "proposal" -NotePropertyValue "team/proposals/$Id.md"
    $task | Add-Member -NotePropertyName "reason" -NotePropertyValue $Reason
    return $task
}

function New-Card {
    param([string]$Id, [string]$Row = "Secretary", [string[]]$Area = @(), [string]$Title = "")
    $areas = if (@($Area).Count -gt 0) { @($Area) } else { @("src/$Id") }
    return [ordered]@{
        id = $Id; title = $(if ($Title) { $Title } else { "the card $Id" }); roadmap_row = $Row; area = @($areas)
        goal = "the goal of $Id"; acceptance = "the acceptance of $Id"; evidence_expected = "PROVEN_AUTOMATED"
    }
}

function New-DependentCard {
    <# A card that waits for another (its area is src/<id>). #>
    param([string]$Id, [string[]]$DependsOn)
    $card = New-Card -Id $Id
    $card["depends_on"] = @($DependsOn)
    return $card
}

function ConvertTo-FeedObjects {
    <# Cards as the script reads them: through JSON, as a file would give them. #>
    param([object[]]$Cards)
    return @(ConvertFrom-Json -InputObject (ConvertTo-Json -InputObject @($Cards) -Depth 6))
}

# The roadmap of the sandbox: the shapes of docs/ROADMAP.md, a tenth of its length.
$roadmapFixture = @(
    "# Roadmap",
    "",
    "## The JARVIS target",
    "",
    "### What JARVIS does, and where this system stands (2026-09-27)",
    "",
    "| JARVIS | PersonalAgentOS today | State |",
    "|---|---|---|",
    "| Runs the house: lights, doors, climate | Home Assistant behind a provider | **MISSING** |",
    "| **Records everything and tells him, whenever he asks** — `"her şeyi kaydeden`" | Activity ledger | **PARTIAL** |",
    "",
    "### The limits, stated once",
    "",
    "- **No unauthorised access, ever.** Only assets in the Authorized Asset Registry.",
    "- **An always-on frontier model is not affordable.**",
    "",
    "### The order (binding until the owner changes it)",
    "",
    "1. **Memory** — DONE 2026-09-29.",
    "2. **browser-use, anywhere** — the JARVIS that does anything on the web:",
    "   - 2b. **Execution in the cloud (ADR-0213)** — a browser worker on the Cloud Core.",
    "3. **Secretary** — Radicale, a mail account, then the telephony bridge.",
    "",
    "### Approved ideas (the researcher's, written here by the lead when the owner approves)",
    "",
    "One line per approved idea.",
    "",
    "| Approved | Idea | Serves | Tasks | State |",
    "|---|---|---|---|---|",
    "| 2026-10-01 | **The trial list** — the third gate (``team/proposals/2026-10-01-deneme-listesi.md``). | `"Definition of done`" | ``owner-trials-api`` | queued |",
    "",
    "Deferred by the owner, not listed above: Home Assistant.",
    "",
    "### Definition of done (owner, 2026-09-29)",
    "",
    "Every row says HAVE.",
    "",
    "### How it is built from here (owner decision 2026-09-29)",
    "",
    "A team of agents.",
    ""
) -join "`n"

$ideaRow = "| 2026-10-01 | **Idea one** — what it is (``team/proposals/idea-one.md``). | Secretary | ``idea-one-a`` | queued |"

# ============================================================================ the decisions

Write-Host ""
Write-Host "what counts as runnable"

Test-Case "runnable: the five states the cycle works on count; a gate, a finished and a stopped task do not" {
    $tasks = @(
        (New-Task -Id "t-approved" -State "approved" -Area @("src/a")), (New-Task -Id "t-assigned" -State "assigned" -Area @("src/b")),
        (New-Task -Id "t-returned" -State "returned" -Area @("src/c")), (New-Task -Id "t-progress" -State "in_progress" -Area @("src/d")),
        (New-Task -Id "t-inspecting" -State "inspecting" -Area @("src/e")), (New-Task -Id "t-owner" -State "awaiting_owner" -Area @()),
        (New-Task -Id "t-merged" -State "merged" -Area @("src/f")), (New-Task -Id "t-release" -State "awaiting_release" -Area @("src/g")),
        (New-Task -Id "t-done" -State "done" -Area @("src/h")), (New-Task -Id "t-stopped" -State "stopped" -Area @("src/i")),
        (New-Task -Id "t-proposed" -State "proposed" -Area @())
    )
    $runnable = @(Get-TeamRunnableTasks -Queue (New-Queue -Tasks $tasks))
    Assert-Equal -Expected "t-approved,t-assigned,t-returned,t-progress,t-inspecting" -Actual (@($runnable | ForEach-Object { $_.id }) -join ",") -Because "the five states"
    Assert-Equal -Expected 0 -Actual @(Get-TeamRunnableTasks -Queue (New-Queue)).Count -Because "an empty queue has none"
    Assert-Equal -Expected 1 -Actual @(Get-TeamRunnableTasks -Queue (New-Queue -Tasks @((New-Task -Id "only-one")))).Count -Because "one is one"
}

Test-Case "runnable: a task whose dependencies are not on main does not count; once they are, it does" {
    $first = New-Task -Id "layer-one" -State "merged" -Area @("src/one")
    $second = New-Task -Id "layer-two" -Area @("src/two") -DependsOn @("layer-one")
    $queue = New-Queue -Tasks @($first, $second)
    Assert-Equal -Expected 0 -Actual @(Get-TeamRunnableTasks -Queue $queue).Count -Because "merged is the integration branch, not main: layer-two waits, and a seat is empty"
    $first.state = "awaiting_release"
    Assert-Equal -Expected "layer-two" -Actual (@(Get-TeamRunnableTasks -Queue $queue | ForEach-Object { $_.id }) -join ",") -Because "gated main is main"
}

Write-Host ""
Write-Host "the roadmap's rows"

Test-Case "rows: the table's rows, the order's items, the headings and the approved ideas are rows; prose is not" {
    $rows = @(Get-TeamRoadmapRows -Text $roadmapFixture)
    foreach ($row in @(
            "Runs the house: lights, doors, climate", "Records everything and tells him, whenever he asks",
            "Memory", "browser-use, anywhere", "Execution in the cloud (ADR-0213)", "Execution in the cloud", "Secretary",
            "Definition of done", "How it is built from here", "The trial list")) {
        Assert-True -Condition ($rows -ccontains $row) -Because "'$row' is a row: $($rows -join ' / ')"
    }
    foreach ($row in @("JARVIS", "Approved", "Home Assistant behind a provider", "2026-10-01", "A team of agents.", "---", "")) {
        Assert-True -Condition ($rows -cnotcontains $row) -Because "'$row' is not a row"
    }
}

Test-Case "rows: a card's row is a row quoted exactly, with at most a note in brackets after it" {
    $rows = @(Get-TeamRoadmapRows -Text $roadmapFixture)
    foreach ($row in @("Secretary", "  Secretary ", "browser-use, anywhere (order 2b, ADR-0213)", "How it is built from here (TEAM_PROTOCOL 3a)", "Execution in the cloud (ADR-0213)")) {
        Assert-True -Condition (Test-TeamRoadmapRow -Row $row -Rows $rows) -Because "'$row' names a row"
    }
    foreach ($row in @("", "   ", "secretary", "Secretar", "Secretary and more", "The house", "A new row nobody approved", "Secretary (unclosed", "(Secretary)", "row")) {
        Assert-True -Condition (-not (Test-TeamRoadmapRow -Row $row -Rows $rows)) -Because "'$row' names no row"
    }
}

Test-Case "rows: the rows this repository's queue names today are rows of this repository's ROADMAP.md" {
    $text = [System.IO.File]::ReadAllText((Join-Path $repoRoot "docs\ROADMAP.md"), [System.Text.Encoding]::UTF8)
    $rows = @(Get-TeamRoadmapRows -Text $text)
    Assert-True -Condition (@($rows).Count -ge 20) -Because "the real roadmap has its table, its order and its headings: $(@($rows).Count)"
    foreach ($row in @(
            "Always-listening natural conversation, interruptible, in the owner's language",
            "How it is built from here (TEAM_PROTOCOL 3a)", "How it is built from here",
            "Records everything and tells him, whenever he asks (order 2c)",
            "Repairs and improves itself (kept controlled)",
            "The same JARVIS in the house, the car, the suit, the phone", "Voice and character")) {
        Assert-True -Condition (Test-TeamRoadmapRow -Row $row -Rows $rows) -Because "'$row' is in use in team/queue.json and must stay a row"
    }
    # The owner's JARVIS-only roadmap (2026-10-05) removed the workshop, the house, the Secretary
    # and browser-use rows; finished cards keep the row they were cut from. A card that can still
    # run (any state but done/released) must name a row the roadmap has today.
    $queue = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText((Join-Path $repoRoot "team\queue.json"), [System.Text.Encoding]::UTF8))
    foreach ($task in @($queue.tasks | Where-Object { @("done", "released") -notcontains [string]$_.state })) {
        Assert-True -Condition (Test-TeamRoadmapRow -Row ([string]$task.roadmap_row) -Rows $rows) -Because "'$($task.id)' ($($task.state)) names '$($task.roadmap_row)', which is not a row of ROADMAP.md"
    }
    Assert-True -Condition (-not (Test-TeamRoadmapRow -Row "Flies the suit" -Rows $rows)) -Because "what the roadmap does not name is not a row"
    Assert-True -Condition (-not (Test-TeamRoadmapRow -Row "Breaks into any system; flies the suit; drives the car" -Rows $rows)) -Because "the row the roadmap marks NEVER is a limit, not work to cut"
    Assert-True -Condition ((Get-TeamRoadmapSection -Text $text -Heading "The limits, stated once") -match "No unauthorised access, ever") -Because "the limits are read for the prompt"
    Assert-True -Condition ((Get-TeamRoadmapSection -Text $text -Heading "The order") -match "Research and analysis") -Because "the order is read for the prompt"
}

Write-Host ""
Write-Host "the judge of a feed file"

function Get-FeedProblems {
    param([object[]]$Cards, [object[]]$Existing = @(), [int]$MaxNew = 3)
    return @(Test-TeamFeed -Feed (ConvertTo-FeedObjects -Cards $Cards) -Queue (New-Queue -Tasks $Existing) `
            -RoadmapRows @(Get-TeamRoadmapRows -Text $roadmapFixture) -MaxNew $MaxNew)
}

Test-Case "judge: two sound cards pass, and each becomes an approved, queue-valid task with the roadmap reason, in the lead's order" {
    $cards = @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Row "browser-use, anywhere (order 2b)"))
    Assert-Equal -Expected 0 -Actual @(Get-FeedProblems -Cards $cards).Count -Because ((Get-FeedProblems -Cards $cards) -join "; ")
    $made = @(ConvertTo-TeamFeedTasks -Feed (ConvertTo-FeedObjects -Cards $cards) -RoadmapRows @(Get-TeamRoadmapRows -Text $roadmapFixture) -Date $feedDate -Now ([datetime]"2026-10-01T10:00:00Z").ToUniversalTime())
    Assert-Equal -Expected 2 -Actual @($made).Count -Because "two tasks"
    Assert-Equal -Expected "approved,approved" -Actual (@($made | ForEach-Object { $_.Task.state }) -join ",") -Because "roadmap work is approved in advance"
    Assert-Equal -Expected "roadmap: Secretary; fed by the lead run $feedDate" -Actual $made[0].Task.reason -Because "the reason names the row"
    Assert-Equal -Expected "roadmap: browser-use, anywhere (order 2b); fed by the lead run $feedDate" -Actual $made[1].Task.reason -Because "the reason names the row as the card quoted it"
    Assert-True -Condition ($null -eq $made[0].Task.PSObject.Properties["proposal"]) -Because "a card cut from the roadmap has no proposal: it is not an idea"
    Assert-True -Condition ([string]$made[0].Task.created_at -lt [string]$made[1].Task.created_at) -Because "the queue is ordered by created_at: the lead's order is kept ($($made[0].Task.created_at), $($made[1].Task.created_at))"
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue (New-Queue -Tasks @($made | ForEach-Object { $_.Task }))).Count -Because "the tasks keep the protocol"
}

$refusedCards = @(
    @{ Name = "a roadmap_row that is not a row"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Row "A new row nobody approved")) }; Says = "is not a row of docs/ROADMAP.md" },
    @{ Name = "an id that is in the queue"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "busy-one")) }; Says = "the id is already in the queue" },
    @{ Name = "an area inside an active task's"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Area @("src/busy/deep"))) }; Says = "overlaps the area of busy-one" },
    @{ Name = "a missing field"; Cards = { $card = New-Card -Id "card-b"; $card.Remove("acceptance"); @((New-Card -Id "card-a"), $card) }; Says = "'acceptance' is missing" },
    @{ Name = "a shared file"; Cards = { @((New-Card -Id "card-a" -Area @("docs/HANDOFF.md"))) }; Says = "shared file" },
    @{ Name = "more cards than were asked for"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "card-b"), (New-Card -Id "card-c"), (New-Card -Id "card-d")) }; Says = "at most 3" },
    @{ Name = "a title the queue already has"; Cards = { @((New-Card -Id "card-a" -Title "The task busy-one")) }; Says = "is already the title of busy-one" },
    @{ Name = "needs_owner that is not a sentence"; Cards = { $card = New-Card -Id "card-a"; $card["needs_owner"] = $true; @($card) }; Says = "needs_owner is one sentence" }
)
foreach ($case in $refusedCards) {
    Test-Case "judge: refused - $($case.Name)" {
        $problems = @(Get-FeedProblems -Cards @(& $case.Cards) -Existing @((New-Task -Id "busy-one" -State "assigned" -Area @("src/busy"))))
        Assert-True -Condition (@($problems | Where-Object { $_ -match [regex]::Escape($case.Says) }).Count -ge 1) -Because "says '$($case.Says)': $($problems -join '; ')"
    }
}

Test-Case "judge: the near misses pass - a neighbour of an active area, an area of a FINISHED task, a row with a note" {
    $existing = @((New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")), (New-Task -Id "old-one" -State "done" -Area @("src/old")))
    $cards = @((New-Card -Id "card-a" -Area @("src/busy2")), (New-Card -Id "card-b" -Area @("src/old/deep") -Row "Secretary (the mail account)"))
    Assert-Equal -Expected 0 -Actual @(Get-FeedProblems -Cards $cards -Existing $existing).Count -Because ((Get-FeedProblems -Cards $cards -Existing $existing) -join "; ")
    Assert-Equal -Expected 0 -Actual @(Get-FeedProblems -Cards @()).Count -Because "an empty list is 'nothing can be cut', not a broken file"
}

Test-Case "judge: an item marked needs_owner becomes an awaiting_owner idea with a proposal, never a task - whatever its row" {
    $owner = New-Card -Id "home-assistant" -Row "A row the roadmap does not have"
    $owner["needs_owner"] = "It needs a Home Assistant install and an account the owner must open."
    $owner.Remove("area")
    $cards = @((New-Card -Id "card-a"), $owner)
    Assert-Equal -Expected 0 -Actual @(Get-FeedProblems -Cards $cards).Count -Because ((Get-FeedProblems -Cards $cards) -join "; ")
    $made = @(ConvertTo-TeamFeedTasks -Feed (ConvertTo-FeedObjects -Cards $cards) -RoadmapRows @(Get-TeamRoadmapRows -Text $roadmapFixture) -Date $feedDate)
    $idea = @($made | Where-Object { $_.Task.id -eq "home-assistant" })[0]
    Assert-Equal -Expected "awaiting_owner" -Actual $idea.Task.state -Because "the owner's first gate"
    Assert-Equal -Expected 0 -Actual @($idea.Task.area).Count -Because "an idea has no area: nobody may work on it"
    Assert-Equal -Expected "" -Actual $idea.Task.roadmap_row -Because "a row the roadmap does not have is not written as one"
    Assert-Equal -Expected "team/proposals/$feedDate-feed-home-assistant.md" -Actual $idea.Task.proposal -Because "it is a proposal"
    Assert-True -Condition ($idea.ProposalText -match "Home Assistant install" -and $idea.ProposalText -match "A row the roadmap does not have") -Because "the proposal says why the owner is needed: $($idea.ProposalText)"
    Assert-True -Condition ($idea.Task.reason -match "Home Assistant install") -Because "and so does the queue: $($idea.Task.reason)"
    # Approved later, it must be the lead's to split - an approved task with no area and no
    # proposal would be moved to 'assigned' and break the queue for every cycle.
    $idea.Task.state = "approved"
    Assert-True -Condition (Test-TeamSplitCandidate -Task $idea.Task) -Because "once approved it is split by the lead, never handed to a worker"
    Assert-Equal -Expected "rest" -Actual (Get-TeamNextRole -Task $idea.Task).Kind -Because "the cycle's task loop leaves it alone"
    $idea.Task.state = "awaiting_owner"
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue (New-Queue -Tasks @($made | ForEach-Object { $_.Task }))).Count -Because "the queue keeps the protocol"
    $taken = New-Card -Id "busy-one"; $taken["needs_owner"] = "It needs a paid account."
    $problems = @(Get-FeedProblems -Cards @($taken) -Existing @((New-Task -Id "busy-one")))
    Assert-True -Condition (@($problems | Where-Object { $_ -match "already in the queue" }).Count -ge 1) -Because "an idea's id must be free too: $($problems -join '; ')"
}

function Get-FeedProposalText {
    <# The proposal the feeder writes for one owner item (no area; goal/acceptance as given). #>
    param([string]$Id, [string]$Title, [string]$Sentence, [string]$Row, [string]$Goal, [string]$Acceptance, [string]$Date = $feedDate)
    $owner = [ordered]@{ id = $Id; title = $Title; roadmap_row = $Row; needs_owner = $Sentence; evidence_expected = "PROVEN_AUTOMATED" }
    if ($Goal) { $owner["goal"] = $Goal }
    if ($Acceptance) { $owner["acceptance"] = $Acceptance }
    $made = @(ConvertTo-TeamFeedTasks -Feed (ConvertTo-FeedObjects -Cards @($owner)) -RoadmapRows @(Get-TeamRoadmapRows -Text $roadmapFixture) -Date $Date)
    Assert-Equal -Expected "owner" -Actual $made[0].Kind -Because "an item with needs_owner is the owner's"
    return [string]$made[0].ProposalText
}

function Get-MarkdownHeadings {
    param([string]$Text)
    return @(($Text -split "`r?`n") | Where-Object { $_.StartsWith("## ") } | ForEach-Object { $_.Substring(3).Trim() })
}

Test-Case "proposal: the owner item is written in the researcher's shape - '## Ne' first, then row, goal, acceptance, decision" {
    # apps/web/tests/approvals/proposal-shapes.test.ts reads every file under team/proposals and
    # wants exactly one "Ne" section and 3+ '## ' headings; the old '## Sahibe sorulan' turned the
    # Stage 55 gate red.
    $text = Get-FeedProposalText -Id "q" -Title "Bir fikir" -Sentence "Soru?" -Row "Yok" -Goal "H" -Acceptance "K"
    $lines = @($text -split "`n")
    $order = @("# ", "Kaynak: lead koşusu", "## Ne", "## Roadmap satırı", "## Hedef", "## Kabul", "## Karar")
    $at = -1
    foreach ($mark in $order) {
        $found = -1
        for ($i = $at + 1; $i -lt $lines.Count; $i++) { if ($lines[$i].StartsWith($mark)) { $found = $i; break } }
        Assert-True -Condition ($found -gt $at) -Because "'$mark' comes after line $at`: $text"
        $at = $found
    }
    $headings = @(Get-MarkdownHeadings -Text $text)
    Assert-Equal -Expected "Ne" -Actual $headings[0] -Because "the first section is the one the detail view shows: $($headings -join ' / ')"
    Assert-True -Condition ($headings.Count -ge 3) -Because "three or more '## ' headings: $($headings -join ' / ')"
    Assert-Equal -Expected 1 -Actual @($headings | Where-Object { $_ -ceq "Ne" }).Count -Because "one 'Ne' section"
    Assert-True -Condition ($text -notmatch '\*\*' -and $text -notmatch 'http') -Because "the script adds no bold and no link of its own: $text"
    $ne = [array]::IndexOf($lines, "## Ne")
    Assert-Equal -Expected "" -Actual $lines[$ne + 1] -Because "a blank line under the heading"
    Assert-Equal -Expected "Soru?" -Actual $lines[$ne + 2] -Because "the owner's sentence is the body of '## Ne'"
    Assert-True -Condition ($lines[$ne + 4].StartsWith("## ")) -Because "and the whole of it: $text"
    Assert-Equal -Expected "Sahip: evet / hayır / ertele." -Actual $lines[[array]::IndexOf($lines, "## Karar") + 2] -Because "the decision line"
}

Test-Case "proposal: without goal and acceptance the sections are still there, saying 'Belirtilmedi.'" {
    $text = Get-FeedProposalText -Id "q" -Title "Bir fikir" -Sentence "Soru?" -Row "" -Goal "" -Acceptance ""
    $headings = @(Get-MarkdownHeadings -Text $text)
    Assert-Equal -Expected "Ne|Roadmap satırı|Hedef|Kabul|Karar" -Actual ($headings -join "|") -Because "the same five sections: $text"
    $lines = @($text -split "`n")
    foreach ($heading in @("## Roadmap satırı", "## Hedef", "## Kabul")) {
        Assert-Equal -Expected "Belirtilmedi." -Actual $lines[[array]::IndexOf($lines, $heading) + 2] -Because "'$heading' says it was not given"
    }
}

Test-Case "proposal: the web test's FEED_SHAPE fixture is what the script writes, line by line" {
    # The two halves read each other: the vitest fixture runs the rule, this case runs the script.
    $fixtures = [IO.File]::ReadAllText((Join-Path $repoRoot "apps\web\tests\approvals\fixtures.ts"), [Text.Encoding]::UTF8)
    $match = [regex]::Match($fixtures, '(?s)export const FEED_SHAPE = `([^`]*)`;')
    Assert-True -Condition $match.Success -Because "fixtures.ts has 'export const FEED_SHAPE = ``...``;'"
    $fixture = $match.Groups[1].Value -replace "`r`n", "`n"
    $text = Get-FeedProposalText -Id "radicale-calendar-server" -Title "Radicale takvim sunucusu" `
        -Sentence "Radicale'yi ev PC'sine kuralım mı?" -Row "Ev takvimi kendi sunucumuzda" `
        -Goal "Takvim verisi evde durur." -Acceptance "Telefon ve web aynı takvimi gösterir." -Date "2026-10-06"
    Assert-Equal -Expected ((Get-MarkdownHeadings -Text $text) -join "|") -Actual ((Get-MarkdownHeadings -Text $fixture) -join "|") -Because "the same headings"
    $want = @($text -split "`n"); $have = @($fixture -split "`n")
    for ($i = 0; $i -lt [math]::Max($want.Count, $have.Count); $i++) {
        $w = if ($i -lt $want.Count) { $want[$i] } else { "<none>" }
        $h = if ($i -lt $have.Count) { $have[$i] } else { "<none>" }
        Assert-Equal -Expected $w -Actual $h -Because "line $($i + 1) of FEED_SHAPE"
    }
}

Write-Host ""
Write-Host "approved ideas and the roadmap's table"

Test-Case "ideas: done + proposal + 'sahip onayladı' + not named in the roadmap is an idea to write; each near miss is not" {
    $missing = New-ApprovedIdea -Id "idea-one"
    $named = New-ApprovedIdea -Id "idea-named"; $named.proposal = "team/proposals/2026-10-01-deneme-listesi.md"
    $open = New-ApprovedIdea -Id "idea-open"; $open.state = "approved"
    $split = New-ApprovedIdea -Id "idea-split" -Reason "bölündü: a, b"
    $deferred = New-ApprovedIdea -Id "idea-deferred" -Reason "sahip erteledi (2026-10-01)"
    $plain = New-Task -Id "plain-done" -State "done"; $plain | Add-Member -NotePropertyName "reason" -NotePropertyValue "sahip onayladı"
    $ideas = @(Get-TeamApprovedIdeasMissing -Queue (New-Queue -Tasks @($missing, $named, $open, $split, $deferred, $plain)) -RoadmapText $roadmapFixture)
    Assert-Equal -Expected "idea-one" -Actual (@($ideas | ForEach-Object { $_.id }) -join ",") -Because "only the approved, finished, unnamed proposal"
    $after = $roadmapFixture.Replace("`nDeferred by the owner", "$ideaRow`nDeferred by the owner")
    Assert-Equal -Expected 0 -Actual @(Get-TeamApprovedIdeasMissing -Queue (New-Queue -Tasks @($missing)) -RoadmapText $after).Count -Because "once its line is there it is not written again"
}

function Get-RoadmapWith {
    <# The fixture with lines put after the table's last row. #>
    param([string[]]$Rows)
    $anchor = "| queued |`n"
    return $roadmapFixture.Replace($anchor, $anchor + (($Rows | ForEach-Object { $_ + "`n" }) -join ""))
}

Test-Case "table: one new row that names the idea's proposal is accepted; the same text twice changes nothing" {
    $ideas = @((New-ApprovedIdea -Id "idea-one"))
    $verdict = Test-TeamFeedRoadmapEdit -Before $roadmapFixture -After (Get-RoadmapWith -Rows @($ideaRow)) -Ideas $ideas
    Assert-Equal -Expected 0 -Actual @($verdict.Problems).Count -Because (@($verdict.Problems) -join "; ")
    Assert-Equal -Expected "idea-one" -Actual (@($verdict.Added) -join ",") -Because "the idea it wrote"
    $same = Test-TeamFeedRoadmapEdit -Before $roadmapFixture -After $roadmapFixture -Ideas $ideas
    Assert-Equal -Expected 0 -Actual (@($same.Problems).Count + @($same.Added).Count) -Because "no edit: no problem, nothing added"
}

$badEdits = @(
    @{ Name = "a line outside the table"; After = { $roadmapFixture + "A sentence the lead added.`n" }; Says = "outside the 'Approved ideas' table" },
    @{ Name = "a row put into the OTHER table"; After = { $roadmapFixture.Replace("| **MISSING** |`n", "| **MISSING** |`n$ideaRow`n") }; Says = "outside the 'Approved ideas' table" },
    @{ Name = "an existing row rewritten"; After = { $roadmapFixture.Replace("| queued |", "| done |") }; Says = "removes or rewrites" },
    @{ Name = "a line removed"; After = { $roadmapFixture.Replace("Every row says HAVE.`n", "") }; Says = "removes or rewrites" },
    @{ Name = "two rows for one idea"; After = { Get-RoadmapWith -Rows @($ideaRow, $ideaRow) }; Says = "more than one row" },
    @{ Name = "a row for an idea nobody approved"; After = { Get-RoadmapWith -Rows @($ideaRow.Replace("idea-one.md", "idea-other.md")) }; Says = "names no approved idea" },
    @{ Name = "a row with a cell too many"; After = { Get-RoadmapWith -Rows @($ideaRow.Replace("| queued |", "| queued | extra |")) }; Says = "five cells" },
    @{ Name = "a row without its date"; After = { Get-RoadmapWith -Rows @($ideaRow.Replace("| 2026-10-01 |", "| today |")) }; Says = "date" },
    @{ Name = "a row with an empty cell"; After = { Get-RoadmapWith -Rows @($ideaRow.Replace("| Secretary |", "|  |")) }; Says = "empty cell" }
)
foreach ($case in $badEdits) {
    Test-Case "table: refused - $($case.Name)" {
        $verdict = Test-TeamFeedRoadmapEdit -Before $roadmapFixture -After (& $case.After) -Ideas @((New-ApprovedIdea -Id "idea-one"))
        Assert-True -Condition (@($verdict.Problems | Where-Object { $_ -match [regex]::Escape($case.Says) }).Count -ge 1) -Because "says '$($case.Says)': $(@($verdict.Problems) -join '; ')"
    }
}

Write-Host ""
Write-Host "the prompt and the scripts themselves"

Test-Case "prompt: it names the file, the count, the rows, the limits, the order, the queue and the areas that are taken" {
    $queue = New-Queue -Tasks @((New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")), (New-ApprovedIdea -Id "idea-one"))
    $card = New-TeamFeedCard -Queue $queue -RoadmapText $roadmapFixture -FeedFile "team/plans/feed-$feedDate-1.json" -MaxNew 2 -Date $feedDate -Ideas @((New-ApprovedIdea -Id "idea-one"))
    foreach ($part in @(
            "- feed_file: team/plans/feed-$feedDate-1.json", "- max_new: 2", "No unauthorised access, ever", "browser-use, anywhere",
            "- Secretary", "- busy-one [assigned]: the task busy-one", "src/busy", "needs_owner", "- idea: idea-one",
            "team/proposals/idea-one.md", "docs/product/", "| Approved | Idea | Serves | Tasks | State |")) {
        Assert-True -Condition ($card.Contains($part)) -Because "the prompt has '$part'"
    }
    $bare = New-TeamFeedCard -Queue $queue -RoadmapText $roadmapFixture -FeedFile "team/plans/feed-$feedDate-1.json" -MaxNew 3 -Date $feedDate -Ideas @()
    Assert-True -Condition (-not $bare.Contains("- idea:")) -Because "with no idea to write the run is not asked to touch the roadmap"
    Assert-True -Condition ($bare -match "Do NOT edit docs/ROADMAP.md") -Because "and is told so"
}

# 2026-10-06: two feed runs (5.75 + 5.88 USD) were refused whole because the prompt said "ONE
# sentence" and the judge counted 400 characters on one line. The prompt's limits are read from
# the judge's own variables here, so the two halves cannot drift apart again.
Test-Case "prompt: it states the judge's needs_owner limit, read from the judge's own number" {
    $queue = New-Queue -Tasks @((New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")))
    $card = New-TeamFeedCard -Queue $queue -RoadmapText $roadmapFixture -FeedFile "team/plans/feed-$feedDate-1.json" -MaxNew 3 -Date $feedDate
    Assert-True -Condition ($card.Contains("at most $script:TeamFeedOwnerSentenceMax characters")) -Because "the prompt names the judge's limit ($script:TeamFeedOwnerSentenceMax)"
    Assert-True -Condition ($card.Contains("ONE line")) -Because "the prompt says the sentence is on one line"
    $saved = $script:TeamFeedOwnerSentenceMax
    try {
        $script:TeamFeedOwnerSentenceMax = 401
        $moved = New-TeamFeedCard -Queue $queue -RoadmapText $roadmapFixture -FeedFile "team/plans/feed-$feedDate-1.json" -MaxNew 3 -Date $feedDate
        Assert-True -Condition ($moved.Contains("at most 401 characters")) -Because "the prompt's number is the judge's variable, not a second copy"
        Assert-True -Condition (-not $moved.Contains("400 characters")) -Because "no mention of the limit is a hand-written copy: every one follows the variable"
    }
    finally { $script:TeamFeedOwnerSentenceMax = $saved }
}

Test-Case "prompt: it names every rule the judge refuses on - cards and owner items counted apart, titles, the note, the area, depends_on" {
    $card = New-TeamFeedCard -Queue (New-Queue) -RoadmapText $roadmapFixture -FeedFile "team/plans/feed-$feedDate-1.json" -MaxNew 3 -Date $feedDate
    foreach ($part in @(
            "at most max_new cards", "at most max_new owner items", "differs only in upper or lower case",
            "no brackets inside the note", "at most $script:TeamMaxAreaEntries entries",
            "every id in depends_on is in the queue or in this file")) {
        Assert-True -Condition ($card.Contains($part)) -Because "the prompt has '$part'"
    }
    Assert-True -Condition (-not $card.Contains("at most max_new items")) -Because "'items' alone hides that cards and owner items are counted apart"
}

Test-Case "judge: needs_owner refusals say what was measured; 400 characters on one line pass" {
    $long = New-Card -Id "card-long"; $long.Remove("area"); $long["needs_owner"] = ("a" * 611) + "."
    $problems = @(Get-FeedProblems -Cards @($long))
    $said = @($problems | Where-Object { $_ -match "needs_owner is one sentence" })
    Assert-Equal -Expected 1 -Actual @($said).Count -Because "refused: $($problems -join '; ')"
    Assert-True -Condition ($said[0] -match "needs_owner is one sentence" -and $said[0] -match "\b612 characters") -Because "the refusal says 612: $($said[0])"
    $lines = New-Card -Id "card-lines"; $lines.Remove("area"); $lines["needs_owner"] = "One.`nTwo.`nThree."
    $problems = @(Get-FeedProblems -Cards @($lines))
    $said = @($problems | Where-Object { $_ -match "needs_owner is one sentence" })
    Assert-True -Condition (@($said).Count -eq 1 -and $said[0] -match "\b3 lines") -Because "the refusal says 3 lines: $($problems -join '; ')"
    $edge = New-Card -Id "card-edge"; $edge.Remove("area"); $edge["needs_owner"] = ("a" * ($script:TeamFeedOwnerSentenceMax - 1)) + "."
    Assert-Equal -Expected 0 -Actual @(Get-FeedProblems -Cards @($edge)).Count -Because "exactly $script:TeamFeedOwnerSentenceMax characters is inside the limit: $((Get-FeedProblems -Cards @($edge)) -join '; ')"
}

Test-Case "the feeder's scripts say which encoding they are in, never name main as a branch to write and never push" {
    foreach ($relative in @("scripts\team\feed.ps1", "scripts\lib\TeamFeed.ps1", "scripts\tests\team-feed.tests.ps1")) {
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $repoRoot $relative))
        if (@($bytes | Where-Object { $_ -gt 127 }).Count -eq 0) { continue }
        Assert-True -Condition ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) -Because "$relative holds non-ASCII text and has no byte-order mark"
    }
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repoRoot "scripts\team\feed.ps1"), [ref]$null, [ref]$errors)
    Assert-Equal -Expected 0 -Actual @($errors).Count -Because "feed.ps1 parses"
    $strings = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.StringConstantExpressionAst] }, $true) | ForEach-Object { $_.Value })
    Assert-True -Condition ($strings -contains "commit") -Because "the probe sees the git verbs the script uses"
    foreach ($verb in @("push", "merge", "reset", "checkout", "rebase", "clean")) {
        Assert-True -Condition ($strings -notcontains $verb) -Because "feed.ps1 never runs git $verb"
    }
}

# ============================================================================ the feeder, run

Write-Host ""
Write-Host "the feeder, in a repository of its own, with a fake in place of the model"

$sandboxes = New-Object System.Collections.ArrayList
$fakeApis = New-Object System.Collections.ArrayList
$asyncFeeds = New-Object System.Collections.ArrayList

# The fake: it does what the PLAN says (a JSON file the test wrote), and logs how it was started.
#   (always)      team/lock.json, as the run finds it, is copied beside the log
#   feed_text     written, as it is, to the file the card names in '- feed_file:'
#   roadmap_rows  put after the last row of the 'Approved ideas' table - only when the card asks
#                 for an idea line ('- idea:'), or always with roadmap_force
#   roadmap_tail  appended to docs/ROADMAP.md (an edit outside the table)
#   stray         a file, repository-relative, the run has no business writing
#   raise_stop    with limited_first: that first call also writes team/stop.flag
#   limited_first the FIRST call answers with the subscription's usage-limit error
#   limited_models model ids: a call on one of them answers as the real tool does when THAT
#                 model's limit is met - the rejected rate_limit_event and "out of usage credits"
#   limit_reset_seconds  when those limits lift, seconds from now (default: 200 s ago / 3 days)
#   silent        the run prints something that is not the result document
#   fail_after    the run does everything above (the feed file is on disk), THEN fails
#   hang_after    the run does everything above, then never ends: the feeder's deadline kills it
#   outside_writes ABSOLUTE paths written while the run works: the running cycle's files in the
#                 main checkout (its reports, a split file, a proposal) - not the run's own
#   store_puts    { url, token, task }: ANOTHER WRITER creates that task in the store while the
#                 run works (a PUT with expected_updated_at null, as a create is)
#   hold_until    a path: the run waits for that file to exist (60 s hang guard) before it ends
$fakeText = @'
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$log = [string]$env:PAGENTOS_FAKE_FEED_LOG
$plan = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText([string]$env:PAGENTOS_FAKE_FEED_PLAN, $utf8))
function Get-Plan { param([string]$Name) $p = $plan.PSObject.Properties[$Name]; if ($null -eq $p) { return $null }; return $p.Value }
$roleFile = ""; $tools = ""; $denied = ""; $model = ""
for ($i = 0; $i -lt $Rest.Length; $i++) {
    if ($Rest[$i] -eq "--append-system-prompt-file") { $roleFile = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--allowedTools") { $tools = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--disallowedTools") { $denied = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--model") { $model = $Rest[$i + 1] }
}
$card = [Console]::In.ReadToEnd()
$here = (Get-Location).ProviderPath
$feedFile = ""
if ($card -match '(?m)^- feed_file: (\S+)') { $feedFile = $Matches[1] }
$ideas = @([regex]::Matches($card, '(?m)^- idea: (\S+)') | ForEach-Object { $_.Groups[1].Value })
$earlier = 0
if (Test-Path -LiteralPath $log) { $earlier = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() }).Count }
$entry = [pscustomobject]@{ role = [System.IO.Path]::GetFileNameWithoutExtension($roleFile); cwd = $here; tools = $tools; denied = $denied; model = $model; feed_file = $feedFile; ideas = @($ideas); card = $card }
[System.IO.File]::AppendAllText($log, ($entry | ConvertTo-Json -Compress) + "`n", $utf8)
$lockFile = Join-Path $here "team\lock.json"
if (Test-Path -LiteralPath $lockFile) { Copy-Item -LiteralPath $lockFile -Destination "$log.lock.json" -Force }

if (@(Get-Plan "limited_models") -contains $model -and $model) {
    $away = 259200
    if ($null -ne (Get-Plan "limit_reset_seconds")) { $away = [int](Get-Plan "limit_reset_seconds") }
    $resets = [DateTimeOffset]::UtcNow.AddSeconds($away).ToUnixTimeSeconds()
    $types = @{ "claude-fable-5-1" = "seven_day_overage_included"; "claude-opus-5-5" = "seven_day_opus"; "claude-sonnet-5-5" = "seven_day_sonnet" }
    $info = [ordered]@{ status = "rejected"; resetsAt = $resets; rateLimitType = [string]$types[$model]; isUsingOverage = $false }
    $lines = @(
        (([ordered]@{ type = "system"; subtype = "init"; model = $model; session_id = "s" }) | ConvertTo-Json -Compress),
        (([ordered]@{ type = "rate_limit_event"; rate_limit_info = $info; uuid = "u"; session_id = "s" }) | ConvertTo-Json -Compress -Depth 6),
        (([ordered]@{ type = "result"; subtype = "success"; is_error = $true; result = "You're out of usage credits. Switch to another model to continue."; total_cost_usd = 0 }) | ConvertTo-Json -Compress)
    )
    [Console]::Out.Write(($lines -join "`n") + "`n")
    exit 1
}
if ((Get-Plan "limited_first") -and $earlier -eq 0) {
    if (Get-Plan "raise_stop") { [System.IO.File]::WriteAllText((Join-Path $here "team\stop.flag"), "stop", $utf8) }
    $epoch = [DateTimeOffset]::UtcNow.AddSeconds(-200).ToUnixTimeSeconds()
    if ($null -ne (Get-Plan "limit_reset_seconds")) { $epoch = [DateTimeOffset]::UtcNow.AddSeconds([int](Get-Plan "limit_reset_seconds")).ToUnixTimeSeconds() }
    $document = [pscustomobject]@{ type = "result"; subtype = "success"; is_error = $true; result = "Claude AI usage limit reached|$epoch"; total_cost_usd = 0 }
    [Console]::Out.Write(($document | ConvertTo-Json -Compress))
    exit 1
}
if (Get-Plan "silent") { [Console]::Out.Write("I could not do that."); exit 0 }
foreach ($outside in @(Get-Plan "outside_writes" | Where-Object { $_ })) {
    $folder = Split-Path -Parent ([string]$outside)
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    [System.IO.File]::WriteAllText([string]$outside, "the running cycle wrote this`n", $utf8)
}
foreach ($put in @(Get-Plan "store_puts" | Where-Object { $null -ne $_ })) {
    $client = New-Object System.Net.WebClient
    $client.Encoding = $utf8
    $client.Headers.Add("Authorization", "Bearer " + [string]$put.token)
    $client.Headers.Add("Content-Type", "application/json; charset=utf-8")
    $body = ConvertTo-Json -InputObject ([ordered]@{ task = $put.task; expected_updated_at = $null }) -Depth 8 -Compress
    [void]$client.UploadString([string]$put.url, "PUT", $body)
}
$hold = Get-Plan "hold_until"
if ($hold) {
    $until = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath ([string]$hold)) -and [datetime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 200 }
}

$feedText = Get-Plan "feed_text"
if ($null -ne $feedText -and $feedFile) {
    $target = Join-Path $here ($feedFile -replace "/", "\")
    $folder = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    [System.IO.File]::WriteAllText($target, [string]$feedText, $utf8)
}
$roadmap = Join-Path $here "docs\ROADMAP.md"
$rows = @(Get-Plan "roadmap_rows" | Where-Object { $null -ne $_ })
if (@($rows).Count -gt 0 -and (@($ideas).Count -gt 0 -or (Get-Plan "roadmap_force"))) {
    $text = [System.IO.File]::ReadAllText($roadmap, $utf8)
    $anchor = "| queued |`n"
    $at = $text.LastIndexOf($anchor) + $anchor.Length
    $text = $text.Substring(0, $at) + ((@($rows) | ForEach-Object { [string]$_ + "`n" }) -join "") + $text.Substring($at)
    [System.IO.File]::WriteAllText($roadmap, $text, $utf8)
}
$tail = Get-Plan "roadmap_tail"
if ($tail) { [System.IO.File]::AppendAllText($roadmap, [string]$tail, $utf8) }
$stray = Get-Plan "stray"
if ($stray) {
    $target = Join-Path $here ([string]$stray -replace "/", "\")
    $folder = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    [System.IO.File]::AppendAllText($target, "the lead run was here`n", $utf8)
}
if (Get-Plan "fail_after") { [Console]::Out.Write("I wrote the file and then broke."); exit 1 }
if (Get-Plan "hang_after") { Start-Sleep -Seconds 600 }
$document =[pscustomobject]@{ type = "result"; subtype = "success"; is_error = $false; result = "feed written: $feedFile"; total_cost_usd = 0.25 }
[Console]::Out.Write(($document | ConvertTo-Json -Compress))
exit 0
'@

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function New-FeedSandbox {
    <# A repository of its own, on the lead's branch, with the roadmap, the queue and the lock. #>
    param([object[]]$Tasks = @(), $Lock = $null, [string]$Branch = "team/nightly/lead", $Models = $null)
    $work = Join-Path $env:TEMP ("pagentos-feed-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $root = Join-Path $work "repo"
    [void]$sandboxes.Add($work)
    foreach ($folder in @("scripts\lib", "scripts\team", ".claude\agents", "team", "docs", "src\area")) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder))
    }
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamFeed.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\$name") -Destination (Join-Path $root "scripts\lib\$name")
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\team\feed.ps1") -Destination (Join-Path $root "scripts\team\feed.ps1")
    Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\lead.md") -Destination (Join-Path $root ".claude\agents\lead.md")
    [System.IO.File]::WriteAllText((Join-Path $work "fake-feed-claude.ps1"), $fakeText, $utf8)
    [System.IO.File]::WriteAllText((Join-Path $root ".gitignore"), ".claude/worktrees/`n", $utf8)
    [System.IO.File]::WriteAllText((Join-Path $root "src\area\README.txt"), "the area`n", $utf8)
    [System.IO.File]::WriteAllText((Join-Path $root "docs\ROADMAP.md"), $roadmapFixture, $utf8)
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks $Tasks)
    $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document $lockDocument
    if ($null -ne $Models) { Write-TeamJson -Path (Join-Path $root "team\models.json") -Document $Models }
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
    if ($Branch -ne "main") { [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", $Branch)) }
    return [pscustomobject]@{ Root = $root; Work = $work; Runs = 0 }
}

# Whether the script under test has the feeder's own lock (ADR-0214 addendum, feeder-own-lock).
# The path is handed over only then: the red run against the script without it must show that
# script's own answer ("the lock is held", exit 3), not a parameter it does not know.
$feedHasOwnLock = (Get-Command (Join-Path $repoRoot "scripts\team\feed.ps1")).Parameters.ContainsKey("FeederLockPath")

function Get-FeedArguments {
    <# The feeder's command line: `& script` inside -Command (the way an owner types it). #>
    param($Sandbox, [string]$Machine, [string]$ExtraArguments, [string]$QueueUrl, [string]$QueueTokenFile, [string]$FeederLock)
    return @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        ("& '" + (Join-Path $Sandbox.Root "scripts\team\feed.ps1") + "' -FeedDate '$feedDate' -Machine '$Machine'" +
        " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" +
        (Join-Path $Sandbox.Work "fake-feed-claude.ps1") + "'" +
        $(if ($QueueUrl) { " -QueueUrl '$QueueUrl' -QueueToken '$QueueTokenFile'" } else { "" }) +
        $(if ($FeederLock -and $feedHasOwnLock) { " -FeederLockPath '$FeederLock'" } else { "" }) +
        $(if ($ExtraArguments) { " " + $ExtraArguments } else { "" }) + "; exit `$LASTEXITCODE")
    )
}

function Invoke-Feed {
    param(
        $Sandbox, [hashtable]$Plan = @{}, [string]$Machine = "MAIL", [string]$ExtraArguments = "",
        [string]$QueueUrl = "", [string]$QueueTokenFile = "", [string]$FeederLock = ""
    )
    $root = $Sandbox.Root
    $Sandbox.Runs = [int]$Sandbox.Runs + 1
    $log = Join-Path $Sandbox.Work "fake-$($Sandbox.Runs).log"
    $planPath = Join-Path $Sandbox.Work "plan-$($Sandbox.Runs).json"
    [System.IO.File]::WriteAllText($planPath, (ConvertTo-Json -InputObject ([pscustomobject]$Plan) -Depth 8), $utf8)
    $env:PAGENTOS_FAKE_FEED_LOG = $log
    $env:PAGENTOS_FAKE_FEED_PLAN = $planPath
    try {
        $arguments = Get-FeedArguments -Sandbox $Sandbox -Machine $Machine -ExtraArguments $ExtraArguments -QueueUrl $QueueUrl -QueueTokenFile $QueueTokenFile -FeederLock $FeederLock
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments $arguments -WorkingDirectory $root `
            -TimeoutSeconds 300 -SuccessExitCodes @(0, 1, 2, 3)
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_FEED_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:\PAGENTOS_FAKE_FEED_PLAN -ErrorAction SilentlyContinue
    }
    $calls = @()
    if (Test-Path -LiteralPath $log) {
        $calls = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { ConvertFrom-Json -InputObject $_ })
    }
    $reportPath = Join-Path $root "team\reports\feed-$feedDate.md"
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; StdOut = $result.StdOut; StdErr = $result.StdErr; Calls = $calls
        Said     = ($result.StdOut + $result.StdErr)
        Queue    = (Read-TeamJson -Path (Join-Path $root "team\queue.json"))
        Lock     = (Read-TeamJson -Path (Join-Path $root "team\lock.json"))
        Report   = $(if (Test-Path -LiteralPath $reportPath) { [System.IO.File]::ReadAllText($reportPath, [System.Text.Encoding]::UTF8) } else { "" })
        Roadmap  = [System.IO.File]::ReadAllText((Join-Path $root "docs\ROADMAP.md"), [System.Text.Encoding]::UTF8)
        Head     = (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "HEAD"))
    }
}

function Get-TaskById {
    param($Queue, [string]$Id)
    $found = @(Get-TeamTasks -Queue $Queue | Where-Object { $_.id -eq $Id })
    if (@($found).Count -eq 0) { return $null }
    return $found[0]
}

function Get-FeedText {
    param([object[]]$Cards)
    return (ConvertTo-Json -InputObject @($Cards) -Depth 6)
}

function Get-QueueIds {
    param($Queue)
    return (@(Get-TeamTasks -Queue $Queue | ForEach-Object { [string]$_.id }) -join ",")
}

$twoCards = @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Row "browser-use, anywhere (order 2b)"))

try {
    Test-Case "fed: with three runnable tasks nothing is started, nothing is written, and the script says so" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a")), (New-Task -Id "task-two" -Area @("src/b")), (New-Task -Id "task-three" -State "inspecting" -Area @("src/c")))
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "the seats are full: no lead run"
        Assert-Equal -Expected "task-one,task-two,task-three" -Actual (Get-QueueIds -Queue $run.Queue) -Because "the queue is as it was"
        Assert-True -Condition ($run.StdOut -match "3 runnable") -Because "it says why: $($run.StdOut)"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain")) -Because "not a file was written"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed"
    }

    Test-Case "fed: with one runnable task the lead run is started once and its two cards are queued as approved, with the roadmap reason" {
        $models = [pscustomobject]@{ roles = [pscustomobject]@{ lead = "model-for-the-lead"; worker = "model-for-workers" } }
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Models $models
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "ONE lead run: $($run.Said)"
        $call = $run.Calls[0]
        Assert-Equal -Expected "lead" -Actual $call.role -Because "the lead's role file is the system prompt"
        Assert-Equal -Expected "team/plans/feed-$feedDate-1.json" -Actual $call.feed_file -Because "the one file it may write"
        $tools = @(([string]$call.tools).Split(","))
        Assert-True -Condition ($tools -contains "Read" -and $tools -contains "Write") -Because "read, and write the one file: $($tools -join ',')"
        Assert-True -Condition ($tools -notcontains "Bash" -and $tools -notcontains "Agent" -and $tools -notcontains "Edit") -Because "no shell, no agents, and no edit when there is no idea line to write: $($tools -join ',')"
        Assert-True -Condition (([string]$call.denied).Split(",") -contains "Bash") -Because "the shell is denied by name: $($call.denied)"
        Assert-Equal -Expected "model-for-the-lead" -Actual $call.model -Because "the model the team's setting names for the lead"
        Assert-Equal -Expected ([string]$box.Root).ToLowerInvariant() -Actual ([string]$call.cwd).ToLowerInvariant() -Because "it works in the checkout, where team/plans and the roadmap are"
        Assert-True -Condition ([string]$call.card -match "- task-one \[approved\]") -Because "the prompt names what the queue already has"
        Assert-Equal -Expected "task-one,card-a,card-b" -Actual (Get-QueueIds -Queue $run.Queue) -Because $run.Said
        foreach ($id in @("card-a", "card-b")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "approved" -Actual $task.state -Because "roadmap work needs no approval"
            Assert-True -Condition ($task.reason -match "^roadmap: .+; fed by the lead run $feedDate$") -Because "the reason: $($task.reason)"
            Assert-Equal -Expected "the goal of $id" -Actual $task.goal -Because "the card's goal is the task's"
        }
        Assert-Equal -Expected "roadmap: Secretary; fed by the lead run $feedDate" -Actual (Get-TaskById -Queue $run.Queue -Id "card-a").reason -Because "the row is named"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue the feeder wrote keeps the protocol"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
        Assert-True -Condition ($run.Report -match "card-a" -and $run.Report -match "card-b" -and $run.Report -match "kuyruğa eklendi") -Because "the report names what was cut: $($run.Report)"
        Assert-True -Condition ($run.Report -match "model-for-the-lead") -Because "and the model the run used"
    }

    Test-Case "fed: while the run works the lock is held as cycle 'feed-<date>' by this machine" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        # The fake copies team/lock.json, as it finds it, beside its own log.
        $copy = Join-Path $box.Work "fake-1.log.lock.json"
        Assert-True -Condition (Test-Path -LiteralPath $copy) -Because "the lock as it was during the run: $($run.Said)"
        $held = Read-TeamJson -Path $copy
        Assert-Equal -Expected $true -Actual ([bool]$held.held) -Because "held"
        Assert-Equal -Expected "feed-$feedDate" -Actual $held.cycle_id -Because "as cycle feed-<date>"
        Assert-Equal -Expected "MAIL" -Actual $held.machine -Because "by this machine"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "and released afterwards"
    }

    Test-Case "fed: a task whose dependencies are unmet does not count - three tasks by state, two runnable, the lead run starts" {
        $box = New-FeedSandbox -Tasks @(
            (New-Task -Id "task-one" -Area @("src/a")), (New-Task -Id "task-two" -Area @("src/b")),
            (New-Task -Id "task-three" -Area @("src/c") -DependsOn @("task-one")))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "task-three waits for task-one: only two can run. $($run.Said)"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-a")) -Because "and the cards were queued"
    }

    $refusedFiles = @(
        @{ Name = "a roadmap_row that is not a row of ROADMAP.md"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Row "A new row nobody approved")) }; Says = "is not a row of docs/ROADMAP.md" },
        @{ Name = "an id that exists"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "busy-one")) }; Says = "the id is already in the queue" },
        @{ Name = "an area that overlaps an active task's"; Cards = { @((New-Card -Id "card-a"), (New-Card -Id "card-b" -Area @("src/busy/deep"))) }; Says = "overlaps the area of busy-one" },
        @{ Name = "a broken schema (no acceptance)"; Cards = { $card = New-Card -Id "card-b"; $card.Remove("acceptance"); @((New-Card -Id "card-a"), $card) }; Says = "'acceptance' is missing" }
    )
    foreach ($case in $refusedFiles) {
        Test-Case "refused: a feed file with $($case.Name) queues NOTHING - the good card included - and the reason is in the report" {
            $box = New-FeedSandbox -Tasks @((New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")))
            $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @(& $case.Cards)) }
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
            Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "one try"
            Assert-Equal -Expected "busy-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "whole or nothing: $($run.Report)"
            Assert-True -Condition ($run.Report -match "reddedildi" -and $run.Report -match [regex]::Escape($case.Says)) -Because "the reason is in the report: $($run.Report)"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
        }
    }

    Test-Case "refused: a file that is not JSON, and no file at all, queue nothing and say so" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = "{ not json" }
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because $run.Said
        Assert-True -Condition ($run.Report -match "reddedildi" -and $run.Report -match "not JSON") -Because $run.Report
        $none = Invoke-Feed -Sandbox $box -Plan @{}
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $none.Queue) -Because $none.Said
        Assert-True -Condition ($none.Report -match "wrote no") -Because $none.Report
        Assert-Equal -Expected "team/plans/feed-$feedDate-2.json" -Actual $none.Calls[0].feed_file -Because "the second run of the day has its own file"
    }

    Test-Case "refused: a lead run that fails queues nothing, and the lock is released" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ silent = $true; feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "a run that printed no result is not trusted, whatever it left on disk"
        Assert-True -Condition ($run.Report -match "lead koşusu: başarısız") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "refused: a lead run that FAILS AFTER writing a valid feed file queues nothing - the file on disk is not used" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ fail_after = $true; feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "one run, and a failure is not tried again"
        # The file is there and it is sound: the judge of the first 'fed' case would queue both cards.
        $left = Read-TeamSplitFile -Path (Join-Path $box.Root "team\plans\feed-$feedDate-1.json")
        Assert-True -Condition ([bool]$left.Ok -and @($left.Split).Count -eq 2) -Because "the failed run left a valid feed file of two cards: $($left.Why)"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "a run that did not end well is not trusted with the queue: $($run.Report)"
        Assert-True -Condition ($run.Report -match "lead koşusu: başarısız" -and $run.Report -match "kuyruğa hiçbir şey eklenmedi") -Because $run.Report
        Assert-True -Condition ($run.Report -notmatch "kuyruğa eklendi") -Because "the report claims no card: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "refused: a lead run killed at its deadline AFTER writing a valid feed file queues nothing" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        # 0.3 minutes is the hang guard, not the claim: the fake writes the file and then never
        # ends, and the assertion below fails the case if the file was not there in time.
        $run = Invoke-Feed -Sandbox $box -Plan @{ hang_after = $true; feed_text = (Get-FeedText -Cards $twoCards) } -ExtraArguments "-RunMinutes 0.3"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "one run"
        $left = Read-TeamSplitFile -Path (Join-Path $box.Root "team\plans\feed-$feedDate-1.json")
        Assert-True -Condition ([bool]$left.Ok -and @($left.Split).Count -eq 2) -Because "the killed run had written a valid feed file of two cards: $($left.Why)"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "a run that was killed is not trusted with the queue: $($run.Report)"
        Assert-True -Condition ($run.Report -match "lead koşusu: süre doldu" -and $run.Report -match "kuyruğa hiçbir şey eklenmedi") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "owner: a 'needs_owner' item becomes an awaiting_owner idea with a proposal file, never a task; the card beside it is queued" {
        $owner = New-Card -Id "home-assistant" -Row "The house"
        $owner["needs_owner"] = "It needs a Home Assistant install and an account the owner must open."
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-a"), $owner)) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "task-one,card-a,home-assistant" -Actual (Get-QueueIds -Queue $run.Queue) -Because $run.Report
        $idea = Get-TaskById -Queue $run.Queue -Id "home-assistant"
        Assert-Equal -Expected "awaiting_owner" -Actual $idea.state -Because "it waits for the owner"
        Assert-Equal -Expected 0 -Actual @($idea.area).Count -Because "no area: no worker can be given it"
        Assert-Equal -Expected 0 -Actual @(Get-TeamRunnableTasks -Queue (New-Queue -Tasks @($idea))).Count -Because "and it fills no seat"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $box.Root ($idea.proposal -replace "/", "\"))) -Because "its proposal is a file: $($idea.proposal)"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "card-a").state -Because "the card that needs nobody is work"
        Assert-True -Condition ($run.Report -match "sahibe soruldu" -and $run.Report -match "home-assistant") -Because $run.Report
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "ideas: an approved idea missing from the roadmap gets exactly one table line, committed on the lead's branch; a second run adds none" {
        $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one"))
        $main = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "main")
        $plan = @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-a"))); roadmap_rows = @($ideaRow) }
        $run = Invoke-Feed -Sandbox $box -Plan $plan
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "idea-one" -Actual (@($run.Calls[0].ideas) -join ",") -Because "the run was asked for the idea's line"
        Assert-True -Condition (([string]$run.Calls[0].tools).Split(",") -contains "Edit") -Because "and may edit, to write it: $($run.Calls[0].tools)"
        Assert-Equal -Expected 1 -Actual @($run.Roadmap -split "`n" | Where-Object { $_ -match "team/proposals/idea-one\.md" }).Count -Because "exactly one line: $($run.Report)"
        Assert-Equal -Expected "1" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("rev-list", "--count", "main..HEAD")) -Because "one commit on the lead's branch: $($run.Report)"
        Assert-Equal -Expected "docs/ROADMAP.md" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("show", "--name-only", "--format=", "HEAD")) -Because "the commit holds the roadmap and nothing else"
        $subject = Invoke-SandboxGit -Root $box.Root -Arguments @("log", "-1", "--format=%s")
        Assert-True -Condition ($subject -match "idea-one") -Because "the message names the idea: $subject"
        Assert-Equal -Expected $main -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "main")) -Because "main was not written to"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain", "--", "docs")) -Because "the roadmap is committed, not left dirty"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "card-a").state -Because "the card of the same run is queued"
        Assert-True -Condition ($run.Report -match "Approved ideas" -and $run.Report -match "idea-one") -Because $run.Report

        $again = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-b"))); roadmap_rows = @($ideaRow) }
        Assert-Equal -Expected 0 -Actual @($again.Calls[0].ideas).Count -Because "the idea is named now: the second run is not asked for it"
        Assert-True -Condition (([string]$again.Calls[0].tools).Split(",") -notcontains "Edit") -Because "and cannot edit: $($again.Calls[0].tools)"
        Assert-Equal -Expected 1 -Actual @($again.Roadmap -split "`n" | Where-Object { $_ -match "team/proposals/idea-one\.md" }).Count -Because "still exactly one line"
        Assert-Equal -Expected $run.Head -Actual $again.Head -Because "and no second commit"
    }

    Test-Case "ideas: on main the idea line is not asked for and nothing is committed; the cards are still queued" {
        $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one")) -Branch "main"
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-a"))); roadmap_rows = @($ideaRow) }
        Assert-Equal -Expected 0 -Actual @($run.Calls[0].ideas).Count -Because "main is never written: the line waits for the lead's branch"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed on main"
        Assert-Equal -Expected $roadmapFixture -Actual $run.Roadmap -Because "the roadmap is as it was"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-a")) -Because "feeding the queue does not need the branch: $($run.Report)"
        Assert-True -Condition ($run.Report -match "main") -Because "the report says why the line was not written: $($run.Report)"
    }

    Test-Case "ideas: on a detached HEAD the idea line is not asked for and nothing is committed; the cards are still queued" {
        $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one"))
        [void](Invoke-SandboxGit -Root $box.Root -Arguments @("checkout", "-q", "--detach"))
        Assert-Equal -Expected "HEAD" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "--abbrev-ref", "HEAD")) -Because "the sandbox is on no branch"
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-a"))); roadmap_rows = @($ideaRow) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "the cards are still asked for: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @($run.Calls[0].ideas).Count -Because "a commit on no branch belongs to nobody: the line waits for the lead's branch"
        Assert-True -Condition (([string]$run.Calls[0].tools).Split(",") -notcontains "Edit") -Because "and the run cannot edit: $($run.Calls[0].tools)"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed on the detached HEAD"
        Assert-Equal -Expected $roadmapFixture -Actual $run.Roadmap -Because "the roadmap is as it was"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain", "--", "docs")) -Because "and not left dirty"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-a")) -Because "feeding the queue does not need a branch: $($run.Report)"
        Assert-True -Condition ($run.Report -match "onaylanan fikir satırı bu koşuda yazılmadı \(idea-one\)" -and $run.Report -match "ayrık HEAD") -Because "the report says why the line was not written: $($run.Report)"
    }

    Test-Case "ideas: with an uncommitted edit in docs/ROADMAP.md the idea line is not asked for, nothing is committed, and the edit is left as found" {
        $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one"))
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        # Somebody's work in progress: a sentence under "Definition of done", not committed.
        $theirs = $roadmapFixture.Replace("Every row says HAVE.`n", "Every row says HAVE.`nA sentence somebody is still writing.`n")
        [System.IO.File]::WriteAllText((Join-Path $box.Root "docs\ROADMAP.md"), $theirs, $utf8)
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-a"))); roadmap_rows = @($ideaRow) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "the cards are still asked for: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @($run.Calls[0].ideas).Count -Because "a row committed now would carry somebody's unfinished edit with it"
        Assert-True -Condition (([string]$run.Calls[0].tools).Split(",") -notcontains "Edit") -Because "and the run cannot edit: $($run.Calls[0].tools)"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed"
        Assert-Equal -Expected $theirs -Actual $run.Roadmap -Because "the edit is left exactly as it was found"
        Assert-Equal -Expected "M docs/ROADMAP.md" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain", "--", "docs")) -Because "still uncommitted, still unstaged"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-a")) -Because "feeding the queue does not need a clean roadmap: $($run.Report)"
        Assert-True -Condition ($run.Report -match "onaylanan fikir satırı bu koşuda yazılmadı \(idea-one\)" -and $run.Report -match "kaydedilmemiş değişiklik") -Because "the report says why the line was not written: $($run.Report)"
    }

    $strays = @(
        @{ Name = "a file outside the feed file"; Plan = @{ stray = "src/area/README.txt" }; Says = "src/area/README.txt" },
        @{ Name = "a NEW file somewhere else"; Plan = @{ stray = "docs/notes.md" }; Says = "docs/notes.md" },
        @{ Name = "the roadmap outside the table"; Plan = @{ roadmap_tail = "A sentence the lead added.`n" }; Says = "outside the 'Approved ideas' table" }
    )
    foreach ($case in $strays) {
        Test-Case "refused: a lead run that edits $($case.Name) queues nothing and commits nothing, and the roadmap is as it was" {
            $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one"))
            $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
            $plan = @{ feed_text = (Get-FeedText -Cards $twoCards); roadmap_rows = @($ideaRow) }
            foreach ($key in @($case.Plan.Keys)) { $plan[$key] = $case.Plan[$key] }
            $run = Invoke-Feed -Sandbox $box -Plan $plan
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
            Assert-Equal -Expected "idea-one,task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "a run that left its file is not trusted with the queue: $($run.Report)"
            Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed"
            Assert-Equal -Expected $roadmapFixture -Actual $run.Roadmap -Because "the roadmap was put back, byte for byte"
            Assert-True -Condition ($run.Report -match "reddedildi" -and $run.Report -match [regex]::Escape($case.Says)) -Because "the report names it: $($run.Report)"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
        }
    }

    Test-Case "refused: a roadmap edit nobody asked for (no idea to write) is put back, and nothing is queued or committed" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards); roadmap_rows = @($ideaRow); roadmap_force = $true }
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because $run.Report
        Assert-Equal -Expected $roadmapFixture -Actual $run.Roadmap -Because "put back"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed"
        Assert-True -Condition ($run.Report -match "docs/ROADMAP\.md") -Because $run.Report
    }

    Test-Case "the other machine's lock stops it before it starts anything" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Lock $held
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "the queue was not written"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $run.Lock.machine -Because "the lock is still theirs"
        Assert-True -Condition ($run.Said -match "GMKADIRAKBABA") -Because "it says who holds the lock: $($run.Said)"
        # A report is of a run. The Onay Merkezi shows the newest report: one written here, every
        # 30 minutes while a cycle runs, would stand in the place of the cycle's.
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $box.Root "team\reports"))) -Because "nothing was started, so there is no report: $($run.Report)"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain")) -Because "not a file was written"
    }

    Test-Case "a lock the other machine held for seven hours is taken over, and the report says so" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-7))
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Lock $held
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-a")) -Because $run.Said
        Assert-True -Condition ($run.Report -match "bayat kilit devralındı: GMKADIRAKBABA") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "team/stop.flag stops it: no run, no card, and the flag is left for the cycle it was written for" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $flag = Join-Path $box.Root "team\stop.flag"
        [System.IO.File]::WriteAllText($flag, "stop`n", $utf8)
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no new run starts under the flag"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "nothing was queued"
        Assert-True -Condition (Test-Path -LiteralPath $flag) -Because "the flag is the cycle's to remove"
        Assert-True -Condition ($run.Said -match "stop\.flag") -Because "it says why: $($run.Said)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was never taken"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $box.Root "team\reports"))) -Because "nothing was started, so there is no report: $($run.Report)"
        Assert-Equal -Expected "?? team/stop.flag" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain")) -Because "the flag the test wrote is the only change"
    }

    Test-Case "-DryRun changes nothing: no run, no lock, no file, and it prints what it would do" {
        $box = New-FeedSandbox -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one"))
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards); roadmap_rows = @($ideaRow) } -ExtraArguments "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing is started"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $box.Root -Arguments @("status", "--porcelain")) -Because "not a file was written - the lock, the report and the feed file included"
        Assert-Equal -Expected $head -Actual $run.Head -Because "nothing was committed"
        Assert-True -Condition ($run.StdOut -match "1 runnable" -and $run.StdOut -match "would start" -and $run.StdOut -match "idea-one") -Because "what it would do: $($run.StdOut)"
        Assert-True -Condition ($run.StdOut -match "team/plans/feed-$feedDate-1\.json") -Because "and the file it would ask for"
    }

    Test-Case "the usage limit: the run that hit it is tried again when the limit has lifted, and the cards are queued" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_first = $true; feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "the limited run, then the one that worked"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-b")) -Because $run.Report
        Assert-True -Condition ($run.Report -match "Max kullanım limiti") -Because "the report says the limit was met: $($run.Report)"
    }

    $leadOnFable = [pscustomobject]@{ roles = [pscustomobject]@{ lead = "claude-fable-5-1" } }

    Test-Case "the usage limit: a lead model the cycle already knows is limited is not tried - the feeder starts one model down, and says so" {
        # 2026-10-02: Fable's week was used up (until Monday). The feeder asked for Fable all the
        # same, was refused, and WAITED for the reset - three days, holding the team's lock, so
        # no cycle could start. The cycle had lowered its own runs hours before.
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Models $leadOnFable
        $until = [datetime]::UtcNow.AddDays(3).ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)
        Write-TeamJson -Path (Join-Path $box.Root "team\limits.json") -Document ([ordered]@{ models = [ordered]@{ "claude-fable-5-1" = [ordered]@{ until = $until; type = "seven_day_overage_included" } } })
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_models = @("claude-fable-5-1"); feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "claude-opus-5-5" -Actual (@($run.Calls | ForEach-Object { $_.model }) -join ",") -Because "one run, on the next model down: Fable was not asked"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-b")) -Because $run.Report
        Assert-True -Condition ($run.Report -match "model düşürüldü: claude-fable-5-1 -> claude-opus-5-5") -Because "the report says the model was lowered: $($run.Report)"
    }

    Test-Case "the usage limit: a model limit met in the run is not waited for - the same feed is asked again at once, one model down" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Models $leadOnFable
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_models = @("claude-fable-5-1"); feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "claude-fable-5-1,claude-opus-5-5" -Actual (@($run.Calls | ForEach-Object { $_.model }) -join ",") -Because "the limited run, then the next model down"
        Assert-True -Condition ($null -ne (Get-TaskById -Queue $run.Queue -Id "card-b")) -Because $run.Report
        Assert-True -Condition ($run.Report -notmatch "beklendi") -Because "three days are not waited for: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
    }

    Test-Case "the usage limit: every model limited for days - the feeder does not wait, queues nothing, says so and lets the lock go" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Models $leadOnFable
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_models = @("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"); feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected "claude-fable-5-1,claude-opus-5-5,claude-sonnet-5-5" -Actual (@($run.Calls | ForEach-Object { $_.model }) -join ",") -Because "each model once, strongest first"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "nothing was queued"
        Assert-True -Condition ($run.Report -match "beklenmedi") -Because "the report says it did not wait: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released: the cycle can run"
    }

    Test-Case "the usage limit: a limit that lifts in days is never waited for, whatever the model - only one that lifts within -MaxLimitWaitMinutes" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_first = $true; limit_reset_seconds = 259200; feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "no second run: the reset is three days away"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "nothing was queued"
        Assert-True -Condition ($run.Report -match "beklenmedi") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "the usage limit: without -WaitForUsageLimit the feeder stops, queues nothing, and says so" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_first = $true; feed_text = (Get-FeedText -Cards $twoCards) } -ExtraArguments "-WaitForUsageLimit `$false"
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "no second try"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "nothing was queued"
        Assert-True -Condition ($run.Report -match "Max kullanım limiti") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "the usage limit: a stop flag that came up while the limit was met ends it - the run is not started again" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ limited_first = $true; raise_stop = $true; feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "the limited run, and no new run under the flag"
        Assert-Equal -Expected "task-one" -Actual (Get-QueueIds -Queue $run.Queue) -Because "nothing was queued"
        Assert-True -Condition ($run.Report -match "stop\.flag") -Because $run.Report
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $box.Root "team\stop.flag")) -Because "the flag is the cycle's to remove"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "a queue that breaks the protocol starts nothing" {
        $bad = New-Task -Id "task-one" -State "assigned" -Area @()
        $box = New-FeedSandbox -Tasks @($bad)
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) }
        Assert-Equal -Expected 2 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
    }

    # ------------------------------------------------------------------ the queue on the Cloud Core
    Write-Host ""
    Write-Host "the queue and the lock on the Cloud Core (the cycle's fake listener)"

    function Start-FakeApi {
        # -Extra: more of the listener's seed, as it is (`faults`).
        param([object[]]$Tasks = @(), $Lock = $null, [hashtable]$Extra = @{})
        $work = Join-Path $env:TEMP ("pagentos-feedapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void](New-Item -ItemType Directory -Force -Path $work)
        [void]$sandboxes.Add($work)
        $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
        $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = $lockDocument }
        foreach ($key in @($Extra.Keys)) { $seed | Add-Member -NotePropertyName $key -NotePropertyValue $Extra[$key] }
        [System.IO.File]::WriteAllText((Join-Path $work "seed.json"), (ConvertTo-Json -InputObject $seed -Depth 12), $utf8)
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $repoRoot "scripts\team\fake-team-api.ps1") + '"'),
            "-Port", $port, "-Seed", ('"' + (Join-Path $work "seed.json") + '"'), "-Log", ('"' + (Join-Path $work "requests.log") + '"'),
            "-Ready", ('"' + (Join-Path $work "ready") + '"'), "-Token", "test-token")
        $process = Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Hidden
        [void]$fakeApis.Add($process)
        $deadline = [datetime]::UtcNow.AddSeconds(40)
        while (-not (Test-Path -LiteralPath (Join-Path $work "ready"))) {
            if ([datetime]::UtcNow -gt $deadline -or $process.HasExited) { throw "the fake team API did not start" }
            Start-Sleep -Milliseconds 200
        }
        $tokenFile = Join-Path $work "token.txt"
        [System.IO.File]::WriteAllText($tokenFile, "test-token`n", $utf8)
        return [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Work = $work; TokenFile = $tokenFile }
    }

    function Get-FakeApiState {
        param($Api)
        return (Invoke-JsonUtf8 -Uri ($Api.Url + "/__state"))
    }

    function Get-FakeApiRequests {
        param($Api)
        # The fake writes a request's line after it answered it; one more call is the barrier.
        [void](Get-FakeApiState -Api $Api)
        $log = Join-Path $Api.Work "requests.log"
        if (-not (Test-Path -LiteralPath $log)) { return @() }
        return @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() -and $_ -notmatch ' /__state ' })
    }

    Test-Case "in API mode the cards are written to the store, the lock is taken and released there, and the files are left alone" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
        $box = New-FeedSandbox -Tasks @((New-Task -Id "local-one" -Area @("src/a")), (New-Task -Id "local-two" -Area @("src/b")), (New-Task -Id "local-three" -Area @("src/c")))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) } -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "the STORE has one runnable task - the three in the local file are not the queue: $($run.Said)"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "task-one,card-a,card-b" -Actual (@($state.tasks | ForEach-Object { $_.id }) -join ",") -Because "the cards reached the store"
        Assert-Equal -Expected "approved" -Actual @($state.tasks | Where-Object { $_.id -eq "card-a" })[0].state -Because "as approved"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was released through the API"
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-Equal -Expected 2 -Actual @($requests | Where-Object { $_ -match "^POST /v1/team/queue/lock 200" }).Count -Because "one acquire, one release: $($requests -join '; ')"
        Assert-Equal -Expected 2 -Actual @($requests | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/card-[ab] 200" }).Count -Because "one write per new card, and none for the task nobody changed"
        Assert-True -Condition ($state.reports.PSObject.Properties["feed-$feedDate.md"].Value -match "card-a") -Because "the report text is in the store"
        Assert-Equal -Expected "local-one,local-two,local-three" -Actual (Get-QueueIds -Queue $run.Queue) -Because "the sandbox's queue.json was not written in API mode"
    }

    Test-Case "in API mode the other machine's fresh lock stops it, and nothing is written" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock $held
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) } -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual (Get-FakeApiState -Api $api).lock.machine -Because "the lock is still theirs"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|POST|DELETE|PATCH) " }).Count -Because "no write at all: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected 0 -Actual @((Get-FakeApiState -Api $api).reports.PSObject.Properties).Count -Because "no report in the store: the Onay Merkezi keeps the report of the cycle that holds the lock"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $box.Root "team\reports"))) -Because "and none on disk"
    }

    Test-Case "in API mode team/stop.flag stops it, and no report is posted to the store" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one"))
        [System.IO.File]::WriteAllText((Join-Path $box.Root "team\stop.flag"), "stop`n", $utf8)
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $twoCards) } -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|POST|DELETE|PATCH) " }).Count -Because "no write at all: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected 0 -Actual @((Get-FakeApiState -Api $api).reports.PSObject.Properties).Count -Because "no report in the store"
        Assert-True -Condition ($run.Said -match "stop\.flag") -Because "it says why: $($run.Said)"
    }

    # ------------------------------------------------------------------ beside a running cycle
    Write-Host ""
    Write-Host "a live cycle of this machine holds the lock: the feeder's own lock, create-only writes (API mode)"

    function New-LiveCycleLock {
        <# The lock of a cycle of THIS machine that is running: its pid is this test's, alive. #>
        return [pscustomobject]@{
            held = $true; machine = "MAIL"; cycle_id = "d20261003"; pid = $PID
            acquired_at = (Get-TeamTimestamp -Now ([datetime]::UtcNow.AddMinutes(-30)))
        }
    }

    function Get-FeederLockPath { param($Box) return (Join-Path $Box.Work "feeder\team-feeder.lock") }

    function Invoke-LiveFeed {
        param($Box, $Api, [hashtable]$Plan = @{}, [string]$ExtraArguments = "")
        return (Invoke-Feed -Sandbox $Box -Plan $Plan -QueueUrl $Api.Url -QueueTokenFile $Api.TokenFile -FeederLock (Get-FeederLockPath -Box $Box) -ExtraArguments $ExtraArguments)
    }

    function Get-Json { param($Value) return (ConvertTo-Json -InputObject $Value -Depth 12 -Compress) }

    function Get-StoreTask {
        param($State, [string]$Id)
        $found = @($State.tasks | Where-Object { $null -ne $_ -and [string]$_.id -eq $Id })
        if (@($found).Count -eq 0) { return $null }
        return $found[0]
    }

    function Get-Worktrees {
        <# The worktrees git knows of in the sandbox, the main one included. #>
        param($Box)
        return @((Invoke-SandboxGit -Root $Box.Root -Arguments @("worktree", "list", "--porcelain")) -split "`n" | Where-Object { $_ -match '^worktree ' })
    }

    function Assert-LockFreeLeftNothing {
        <# The feeder's own lock file and its throwaway worktree are gone. #>
        param($Box, [string]$Because)
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-FeederLockPath -Box $Box))) -Because "the feeder's own lock is released ($Because)"
        Assert-Equal -Expected 1 -Actual @(Get-Worktrees -Box $Box).Count -Because "the throwaway worktree is removed ($Because): $((Get-Worktrees -Box $Box) -join '; ')"
    }

    $liveCards = @((New-Card -Id "card-a" -Area @("src/card-a")), (New-Card -Id "card-b" -Area @("src/card-b") -Row "browser-use, anywhere (order 2b)"))

    Test-Case "lock-free 1: beside a live cycle of ours the feeder cuts its cards under ITS OWN lock - the cycle's lock is equal before and after, never posted" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $lockBefore = Get-Json -Value (Get-FakeApiState -Api $api).lock
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "one lead run: $($run.Said)"
        $state = Get-FakeApiState -Api $api
        foreach ($id in @("card-a", "card-b")) {
            Assert-Equal -Expected "approved" -Actual ([string](Get-StoreTask -State $state -Id $id).state) -Because "$id is in the store as approved: $($run.Said)"
        }
        Assert-Equal -Expected $lockBefore -Actual (Get-Json -Value $state.lock) -Because "the cycle's lock is the cycle's: holder, cycle_id, pid, acquired_at"
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-Equal -Expected 0 -Actual @($requests | Where-Object { $_ -match '^POST /v1/team/queue/lock ' }).Count -Because "the feeder neither took nor released the cycle's lock: $($requests -join '; ')"
        Assert-True -Condition (([string]$run.Calls[0].cwd).ToLowerInvariant() -ne ([string]$box.Root).ToLowerInvariant() -and ([string]$run.Calls[0].cwd) -match '\\\.claude\\worktrees\\') -Because "the lead worked in a throwaway worktree, not where the cycle works: $($run.Calls[0].cwd)"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $box.Root "team\plans\feed-$feedDate-1.json")) -Because "the accepted feed file is kept in the main checkout"
        Assert-LockFreeLeftNothing -Box $box -Because "a normal end"
    }

    Test-Case "lock-free 2: create-only - every PUT is a create (expected_updated_at null) of an id the store did not have; tasks in work are byte-equal, also when the feed names one" {
        $inWork = @(
            (New-Task -Id "work-progress" -State "in_progress" -Area @("src/wp")),
            (New-Task -Id "work-inspecting" -State "inspecting" -Area @("src/wi")),
            (New-Task -Id "work-returned" -State "returned" -Area @("src/wr")))
        foreach ($plan in @(
                @{ Cards = $liveCards; Written = "card-a,card-b" },
                @{ Cards = @((New-Card -Id "card-a" -Area @("src/card-a")), (New-Card -Id "work-progress" -Area @("src/elsewhere"))); Written = "" })) {
            $api = Start-FakeApi -Tasks $inWork -Lock (New-LiveCycleLock)
            $box = New-FeedSandbox
            $before = Get-FakeApiState -Api $api
            $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $plan.Cards) } -ExtraArguments "-MinRunnable 6"
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
            $after = Get-FakeApiState -Api $api
            foreach ($id in @("work-progress", "work-inspecting", "work-returned")) {
                Assert-Equal -Expected (Get-Json -Value (Get-StoreTask -State $before -Id $id)) -Actual (Get-Json -Value (Get-StoreTask -State $after -Id $id)) -Because "$id is a task in work: never written"
            }
            $puts = @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^PUT /v1/team/queue/tasks/' })
            $ids = @($puts | ForEach-Object { ($_ -split ' ')[1] -replace '^/v1/team/queue/tasks/', '' })
            Assert-Equal -Expected $plan.Written -Actual ($ids -join ",") -Because "only new ids are sent: $($puts -join '; ') / $($run.Report)"
            Assert-Equal -Expected @($plan.Written -split ',' | Where-Object { $_ }).Count -Actual @($puts | Where-Object { $_ -match ' 200$' }).Count -Because "each as a create the store took: $($puts -join '; ')"
            if (-not $plan.Written) {
                Assert-True -Condition ($run.Report -match "the id is already in the queue" -and $run.Report -match "work-progress") -Because "the file is refused whole, and says why: $($run.Report)"
            }
        }
        # The body of a create: expected_updated_at is null. The listener answers 409 to a create
        # that carries a version (a task it does not have) - the 200s above prove the null.
    }

    Test-Case "lock-free 3: the running cycle's writes in the main checkout neither refuse the feed nor are blamed on it; the run's OWN stray write still refuses it whole" {
        $cases = @(
            @{ Name = "the cycle writes its report, a split and a proposal"; Outside = $true; Stray = ""; Accepted = $true },
            @{ Name = "the lead run writes a stray file where it works"; Outside = $false; Stray = "src/area/README.txt"; Accepted = $false },
            @{ Name = "both at once"; Outside = $true; Stray = "docs/notes.md"; Accepted = $false }
        )
        foreach ($case in $cases) {
            $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
            $box = New-FeedSandbox
            $plan = @{ feed_text = (Get-FeedText -Cards $liveCards) }
            if ($case.Outside) {
                $plan["outside_writes"] = @(
                    (Join-Path $box.Root "team\reports\d20261003\worker-task-one.md"),
                    (Join-Path $box.Root "team\plans\d20261003-split-task-one.json"),
                    (Join-Path $box.Root "team\proposals\2026-10-03-an-idea.md"))
            }
            if ($case.Stray) { $plan["stray"] = $case.Stray }
            $run = Invoke-LiveFeed -Box $box -Api $api -Plan $plan
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "$($case.Name): $($run.Said)"
            $state = Get-FakeApiState -Api $api
            $queued = (@($state.tasks | ForEach-Object { [string]$_.id }) -join ",")
            if ($case.Accepted) {
                Assert-Equal -Expected "task-one,card-a,card-b" -Actual $queued -Because "$($case.Name): the feed is accepted: $($run.Report)"
            }
            else {
                Assert-Equal -Expected "task-one" -Actual $queued -Because "$($case.Name): refused whole: $($run.Report)"
                Assert-True -Condition ($run.Report -match "reddedildi" -and $run.Report -match [regex]::Escape($case.Stray)) -Because "$($case.Name): the run's own write is named: $($run.Report)"
            }
            Assert-True -Condition ($run.Report -notmatch "team/reports/d20261003" -and $run.Report -notmatch "split-task-one" -and $run.Report -notmatch "an-idea") -Because "$($case.Name): the cycle's files are not the run's: $($run.Report)"
            if ($case.Outside) {
                Assert-True -Condition (Test-Path -LiteralPath (Join-Path $box.Root "team\reports\d20261003\worker-task-one.md")) -Because "the cycle's files are left as they are"
            }
            Assert-LockFreeLeftNothing -Box $box -Because $case.Name
        }
    }

    Test-Case "lock-free 4: an id another writer created DURING the run is not written - its version stands, the report names it; a card that does not depend on it is written, one that does is not" {
        foreach ($variant in @(
                @{ Cards = $liveCards; Written = "card-b" },
                @{ Cards = @((New-Card -Id "card-a" -Area @("src/card-a")), (New-DependentCard -Id "card-b" -DependsOn "card-a")); Written = "" })) {
            $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
            $box = New-FeedSandbox
            $theirs = New-Task -Id "card-a" -Area @("src/theirs")
            $theirs.title = "the second writer's card-a"
            $plan = @{ feed_text = (Get-FeedText -Cards $variant.Cards); store_puts = @(@{ url = "$($api.Url)/v1/team/queue/tasks/card-a"; token = "test-token"; task = $theirs }) }
            $run = Invoke-LiveFeed -Box $box -Api $api -Plan $plan
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
            $state = Get-FakeApiState -Api $api
            Assert-Equal -Expected (Get-Json -Value $theirs) -Actual (Get-Json -Value (Get-StoreTask -State $state -Id "card-a")) -Because "the second writer's card-a stands: $($run.Report)"
            $mine = @($state.tasks | Where-Object { @("card-b") -contains [string]$_.id } | ForEach-Object { [string]$_.id }) -join ","
            Assert-Equal -Expected $variant.Written -Actual $mine -Because "what was written of the rest: $($run.Report)"
            Assert-True -Condition ($run.Report -match "card-a" -and $run.Report -match "yazılmadı") -Because "the report names the card that was not written: $($run.Report)"
            Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^PUT /v1/team/queue/tasks/card-a 200$' }).Count -Because "card-a was stored once - by its other writer"
            Assert-LockFreeLeftNothing -Box $box -Because "a card dropped"
        }
    }

    Test-Case "lock-free 5: a title another writer queued DURING the run refuses the feed whole - judged against the FRESH queue, not the one read at the start" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $theirs = New-Task -Id "other-one" -Area @("src/other")
        $theirs.title = "the card card-b"
        $plan = @{ feed_text = (Get-FeedText -Cards $liveCards); store_puts = @(@{ url = "$($api.Url)/v1/team/queue/tasks/other-one"; token = "test-token"; task = $theirs }) }
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan $plan
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "task-one,other-one" -Actual (@($state.tasks | ForEach-Object { [string]$_.id }) -join ",") -Because "nothing of the feed is written: $($run.Report)"
        Assert-True -Condition ($run.Report -match "is already the title of other-one") -Because "the fresh judge says why: $($run.Report)"
        Assert-LockFreeLeftNothing -Box $box -Because "a refused feed"
    }

    Test-Case "lock-free 6: two feeders at once - ONE lead run, the second exits 3 with no report; a dead feeder's lock is taken over and said; the lock is gone after every end" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $release = Join-Path $box.Work "release-the-first"
        $firstLog = Join-Path $box.Work "fake-first.log"
        $planPath = Join-Path $box.Work "plan-first.json"
        [System.IO.File]::WriteAllText($planPath, (ConvertTo-Json -InputObject ([pscustomobject]@{ feed_text = (Get-FeedText -Cards $liveCards); hold_until = $release }) -Depth 8), $utf8)
        $env:PAGENTOS_FAKE_FEED_LOG = $firstLog
        $env:PAGENTOS_FAKE_FEED_PLAN = $planPath
        try {
            $arguments = Get-FeedArguments -Sandbox $box -Machine "MAIL" -ExtraArguments "" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile -FeederLock (Get-FeederLockPath -Box $box)
            $first = Start-Process -FilePath $powershell -ArgumentList (ConvertTo-NativeArgumentLine -Arguments $arguments) -PassThru -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $box.Work "first.out") -RedirectStandardError (Join-Path $box.Work "first.err")
            [void]$asyncFeeds.Add($first)
            $handle = $first.Handle
        }
        finally {
            Remove-Item Env:\PAGENTOS_FAKE_FEED_LOG -ErrorAction SilentlyContinue
            Remove-Item Env:\PAGENTOS_FAKE_FEED_PLAN -ErrorAction SilentlyContinue
        }
        try {
            $deadline = [datetime]::UtcNow.AddSeconds(120)
            while (-not (Test-Path -LiteralPath $firstLog)) {
                if ([datetime]::UtcNow -gt $deadline -or $first.HasExited) { throw "the first feeder's lead run did not start: $([System.IO.File]::ReadAllText((Join-Path $box.Work 'first.out')))" }
                Start-Sleep -Milliseconds 200
            }
            $second = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-c" -Area @("src/card-c")))) }
        }
        finally {
            [System.IO.File]::WriteAllText($release, "go", $utf8)
            if (-not $first.WaitForExit(180000)) { Stop-TeamProcessTree -ProcessId $first.Id; throw "the first feeder did not end within its guard" }
        }
        Assert-Equal -Expected 3 -Actual $second.ExitCode -Because "the second feeder stops: $($second.Said)"
        Assert-True -Condition ($second.Said -match "feeder's own lock") -Because "and says why: $($second.Said)"
        Assert-Equal -Expected 0 -Actual @($second.Calls).Count -Because "the second started no lead run"
        Assert-Equal -Expected 1 -Actual @(Get-Content -LiteralPath $firstLog -Encoding UTF8 | Where-Object { $_.Trim() }).Count -Because "exactly one lead run in all"
        Assert-Equal -Expected 0 -Actual $first.ExitCode -Because "the first ended well: $([System.IO.File]::ReadAllText((Join-Path $box.Work 'first.out')))"
        $report = [System.IO.File]::ReadAllText((Join-Path $box.Root "team\reports\feed-$feedDate.md"), [System.Text.Encoding]::UTF8)
        Assert-Equal -Expected 1 -Actual @([regex]::Matches($report, '(?m)^## ')).Count -Because "one section - the first feeder's; the second wrote none: $report"
        Assert-True -Condition ($report -notmatch "card-c") -Because "the second feeder's card is nowhere"
        Assert-LockFreeLeftNothing -Box $box -Because "a normal end, the second feeder beside it"

        # A lock left by a feeder that died: taken over, and said.
        $dead = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\cmd.exe") -ArgumentList "/c exit 0" -PassThru -WindowStyle Hidden
        if (-not $dead.WaitForExit(30000)) { throw "cmd did not end" }
        $lockFile = Get-FeederLockPath -Box $box
        [System.IO.File]::WriteAllText($lockFile, (Get-Json -Value ([ordered]@{ pid = $dead.Id; machine = "MAIL"; acquired_at = (Get-TeamTimestamp) })), $utf8)
        $taken = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-d" -Area @("src/card-d")))) } -ExtraArguments "-MinRunnable 10"
        Assert-Equal -Expected 0 -Actual $taken.ExitCode -Because $taken.Said
        Assert-Equal -Expected 1 -Actual @($taken.Calls).Count -Because "the dead feeder's lock does not stop a run: $($taken.Said)"
        Assert-True -Condition ($taken.Report -match "besleyici kilidi devralındı" -and $taken.Report -match "pid $($dead.Id)") -Because "and the report says it was taken over: $($taken.Report)"
        Assert-LockFreeLeftNothing -Box $box -Because "after a takeover"

        # The two other ends: a refused feed, and a lead run that fails.
        $refused = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards); stray = "docs/notes.md" } -ExtraArguments "-MinRunnable 10"
        Assert-True -Condition ($refused.Report -match "reddedildi") -Because $refused.Report
        Assert-LockFreeLeftNothing -Box $box -Because "a refused feed"
        $failed = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards @((New-Card -Id "card-e" -Area @("src/card-e")))); fail_after = $true } -ExtraArguments "-MinRunnable 10"
        Assert-True -Condition ($failed.Report -match "lead koşusu: başarısız") -Because $failed.Report
        Assert-LockFreeLeftNothing -Box $box -Because "a lead run that failed"
    }

    Test-Case "lock-free 7: no idea row beside a running cycle - the roadmap is byte-equal, no commit, no Edit, and the report says why" {
        $api = Start-FakeApi -Tasks @((New-ApprovedIdea -Id "idea-one"), (New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $head = Invoke-SandboxGit -Root $box.Root -Arguments @("rev-parse", "HEAD")
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $box.Root "docs\ROADMAP.md"))
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards); roadmap_rows = @($ideaRow) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls[0].ideas).Count -Because "the run is not asked for the row"
        Assert-True -Condition (([string]$run.Calls[0].tools).Split(",") -notcontains "Edit") -Because "and cannot edit: $($run.Calls[0].tools)"
        Assert-Equal -Expected ([System.Convert]::ToBase64String($bytes)) -Actual ([System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes((Join-Path $box.Root "docs\ROADMAP.md")))) -Because "the roadmap is byte-equal"
        Assert-Equal -Expected $head -Actual $run.Head -Because "no commit in the checkout a running cycle works from"
        Assert-True -Condition ($run.Report -match "onaylanan fikir satırı bu koşuda yazılmadı" -and $run.Report -match "döngü çalışıyor") -Because "the report says why: $($run.Report)"
        Assert-Equal -Expected "approved" -Actual ([string](Get-StoreTask -State (Get-FakeApiState -Api $api) -Id "card-a").state) -Because "the cards are still cut"
    }

    Test-Case "lock-free 8: the report of a run beside the cycle is a file, and is NOT posted - the Onay Merkezi keeps the cycle's" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-True -Condition ($run.Report -match "card-a" -and $run.Report -match "kuyruğa eklendi") -Because "the report file names the cards: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^POST /v1/team/queue/reports ' }).Count -Because "no report posted: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected 0 -Actual @((Get-FakeApiState -Api $api).reports.PSObject.Properties).Count -Because "none in the store"
    }

    Test-Case "lock-free 9: what is unchanged - a live cycle's lock in FILE mode, and another machine's fresh lock in API mode, still stop it (exit 3)" {
        $box = New-FeedSandbox -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $run = Invoke-Feed -Sandbox $box -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) } -FeederLock (Get-FeederLockPath -Box $box)
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because "file mode has one writer, the lock's holder: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-True -Condition ($run.Said -match "the lock is held by MAIL") -Because $run.Said
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-FeederLockPath -Box $box))) -Because "the feeder's own lock is an API-mode thing"
        $other = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock $other
        $box = New-FeedSandbox
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because "two machines' feeders are not serialised by a local lock: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
    }

    Test-Case "lock-free 10: a store that stops answering at the re-read - nothing is written, the feed file is left, exit non-zero, the feeder's lock released" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock) -Extra @{ faults = [pscustomobject]@{ queue_get_after = 1 } }
        $box = New-FeedSandbox
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-True -Condition ($run.ExitCode -ne 0) -Because "a failed feed: $($run.Said)"
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "the run happened: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^PUT ' }).Count -Because "nothing was written: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $box.Root "team\plans\feed-$feedDate-1.json")) -Because "the feed file stays on disk"
        Assert-True -Condition ($run.Report -match "yeniden okunamadı") -Because "the report says so: $($run.Report)"
        Assert-LockFreeLeftNothing -Box $box -Because "a store that went away"
    }

    Test-Case "lock-free 11: the stop flag beside a live cycle - nothing starts, the flag is left, no lock file" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $flag = Join-Path $box.Root "team\stop.flag"
        [System.IO.File]::WriteAllText($flag, "stop`n", $utf8)
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-True -Condition (Test-Path -LiteralPath $flag) -Because "the flag is the cycle's"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-FeederLockPath -Box $box))) -Because "no lock was taken"
    }

    Test-Case "lock-free 12: -DryRun beside a live cycle prints the lock-free plan and writes nothing - no lock file, no worktree, only GETs" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock (New-LiveCycleLock)
        $box = New-FeedSandbox
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) } -ExtraArguments "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Said
        Assert-True -Condition ($run.StdOut -match "lock-free" -and $run.StdOut -match "would start") -Because "it says which path it would take: $($run.StdOut)"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-FeederLockPath -Box $box))) -Because "no lock file"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $box.Root ".claude\worktrees"))) -Because "no worktree"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -notmatch '^GET ' }).Count -Because "only GETs: $((Get-FakeApiRequests -Api $api) -join '; ')"
    }

    Test-Case "lock-free 13: a live FEEDER of ours holds the lock (cycle feed-<date>) - not a cycle, so exit 3, no lead run, no report, no lock file" {
        $feederHeld = New-LiveCycleLock
        $feederHeld.cycle_id = "feed-$feedDate"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock $feederHeld
        $box = New-FeedSandbox
        $lockBefore = Get-Json -Value (Get-FakeApiState -Api $api).lock
        $run = Invoke-LiveFeed -Box $box -Api $api -Plan @{ feed_text = (Get-FeedText -Cards $liveCards) }
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because "a second feeder beside a feeder would be a second lead run: $($run.Said)"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no lead run"
        Assert-True -Condition ($run.Said -match "the lock is held by MAIL") -Because $run.Said
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $box.Root "team\reports"))) -Because "no report on disk"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|POST|DELETE|PATCH) " }).Count -Because "no write at all: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected $lockBefore -Actual (Get-Json -Value (Get-FakeApiState -Api $api).lock) -Because "the feeder's lock is untouched"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-FeederLockPath -Box $box))) -Because "no feeder lock file"
    }

    Test-Case "lock-free writes: a create the store answers 409 drops that card and every card that depends on it; the others are written, in dependency order" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "card-a" -Area @("src/theirs")))
        $store = New-TeamApiStore -Url $api.Url -TokenFile $api.TokenFile
        $cards = ConvertTo-FeedObjects -Cards @(
            (New-DependentCard -Id "card-d" -DependsOn "card-c"),
            (New-Card -Id "card-a" -Area @("src/card-a")),
            (New-DependentCard -Id "card-b" -DependsOn "card-a"),
            (New-Card -Id "card-c" -Area @("src/card-c")))
        $made = @(ConvertTo-TeamFeedTasks -Feed $cards -RoadmapRows @(Get-TeamRoadmapRows -Text $roadmapFixture) -Date $feedDate)
        $before = Get-Json -Value (Get-StoreTask -State (Get-FakeApiState -Api $api) -Id "card-a")
        $saved = Save-TeamFeedCreates -Store $store -Tasks @($made | ForEach-Object { $_.Task })
        Assert-Equal -Expected "card-c,card-d" -Actual (@($saved.Written) -join ",") -Because "the cards nobody took, a dependency before its dependant"
        Assert-Equal -Expected "card-a,card-b" -Actual (@($saved.Dropped | ForEach-Object { $_.Id }) -join ",") -Because "the taken id and the card that waits for it"
        Assert-True -Condition ([string]$saved.Failed -eq "") -Because "no failure: $($saved.Failed)"
        Assert-Equal -Expected $before -Actual (Get-Json -Value (Get-StoreTask -State (Get-FakeApiState -Api $api) -Id "card-a")) -Because "the store's card-a is not overwritten"
        $puts = @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^PUT ' })
        Assert-Equal -Expected "PUT /v1/team/queue/tasks/card-a 409|PUT /v1/team/queue/tasks/card-c 200|PUT /v1/team/queue/tasks/card-d 200" -Actual (($puts | Sort-Object) -join "|") -Because"one create each, card-b never sent: $($puts -join '; ')"
        Assert-True -Condition ([array]::IndexOf($puts, "PUT /v1/team/queue/tasks/card-c 200") -lt [array]::IndexOf($puts, "PUT /v1/team/queue/tasks/card-d 200")) -Because "card-c before card-d: $($puts -join '; ')"
    }

    Test-Case "lock-free judge: the cards whose id another writer created since the first read are dropped with their dependants; the rest are kept in order" {
        $first = New-Queue -Tasks @((New-Task -Id "task-one"))
        $fresh = New-Queue -Tasks @((New-Task -Id "task-one"), (New-Task -Id "card-a" -Area @("src/theirs")))
        $cards = ConvertTo-FeedObjects -Cards @(
            (New-Card -Id "card-a"), (New-DependentCard -Id "card-b" -DependsOn "card-a"),
            (New-DependentCard -Id "card-c" -DependsOn "card-b"), (New-Card -Id "card-d"))
        $kept = Select-TeamFeedFresh -Feed $cards -Before $first -Fresh $fresh
        Assert-Equal -Expected "card-d" -Actual (@($kept.Feed | ForEach-Object { $_.id }) -join ",") -Because "only what nobody took and nothing taken waits for"
        Assert-Equal -Expected "card-a,card-b,card-c" -Actual (@($kept.Dropped | ForEach-Object { $_.Id }) -join ",") -Because "the taken id, then its dependants"
        $none = Select-TeamFeedFresh -Feed $cards -Before $first -Fresh $first
        Assert-Equal -Expected 4 -Actual @($none.Feed).Count -Because "nothing taken: nothing dropped"
    }
}
finally {
    foreach ($feed in $asyncFeeds) { try { if (-not $feed.HasExited) { Stop-TeamProcessTree -ProcessId $feed.Id } } catch { } }
    foreach ($api in $fakeApis) { try { if (-not $api.HasExited) { $api.Kill() } } catch { } }
    foreach ($work in $sandboxes) {
        if (-not (Test-Path -LiteralPath $work)) { continue }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-feed tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
