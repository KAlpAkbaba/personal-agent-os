<#
.SYNOPSIS
    The rules of "alan dışı geri verme" (scripts/lib/TeamArea.ps1): the one line of a report
    that asks for files outside a card's area, and what the cycle may do about it.

.DESCRIPTION
    Every rule is a function that takes its inputs and returns its answer, so this file
    starts no model, no git and no clock, and writes nothing: tasks and queues are objects
    made here, as `team/queue.json` would give them.

    The groups, by the prefix of a case's name:

      parse       the request line of an inspector's and of a worker's report;
      widen       nobody holds the files: the area grows, and the return is not a right;
      wait        a task in work holds one: the card waits behind it, the area unchanged;
      protected   a path nobody widens into: every entry of the constant, one case each,
                  and TeamQueue.ps1's shared files read from ITS text (two lists, one rule);
      cap         two widenings per task, twenty-five entries per area;
      idempotent  the same resolution applied twice changes nothing the second time;
      d20261001   the two stopped tasks of that cycle, as fixtures.

    Run: powershell -NoProfile -File scripts\tests\team-area.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamArea.ps1")

$script:Failures = 0
$script:Passes = 0
$now = [datetime]::new(2026, 10, 2, 9, 30, 0, [System.DateTimeKind]::Utc)

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

function Assert-List {
    <# Two lists of strings, same entries in the same order (case matters: these are paths). #>
    param([string[]]$Expected = @(), $Actual, [string]$Because)
    $want = @($Expected) -join " | "
    $got = @($Actual | Where-Object { $null -ne $_ }) -join " | "
    if ($want -cne $got) { throw "$Because`n          expected: <$want>`n          actual  : <$got>" }
}

function New-Task {
    param([string]$Id, [string]$State = "returned", [string[]]$Area = @("src/area"), [string[]]$DependsOn = @())
    $task = [pscustomobject]@{
        id = $Id; title = "the task $Id"; roadmap_row = "Secretary"; state = $State; area = @($Area)
        branch = ""; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-01T00:00:00Z"; updated_at = "2026-10-01T00:00:00Z"
    }
    if (@($DependsOn).Count -gt 0) { $task | Add-Member -NotePropertyName "depends_on" -NotePropertyValue @($DependsOn) }
    return $task
}

function New-Queue {
    param([object[]]$Tasks = @())
    return [pscustomobject]@{ version = 1; tasks = @($Tasks) }
}

function Get-TaskShape {
    <# What a widening may change, as one string: two shapes equal = nothing changed. #>
    param($Task)
    return (ConvertTo-Json -Depth 6 -Compress -InputObject ([ordered]@{
                area           = @(Get-TeamProperty -InputObject $Task -Name "area" -Default @())
                depends_on     = @(Get-TeamProperty -InputObject $Task -Name "depends_on" -Default @())
                area_widenings = [int](Get-TeamProperty -InputObject $Task -Name "area_widenings" -Default 0)
                area_history   = @(Get-TeamProperty -InputObject $Task -Name "area_history" -Default @())
            }))
}

$intents = "services/api/app/voice/intents.py"
$gateway = "services/api/app/gateway/x.py"

# An inspector's report as one is written: findings, the request line, the verdict last.
function New-InspectorReport {
    param([string[]]$Lines)
    return (@("# Denetim: narrative-failures-only-model", "", "1. 'ne başarısız oldu' gerçek yönlendiriciden anlatıya ulaşmıyor.", "") +
        @($Lines) + @("RETURN (1: düzeltme kartın alanı dışında)")) -join "`n"
}

# ------------------------------------------------------------------------------- parse

Test-Case "parse: the inspector's alan_disi line above RETURN gives both paths" {
    $report = New-InspectorReport -Lines @("alan_disi: [$intents, $gateway]")
    $asked = Get-TeamAreaRequest -Report $report -Role "inspector"
    Assert-True -Condition $asked.Asked -Because "a request was made"
    Assert-List -Expected @($intents, $gateway) -Actual $asked.Files -Because "both paths, in the order written"
    Assert-Equal -Expected 0 -Actual @($asked.Bad).Count -Because "nothing dropped"
    Assert-Equal -Expected "RETURN" -Actual (Get-TeamVerdict -Report $report).Verdict -Because "and the verdict is still read"
}

Test-Case "parse: the same line wrapped in backticks" {
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("``alan_disi: [$intents, $gateway]``")) -Role "inspector"
    Assert-List -Expected @($intents, $gateway) -Actual $asked.Files -Because "backticks around the line are ignored"
}

Test-Case "parse: the same line in bold, and with each path in backticks" {
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("  **alan_disi: [``$intents``, ``$gateway``]**  ")) -Role "inspector"
    Assert-List -Expected @($intents, $gateway) -Actual $asked.Files -Because "asterisks, backticks and spaces are ignored"
    $label = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("**alan_disi:** [$intents]")) -Role "inspector"
    Assert-List -Expected @($intents) -Actual $label.Files -Because "a bold key is the key"
}

Test-Case "parse: back-slashed paths, a leading ./ and a duplicate are normalised" {
    $line = "alan_disi: [services\api\app\voice\intents.py, ./$gateway, $intents, .\services\api\app\gateway\x.py]"
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @($line)) -Role "inspector"
    Assert-List -Expected @($intents, $gateway) -Actual $asked.Files -Because "forward slashes, no ./, each path once, order kept"
}

Test-Case "parse: the last request line of the report wins" {
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [docs/old.md]", "text", "alan_disi: [$gateway]")) -Role "inspector"
    Assert-List -Expected @($gateway) -Actual $asked.Files -Because "the last line, not the first and not both"
    $withdrawn = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [$gateway]", "alan_disi: []")) -Role "inspector"
    Assert-True -Condition (-not $withdrawn.Asked) -Because "a later empty list withdraws the earlier one"
}

Test-Case "parse: an empty list is no request" {
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: []")) -Role "inspector"
    Assert-True -Condition (-not $asked.Asked) -Because "nothing asked"
    Assert-Equal -Expected 0 -Actual @($asked.Files).Count -Because "no file"
    $blank = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [ , ]")) -Role "inspector"
    Assert-True -Condition (-not $blank.Asked) -Because "commas and spaces are an empty list"
}

Test-Case "parse: a line without brackets is no request" {
    foreach ($line in @("alan_disi: $intents", "alan_disi: [$intents", "alan_disi: $intents]", "alan_disi:")) {
        $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @($line)) -Role "inspector"
        Assert-True -Condition (-not $asked.Asked) -Because "'$line' is not a list"
        Assert-Equal -Expected 0 -Actual @($asked.Files).Count -Because "'$line' gives no file"
    }
}

Test-Case "parse: the key in prose, in another case or inside a line is no request" {
    foreach ($line in @("alan_disi listesi yok", "Alan_Disi: [$intents]", "ALAN_DISI: [$intents]", "bkz. alan_disi: [$intents]", "alan_disi_eski: [$intents]", "alan_disi: [$intents] ve fazlası")) {
        $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @($line)) -Role "inspector"
        Assert-True -Condition (-not $asked.Asked) -Because "'$line' is not the request line"
    }
    Assert-True -Condition (-not (Get-TeamAreaRequest -Report "" -Role "inspector").Asked) -Because "an empty report asks for nothing"
}

Test-Case "parse: '../x', '/x' and 'C:/x' land in Bad and never in Files" {
    $line = "alan_disi: [../x, /x, C:/x, $intents, a/../b.py, C:\Users\x, \\host\share\x, ., src/*.py]"
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @($line)) -Role "inspector"
    Assert-List -Expected @($intents) -Actual $asked.Files -Because "only the path inside the repository"
    foreach ($bad in @("../x", "/x", "C:/x", "a/../b.py", "C:/Users/x", "//host/share/x", ".", "src/*.py")) {
        Assert-True -Condition (@($asked.Bad) -ccontains $bad) -Because "'$bad' is in Bad: $(@($asked.Bad) -join ' | ')"
        Assert-True -Condition (@($asked.Files) -cnotcontains $bad) -Because "'$bad' is not in Files"
    }
    $only = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [../x]")) -Role "inspector"
    Assert-True -Condition $only.Asked -Because "a request of bad paths only is still a request (it is refused, not ignored)"
    Assert-Equal -Expected 0 -Actual @($only.Files).Count -Because "with no file"
}

Test-Case "parse: a worker's ALAN_ISTEGI is read for the worker and not for the inspector" {
    $report = @("## Şu an üzerinde çalışılan", "kırmızı test yazıldı; yeşil için alan dışı dosya gerek.", "ALAN_ISTEGI: [$intents]") -join "`r`n"
    $worker = Get-TeamAreaRequest -Report $report -Role "worker"
    Assert-True -Condition $worker.Asked -Because "the worker asked"
    Assert-List -Expected @($intents) -Actual $worker.Files -Because "the worker's file (CRLF report)"
    Assert-True -Condition (-not (Get-TeamAreaRequest -Report $report -Role "inspector").Asked) -Because "ALAN_ISTEGI is not the inspector's key"
}

Test-Case "parse: an inspector's alan_disi is not read for the worker, and a wrong role is refused" {
    $report = New-InspectorReport -Lines @("alan_disi: [$intents]")
    Assert-True -Condition (-not (Get-TeamAreaRequest -Report $report -Role "worker").Asked) -Because "alan_disi is not the worker's key"
    $both = @("ALAN_ISTEGI: [$gateway]", "alan_disi: [$intents]") -join "`n"
    Assert-List -Expected @($gateway) -Actual (Get-TeamAreaRequest -Report $both -Role "worker").Files -Because "each role reads its own key"
    Assert-List -Expected @($intents) -Actual (Get-TeamAreaRequest -Report $both -Role "inspector").Files -Because "each role reads its own key"
    $threw = $false
    try { [void](Get-TeamAreaRequest -Report $both -Role "lead") } catch { $threw = $true }
    Assert-True -Condition $threw -Because "only an inspector or a worker asks"
}

# ------------------------------------------------------------------------------- widen

Test-Case "widen: no conflict - the area gains exactly the missing files" {
    $task = New-Task -Id "card-one" -Area @("services/api/app/narrative", "services/api/tests/unit/test_narrative.py")
    $other = New-Task -Id "card-two" -State "in_progress" -Area @("apps/web/src")
    $queue = New-Queue -Tasks @($task, $other)
    $files = @($intents, "services/api/app/narrative/collector.py", $gateway)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files $files
    Assert-Equal -Expected "widen" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected @($intents, $gateway) -Actual $resolution.Add -Because "a file already inside a directory of the area is not added again"
    Assert-Equal -Expected 0 -Actual @($resolution.DependsOn).Count -Because "nobody to wait for"
    Assert-True -Condition ($resolution.Why.Length -gt 0) -Because "a sentence for the report"
    Assert-List -Expected @("services/api/app/narrative", "services/api/tests/unit/test_narrative.py") -Actual $task.area -Because "resolving changes nothing"

    $after = Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "düzeltme app/voice/intents içinde" -Now $now
    Assert-True -Condition ([object]::ReferenceEquals($after, $task)) -Because "the task itself is changed and returned"
    Assert-List -Expected @("services/api/app/narrative", "services/api/tests/unit/test_narrative.py", $intents, $gateway) -Actual $task.area -Because "the old area, then the new files"
    Assert-Equal -Expected 1 -Actual ([int]$task.area_widenings) -Because "one widening"
    Assert-Equal -Expected 1 -Actual @($task.area_history).Count -Because "one record"
    $record = @($task.area_history)[0]
    Assert-Equal -Expected "2026-10-02T09:30:00Z" -Actual $record.at -Because "when"
    Assert-Equal -Expected "inspector" -Actual $record.by -Because "who"
    Assert-Equal -Expected "düzeltme app/voice/intents içinde" -Actual $record.why -Because "why"
    Assert-List -Expected @($intents, $gateway) -Actual $record.files -Because "which files"
    Assert-True -Condition ($null -eq $record.PSObject.Properties["waits_for"]) -Because "a widening waits for nobody"
    Assert-True -Condition ($null -eq $task.PSObject.Properties["depends_on"]) -Because "no dependency was added"
    Assert-Equal -Expected $false -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "the return is the card's fault, not the worker's"
}

Test-Case "widen: everything asked is already inside the area - refuse, nothing to widen" {
    $task = New-Task -Id "card-one" -Area @("services/api/app/narrative", "docs/notes.md")
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @("services/api/app/narrative/collector.py", "docs/notes.md")
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because $resolution.Why
    Assert-Equal -Expected 0 -Actual @($resolution.Add).Count -Because "nothing to add"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "an ordinary return"
    $none = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @()
    Assert-Equal -Expected "refuse" -Actual $none.Decision -Because "no file asked"
}

Test-Case "widen: a task's own area and a second widening with other files" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $queue = New-Queue -Tasks @($task)
    $first = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @("src/b.py")
    [void](Add-TeamAreaWidening -Task $task -Resolution $first -By "inspector" -Why "bir" -Now $now)
    $second = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @("src/b.py", "src/c.py")
    Assert-Equal -Expected "widen" -Actual $second.Decision -Because "the task's own area is no conflict: $($second.Why)"
    Assert-List -Expected @("src/c.py") -Actual $second.Add -Because "b.py is inside the area by now"
    [void](Add-TeamAreaWidening -Task $task -Resolution $second -By "worker" -Why "iki" -Now $now.AddMinutes(5))
    Assert-List -Expected @("src/a.py", "src/b.py", "src/c.py") -Actual $task.area -Because "both widenings"
    Assert-Equal -Expected 2 -Actual ([int]$task.area_widenings) -Because "two widenings"
    Assert-List -Expected @("inspector", "worker") -Actual @($task.area_history | ForEach-Object { $_.by }) -Because "two records, in order"
}

Test-Case "widen: a path that is not inside the repository is refused whole" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    foreach ($bad in @("../x", "/etc/passwd", "C:/x", "C:\x", ".", "")) {
        $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @("src/b.py", $bad)
        Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because "'$bad' beside a free path: $($resolution.Why)"
        Assert-Equal -Expected 0 -Actual @($resolution.Add).Count -Because "nothing partial for '$bad'"
    }
}

Test-Case "widen: refuse and an unknown resolution change nothing and count" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $before = Get-TaskShape -Task $task
    $refuse = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @("docs/ROADMAP.md")
    [void](Add-TeamAreaWidening -Task $task -Resolution $refuse -By "inspector" -Why "x" -Now $now)
    Assert-Equal -Expected $before -Actual (Get-TaskShape -Task $task) -Because "a refusal changes nothing"
    Assert-True -Condition ($null -eq $task.PSObject.Properties["area_history"]) -Because "and leaves no record"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution $null) -Because "no resolution: the return counts"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution ([pscustomobject]@{ Decision = "bilinmeyen" })) -Because "an unknown decision: the return counts"
}

# -------------------------------------------------------------------------------- wait

foreach ($state in @("approved", "assigned", "in_progress", "inspecting", "returned")) {
    Test-Case "wait: a file inside the directory area of a task that is $state" {
        $task = New-Task -Id "card-one" -Area @("services/api/app/narrative")
        $holder = New-Task -Id "voice-card" -State $state -Area @("services/api/app/voice")
        $queue = New-Queue -Tasks @($task, $holder)
        $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @($intents, $gateway)
        Assert-Equal -Expected "wait" -Actual $resolution.Decision -Because $resolution.Why
        Assert-List -Expected @("voice-card") -Actual $resolution.DependsOn -Because "the holder"
        Assert-True -Condition ($resolution.Why -match "voice-card") -Because "the sentence names it: $($resolution.Why)"
        Assert-Equal -Expected $false -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "waiting is not one of the two rights"

        [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "voice-card tutuyor" -Now $now)
        Assert-List -Expected @("services/api/app/narrative") -Actual $task.area -Because "the area is unchanged: nothing partial, not even the free file"
        Assert-List -Expected @("voice-card") -Actual $task.depends_on -Because "the card is put behind the holder"
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $task -Name "area_widenings" -Default 0)) -Because "waiting is not a widening"
        $record = @($task.area_history)[0]
        Assert-List -Expected @("voice-card") -Actual $record.waits_for -Because "the record says for whom"
        Assert-List -Expected @($intents, $gateway) -Actual $record.files -Because "and for which files"
        Assert-Equal -Expected "inspector" -Actual $record.by -Because "who"
        Assert-List -Expected @("voice-card") -Actual (Get-TeamUnmetDependencies -Task $task -Queue $queue) -Because "the queue's own rule now holds the card back"
    }
}

foreach ($state in @("merged", "awaiting_release", "released", "done", "stopped", "proposed", "awaiting_owner", "awaiting_real_evidence")) {
    Test-Case "wait: the same file held only by a task that is $state does not conflict" {
        $task = New-Task -Id "card-one" -Area @("services/api/app/narrative")
        $holder = New-Task -Id "voice-card" -State $state -Area @("services/api/app/voice")
        $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @($intents)
        Assert-Equal -Expected "widen" -Actual $resolution.Decision -Because $resolution.Why
        Assert-List -Expected @($intents) -Actual $resolution.Add -Because "the file is free"
    }
}

Test-Case "wait: a directory request holding another task's file conflicts too" {
    $task = New-Task -Id "card-one" -Area @("services/api/app/narrative")
    $holder = New-Task -Id "voice-card" -State "approved" -Area @($intents)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @("services/api/app/voice")
    Assert-Equal -Expected "wait" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected @("voice-card") -Actual $resolution.DependsOn -Because "the directory holds its file"
    $same = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @("Services\API\app\voice\intents.py")
    Assert-Equal -Expected "wait" -Actual $same.Decision -Because "the same file in another spelling is the same file"
    $near = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @("services/api/app/voice/intents.pyi", "services/api/app/voice2")
    Assert-Equal -Expected "widen" -Actual $near.Decision -Because "a name that only starts the same is another file: $($near.Why)"
}

Test-Case "wait: two holders give both ids once, in the queue's order, and never the task's own" {
    $task = New-Task -Id "card-one" -Area @("src/a.py") -DependsOn @("earlier")
    $first = New-Task -Id "voice-card" -State "approved" -Area @("services/api/app/voice", "services/api/app/gateway/x.py")
    $second = New-Task -Id "web-card" -State "inspecting" -Area @("apps/web/src")
    $queue = New-Queue -Tasks @($second, $task, $first)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @($intents, $gateway, "apps/web/src/page.tsx")
    Assert-Equal -Expected "wait" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected @("web-card", "voice-card") -Actual $resolution.DependsOn -Because "each holder once"
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "worker" -Why "x" -Now $now)
    Assert-List -Expected @("earlier", "web-card", "voice-card") -Actual $task.depends_on -Because "added after what was there"
    $self = [pscustomobject]@{ Decision = "wait"; Add = @("src/z.py"); DependsOn = @("card-one", "voice-card", "third"); Why = "x" }
    [void](Add-TeamAreaWidening -Task $task -Resolution $self -By "worker" -Why "x" -Now $now)
    Assert-List -Expected @("earlier", "web-card", "voice-card", "third") -Actual $task.depends_on -Because "no duplicate, never the task's own id"
}

Test-Case "wait: a holder that itself waits for this task is refused to the lead, not a deadlock" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $middle = New-Task -Id "middle" -State "approved" -Area @("src/m.py") -DependsOn @("card-one")
    $holder = New-Task -Id "voice-card" -State "approved" -Area @($intents) -DependsOn @("middle")
    $queue = New-Queue -Tasks @($task, $middle, $holder)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @($intents)
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because "each would wait for the other: $($resolution.Why)"
    Assert-Equal -Expected 0 -Actual @($resolution.DependsOn).Count -Because "no dependency is offered"
    Assert-True -Condition ($resolution.Why -match "voice-card") -Because "the sentence names the holder: $($resolution.Why)"
}

# --------------------------------------------------------------------------- protected

# One request per entry of the constant: a path the entry must refuse. An entry with no
# request here fails the "every entry" case below, so the list cannot grow untested.
$protectedSamples = @{
    "docs/HANDOFF.md"                              = "docs/HANDOFF.md"
    "docs/DECISIONS.md"                            = "docs/DECISIONS.md"
    "state/BUILD_STATE.json"                       = "state/BUILD_STATE.json"
    "docs/THIRD_PARTY_COMPONENTS.md"               = "docs/THIRD_PARTY_COMPONENTS.md"
    "team/queue.json"                              = "team/queue.json"
    "team/lock.json"                               = "team/lock.json"
    "docs/ROADMAP.md"                              = "docs/ROADMAP.md"
    "docs/TEAM_PROTOCOL.md"                        = "docs/TEAM_PROTOCOL.md"
    ".claude/agents"                               = ".claude/agents/worker.md"
    "hand-gestures"                                = "devices/windows-agent/src/hand-gestures/Stage1.cs"
    "env-file"                                     = "services/api/.env.production"
    "key-material"                                 = "infra/opentofu/deploy.pem"
    "tofu-state"                                   = "infra/opentofu/terraform.tfstate"
    "secrets"                                      = "secrets/local/openai.txt"
    "services/api/var"                             = "services/api/var/identity/root.json"
    "scripts/lib/SecretStore.ps1"                  = "scripts/lib/SecretStore.ps1"
    "scripts/secret-store.ps1"                     = "scripts/secret-store.ps1"
    "scripts/cloud/install-env-secret.sh"          = "scripts/cloud/install-env-secret.sh"
    "scripts/cloud/set-cloud-secret.ps1"           = "scripts/cloud/set-cloud-secret.ps1"
    "last-known-good"                              = "services/api/app/release/LAST_KNOWN_GOOD"
    "scripts/cloud/release-cloud-core-bluegreen.sh" = "scripts/cloud/release-cloud-core-bluegreen.sh"
    "services/recovery-supervisor"                 = "services/recovery-supervisor/recovery_supervisor/workspace.py"
    "services/api/app/identity/root.py"            = "services/api/app/identity/root.py"
    "infra/docker/docker-compose.prod.yml"         = "infra/docker/docker-compose.prod.yml"
    "infra/docker/edge"                            = "infra/docker/edge/nginx.conf"
    "infra/systemd"                                = "infra/systemd/pagentos-bluegreen-reconcile.service"
    "scripts/cloud/install-recovery-supervisor.sh" = "scripts/cloud/install-recovery-supervisor.sh"
    "scripts/cloud/uninstall-recovery-supervisor.sh" = "scripts/cloud/uninstall-recovery-supervisor.sh"
    "scripts/cloud/backup-cloud-core.sh"           = "scripts/cloud/backup-cloud-core.sh"
    "scripts/cloud/restore-cloud-core.sh"          = "scripts/cloud/restore-cloud-core.sh"
}

function Assert-Refused {
    <# The path, alone and beside a free one, on a task nobody is in the way of. #>
    param([string]$Path, [string]$Because)
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $queue = New-Queue -Tasks @($task)
    $before = Get-TaskShape -Task $task
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @($Path)
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because "$Because ('$Path'): $($resolution.Why)"
    Assert-Equal -Expected 0 -Actual @($resolution.Add).Count -Because "$Because ('$Path'): nothing to add"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "$Because ('$Path'): the return counts"
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "x" -Now $now)
    Assert-Equal -Expected $before -Actual (Get-TaskShape -Task $task) -Because "$Because ('$Path'): the task is unchanged"
}

$protectedEntries = @(Get-TeamAreaProtected)
Test-Case "protected: the constant is one list, each entry with a name, a kind and a source" {
    Assert-True -Condition (@($protectedEntries).Count -ge 20) -Because "the list is there: $(@($protectedEntries).Count) entries"
    foreach ($entry in $protectedEntries) {
        Assert-True -Condition (@("path", "pattern") -ccontains [string]$entry.Kind) -Because "'$($entry.Name)' is a path or a pattern"
        Assert-True -Condition (([string]$entry.Value).Length -gt 0 -and ([string]$entry.Source).Length -gt 0) -Because "'$($entry.Name)' has a value and names its source"
        Assert-True -Condition $protectedSamples.ContainsKey([string]$entry.Name) -Because "the entry '$($entry.Name)' has no case in this suite"
    }
    Assert-Equal -Expected $protectedSamples.Count -Actual @($protectedEntries | ForEach-Object { $_.Name } | Sort-Object -Unique).Count -Because "one case per entry, one entry per case"
}

# Test-Case runs its body at once, so the loop's variable is the one the body reads (and
# `$name` would be Test-Case's own parameter).
foreach ($entry in $protectedEntries) {
    $entryName = [string]$entry.Name
    Test-Case "protected: $entryName is refused" {
        Assert-True -Condition $protectedSamples.ContainsKey($entryName) -Because "the entry '$entryName' has no case in this suite"
        Assert-Refused -Path $protectedSamples[$entryName] -Because "protected ($($entry.Source))"
    }
}

foreach ($directory in @("docs", "state", "team", "docs/", "scripts", "services/api", ".claude", "infra")) {
    Test-Case "protected: the directory '$directory', which holds a protected path, is refused" {
        Assert-Refused -Path $directory -Because "a directory holding a protected path"
    }
}

Test-Case "protected: other spellings of a protected path are refused" {
    foreach ($path in @("DOCS\handoff.md", "./docs/ROADMAP.md", ".claude/agents", ".claude/agents/", "feat/Hand-Gestures-stage1/x.cs", ".env", "apps/web/.env.local",
            "certs/owner.PFX", "x/id.key", "infra/opentofu/prod.tfvars", "infra/opentofu/tfplan.binary", "infra/opentofu/terraform.tfstate.backup",
            "var/last_known_good.txt", "services/api/app/release/last-known-good.json")) {
        Assert-Refused -Path $path -Because "another spelling"
    }
}

Test-Case "protected: near misses are free" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $free = @("docs/HANDOFF.md.bak", "docs/voice/ROADMAP.md", "docs/notes.md", "services/api/app/identity/routes.py", "scripts/lib/TeamRun.ps1",
        "services/recovery-supervisor-notes.md", "infra/docker/docker-compose.dev.yml", "apps/web/src/environment.ts", "apps/web/src/keyboard.ts", "team/plans/x-adr.md")
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files $free
    Assert-Equal -Expected "widen" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected $free -Actual $resolution.Add -Because "all of them"
}

Test-Case "protected: one protected path beside one free path - nothing is widened" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $before = Get-TaskShape -Task $task
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @($intents, "docs/ROADMAP.md")
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because $resolution.Why
    Assert-Equal -Expected 0 -Actual @($resolution.Add).Count -Because "not even the free file"
    Assert-True -Condition ($resolution.Why -match [regex]::Escape("docs/ROADMAP.md")) -Because "the sentence names the path: $($resolution.Why)"
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "x" -Now $now)
    Assert-Equal -Expected $before -Actual (Get-TaskShape -Task $task) -Because "nothing partial"
}

Test-Case "protected: a protected path wins over a conflict (refuse, not wait)" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $holder = New-Task -Id "voice-card" -State "approved" -Area @($intents)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @($intents, "team/queue.json")
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because $resolution.Why
    Assert-Equal -Expected 0 -Actual @($resolution.DependsOn).Count -Because "no waiting for a request that is refused"
}

Test-Case "protected: every entry of TeamQueue.ps1's shared files, read from its text, is refused" {
    $text = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\lib\TeamQueue.ps1"), [System.Text.Encoding]::UTF8)
    $block = [regex]::Match($text, '(?s)\$script:TeamSharedFiles\s*=\s*@\((.*?)\)')
    Assert-True -Condition $block.Success -Because "TeamQueue.ps1 still declares `$script:TeamSharedFiles"
    $shared = @([regex]::Matches($block.Groups[1].Value, '"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    Assert-True -Condition (@($shared).Count -ge 6) -Because "the list was read: $(@($shared) -join ', ')"
    foreach ($known in @("docs/HANDOFF.md", "docs/DECISIONS.md", "state/BUILD_STATE.json", "docs/THIRD_PARTY_COMPONENTS.md", "team/queue.json", "team/lock.json")) {
        Assert-True -Condition (@($shared) -ccontains $known) -Because "the text names $known"
    }
    foreach ($file in $shared) {
        Assert-Refused -Path $file -Because "a shared file of TeamQueue.ps1"
        Assert-Refused -Path (Split-Path -Parent $file) -Because "the directory of a shared file of TeamQueue.ps1"
    }
}

# --------------------------------------------------------------------------------- cap

Test-Case "cap: the third widening of one task is refused, to the lead" {
    Assert-Equal -Expected 2 -Actual (Get-TeamAreaMaxWidenings) -Because "the cap is a named constant"
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $queue = New-Queue -Tasks @($task)
    foreach ($file in @("src/b.py", "src/c.py")) {
        $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @($file)
        Assert-Equal -Expected "widen" -Actual $resolution.Decision -Because "'$file': $($resolution.Why)"
        [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why $file -Now $now)
    }
    Assert-Equal -Expected 2 -Actual ([int]$task.area_widenings) -Because "widened twice"
    $before = Get-TaskShape -Task $task
    $third = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files @("src/d.py")
    Assert-Equal -Expected "refuse" -Actual $third.Decision -Because $third.Why
    Assert-True -Condition ($third.Why -match "lead") -Because "the sentence sends it to the lead: $($third.Why)"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution $third) -Because "the return counts"
    [void](Add-TeamAreaWidening -Task $task -Resolution $third -By "inspector" -Why "x" -Now $now)
    Assert-Equal -Expected $before -Actual (Get-TaskShape -Task $task) -Because "the task is unchanged"
}

Test-Case "cap: a third request that would only wait is refused too" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $task | Add-Member -NotePropertyName "area_widenings" -NotePropertyValue 2
    $holder = New-Task -Id "voice-card" -State "approved" -Area @($intents)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @($intents)
    Assert-Equal -Expected "refuse" -Actual $resolution.Decision -Because "the cap is judged before the conflict: $($resolution.Why)"
}

Test-Case "cap: a 26-entry result is refused, a 25-entry result is not" {
    $area = @(1..20 | ForEach-Object { "src/file$_.py" })
    $task = New-Task -Id "card-one" -Area $area
    $queue = New-Queue -Tasks @($task)
    $five = @(1..5 | ForEach-Object { "lib/new$_.py" })
    $fits = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files $five
    Assert-Equal -Expected "widen" -Actual $fits.Decision -Because "20 + 5 = 25: $($fits.Why)"
    $six = @(1..6 | ForEach-Object { "lib/new$_.py" })
    $over = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files $six
    Assert-Equal -Expected "refuse" -Actual $over.Decision -Because "20 + 6 = 26: $($over.Why)"
    Assert-Equal -Expected 0 -Actual @($over.Add).Count -Because "nothing partial"
    Assert-Equal -Expected $true -Actual (Test-TeamAreaReturnCounts -Resolution $over) -Because "the return counts"
    $inside = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files (@($five) + @("src/file1.py", "src/file2.py"))
    Assert-Equal -Expected "widen" -Actual $inside.Decision -Because "files already inside the area are not counted twice: $($inside.Why)"
}

# -------------------------------------------------------------------------- idempotent

Test-Case "idempotent: a widening applied twice equals the first result" {
    $task = New-Task -Id "card-one" -Area @("src/a.py") -DependsOn @("earlier")
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @($intents, $gateway)
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "bir kez" -Now $now)
    $first = Get-TaskShape -Task $task
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "bir kez" -Now $now.AddMinutes(7))
    Assert-Equal -Expected $first -Actual (Get-TaskShape -Task $task) -Because "area, depends_on, area_widenings and area_history are those of the first run"
    Assert-Equal -Expected 1 -Actual ([int]$task.area_widenings) -Because "counted once"
    Assert-Equal -Expected 1 -Actual @($task.area_history).Count -Because "recorded once"
    Assert-List -Expected @("src/a.py", $intents, $gateway) -Actual $task.area -Because "added once"
}

Test-Case "idempotent: a wait applied twice equals the first result" {
    $task = New-Task -Id "card-one" -Area @("src/a.py")
    $holder = New-Task -Id "voice-card" -State "approved" -Area @($intents)
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task, $holder)) -Files @($intents)
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "worker" -Why "bekle" -Now $now)
    $first = Get-TaskShape -Task $task
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "worker" -Why "bekle" -Now $now.AddMinutes(7))
    Assert-Equal -Expected $first -Actual (Get-TaskShape -Task $task) -Because "area, depends_on, area_widenings and area_history are those of the first run"
    Assert-List -Expected @("voice-card") -Actual $task.depends_on -Because "one dependency"
    Assert-Equal -Expected 1 -Actual @($task.area_history).Count -Because "recorded once"
}

Test-Case "idempotent: a task read from JSON is widened and written back as the queue's shapes" {
    $json = '{"id":"card-one","state":"returned","area":["src/a.py"],"depends_on":[]}'
    $task = ConvertFrom-Json -InputObject $json
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue (New-Queue -Tasks @($task)) -Files @($intents)
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "x" -Now $now)
    $back = ConvertFrom-Json -InputObject (ConvertTo-Json -InputObject $task -Depth 6)
    Assert-List -Expected @("src/a.py", $intents) -Actual $back.area -Because "the area is an array of strings"
    Assert-Equal -Expected 1 -Actual ([int]$back.area_widenings) -Because "the count is a number"
    Assert-List -Expected @($intents) -Actual @($back.area_history)[0].files -Because "the record keeps its files"
}

# --------------------------------------------------------------------------- d20261001

# narrative-failures-only-model stopped after two returns with one finding: the fix is in
# app/voice/intents, outside the card's area (team/proposals/2026-10-02-alan-disi-geri-verme.md).
$narrativeArea = @("services/api/app/narrative", "services/api/tests/unit/test_narrative_failures.py")

Test-Case "d20261001: narrative-failures-only-model asks for intents.py and nobody holds it - widen" {
    $task = New-Task -Id "narrative-failures-only-model" -State "inspecting" -Area $narrativeArea
    $queue = New-Queue -Tasks @($task, (New-Task -Id "execution-call-site-research" -State "stopped" -Area @("services/api/app/gateway")), (New-Task -Id "office-page" -State "merged" -Area @("apps/web/src/office")))
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [$intents]")) -Role "inspector"
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files $asked.Files
    Assert-Equal -Expected "widen" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected @($intents) -Actual $resolution.Add -Because "the one file"
    Assert-Equal -Expected $false -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "this return would not have been one of the two"
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "düzeltme app/voice/intents içinde, alan dışı" -Now $now)
    Assert-List -Expected (@($narrativeArea) + @($intents)) -Actual $task.area -Because "the second round can do the real work"
}

Test-Case "d20261001: the same request while an approved card holds intents.py - wait on that card" {
    $task = New-Task -Id "narrative-failures-only-model" -State "inspecting" -Area $narrativeArea
    $holder = New-Task -Id "answer-mode-intent-precision" -State "approved" -Area @($intents, "services/api/tests/unit/test_intents.py")
    $queue = New-Queue -Tasks @($holder, $task)
    $asked = Get-TeamAreaRequest -Report (New-InspectorReport -Lines @("alan_disi: [$intents]")) -Role "inspector"
    $resolution = Resolve-TeamAreaRequest -Task $task -Queue $queue -Files $asked.Files
    Assert-Equal -Expected "wait" -Actual $resolution.Decision -Because $resolution.Why
    Assert-List -Expected @("answer-mode-intent-precision") -Actual $resolution.DependsOn -Because "the card that holds the file"
    Assert-Equal -Expected $false -Actual (Test-TeamAreaReturnCounts -Resolution $resolution) -Because "not one of the two rights"
    [void](Add-TeamAreaWidening -Task $task -Resolution $resolution -By "inspector" -Why "intents.py başka kartta" -Now $now)
    Assert-List -Expected $narrativeArea -Actual $task.area -Because "the area is unchanged"
    Assert-List -Expected @("answer-mode-intent-precision") -Actual $task.depends_on -Because "behind that card"
}

# -------------------------------------------------------------------------------- gate

Test-Case "gate: quality-gate.ps1 runs this suite as its own step, under PS 5.1, and reads its exit code" {
    $gate = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\quality-gate.ps1"), [System.Text.Encoding]::UTF8)
    $step = [regex]::Match($gate, '(?s)Invoke-Step "Agent team area widening rules[^"]*" \{(.*?)\n  \}')
    Assert-True -Condition $step.Success -Because "the step is registered"
    $body = $step.Groups[1].Value
    Assert-True -Condition ($body -match 'scripts\\tests\\team-area\.tests\.ps1') -Because "it names this file"
    Assert-True -Condition ($body -match '&\s*\$powershell5\s+-NoProfile\s.*-File\s+\$script') -Because "it runs it under Windows PowerShell 5.1"
    Assert-True -Condition ($body -match 'Assert-ExitCode "team-area tests"') -Because "a red suite fails the gate"
    Assert-True -Condition ($gate.IndexOf('Invoke-Step "Agent team roadmap feeder') -lt $step.Index) -Because "next to the roadmap feeder's step"
}

Write-Host ""
Write-Host "team-area tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
