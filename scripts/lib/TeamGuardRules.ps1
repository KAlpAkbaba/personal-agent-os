<#
.SYNOPSIS
    What a guard run's RESULT means for the team: the card's `guards` field, the inspector's
    lines, the lead's wiring list, whether the gate may start, the Ofis sentence.

.DESCRIPTION
    The runner (scripts/lib/TeamGuards.ps1, scripts/team/guards.ps1) runs `team/guards.json`
    on one tree and returns a RESULT:

        { at, sha, seconds, status: 'green' | 'red',
          rows: [ { id, path, outcome: 'green' | 'red' | 'hung' | 'missing', seconds, label, detail } ] }

    This layer owns one field of a task card:

        task.guards = { at, sha, status, runs,
                        open:     [ { id, label, outcome, detail, sha } ],
                        resolved: [ { id, by: 'branch' | 'integration', sha, at } ] }

      * Set-TeamGuardResult         a run on the task's OWN branch;
      * Resolve-TeamGuardRows       a run on the INTEGRATION branch;
      * Get-TeamGuardInspectorNote  what the inspector reads before its run;
      * Get-TeamGuardWiringList     the lead's bağlama listesi;
      * Test-TeamGuardsBlockGate    no gate while a merged task has an open row;
      * Get-TeamGuardOfficeText     the Ofis sentence.

    A row leaves `open` only by a later GREEN run of the same guard - on the branch or on the
    integration branch - never by a hand edit. A red guard alone changes no state: a new
    suite is wired into the gate only at integration, so a task branch can be red by nature.

    Pure: no process, no file, no store. Every function takes its inputs and returns; the
    two that record a run change `task.guards` of the objects they are given and nothing else.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "TeamQueue.ps1")

$script:TeamGuardRulesNoteDetailMax = 400

function Get-TeamGuardField {
    <# The card's guards field, or $null for a task never guard-run (cards from before the step). #>
    param($Task)
    $guards = Get-TeamProperty -InputObject $Task -Name "guards" -Default $null
    if ($null -eq $guards -or $guards -is [string]) { return $null }
    return $guards
}

function Get-TeamGuardOpenRows {
    param($Guards)
    if ($null -eq $Guards) { return @() }
    return @(@(Get-TeamProperty -InputObject $Guards -Name "open" -Default @()) | Where-Object { $null -ne $_ })
}

function Get-TeamGuardResolvedRows {
    param($Guards)
    if ($null -eq $Guards) { return @() }
    return @(@(Get-TeamProperty -InputObject $Guards -Name "resolved" -Default @()) | Where-Object { $null -ne $_ })
}

function Get-TeamGuardRowText {
    <# A property of a row as text; never 'null'. #>
    param($Row, [Parameter(Mandatory = $true)][string]$Name)
    $value = Get-TeamProperty -InputObject $Row -Name $Name -Default ""
    if ($null -eq $value) { return "" }
    return [string]$value
}

function Get-TeamGuardRowLabel {
    <# The label the owner reads; a row with no label is named by its id. #>
    param($Row)
    $label = (Get-TeamGuardRowText -Row $Row -Name "label").Trim()
    if ($label) { return $label }
    $id = (Get-TeamGuardRowText -Row $Row -Name "id").Trim()
    if ($id) { return $id }
    return "adı olmayan koruyucu"
}

function Get-TeamGuardResultRows {
    param($Result)
    return @(@(Get-TeamProperty -InputObject $Result -Name "rows" -Default @()) | Where-Object { $null -ne $_ })
}

function Get-TeamGuardGreenIds {
    <# The ids whose row is green in a RESULT. #>
    param($Result)
    $green = @{}
    foreach ($row in @(Get-TeamGuardResultRows -Result $Result)) {
        if ((Get-TeamGuardRowText -Row $row -Name "outcome") -eq "green") { $green[(Get-TeamGuardRowText -Row $row -Name "id")] = $true }
    }
    return $green
}

function New-TeamGuardResolved {
    param([string]$Id, [string]$By, [string]$Sha, [string]$At)
    return [pscustomobject]@{ id = $Id; by = $By; sha = $Sha; at = $At }
}

function Set-TeamGuardResult {
    <#
    .SYNOPSIS
        Record a guard run on the task's OWN branch in `task.guards`; returns the task.

    .DESCRIPTION
        `open` becomes exactly the rows of this RESULT that are not green; a row that was open
        and is green now moves to `resolved` with by = 'branch'; `runs` grows by one. The same
        RESULT (same sha) given again changes nothing. Only `task.guards` is written: state,
        returns, reason, area and depends_on stay what they were - a red guard alone neither
        stops nor returns a task.
    #>
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Result,
        [datetime]$Now = [datetime]::UtcNow
    )
    $sha = Get-TeamGuardRowText -Row $Result -Name "sha"
    $previous = Get-TeamGuardField -Task $Task
    if ($null -ne $previous -and $sha -and (Get-TeamGuardRowText -Row $previous -Name "sha") -eq $sha) { return $Task }

    $at = Get-TeamTimestamp -Now $Now
    $green = Get-TeamGuardGreenIds -Result $Result
    $resolved = New-Object System.Collections.ArrayList
    foreach ($row in @(Get-TeamGuardResolvedRows -Guards $previous)) { [void]$resolved.Add($row) }
    foreach ($row in @(Get-TeamGuardOpenRows -Guards $previous)) {
        $id = Get-TeamGuardRowText -Row $row -Name "id"
        if ($green.ContainsKey($id)) { [void]$resolved.Add((New-TeamGuardResolved -Id $id -By "branch" -Sha $sha -At $at)) }
    }
    $open = New-Object System.Collections.ArrayList
    foreach ($row in @(Get-TeamGuardResultRows -Result $Result)) {
        if ((Get-TeamGuardRowText -Row $row -Name "outcome") -eq "green") { continue }
        [void]$open.Add([pscustomobject]@{
                id      = Get-TeamGuardRowText -Row $row -Name "id"
                label   = Get-TeamGuardRowText -Row $row -Name "label"
                outcome = Get-TeamGuardRowText -Row $row -Name "outcome"
                detail  = Get-TeamGuardRowText -Row $row -Name "detail"
                sha     = $sha
            })
    }
    $runs = 0
    if ($null -ne $previous) { $runs = [int](Get-TeamProperty -InputObject $previous -Name "runs" -Default 0) }
    $resultAt = Get-TeamGuardRowText -Row $Result -Name "at"
    if (-not $resultAt) { $resultAt = $at }
    $guards = [pscustomobject]@{
        at       = $resultAt
        sha      = $sha
        status   = $(if (@($open).Count -eq 0) { "green" } else { "red" })
        runs     = $runs + 1
        open     = @($open.ToArray())
        resolved = @($resolved.ToArray())
    }
    Set-TeamProperty -InputObject $Task -Name "guards" -Value $guards
    return $Task
}

function Resolve-TeamGuardRows {
    <#
    .SYNOPSIS
        Record a guard run on the INTEGRATION branch; returns the tasks.

    .DESCRIPTION
        An open row of a MERGED task whose guard is green in this RESULT moves to `resolved`
        with by = 'integration' and the RESULT's sha. A row whose guard is not green there, or
        that the RESULT does not carry, stays open. Tasks in any other state are not touched:
        their branch is not on the integration branch. Running it again changes nothing.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Tasks,
        [Parameter(Mandatory = $true)]$Result,
        [datetime]$Now = [datetime]::UtcNow
    )
    $sha = Get-TeamGuardRowText -Row $Result -Name "sha"
    $at = Get-TeamTimestamp -Now $Now
    $green = Get-TeamGuardGreenIds -Result $Result
    foreach ($task in @($Tasks)) {
        if ($null -eq $task -or (Get-TeamGuardRowText -Row $task -Name "state") -ne "merged") { continue }
        $guards = Get-TeamGuardField -Task $task
        if ($null -eq $guards) { continue }
        $open = @(Get-TeamGuardOpenRows -Guards $guards)
        if (@($open).Count -eq 0) { continue }
        $still = New-Object System.Collections.ArrayList
        $resolved = New-Object System.Collections.ArrayList
        foreach ($row in @(Get-TeamGuardResolvedRows -Guards $guards)) { [void]$resolved.Add($row) }
        foreach ($row in $open) {
            $id = Get-TeamGuardRowText -Row $row -Name "id"
            if ($green.ContainsKey($id)) { [void]$resolved.Add((New-TeamGuardResolved -Id $id -By "integration" -Sha $sha -At $at)) }
            else { [void]$still.Add($row) }
        }
        if (@($still).Count -eq @($open).Count) { continue }
        Set-TeamProperty -InputObject $guards -Name "open" -Value @($still.ToArray())
        Set-TeamProperty -InputObject $guards -Name "resolved" -Value @($resolved.ToArray())
        if (@($still).Count -eq 0) { Set-TeamProperty -InputObject $guards -Name "status" -Value "green" }
    }
    return @($Tasks)
}

function Get-TeamGuardShortSha {
    param($Guards)
    $sha = Get-TeamGuardRowText -Row $Guards -Name "sha"
    if ($sha.Length -gt 8) { return $sha.Substring(0, 8) }
    return $sha
}

function Get-TeamGuardInspectorNote {
    <#
    .SYNOPSIS
        The text the cycle puts in front of the inspector: '' for a task never guard-run,
        one green line, or one line per open row and the two-way rule.

    .DESCRIPTION
        No line of it is a verdict word, so Get-TeamVerdict over 'note + report' reads the
        report's verdict: the verdict stays the inspector's.
    #>
    param([Parameter(Mandatory = $true)]$Task)
    $guards = Get-TeamGuardField -Task $Task
    if ($null -eq $guards) { return "" }
    $short = Get-TeamGuardShortSha -Guards $guards
    $open = @(Get-TeamGuardOpenRows -Guards $guards)
    if (@($open).Count -eq 0) { return "koruyucular: yeşil ($short)" }
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("koruyucular: kırmızı, $(@($open).Count) satır açık ($short):")
    foreach ($row in $open) {
        # One line per row, with no backtick: a detail can never read as a verdict line.
        $detail = ((Get-TeamGuardRowText -Row $row -Name "detail") -replace "\s*\r?\n\s*", " | " -replace '`', "'").Trim()
        if ($detail.Length -gt $script:TeamGuardRulesNoteDetailMax) { $detail = $detail.Substring(0, $script:TeamGuardRulesNoteDetailMax) }
        $line = "- $(Get-TeamGuardRowText -Row $row -Name 'id'): $(Get-TeamGuardRowLabel -Row $row) [$(Get-TeamGuardRowText -Row $row -Name 'outcome')]"
        if ($detail) { $line = "$line - $detail" }
        [void]$lines.Add($line)
    }
    [void]$lines.Add("İki yol: düzeltme kartın alanının içindeyse geri verilecek işin bir maddesidir; alanının dışındaysa (ortak bir dosya, kapının listesi) raporda adıyla yazılır ve lead'in bağlama listesine satır olur - hüküm denetleyicinindir.")
    return (@($lines.ToArray()) -join "`n")
}

function Get-TeamGuardWiringList {
    <# The lead's bağlama listesi: { task, id, label, detail } for every open row of a MERGED task. #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Tasks)
    $records = New-Object System.Collections.ArrayList
    foreach ($task in @($Tasks)) {
        if ($null -eq $task -or (Get-TeamGuardRowText -Row $task -Name "state") -ne "merged") { continue }
        foreach ($row in @(Get-TeamGuardOpenRows -Guards (Get-TeamGuardField -Task $task))) {
            [void]$records.Add([pscustomobject]@{
                    task   = Get-TeamGuardRowText -Row $task -Name "id"
                    id     = Get-TeamGuardRowText -Row $row -Name "id"
                    label  = Get-TeamGuardRowLabel -Row $row
                    detail = Get-TeamGuardRowText -Row $row -Name "detail"
                })
        }
    }
    return @($records.ToArray())
}

function Test-TeamGuardsBlockGate {
    <#
    .SYNOPSIS
        Blocked (bool) and Why (Turkish): no full gate while a merged task has an open row.
        A task with no guards field does not block (cards from before the step).
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Tasks)
    $parts = New-Object System.Collections.ArrayList
    foreach ($task in @($Tasks)) {
        if ($null -eq $task -or (Get-TeamGuardRowText -Row $task -Name "state") -ne "merged") { continue }
        $open = @(Get-TeamGuardOpenRows -Guards (Get-TeamGuardField -Task $task))
        if (@($open).Count -eq 0) { continue }
        $labels = @($open | ForEach-Object { Get-TeamGuardRowLabel -Row $_ }) -join ", "
        [void]$parts.Add("$(Get-TeamGuardRowText -Row $task -Name 'id') ($labels)")
    }
    if (@($parts).Count -eq 0) { return [pscustomobject]@{ Blocked = $false; Why = "" } }
    $why = "Kapı başlamaz: birleştirilmiş işlerde çözülmemiş koruyucu satırı var - " + (@($parts.ToArray()) -join "; ") + "."
    return [pscustomobject]@{ Blocked = $true; Why = $why }
}

function Get-TeamGuardOfficeLine {
    param($Row)
    $label = Get-TeamGuardRowLabel -Row $Row
    switch (Get-TeamGuardRowText -Row $Row -Name "outcome") {
        "red" { return "koruyucu kırmızı: $label" }
        "hung" { return "koruyucu asılı kaldı, durduruldu: $label" }
        "missing" { return "koruyucu dosyası bu dalda yok: $label" }
    }
    return "koruyucu sonucu bilinmiyor: $label"
}

function Get-TeamGuardOfficeText {
    <# The Ofis sentence: '' for never run, 'koruyucular: yeşil', or the open rows joined with '; '. #>
    param([Parameter(Mandatory = $true)]$Task)
    $guards = Get-TeamGuardField -Task $Task
    if ($null -eq $guards) { return "" }
    $open = @(Get-TeamGuardOpenRows -Guards $guards)
    if (@($open).Count -eq 0) { return "koruyucular: yeşil" }
    return (@($open | ForEach-Object { Get-TeamGuardOfficeLine -Row $_ }) -join "; ")
}
