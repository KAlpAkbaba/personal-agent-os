<#
.SYNOPSIS
    Journaled, versioned, transactional deployment of the agent's binary trees.

.DESCRIPTION
    Written after a real deployment failure whose anatomy dictated every rule here. The old
    flow renamed the live `service` directory while the service was RUNNING from it — NTFS
    refuses to rename a directory whose subtree contains memory-mapped images, so the first
    move died with access denied. Because the companion had been killed before that call and
    its restart sat after it with no try/finally, the failure left the service up, the
    companion down, an empty `.previous`, and a complete-but-unused `.staging` — and nothing
    recorded which of those states the machine was in.

    Rules, each traceable to that incident:

      * a persistent JOURNAL (`.deploy-journal.json`) records every phase transition:
        staged -> runtime_stopped -> old_moved -> candidate_promoted -> acl_applied ->
        runtime_started -> health_verified -> committed (or rolled_back). Crash recovery
        reads the journal; it never infers state from directory names alone;
      * the runtime is stopped BEFORE any move, and the stop is verified: the engine waits
        for the named processes to exit and then confirms no process is still executing out
        of the trees it is about to move;
      * shutdown/startup are symmetric and injected as script blocks (StopRuntime /
        StartRuntime / TestHealth), so tests drive the engine with fakes and production
        passes the real service+companion handlers. EVERY failure path restarts the runtime —
        candidate if promoted, previous otherwise — inside finally;
      * moves are same-volume renames (Move-Item on the same NTFS volume), never recursive
        copies over a live tree; `.previous\<version>` keeps the displaced trees until commit;
      * rollback is the mirror image: stop candidate, restore `.previous`, restart, verify.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")
. (Join-Path $PSScriptRoot "InstallAcl.ps1")

$script:DeployPhases = @(
    "staged", "runtime_stopped", "old_moved", "candidate_promoted",
    "acl_applied", "runtime_started", "health_verified", "committed", "rolled_back"
)

function Get-DeployJournalPath {
    param([string]$Root)
    return Join-Path $Root ".deploy-journal.json"
}

function Read-DeployJournal {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    $path = Get-DeployJournalPath -Root $Root
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    }
    catch {
        # An unreadable journal is itself state: report it, never guess past it.
        throw "the deployment journal at $path exists but cannot be parsed; resolve it before deploying"
    }
}

function Write-DeployPhase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][ValidateSet(
            "staged", "runtime_stopped", "old_moved", "candidate_promoted",
            "acl_applied", "runtime_started", "health_verified", "committed", "rolled_back")]
        [string]$Phase,
        [string]$Detail = ""
    )

    $journal = Read-DeployJournal -Root $Root
    $history = @()
    if ($journal -and ($journal.PSObject.Properties.Name -contains "history")) {
        $history = @($journal.history)
    }
    $history += [pscustomobject]@{ phase = $Phase; at = (Get-Date).ToUniversalTime().ToString("o"); detail = $Detail }

    $document = [pscustomobject]@{
        version = $Version
        phase   = $Phase
        history = $history
    }
    [System.IO.File]::WriteAllText(
        (Get-DeployJournalPath -Root $Root),
        ($document | ConvertTo-Json -Depth 6),
        (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ProcessesUsingTree {
    <#
    .SYNOPSIS
        Processes whose executable lives under the given directory — the ones whose mapped
        images make a rename of that directory impossible.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    $prefix = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\') + '\'
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Assert-TreesUnlocked {
    <#
    .SYNOPSIS
        Refuse to move a tree something is still executing from — with names, not a
        mysterious access-denied later.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$Trees)

    $holders = @()
    foreach ($tree in $Trees) {
        if (-not (Test-Path -LiteralPath $tree)) { continue }
        foreach ($process in @(Get-ProcessesUsingTree -Root $tree)) {
            $holders += "$($process.Name) (pid $($process.ProcessId)) runs from $tree"
        }
    }

    if (@($holders).Count -gt 0) {
        throw ("cannot move binary trees while processes execute from them:" +
            [Environment]::NewLine + "  " + ($holders -join "$([Environment]::NewLine)  ") +
            [Environment]::NewLine + "NTFS refuses to rename a directory containing mapped images; stop the runtime first.")
    }
}

function Invoke-AgentDeployment {
    <#
    .SYNOPSIS
        Run the full transaction: candidate in `.staging` -> live, with journal, verified
        stop, atomic renames, ACL, restart, health check, and rollback on any failure.

    .PARAMETER StopRuntime / StartRuntime / TestHealth
        Injected handlers. StopRuntime must stop everything executing from the live trees
        and return only when they are gone. StartRuntime starts service AND companion.
        TestHealth returns $true only when both halves are actually working — "the service
        is Running" alone is not health; a real incident had the service Running with its
        pipe server dead.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Components,
        [Parameter(Mandatory = $true)][scriptblock]$StopRuntime,
        [Parameter(Mandatory = $true)][scriptblock]$StartRuntime,
        [Parameter(Mandatory = $true)][scriptblock]$TestHealth,
        [scriptblock]$ApplyAcl,
        [string]$Version = (Get-Date -Format "yyyyMMdd-HHmmss")
    )

    $stagingRoot = Join-Path $Root ".staging"
    $previousRoot = Join-Path $Root ".previous\$Version"

    # ---- validate the candidate BEFORE touching anything -----------------------------------
    foreach ($component in $Components) {
        $staged = Join-Path $stagingRoot $component
        if (-not (Test-Path -LiteralPath $staged)) {
            throw "no staged candidate for '$component' at $staged; nothing was changed"
        }
        $exe = @(Get-ChildItem -LiteralPath $staged -Filter "PagentOS.*.exe" -ErrorAction SilentlyContinue)
        if (@($exe).Count -eq 0) {
            throw "staged '$component' contains no PagentOS executable; refusing to promote a broken candidate"
        }
    }
    Write-DeployPhase -Root $Root -Version $Version -Phase "staged" -Detail ($Components -join ",")

    $moved = @()        # components whose live tree went to .previous
    $promoted = @()     # components whose staging tree became live
    $runtimeStopped = $false
    $succeeded = $false

    try {
        # ---- stop, then PROVE stopped ------------------------------------------------------
        # Handler pipeline output is discarded: a handler that emits a value (WaitForExit
        # returns a Boolean, for one) must not pollute this function's own return stream.
        $null = & $StopRuntime
        $runtimeStopped = $true
        Assert-TreesUnlocked -Trees @($Components | ForEach-Object { Join-Path $Root $_ })
        Write-DeployPhase -Root $Root -Version $Version -Phase "runtime_stopped"

        # ---- swap: live -> .previous\<version>, staging -> live (same-volume renames) ------
        New-Item -ItemType Directory -Force -Path $previousRoot | Out-Null
        foreach ($component in $Components) {
            $live = Join-Path $Root $component
            if (Test-Path -LiteralPath $live) {
                Move-Item -LiteralPath $live -Destination (Join-Path $previousRoot $component) -Force
                $moved += $component
            }
        }
        Write-DeployPhase -Root $Root -Version $Version -Phase "old_moved" -Detail ($moved -join ",")

        foreach ($component in $Components) {
            Move-Item -LiteralPath (Join-Path $stagingRoot $component) -Destination (Join-Path $Root $component) -Force
            $promoted += $component
        }
        Write-DeployPhase -Root $Root -Version $Version -Phase "candidate_promoted" -Detail ($promoted -join ",")

        if ($ApplyAcl) {
            $null = & $ApplyAcl
        }
        Write-DeployPhase -Root $Root -Version $Version -Phase "acl_applied"

        # ---- start and verify --------------------------------------------------------------
        $null = & $StartRuntime
        Write-DeployPhase -Root $Root -Version $Version -Phase "runtime_started"

        if (-not (& $TestHealth)) {
            throw "the candidate started but did not become healthy; rolling back"
        }
        Write-DeployPhase -Root $Root -Version $Version -Phase "health_verified"

        # ---- commit: only now does the old version die -------------------------------------
        Remove-Item -LiteralPath $previousRoot -Recurse -Force -ErrorAction SilentlyContinue
        $previousParent = Join-Path $Root ".previous"
        if ((Test-Path -LiteralPath $previousParent) -and
            @(Get-ChildItem -LiteralPath $previousParent -Force -ErrorAction SilentlyContinue).Count -eq 0) {
            Remove-Item -LiteralPath $previousParent -Recurse -Force -ErrorAction SilentlyContinue
        }
        $stagingLeftover = Join-Path $Root ".staging"
        if ((Test-Path -LiteralPath $stagingLeftover) -and
            @(Get-ChildItem -LiteralPath $stagingLeftover -Force -ErrorAction SilentlyContinue).Count -eq 0) {
            Remove-Item -LiteralPath $stagingLeftover -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-DeployPhase -Root $Root -Version $Version -Phase "committed"
        $succeeded = $true
        return $true
    }
    finally {
        if (-not $succeeded) {
            # ---- rollback: mirror image, inside finally so a crash cannot skip it ----------
            try {
                $null = & $StopRuntime   # stop whatever half-started candidate is running
            }
            catch { }

            foreach ($component in @($promoted)) {
                $live = Join-Path $Root $component
                if (Test-Path -LiteralPath $live) {
                    # The candidate goes back to staging so a rerun can retry it.
                    $backTo = Join-Path $stagingRoot $component
                    if (Test-Path -LiteralPath $backTo) { Remove-Item -LiteralPath $backTo -Recurse -Force -ErrorAction SilentlyContinue }
                    try { Move-Item -LiteralPath $live -Destination $backTo -Force } catch { }
                }
            }
            foreach ($component in @($moved)) {
                $previous = Join-Path $previousRoot $component
                $live = Join-Path $Root $component
                if ((Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $live)) {
                    try { Move-Item -LiteralPath $previous -Destination $live -Force } catch { }
                }
            }

            if ($runtimeStopped) {
                try {
                    $null = & $StartRuntime
                    if (& $TestHealth) {
                        Write-DeployPhase -Root $Root -Version $Version -Phase "rolled_back" -Detail "previous version restored and healthy"
                    }
                    else {
                        Write-DeployPhase -Root $Root -Version $Version -Phase "rolled_back" -Detail "previous version restored but NOT healthy - investigate"
                    }
                }
                catch {
                    Write-DeployPhase -Root $Root -Version $Version -Phase "rolled_back" -Detail "restart failed: $($_.Exception.Message)"
                }
            }
            else {
                Write-DeployPhase -Root $Root -Version $Version -Phase "rolled_back" -Detail "nothing was moved; runtime untouched"
            }
        }
    }
}

function Resolve-InterruptedDeployment {
    <#
    .SYNOPSIS
        Decide, from evidence, what to do about a partial deployment state — including the
        journal-less state the pre-journal engine left behind.

    .OUTPUTS
        An object with Action (None | RetryFromStaging | RollbackToPrevious | Blocked) and
        Reason.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Components
    )

    $journal = Read-DeployJournal -Root $Root
    $stagingRoot = Join-Path $Root ".staging"
    $stagedComplete = $true
    foreach ($component in $Components) {
        if (-not (Test-Path -LiteralPath (Join-Path $stagingRoot $component))) { $stagedComplete = $false }
    }
    $liveComplete = $true
    foreach ($component in $Components) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $component))) { $liveComplete = $false }
    }

    if ($journal) {
        switch ($journal.phase) {
            "committed"   { return [pscustomobject]@{ Action = "None"; Reason = "last deployment committed" } }
            "rolled_back" { return [pscustomobject]@{ Action = $(if ($stagedComplete) { "RetryFromStaging" } else { "None" }); Reason = "last deployment rolled back$(if ($stagedComplete) { '; candidate still staged' })" } }
            "staged"      { return [pscustomobject]@{ Action = "RetryFromStaging"; Reason = "candidate staged, runtime never stopped" } }
            default {
                # Stopped/moved/promoted but never verified: the journal knows exactly how far
                # it got, and the safe direction is back to the last version that worked.
                return [pscustomobject]@{ Action = "RollbackToPrevious"; Reason = "journal shows phase '$($journal.phase)' without health verification" }
            }
        }
    }

    # No journal: the pre-journal engine's states. Live trees intact + complete staging is
    # the exact state a failed pre-journal swap left (its first move was refused, so nothing
    # was displaced): the candidate is intact and retryable.
    if ($liveComplete -and $stagedComplete) {
        return [pscustomobject]@{ Action = "RetryFromStaging"; Reason = "no journal; live trees intact and a complete candidate is staged (pre-journal interrupted swap)" }
    }
    if ($liveComplete) {
        return [pscustomobject]@{ Action = "None"; Reason = "no journal; live trees intact, nothing staged" }
    }
    return [pscustomobject]@{ Action = "Blocked"; Reason = "no journal and the live trees are incomplete; refuse to guess - inspect .previous and .staging by hand" }
}
