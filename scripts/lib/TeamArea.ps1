<#
.SYNOPSIS
    "Alan dışı geri verme": what a role's request for files outside a card's area is, and
    whether the cycle may widen the area, must put the card behind another, or must refuse.

.DESCRIPTION
    A card's fix is sometimes outside the card's file area. Until now the worker could not
    make it, the inspector returned the card twice and it stopped for the lead to open by
    hand (cycle d20261001: narrative-failures-only-model, execution-call-site-research).
    The rules here let the cycle decide instead (team/proposals/2026-10-02-alan-disi-geri-verme.md):

      * THE REQUEST is one line of a report, alone on its line: the inspector writes
        `alan_disi: [path, path]` above its verdict, the worker writes
        `ALAN_ISTEGI: [path, path]` (Get-TeamAreaRequest);
      * THE JUDGEMENT, in this order (Resolve-TeamAreaRequest): nothing asked outside the
        area, a path that is not inside the repository, a protected path, the cap of
        widenings, the size of an area - each refuses the WHOLE request; a path held by a
        task in work makes the card wait behind that task, the area unchanged; otherwise the
        area is widened;
      * THE WRITING of that judgement onto the task (Add-TeamAreaWidening), which changes
        nothing when it is run a second time;
      * whether the return still counts as one of the worker's two (Test-TeamAreaReturnCounts).

    As in `TeamQueue.ps1`, every rule is a function that takes its inputs and returns its
    answer: this file starts no process, reads no file and writes no store. Nothing here is
    called by the cycle yet; the wiring is a later card, and the fields `area_widenings` and
    `area_history` reach the queue's schema and the store with it.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "TeamQueue.ps1")

# The key each role writes. Case matters: prose that mentions the other spelling is not a request.
$script:TeamAreaRequestKeys = @{
    "inspector" = "alan_disi"
    "worker"    = "ALAN_ISTEGI"
}

# How many times the cycle widens one task's area by itself. The next request is the lead's.
$script:TeamAreaMaxWidenings = 2

function New-TeamAreaProtectedEntry {
    param([string]$Name, [string]$Kind, [string]$Value, [string]$Source)
    return [pscustomobject]@{ Name = $Name; Kind = $Kind; Value = $Value; Source = $Source }
}

# Never widened into, whoever asks. ONE list:
#   path     the path itself, anything under it, and any directory that holds it;
#   pattern  a regular expression on the path as Get-TeamAreaKey spells it (lower case,
#            forward slashes).
# The shared files are TeamQueue.ps1's own list, taken as it is: two lists would drift.
$script:TeamAreaProtected = @(
    @($script:TeamSharedFiles | ForEach-Object {
            New-TeamAreaProtectedEntry -Name $_ -Kind "path" -Value $_ -Source "scripts/lib/TeamQueue.ps1 `$script:TeamSharedFiles (TEAM_PROTOCOL section 4: the lead writes it)"
        })

    # The lead itself may not edit these without the owner.
    New-TeamAreaProtectedEntry -Name "docs/ROADMAP.md" -Kind "path" -Value "docs/ROADMAP.md" -Source ".claude/agents/lead.md (never edit ROADMAP without an owner decision)"
    New-TeamAreaProtectedEntry -Name "docs/TEAM_PROTOCOL.md" -Kind "path" -Value "docs/TEAM_PROTOCOL.md" -Source ".claude/agents/lead.md (never edit TEAM_PROTOCOL without an owner decision)"
    New-TeamAreaProtectedEntry -Name ".claude/agents" -Kind "path" -Value ".claude/agents" -Source "docs/TEAM_PROTOCOL.md section 2 (the role files are the roles' own rules)"
    New-TeamAreaProtectedEntry -Name "hand-gestures" -Kind "pattern" -Value "hand[-_]?gestures" -Source ".claude/agents/worker.md (feat/hand-gestures-stage1 is frozen); Test-TeamSplit refuses the same"

    # Secrets: what .gitignore keeps out of git, and the secret root (constitution section 6).
    New-TeamAreaProtectedEntry -Name "env-file" -Kind "pattern" -Value "(^|/)\.env(\.[^/]*)?$" -Source ".gitignore '# Secrets': .env, .env.*"
    New-TeamAreaProtectedEntry -Name "key-material" -Kind "pattern" -Value "\.(key|pem|pfx)$" -Source ".gitignore '# Secrets': *.key, *.pem, *.pfx"
    New-TeamAreaProtectedEntry -Name "tofu-state" -Kind "pattern" -Value "(\.tfstate(\.[^/]*)?|\.tfvars|\.tfplan|(^|/)tfplan\.binary)$" -Source ".gitignore: *.tfstate, *.tfvars, tfplan.binary, *.tfplan (they hold the tokens)"
    New-TeamAreaProtectedEntry -Name "secrets" -Kind "path" -Value "secrets" -Source ".gitignore '# Secrets': secrets/local/"
    New-TeamAreaProtectedEntry -Name "services/api/var" -Kind "path" -Value "services/api/var" -Source ".gitignore: the owner identity root (the hash of the owner credential)"
    New-TeamAreaProtectedEntry -Name "scripts/lib/SecretStore.ps1" -Kind "path" -Value "scripts/lib/SecretStore.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': the DPAPI store"
    New-TeamAreaProtectedEntry -Name "scripts/secret-store.ps1" -Kind "path" -Value "scripts/secret-store.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': the store's entry point"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/install-env-secret.sh" -Kind "path" -Value "scripts/cloud/install-env-secret.sh" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': writes the Cloud Core's .env"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/set-cloud-secret.ps1" -Kind "path" -Value "scripts/cloud/set-cloud-secret.ps1" -Source "PROJECT_CONSTITUTION.md section 6 'secret root': sends a secret to the Cloud Core"

    # Last-known-good metadata: the pointer and the two places that write it.
    New-TeamAreaProtectedEntry -Name "last-known-good" -Kind "pattern" -Value "last[-_]?known[-_]?good" -Source "PROJECT_CONSTITUTION.md section 6 'last-known-good release pointer'; docs/DEVELOPMENT_POLICY.md section 11"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/release-cloud-core-bluegreen.sh" -Kind "path" -Value "scripts/cloud/release-cloud-core-bluegreen.sh" -Source "writes RELEASE and LAST_KNOWN_GOOD on the host; installed as the recovery root's reconcile.sh (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "services/recovery-supervisor" -Kind "path" -Value "services/recovery-supervisor" -Source "PROJECT_CONSTITUTION.md section 6 'recovery supervisor'; recovery_supervisor/workspace.py holds last_known_good.txt; services/api/app/evolution/sandbox.py PROTECTED_TREES"

    # The recovery roots: what must run when the main application is broken.
    New-TeamAreaProtectedEntry -Name "services/api/app/identity/root.py" -Kind "path" -Value "services/api/app/identity/root.py" -Source "PROJECT_CONSTITUTION.md section 6 'owner identity root'"
    New-TeamAreaProtectedEntry -Name "infra/docker/docker-compose.prod.yml" -Kind "path" -Value "infra/docker/docker-compose.prod.yml" -Source "copied into /opt/pagentos-recovery (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "infra/docker/edge" -Kind "path" -Value "infra/docker/edge" -Source "nginx.conf is copied into /opt/pagentos-recovery (scripts/cloud/install-recovery-supervisor.sh)"
    New-TeamAreaProtectedEntry -Name "infra/systemd" -Kind "path" -Value "infra/systemd" -Source "the root units of the reconcile timer and the backup (services/api/app/evolution/risk.py: 'the recovery timer')"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/install-recovery-supervisor.sh" -Kind "path" -Value "scripts/cloud/install-recovery-supervisor.sh" -Source "writes the recovery root and its pin (APPROVED_SHA)"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/uninstall-recovery-supervisor.sh" -Kind "path" -Value "scripts/cloud/uninstall-recovery-supervisor.sh" -Source "removes the recovery root"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/backup-cloud-core.sh" -Kind "path" -Value "scripts/cloud/backup-cloud-core.sh" -Source "PROJECT_CONSTITUTION.md section 6 'backup/restore primitives'"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/restore-cloud-core.sh" -Kind "path" -Value "scripts/cloud/restore-cloud-core.sh" -Source "PROJECT_CONSTITUTION.md section 6 'backup/restore primitives'"

    # The lead's ruling at merge (ADR-0253, 'Protected by the lead'): what governs the agents
    # themselves and what sends a tree to production is never widened into by a request.
    # Shared code that cards hold every day (the gate, the CI file, cycle.ps1, TeamQueue.ps1)
    # is deliberately NOT here: a holder in work makes the request wait, the inspector reads
    # the diff, and a new suite needs its gate line - refusing that would stop the very
    # cards this file exists for.
    New-TeamAreaProtectedEntry -Name "scripts/lib/TeamArea.ps1" -Kind "path" -Value "scripts/lib/TeamArea.ps1" -Source "this list: a request must not be able to shorten it"
    New-TeamAreaProtectedEntry -Name ".claude/hooks" -Kind "path" -Value ".claude/hooks" -Source "CLAUDE.md 'Session continuity': the hook that hands every session its handoff"
    New-TeamAreaProtectedEntry -Name "claude-settings" -Kind "pattern" -Value "(^|/)\.claude/settings(\.[^/]*)?\.json$" -Source "the agents' own permissions and hooks"
    New-TeamAreaProtectedEntry -Name "claude-md" -Kind "pattern" -Value "(^|/)claude\.md$" -Source "the engineering contract every agent is given, in any directory"
    New-TeamAreaProtectedEntry -Name "PROJECT_CONSTITUTION.md" -Kind "path" -Value "PROJECT_CONSTITUTION.md" -Source "the product's constitution: the owner's"
    New-TeamAreaProtectedEntry -Name "docs/DEVELOPMENT_POLICY.md" -Kind "path" -Value "docs/DEVELOPMENT_POLICY.md" -Source "the owner's permanent directive of 2026-09-07 (CLAUDE.md 'Engineering operating mode')"
    New-TeamAreaProtectedEntry -Name "git-internals" -Kind "pattern" -Value "(^|/)\.git(/|$)" -Source "the repository's own files and hooks are not source"
    New-TeamAreaProtectedEntry -Name "gitignore" -Kind "pattern" -Value "(^|/)\.gitignore$" -Source "its '# Secrets' block is what keeps secrets out of git"
    New-TeamAreaProtectedEntry -Name "scripts/cloud/release-cloud-core.ps1" -Kind "path" -Value "scripts/cloud/release-cloud-core.ps1" -Source "sends a tree to the production host (ADR-0214 addendum 9: the release is the lead's)"
)

function Get-TeamAreaProtected { return @($script:TeamAreaProtected) }

function Get-TeamAreaMaxWidenings { return $script:TeamAreaMaxWidenings }

function ConvertTo-TeamAreaPath {
    <#
    .SYNOPSIS
        One entry of a request as a repository-relative path, and whether it is one.

    .DESCRIPTION
        Backticks, asterisks, quotes and spaces around it are dropped, back slashes become
        forward slashes, a leading ./ and ONE trailing slash go. It is NOT a path inside the
        repository - and Path is then the entry as it was written - when:

          * it is absolute, carries a drive letter, or starts at a home ('~');
          * it holds one of  : [ ] ;  or a wildcard (a file:line, a URL, a list in a list);
          * a segment is empty ('a//b'), is '.' or '..', or ends in a dot or a space.

        The last rule is what keeps the protected check and the conflict check honest: both
        compare text, and 'docs//HANDOFF.md', 'docs/./HANDOFF.md' and 'docs/HANDOFF.md.'
        (Windows drops a trailing dot or space) are docs/HANDOFF.md in other letters. Such a
        spelling is not repaired, it is refused: the role writes the path as git names it.
    #>
    param([AllowEmptyString()][string]$Text)
    $written = ([string]$Text).Trim().Trim('`', '*', '"', "'", ' ') -replace '\\', '/'
    $path = $written
    $absolute = ($path.StartsWith("/") -or $path -match '^[A-Za-z]:')
    while ($path.StartsWith("./")) { $path = $path.Substring(2) }
    if (-not $absolute -and $path.EndsWith("/")) { $path = $path.Substring(0, $path.Length - 1) }
    $inside = (-not $absolute -and $path -ne "" -and $path -notmatch '[*?:;\[\]]')
    if ($inside) {
        foreach ($segment in @($path -split '/')) {
            if ($segment -eq "" -or $segment -eq "~" -or $segment -match '[. ]$') { $inside = $false }
        }
    }
    if (-not $inside) { $path = $written }
    return [pscustomobject]@{ Path = $path; Inside = [bool]$inside }
}

function Get-TeamAreaRequest {
    <#
    .SYNOPSIS
        The files a role's report asks for outside the card's area.

    .DESCRIPTION
        The request is ONE line, alone on its line: the role's key (the inspector's
        `alan_disi`, the worker's `ALAN_ISTEGI` - case matters, and neither is read from the
        other's report), a colon, and a bracketed, comma-separated list of repository-relative
        paths. Backticks, asterisks and spaces around the line, the key or a path are ignored.
        The LAST such line wins - an empty list or a line without brackets there is "no
        request", whatever an earlier line said.

        A bullet, a quote mark or a number before the key ('- alan_disi: ...') and anything
        after the closing bracket make the line prose, as for Get-TeamVerdict.

        Returns Asked, Files (forward slashes, no leading ./, each once, order kept) and Bad
        (entries that are not plainly written paths inside the repository - see
        ConvertTo-TeamAreaPath; they are never in Files). A request of bad entries only is
        still Asked. A request with ANY Bad entry is refused whole: the caller resolves
        Files and Bad together (Resolve-TeamAreaRequest -Files (Files + Bad)), never Files alone.
    #>
    param(
        [AllowEmptyString()][string]$Report,
        [Parameter(Mandatory = $true)][ValidateSet("inspector", "worker")][string]$Role
    )
    $key = [regex]::Escape($script:TeamAreaRequestKeys[$Role])
    $list = $null
    foreach ($line in @(([string]$Report) -split "`r?`n")) {
        $text = $line.Trim().Trim('`', '*', ' ')
        if ($text -cnotmatch ('^' + $key + '[\s`*]*:[\s`*]*(.*)$')) { continue }
        $rest = $Matches[1].Trim().Trim('`', '*', ' ')
        $list = if ($rest -match '^\[(.*)\]$') { $Matches[1] } else { $null }
    }
    $files = New-Object System.Collections.Generic.List[string]
    $bad = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    if ($null -ne $list) {
        foreach ($entry in @($list -split ',')) {
            if (-not $entry.Trim().Trim('`', '*', '"', "'", ' ')) { continue }
            $path = ConvertTo-TeamAreaPath -Text $entry
            if (-not $path.Inside) {
                if (-not $bad.Contains($path.Path)) { $bad.Add($path.Path) }
                continue
            }
            $name = Get-TeamAreaKey -Area $path.Path
            if ($seen.ContainsKey($name)) { continue }
            $seen[$name] = $true
            $files.Add($path.Path)
        }
    }
    return [pscustomobject]@{
        Asked = [bool](@($files).Count -gt 0 -or @($bad).Count -gt 0)
        Files = [string[]]$files.ToArray()
        Bad   = [string[]]$bad.ToArray()
    }
}

function Get-TeamAreaProtection {
    <# The entry of the protected list a path falls under, or $null. #>
    param([AllowEmptyString()][string]$Path)
    $key = Get-TeamAreaKey -Area $Path
    foreach ($entry in $script:TeamAreaProtected) {
        if ($entry.Kind -eq "pattern") {
            if ($key -match $entry.Value) { return $entry }
        }
        elseif (Test-TeamAreasOverlap -First $key -Second $entry.Value) { return $entry }
    }
    return $null
}

function Test-TeamAreaWaitsFor {
    <# Whether the task `From` waits, directly or through others, for the task `For`. #>
    param([Parameter(Mandatory = $true)]$Queue, [string]$From, [string]$For)
    $seen = @{}
    $next = New-Object System.Collections.Generic.Queue[string]
    $next.Enqueue($From)
    while (@($next).Count -gt 0) {
        $id = $next.Dequeue()
        if ($seen.ContainsKey($id)) { continue }
        $seen[$id] = $true
        $task = @(Get-TeamTasks -Queue $Queue | Where-Object { [string]$_.id -eq $id })
        if (@($task).Count -eq 0) { continue }
        foreach ($dependency in @(Get-TeamProperty -InputObject $task[0] -Name "depends_on" -Default @())) {
            if ([string]$dependency -eq $For) { return $true }
            $next.Enqueue([string]$dependency)
        }
    }
    return $false
}

function New-TeamAreaResolution {
    param([string]$Decision, [string[]]$Add = @(), [string[]]$DependsOn = @(), [string]$Why)
    return [pscustomobject]@{ Decision = $Decision; Add = [string[]]@($Add); DependsOn = [string[]]@($DependsOn); Why = $Why }
}

function Resolve-TeamAreaRequest {
    <#
    .SYNOPSIS
        What the cycle does with a request for files outside a task's area.

    .DESCRIPTION
        Returns Decision ('widen' | 'wait' | 'refuse'), Add (the asked files that are not
        already inside the area), DependsOn (the ids to wait for) and Why (a sentence for
        the report). Judged in this order; a refusal is of the WHOLE request, never a part:

          1. a path that is not a plainly written path inside the
             repository (ConvertTo-TeamAreaPath)                   -> refuse
          2. nothing asked that is outside the area                -> refuse (nothing to widen)
          3. ANY asked path is protected ($script:TeamAreaProtected) -> refuse
          4. the task was already widened $script:TeamAreaMaxWidenings times -> refuse, to the lead
          5. the area would exceed $script:TeamMaxAreaEntries entries -> refuse
          6. ANY path to add overlaps the area of ANOTHER task in work
             ($script:TeamStatesInWork)                             -> wait for those tasks,
             the area unchanged - unless one of them waits for this task, which would hold
             both for ever                                          -> refuse, to the lead
          7. otherwise                                              -> widen

        Nothing is changed here; Add-TeamAreaWidening writes the answer onto the task.
    #>
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Queue,
        [AllowEmptyCollection()][AllowEmptyString()][string[]]$Files = @()
    )
    $id = [string](Get-TeamProperty -InputObject $Task -Name "id" -Default "")
    $area = [string[]]@(Get-TeamProperty -InputObject $Task -Name "area" -Default @() | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ })

    $asked = New-Object System.Collections.Generic.List[string]
    $add = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($file in @($Files)) {
        $path = ConvertTo-TeamAreaPath -Text $file
        if (-not $path.Inside) {
            return (New-TeamAreaResolution -Decision "refuse" -Why "İstenen '$file' depo içinde düz yazılmış bir yol değil (mutlak yol, '..', boş ya da '.' parça, sonu nokta ya da boşluk, ya da : [ ] ; içeriyor); alan genişletilmedi.")
        }
        $name = Get-TeamAreaKey -Area $path.Path
        if ($seen.ContainsKey($name)) { continue }
        $seen[$name] = $true
        $asked.Add($path.Path)
        if (-not (Test-TeamPathInsideArea -Path $path.Path -Area $area)) { $add.Add($path.Path) }
    }
    if (@($add).Count -eq 0) {
        return (New-TeamAreaResolution -Decision "refuse" -Why "İstenen dosyaların hepsi zaten kartın alanında; genişletilecek bir şey yok.")
    }

    foreach ($path in $asked) {
        $entry = Get-TeamAreaProtection -Path $path
        if ($null -ne $entry) {
            return (New-TeamAreaResolution -Decision "refuse" -Why "Korunan yol istendi: '$path' ($($entry.Name)). Alan genişletilmedi; karar lead'in.")
        }
    }

    $widenings = [int](Get-TeamProperty -InputObject $Task -Name "area_widenings" -Default 0)
    if ($widenings -ge $script:TeamAreaMaxWidenings) {
        return (New-TeamAreaResolution -Decision "refuse" -Why "Bu kartın alanı zaten $widenings kez genişletildi (tavan $script:TeamAreaMaxWidenings); yenisi lead'in kararı.")
    }

    $total = @($area).Count + @($add).Count
    if ($total -gt $script:TeamMaxAreaEntries) {
        return (New-TeamAreaResolution -Decision "refuse" -Why "Alan $total girdiye çıkardı; bir kart en çok $script:TeamMaxAreaEntries girdi tutar. Kartı lead bölmeli.")
    }

    $holders = New-Object System.Collections.Generic.List[string]
    foreach ($other in @(Get-TeamTasks -Queue $Queue)) {
        $otherId = [string](Get-TeamProperty -InputObject $other -Name "id" -Default "")
        if ($otherId -eq $id -or $holders.Contains($otherId)) { continue }
        if ($script:TeamStatesInWork -notcontains [string](Get-TeamProperty -InputObject $other -Name "state" -Default "")) { continue }
        $held = $false
        foreach ($otherArea in @(Get-TeamProperty -InputObject $other -Name "area" -Default @())) {
            foreach ($path in $add) {
                if (Test-TeamAreasOverlap -First $path -Second ([string]$otherArea)) { $held = $true }
            }
        }
        if ($held) { $holders.Add($otherId) }
    }
    if (@($holders).Count -gt 0) {
        foreach ($holder in $holders) {
            if (Test-TeamAreaWaitsFor -Queue $Queue -From $holder -For $id) {
                return (New-TeamAreaResolution -Decision "refuse" -Why "İstenen dosya '$holder' kartının alanında ve o kart bu kartı bekliyor; beklemek ikisini de kilitlerdi. Karar lead'in.")
            }
        }
        $names = @($holders) -join ", "
        return (New-TeamAreaResolution -Decision "wait" -Add $add.ToArray() -DependsOn $holders.ToArray() -Why "İstenen dosya başka kartın alanında ($names); alan değişmedi, kart o iş main'e girene kadar bekler.")
    }

    return (New-TeamAreaResolution -Decision "widen" -Add $add.ToArray() -Why "Çakışma yok; kartın alanı genişletildi: $(@($add) -join ', ').")
}

function Add-TeamAreaWidening {
    <#
    .SYNOPSIS
        Writes a resolution onto the task, and returns the task.

    .DESCRIPTION
        widen   the files that are still outside the area are appended to it,
                `area_widenings` goes up by one and `area_history` gains {at, by, why, files};
        wait    the ids that are not yet there are added to `depends_on` (never the task's
                own id) and `area_history` gains {at, by, why, files, waits_for}; the area
                is not touched;
        refuse  nothing.

        A second run with the same resolution finds nothing left to add and changes nothing:
        no second count, no second record.
    #>
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][AllowNull()]$Resolution,
        [Parameter(Mandatory = $true)][string]$By,
        [AllowEmptyString()][string]$Why = "",
        [datetime]$Now = [datetime]::UtcNow
    )
    $decision = [string](Get-TeamProperty -InputObject $Resolution -Name "Decision" -Default "")
    $files = [string[]]@(Get-TeamProperty -InputObject $Resolution -Name "Add" -Default @() | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ })
    $reason = if ($Why) { $Why } else { [string](Get-TeamProperty -InputObject $Resolution -Name "Why" -Default "") }
    $history = New-Object System.Collections.Generic.List[object]
    foreach ($record in @(Get-TeamProperty -InputObject $Task -Name "area_history" -Default @() | Where-Object { $null -ne $_ })) { $history.Add($record) }

    if ($decision -eq "widen") {
        $area = New-Object System.Collections.Generic.List[string]
        foreach ($entry in @(Get-TeamProperty -InputObject $Task -Name "area" -Default @() | Where-Object { $null -ne $_ })) { $area.Add([string]$entry) }
        $added = New-Object System.Collections.Generic.List[string]
        foreach ($file in $files) {
            if (Test-TeamPathInsideArea -Path $file -Area $area.ToArray()) { continue }
            $area.Add($file)
            $added.Add($file)
        }
        if (@($added).Count -eq 0) { return $Task }
        $history.Add([pscustomobject]@{ at = (Get-TeamTimestamp -Now $Now); by = $By; why = $reason; files = [string[]]$added.ToArray() })
        Set-TeamProperty -InputObject $Task -Name "area" -Value ([string[]]$area.ToArray())
        Set-TeamProperty -InputObject $Task -Name "area_widenings" -Value ([int](Get-TeamProperty -InputObject $Task -Name "area_widenings" -Default 0) + 1)
        Set-TeamProperty -InputObject $Task -Name "area_history" -Value ([object[]]$history.ToArray())
        return $Task
    }

    if ($decision -eq "wait") {
        $id = [string](Get-TeamProperty -InputObject $Task -Name "id" -Default "")
        $depends = New-Object System.Collections.Generic.List[string]
        foreach ($entry in @(Get-TeamProperty -InputObject $Task -Name "depends_on" -Default @() | Where-Object { $null -ne $_ })) { $depends.Add([string]$entry) }
        $added = New-Object System.Collections.Generic.List[string]
        foreach ($other in @(Get-TeamProperty -InputObject $Resolution -Name "DependsOn" -Default @() | Where-Object { $null -ne $_ })) {
            $name = [string]$other
            if (-not $name -or $name -eq $id -or $depends.Contains($name)) { continue }
            $depends.Add($name)
            $added.Add($name)
        }
        if (@($added).Count -eq 0) { return $Task }
        $history.Add([pscustomobject]@{ at = (Get-TeamTimestamp -Now $Now); by = $By; why = $reason; files = $files; waits_for = [string[]]$added.ToArray() })
        Set-TeamProperty -InputObject $Task -Name "depends_on" -Value ([string[]]$depends.ToArray())
        Set-TeamProperty -InputObject $Task -Name "area_history" -Value ([object[]]$history.ToArray())
        return $Task
    }

    return $Task
}

function Test-TeamAreaReturnCounts {
    <#
    .SYNOPSIS
        Whether a return that carried this resolution is one of the worker's two.

    .DESCRIPTION
        A return the cycle answered by widening the area, or by putting the card behind
        another, is the card's fault and not the worker's: it does not count. A refused
        request - and anything that is not a resolution - is an ordinary return.
    #>
    param([AllowNull()]$Resolution)
    $decision = [string](Get-TeamProperty -InputObject $Resolution -Name "Decision" -Default "")
    return [bool](@("widen", "wait") -cnotcontains $decision)
}
