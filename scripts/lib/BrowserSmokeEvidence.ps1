<#
.SYNOPSIS
    Payload and evidence builders for the owner browser smoke (scripts\browser\real-browser-smoke.ps1):
    every `browser.search` payload and every evidence object is constructed ONCE from a fixed
    key list, never merged.

.DESCRIPTION
    Real owner incident (2026-09-04, interactive Google handoff, timeout -> owner chose to fall
    back): the smoke built the fallback payload as `$searchPayload + @{ interstitial = "fallback" }`.
    PowerShell hashtable addition throws "Item has already been added. Key in dictionary:
    'interstitial'" when both operands carry the key - the original payload already had
    `interstitial = "handoff"`. The fallback attempt was never issued and the run died with a
    dictionary error instead of provider evidence.

    Rules encoded here (scripts\tests\browser-smoke-evidence.tests.ps1):
      * New-BrowserSearchPayload builds a fresh hashtable from parameters; two payloads for the
        same query with different interstitial modes are independent objects with one
        `interstitial` key each. Nothing in the smoke adds hashtables.
      * New-HandoffEvidence / ConvertTo-SearchEvidence project the worker's schema-3 result into
        ordered objects with fixed, distinct key sets: the interstitial facts live ONLY in
        `search.verification` (the worker's block); the owner-side handoff block records what the
        owner did (waited, timed out, decided), never the interstitial kind again.
      * Test-EvidenceKeysUnique walks a projection and refuses any duplicate key at one level
        (structurally impossible with [ordered] literals, checked anyway after JSON round-trip).
#>

Set-StrictMode -Version Latest

function New-BrowserSearchPayload {
    <#
    .SYNOPSIS
        The browser.search payload (contract §3/§3a): fixed keys, one `interstitial`, built fresh.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $true)][string]$Query,
        [Parameter(Mandatory = $true)][ValidateSet("interactive", "unattended")][string]$Mode,
        [Parameter(Mandatory = $true)][ValidateSet("handoff", "fallback")][string]$Interstitial,
        [int]$MaxResults = 8,
        # duckduckgo (production default for Research), google (explicit), or auto (ordered chain)
        [ValidateSet("auto", "duckduckgo", "google")][string]$Engine = "duckduckgo"
    )
    return @{
        session_id   = $SessionId
        query        = $Query
        engine       = $Engine
        max_results  = $MaxResults
        mode         = $Mode
        interstitial = $Interstitial
    }
}

function New-HandoffEvidence {
    <#
    .SYNOPSIS
        Owner-side handoff facts (what the owner did), fixed keys. The interstitial itself is
        NOT repeated here: it is the worker's `verification` block in the search evidence.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Mode,
        [int]$TimeoutSec = 0
    )
    return [ordered]@{
        mode            = $Mode
        occurred        = $false
        timeout_s       = $TimeoutSec
        waited_s        = 0
        cleared         = $false
        resumed         = $false
        repeat          = $false
        timed_out       = $false
        owner_decision  = $null
        fallback_issued = $false
    }
}

function Get-ResultProperty {
    param($Result, [string]$Name)
    if ($null -eq $Result) { return $null }
    if ($Result -is [System.Collections.IDictionary]) { if ($Result.Contains($Name)) { return $Result[$Name] } else { return $null } }
    $prop = $Result.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function ConvertTo-SearchEvidence {
    <#
    .SYNOPSIS
        Project a worker browser.search result (schema 3) into the smoke's evidence object.
        Fixed keys; the interstitial facts appear exactly once, under `verification`.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Result)
    $verification = Get-ResultProperty -Result $Result -Name "verification"
    $projectedVerification = $null
    if ($null -ne $verification) {
        $projectedVerification = [ordered]@{
            handoffs         = (Get-ResultProperty -Result $verification -Name "handoffs")
            outcome          = (Get-ResultProperty -Result $verification -Name "outcome")
            interstitial     = (Get-ResultProperty -Result $verification -Name "interstitial")
            verification_url = (Get-ResultProperty -Result $verification -Name "verification_url")
        }
    }
    $attempts = @(Get-ResultProperty -Result $Result -Name "attempts")
    $results = @(Get-ResultProperty -Result $Result -Name "results")
    return [ordered]@{
        schema_version     = (Get-ResultProperty -Result $Result -Name "schema_version")
        requested_provider = (Get-ResultProperty -Result $Result -Name "requested_provider")
        provider           = (Get-ResultProperty -Result $Result -Name "provider")
        fallback           = (Get-ResultProperty -Result $Result -Name "fallback")
        fallback_reason    = (Get-ResultProperty -Result $Result -Name "fallback_reason")
        query              = (Get-ResultProperty -Result $Result -Name "query")
        result_count       = (Get-ResultProperty -Result $Result -Name "result_count")
        locale             = (Get-ResultProperty -Result $Result -Name "locale")
        state              = (Get-ResultProperty -Result $Result -Name "state")
        path               = (Get-ResultProperty -Result $Result -Name "path")
        mode               = (Get-ResultProperty -Result $Result -Name "mode")
        verification       = $projectedVerification
        attempts           = @($attempts | ForEach-Object { [ordered]@{ provider = (Get-ResultProperty -Result $_ -Name "provider"); outcome = (Get-ResultProperty -Result $_ -Name "outcome"); detail = (Get-ResultProperty -Result $_ -Name "detail") } })
        results            = @($results | ForEach-Object { [ordered]@{ rank = (Get-ResultProperty -Result $_ -Name "rank"); title = (Get-ResultProperty -Result $_ -Name "title"); url = (Get-ResultProperty -Result $_ -Name "url") } })
    }
}

function Test-EvidenceKeysUnique {
    <#
    .SYNOPSIS
        Throw when any object level of a JSON document repeats a key (raw-text check, since a
        parsed object cannot even represent a duplicate). Returns $true otherwise.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Json)
    $reader = New-Object System.IO.StringReader($Json)
    $stack = New-Object System.Collections.Generic.Stack[System.Collections.Generic.HashSet[string]]
    $text = $reader.ReadToEnd()
    $i = 0
    $inString = $false
    $current = New-Object System.Text.StringBuilder
    $lastString = $null
    while ($i -lt $text.Length) {
        $ch = $text[$i]
        if ($inString) {
            if ($ch -eq '\') { [void]$current.Append($ch).Append($text[$i + 1]); $i += 2; continue }
            if ($ch -eq '"') { $inString = $false; $lastString = $current.ToString(); [void]$current.Clear(); $i++; continue }
            [void]$current.Append($ch); $i++; continue
        }
        switch ($ch) {
            '"' { $inString = $true }
            '{' { $stack.Push((New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::Ordinal))) }
            '}' { [void]$stack.Pop() }
            ':' {
                if (@($stack).Count -gt 0 -and $null -ne $lastString) {
                    if (-not $stack.Peek().Add($lastString)) { throw "duplicate evidence key '$lastString' at depth $(@($stack).Count)" }
                }
            }
        }
        if ($ch -eq ',' -or $ch -eq '{' -or $ch -eq '[') { $lastString = $null }
        $i++
    }
    return $true
}
