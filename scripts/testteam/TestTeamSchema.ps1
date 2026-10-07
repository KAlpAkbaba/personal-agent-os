<#
.SYNOPSIS
    The test team's file contract (team/plans/testteam-dry-round-schema-adr.md): the result and
    plan files are read by ONE schema each (scripts/testteam/schema/result.json, plan.json), by a
    reader that never throws on what a model wrote.

.DESCRIPTION
    2026-10-06: a role file told the model "write a file like this", a script read the file, and
    the two were written apart. Three fields the model left out (a scenario, a tester, a p95)
    killed two real rounds and took three patches. The schema is the one place that says which
    field is required (the file says nothing without it) and which is optional (the script fills
    it with the default written in the schema, without changing what the file means).

    The schema format is our own (Windows PowerShell 5.1 has no JSON Schema library, and no new
    dependency): { version: 1, doc, fields: { <name>: { required, type: string|int|bool|enum|
    array|object, values (enum), values_from (string), default (null = none), min_items (array),
    items: { fields } (array), fields (object) } } }.

    values_from (test-plan-names-roadmap-rows): "docs/ROADMAP.md#What JARVIS does" - the value is
    one of the counted rows of the table under that heading, read the way app.team.progress
    parse_jarvis reads them (first cell, '**' dropped, whitespace collapsed, no NEVER row). A
    row's whole cell or its bold title is accepted, exactly (no resolve_row looseness); the value
    is read back as the bold title. An absent or unknown value is Missing, and the Why names the
    wrong value and every valid title.

    ConvertTo-TestTeamShape returns a record: Readable (Missing is empty), Value (the document
    with every schema field filled and typed; a field the schema does not know is KEPT), Missing
    (required fields absent or invalid, dotted paths: breaking.tried[2].load), Defaulted
    (optional fields filled with their default), Why (one Turkish sentence). An unreadable file
    is not a 'failed' result: it is staging's bug only when the tester could say so.

    Needs scripts/lib/TeamQueue.ps1 (Get-TeamProperty, Read-TeamJson) dot-sourced by the caller.
    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

$script:TestTeamSchemaDir = Join-Path $PSScriptRoot "schema"
$script:TestTeamSchemaTypes = @("string", "int", "bool", "enum", "array", "object")
$script:TestTeamRepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:TestTeamValuesCache = @{}

function Get-TestTeamValuesFrom {
    <# The valid values of a values_from source "<repository file>#<heading>": the counted rows of
       the table under the heading, each { Full, Title } (Title = the bold part when the first
       cell starts bold, else Full). Throws when the source gives no row: it is our file. #>
    param([Parameter(Mandatory = $true)][string]$Source)
    if ($script:TestTeamValuesCache.ContainsKey($Source)) { return $script:TestTeamValuesCache[$Source] }
    $parts = $Source.Split('#', 2)
    if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) { throw "values_from '$Source': '<dosya>#<başlık>' değil" }
    $path = Join-Path $script:TestTeamRepoRoot ($parts[0].Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "values_from '$Source': dosya yok ($path)" }
    $lines = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) -split "`r?`n"
    $at = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i].StartsWith("#") -and $lines[$i].TrimStart('#').Trim().StartsWith($parts[1], [System.StringComparison]::Ordinal)) { $at = $i; break } }
    if ($at -lt 0) { throw "values_from '$Source': başlık yok" }
    $table = New-Object System.Collections.ArrayList
    for ($i = $at + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith("#")) { break }
        if ($lines[$i].TrimStart().StartsWith("|")) { [void]$table.Add($lines[$i]) }
    }
    $rows = New-Object System.Collections.ArrayList
    foreach ($line in @($table.ToArray() | Select-Object -Skip 2)) {
        $cells = @($line.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
        if ($cells.Count -lt 2) { continue }
        $last = [regex]::Match($cells[$cells.Count - 1], '\*\*(.+?)\*\*')
        if ($last.Success -and $last.Groups[1].Value.Trim().ToUpperInvariant().StartsWith("NEVER")) { continue }
        $full = (($cells[0] -replace '\*\*', '') -split '\s+' | Where-Object { $_ }) -join ' '
        if (-not $full) { continue }
        $bold = [regex]::Match($cells[0], '^\*\*(.+?)\*\*')
        $title = if ($bold.Success) { (($bold.Groups[1].Value -split '\s+' | Where-Object { $_ }) -join ' ') } else { $full }
        [void]$rows.Add([pscustomobject]@{ Full = $full; Title = $title })
    }
    if ($rows.Count -eq 0) { throw "values_from '$Source': tabloda satır yok" }
    $script:TestTeamValuesCache[$Source] = $rows.ToArray()
    return $script:TestTeamValuesCache[$Source]
}

function Resolve-TestTeamValueFrom {
    <# A written value against a values_from source: its Title on an exact match (whitespace
       collapsed) of a row's Title or Full, else $null. #>
    param([string]$Text, [Parameter(Mandatory = $true)][string]$Source)
    $word = (($Text -split '\s+' | Where-Object { $_ }) -join ' ')
    foreach ($row in @(Get-TestTeamValuesFrom -Source $Source)) {
        if ([string]::Equals($row.Title, $word, [System.StringComparison]::Ordinal) -or [string]::Equals($row.Full, $word, [System.StringComparison]::Ordinal)) { return $row.Title }
    }
    return $null
}

function Assert-TestTeamSchemaFields {
    param($Fields, [string]$Prefix, [string]$Path)
    $where = if ($Prefix) { "'$Prefix'" } else { "kök" }
    if ($Fields -isnot [System.Management.Automation.PSCustomObject]) { throw "test ekibi şeması ${Path}: $where alanları bir nesne değil" }
    if (@($Fields.PSObject.Properties).Count -eq 0) { throw "test ekibi şeması ${Path}: $where alanı yok" }
    foreach ($property in $Fields.PSObject.Properties) {
        $name = if ($Prefix) { "$Prefix.$($property.Name)" } else { $property.Name }
        $spec = $property.Value
        if ($spec -isnot [System.Management.Automation.PSCustomObject]) { throw "test ekibi şeması ${Path}: '$name' bir nesne değil" }
        if ((Get-TeamProperty -InputObject $spec -Name "required") -isnot [bool]) { throw "test ekibi şeması ${Path}: '$name' required true|false değil" }
        $type = [string](Get-TeamProperty -InputObject $spec -Name "type" -Default "")
        if ($script:TestTeamSchemaTypes -notcontains $type) { throw "test ekibi şeması ${Path}: '$name' tipi '$type' tanınmıyor" }
        if ($null -eq $spec.PSObject.Properties["default"]) { throw "test ekibi şeması ${Path}: '$name' default yok (yok için null)" }
        if ($type -eq "enum" -and @(Get-TeamProperty -InputObject $spec -Name "values" -Default @()).Count -eq 0) { throw "test ekibi şeması ${Path}: '$name' enum ama values yok" }
        if ($null -ne $spec.PSObject.Properties["values_from"]) {
            if ($type -ne "string") { throw "test ekibi şeması ${Path}: '$name' values_from yalnız string için" }
            try { [void](Get-TestTeamValuesFrom -Source ([string]$spec.values_from)) } catch { throw "test ekibi şeması ${Path}: '$name' $($_.Exception.Message)" }
        }
        if ($type -eq "object") { Assert-TestTeamSchemaFields -Fields (Get-TeamProperty -InputObject $spec -Name "fields") -Prefix $name -Path $Path }
        if ($type -eq "array") { Assert-TestTeamSchemaFields -Fields (Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $spec -Name "items") -Name "fields") -Prefix "$name[]" -Path $Path }
    }
}

function Read-TestTeamSchema {
    <# One schema file. A schema that cannot be read throws: it is our file, and a defect. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "test ekibi şeması yok: $Path" }
    $schema = Read-TeamJson -Path $Path
    if ($schema -isnot [System.Management.Automation.PSCustomObject]) { throw "test ekibi şeması ${Path}: bir nesne değil" }
    if ([string](Get-TeamProperty -InputObject $schema -Name "version" -Default "") -ne "1") { throw "test ekibi şeması ${Path}: sürüm 1 değil" }
    Assert-TestTeamSchemaFields -Fields (Get-TeamProperty -InputObject $schema -Name "fields") -Prefix "" -Path $Path
    return $schema
}

function ConvertTo-TestTeamRecordObject {
    <# A parsed JSON object as it is; a dictionary as an object; anything else $null. #>
    param($Raw)
    if ($null -eq $Raw) { return $null }
    if ($Raw -is [System.Management.Automation.PSCustomObject]) { return $Raw }
    if ($Raw -is [System.Collections.IDictionary]) {
        $object = New-Object PSObject
        foreach ($key in @($Raw.Keys)) { Add-Member -InputObject $object -NotePropertyName ([string]$key) -NotePropertyValue $Raw[$key] -Force }
        return $object
    }
    return $null
}

function Test-TestTeamNumber {
    param($Raw)
    return ($Raw -is [int] -or $Raw -is [long] -or $Raw -is [double] -or $Raw -is [decimal] -or $Raw -is [single] -or $Raw -is [int16] -or $Raw -is [byte] -or $Raw -is [uint32] -or $Raw -is [uint64])
}

function ConvertTo-TestTeamShapeValue {
    <# One field's value by its spec: { Ok, Value }. Ok false = absent or invalid. #>
    param($Raw, $Spec, [string]$Path, $Missing, $Defaulted, $Notes = $null)
    $no = [pscustomobject]@{ Ok = $false; Value = $null }
    if ($null -eq $Raw) { return $no }
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    switch ([string]$Spec.type) {
        "string" {
            $text = $null
            if ($Raw -is [string]) { $text = $Raw }
            elseif ($Raw -is [bool]) { $text = ([string]$Raw).ToLowerInvariant() }
            elseif (Test-TestTeamNumber -Raw $Raw) { $text = [System.Convert]::ToString($Raw, $culture) }
            if ($null -eq $text) { return $no }
            if ($Spec.required -and -not $text.Trim()) { return $no }
            if ($null -ne $Spec.PSObject.Properties["values_from"]) {
                $title = Resolve-TestTeamValueFrom -Text $text -Source ([string]$Spec.values_from)
                if ($null -eq $title) { return $no }
                return [pscustomobject]@{ Ok = $true; Value = $title }
            }
            return [pscustomobject]@{ Ok = $true; Value = $text }
        }
        "int" {
            if ($Raw -is [bool]) { return $no }
            # A hand-written measurement is often a decimal ("p95_ms": 1234.7, "ms": "812.5"): it is
            # rounded, never dropped to the default - a measured value reported as "missing" lies.
            $number = $null
            if (Test-TestTeamNumber -Raw $Raw) { $number = [double]$Raw }
            elseif ($Raw -is [string]) {
                $parsed = [double]0
                if ([double]::TryParse($Raw.Trim(), [System.Globalization.NumberStyles]::Float, $culture, [ref]$parsed)) { $number = $parsed }
            }
            if ($null -eq $number -or [double]::IsNaN($number) -or [double]::IsInfinity($number) -or [Math]::Abs($number) -gt 9e15) { return $no }
            return [pscustomobject]@{ Ok = $true; Value = [int64][Math]::Round($number, [System.MidpointRounding]::AwayFromZero) }
        }
        "bool" {
            if ($Raw -is [bool]) { return [pscustomobject]@{ Ok = $true; Value = $Raw } }
            if ($Raw -is [string]) {
                $word = $Raw.Trim().ToLowerInvariant()
                if ($word -eq "true") { return [pscustomobject]@{ Ok = $true; Value = $true } }
                if ($word -eq "false") { return [pscustomobject]@{ Ok = $true; Value = $false } }
                return $no
            }
            if ((Test-TestTeamNumber -Raw $Raw) -and ([double]$Raw -eq 0 -or [double]$Raw -eq 1)) { return [pscustomobject]@{ Ok = $true; Value = ([double]$Raw -eq 1) } }
            return $no
        }
        "enum" {
            if ($Raw -isnot [string]) { return $no }
            $word = $Raw.Trim()
            foreach ($allowed in @($Spec.values)) {
                if ([string]::Equals([string]$allowed, $word, [System.StringComparison]::OrdinalIgnoreCase)) { return [pscustomobject]@{ Ok = $true; Value = [string]$allowed } }
            }
            return $no
        }
        "object" {
            $object = ConvertTo-TestTeamRecordObject -Raw $Raw
            if ($null -eq $object) { return $no }
            return [pscustomobject]@{ Ok = $true; Value = (ConvertTo-TestTeamShapeObject -Object $object -Fields $Spec.fields -Prefix $Path -Missing $Missing -Defaulted $Defaulted -Notes $Notes) }
        }
        "array" {
            if ($Raw -isnot [array]) { return $no }
            $minimum = [int](Get-TeamProperty -InputObject $Spec -Name "min_items" -Default 0)
            if (@($Raw).Count -lt $minimum) { return $no }
            $items = New-Object System.Collections.ArrayList
            $index = 0
            foreach ($item in $Raw) {
                # An item that is not an object is read as an empty one: its required fields are
                # missing by name, its optional ones take their defaults.
                $object = ConvertTo-TestTeamRecordObject -Raw $item
                if ($null -eq $object) { $object = New-Object PSObject }
                [void]$items.Add((ConvertTo-TestTeamShapeObject -Object $object -Fields $Spec.items.fields -Prefix "$Path[$index]" -Missing $Missing -Defaulted $Defaulted -Notes $Notes))
                $index++
            }
            return [pscustomobject]@{ Ok = $true; Value = $items.ToArray() }
        }
    }
    return $no
}

function ConvertTo-TestTeamShapeObject {
    <# One object by its field specs: every original property kept, every schema field typed or
       defaulted; Missing and Defaulted collect dotted paths. #>
    param($Object, $Fields, [string]$Prefix, $Missing, $Defaulted, $Notes = $null)
    $out = [ordered]@{}
    foreach ($property in $Object.PSObject.Properties) { $out[$property.Name] = $property.Value }
    foreach ($field in $Fields.PSObject.Properties) {
        $name = $field.Name
        $spec = $field.Value
        $path = if ($Prefix) { "$Prefix.$name" } else { $name }
        $property = $Object.PSObject.Properties[$name]
        # Not `$raw = if (...) { ... }`: a statement's output is unrolled, and a one-item array
        # would arrive as its item.
        $raw = $null
        $key = $name
        if ($null -ne $property) { $raw = $property.Value; $key = $property.Name }
        $converted = ConvertTo-TestTeamShapeValue -Raw $raw -Spec $spec -Path $path -Missing $Missing -Defaulted $Defaulted -Notes $Notes
        if ($converted.Ok) { $out[$key] = $converted.Value; continue }
        if ($spec.required) { [void]$Missing.Add($path) } else { [void]$Defaulted.Add($path) }
        if ($spec.required -and $null -ne $Notes -and $null -ne $spec.PSObject.Properties["values_from"]) {
            $said = if ($null -eq $raw -or -not ([string]$raw).Trim()) { "$path yok" } else { "$path '$raw' $($spec.values_from) satırı değil" }
            [void]$Notes.Add([pscustomobject]@{ Text = $said; Source = [string]$spec.values_from })
        }
        $default = $spec.default
        if ($default -is [array]) { $out[$key] = @() } else { $out[$key] = $default }
    }
    $shaped = New-Object PSObject
    foreach ($key in @($out.Keys)) { Add-Member -InputObject $shaped -NotePropertyName ([string]$key) -NotePropertyValue $out[$key] -Force }
    return $shaped
}

function Get-TestTeamSchemaRequired {
    <# The top-level required field names of a schema. #>
    param([Parameter(Mandatory = $true)]$Schema)
    return [string[]]@($Schema.fields.PSObject.Properties | Where-Object { $_.Value.required } | ForEach-Object { $_.Name })
}

function New-TestTeamShapeRecord {
    param([bool]$Readable, $Value, [string[]]$Missing = @(), [string[]]$Defaulted = @(), [string]$Reason = "", $Notes = @())
    if ($Readable) {
        $why = "okundu"
        if (@($Defaulted).Count -gt 0) { $why += "; varsayılanla dolan: " + (@($Defaulted) -join ", ") }
    }
    else {
        $why = "okunamadı, eksik ya da geçersiz zorunlu alan: " + (@($Missing) -join ", ")
        if ($Reason) { $why = "${Reason}; $why" }
        # A values_from field: what was wrong per path, then the valid titles once per source.
        if (@($Notes).Count -gt 0) {
            $why += "; " + (@($Notes | ForEach-Object { $_.Text }) -join "; ")
            foreach ($source in @($Notes | ForEach-Object { $_.Source } | Select-Object -Unique)) {
                $titles = @(Get-TestTeamValuesFrom -Source $source | ForEach-Object { "'$($_.Title)'" })
                $why += "; geçerli başlıklar ($source, tam yazılır): " + ($titles -join " | ")
            }
        }
    }
    return [pscustomobject]@{ Readable = $Readable; Value = $Value; Missing = [string[]]@($Missing); Defaulted = [string[]]@($Defaulted); Why = $why }
}

function ConvertTo-TestTeamShape {
    <#
    .SYNOPSIS
        A parsed document read by a schema: { Readable, Value, Missing, Defaulted, Why }. Never
        throws, whatever the document holds.
    #>
    param($Document, [Parameter(Mandatory = $true)]$Schema)
    $missing = New-Object System.Collections.ArrayList
    $defaulted = New-Object System.Collections.ArrayList
    $notes = New-Object System.Collections.ArrayList
    try {
        $object = ConvertTo-TestTeamRecordObject -Raw $Document
        if ($null -eq $object) {
            return (New-TestTeamShapeRecord -Readable $false -Value $null -Missing (Get-TestTeamSchemaRequired -Schema $Schema) -Reason "belge bir JSON nesnesi değil")
        }
        $value = ConvertTo-TestTeamShapeObject -Object $object -Fields $Schema.fields -Prefix "" -Missing $missing -Defaulted $defaulted -Notes $notes
        return (New-TestTeamShapeRecord -Readable ($missing.Count -eq 0) -Value $value -Missing ([string[]]$missing.ToArray()) -Defaulted ([string[]]$defaulted.ToArray()) -Notes $notes.ToArray())
    }
    catch {
        return (New-TestTeamShapeRecord -Readable $false -Value $null -Missing @("(okuyucu)") -Reason ("okuyucu hatası: " + $_.Exception.Message))
    }
}

function Read-TestTeamDocumentFile {
    <# One file read by a schema; the record carries Path. Never throws on the file. #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Schema)
    $reason = ""
    $document = $null
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $reason = "dosya yok: $Path" }
    else {
        try {
            $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
            if (-not $text.Trim()) { $reason = "JSON değil: dosya boş" }
            else { $document = ConvertFrom-Json -InputObject $text -ErrorAction Stop }
        }
        catch { $reason = "JSON değil: " + (([string]$_.Exception.Message) -split "`r?`n")[0] }
    }
    if ($reason) { $record = New-TestTeamShapeRecord -Readable $false -Value $null -Missing (Get-TestTeamSchemaRequired -Schema $Schema) -Reason $reason }
    else { $record = ConvertTo-TestTeamShape -Document $document -Schema $Schema }
    Add-Member -InputObject $record -NotePropertyName Path -NotePropertyValue $Path -Force
    return $record
}

function Read-TestTeamResult {
    <#
    .SYNOPSIS
        A tester's result file by scripts/testteam/schema/result.json. With -Card (the job card of
        New-TestTeamCards), card / tester / family / scenario the file left out or empty are taken
        from the card and named in Defaulted (2026-10-06, 75ceba26: a hand-written result with no
        tester and no card). An unreadable file is a record with Readable false, never a throw.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, $Card = $null, $Schema = $null)
    if ($null -eq $Schema) { $Schema = Read-TestTeamSchema -Path (Join-Path $script:TestTeamSchemaDir "result.json") }
    $record = Read-TestTeamDocumentFile -Path $Path -Schema $Schema
    if ($null -eq $Card -or $null -eq $record.Value) { return $record }
    $defaulted = New-Object System.Collections.ArrayList
    foreach ($name in @($record.Defaulted)) { [void]$defaulted.Add($name) }
    foreach ($pair in @(@("card", "id"), @("tester", "tester"), @("family", "family"), @("scenario", "scenario"))) {
        $own = [string](Get-TeamProperty -InputObject $record.Value -Name $pair[0] -Default "")
        $fromCard = [string](Get-TeamProperty -InputObject $Card -Name $pair[1] -Default "")
        if ($own -or -not $fromCard) { continue }
        Set-TeamProperty -InputObject $record.Value -Name $pair[0] -Value $fromCard
        if ($defaulted -notcontains $pair[0]) { [void]$defaulted.Add($pair[0]) }
    }
    $record.Defaulted = [string[]]$defaulted.ToArray()
    if ($record.Readable -and $defaulted.Count -gt 0) { $record.Why = "okundu; varsayılanla dolan: " + ($defaulted.ToArray() -join ", ") }
    return $record
}

function Read-TestTeamPlan {
    <# The test lead's plan file by scripts/testteam/schema/plan.json. Never throws on the file. #>
    param([Parameter(Mandatory = $true)][string]$Path, $Schema = $null)
    if ($null -eq $Schema) { $Schema = Read-TestTeamSchema -Path (Join-Path $script:TestTeamSchemaDir "plan.json") }
    return (Read-TestTeamDocumentFile -Path $Path -Schema $Schema)
}

function Get-TestTeamSchemaFieldList {
    <# Every field of a schema with its dotted path; arrays as [0]. #>
    param($Fields, [string]$Prefix = "")
    foreach ($property in $Fields.PSObject.Properties) {
        $path = if ($Prefix) { "$Prefix.$($property.Name)" } else { $property.Name }
        [pscustomobject]@{ Path = $path; Spec = $property.Value }
        if ($property.Value.type -eq "object") { Get-TestTeamSchemaFieldList -Fields $property.Value.fields -Prefix $path }
        if ($property.Value.type -eq "array") { Get-TestTeamSchemaFieldList -Fields $property.Value.items.fields -Prefix "$path[0]" }
    }
}

function New-TestTeamSchemaSample {
    <#
    .SYNOPSIS
        A document built from field specs. Mode 'required': required fields only (containers
        included only when required); 'skeleton': every container and every required leaf, no
        optional leaf; 'full': everything. Skip leaves one field out; Empty writes one array
        empty; Override puts a raw value at a path (and does not descend). Every optional field
        left out is added to Defaulted - what the reader must name.
    #>
    param($Fields, [string]$Mode, [string]$Prefix = "", [string]$Skip = "", [string]$Empty = "", [hashtable]$Override = @{}, $Defaulted)
    $object = New-Object PSObject
    foreach ($property in $Fields.PSObject.Properties) {
        $path = if ($Prefix) { "$Prefix.$($property.Name)" } else { $property.Name }
        $spec = $property.Value
        $type = [string]$spec.type
        if ($path -eq $Skip) { if (-not $spec.required) { [void]$Defaulted.Add($path) }; continue }
        $container = ($type -eq "object" -or $type -eq "array")
        $include = ($spec.required -or $Mode -eq "full" -or ($Mode -eq "skeleton" -and $container))
        if (-not $include) { [void]$Defaulted.Add($path); continue }
        if ($Override.ContainsKey($path)) { Add-Member -InputObject $object -NotePropertyName $property.Name -NotePropertyValue $Override[$path]; continue }
        $value = $null
        switch ($type) {
            "object" { $value = New-TestTeamSchemaSample -Fields $spec.fields -Mode $Mode -Prefix $path -Skip $Skip -Empty $Empty -Override $Override -Defaulted $Defaulted }
            "array" {
                $items = New-Object System.Collections.ArrayList
                if ($path -ne $Empty) {
                    $count = [Math]::Max(1, [int](Get-TeamProperty -InputObject $spec -Name "min_items" -Default 0))
                    for ($i = 0; $i -lt $count; $i++) { [void]$items.Add((New-TestTeamSchemaSample -Fields $spec.items.fields -Mode $Mode -Prefix "$path[$i]" -Skip $Skip -Empty $Empty -Override $Override -Defaulted $Defaulted)) }
                }
                $value = $items.ToArray()
            }
            "enum" { $value = [string]@($spec.values)[0] }
            "string" {
                $value = "ornek"
                if ($null -ne $spec.PSObject.Properties["values_from"]) { $value = [string]@(Get-TestTeamValuesFrom -Source ([string]$spec.values_from))[0].Title }
            }
            "int" { $value = 7 }
            "bool" { $value = $true }
        }
        Add-Member -InputObject $object -NotePropertyName $property.Name -NotePropertyValue $value
    }
    return $object
}

function New-TestTeamHalfDocuments {
    <#
    .SYNOPSIS
        The most half-written documents a schema allows, each { Name, Document | Text, Expect:
        { Readable, Missing, Defaulted, Kept, SameValueAs } }. Generated from the schema: a field
        added later gets its half documents with no new test line.
          zorunlu-yalniz      required fields only, at every depth
          iskelet             every container, no optional leaf (p95_ms, tester, why ... absent)
          zorunlu-eksik:<p>   one required field left out (unreadable, Missing <p>)
          az-oge:<p>          an array below its min_items
          enum-disi:<p>       an enum value off the list (required: unreadable; optional: default)
          yanlis-tip:<p>      a number, bool, array or object field holding text
          tam                 every field
          metin:...           numbers and bools written as text, enums in capitals (same value as tam)
          fazladan            unknown fields at every depth (kept)
          json-degil, bos, nesne-degil   files that are not a JSON object
    #>
    param([Parameter(Mandatory = $true)]$Schema, [Parameter(Mandatory = $true)][ValidateSet("result", "plan")][string]$Kind)
    $fields = $Schema.fields
    $list = @(Get-TestTeamSchemaFieldList -Fields $fields)
    $out = New-Object System.Collections.ArrayList
    $required = Get-TestTeamSchemaRequired -Schema $Schema
    function Add-Doc([string]$Name, $Document, $Text, [bool]$Readable, [string[]]$Missing, $Defaulted, [string[]]$Kept = @(), [string]$SameValueAs = "") {
        [void]$out.Add([pscustomobject]@{
                Name = $Name; Kind = $Kind; Document = $Document; Text = $Text
                Expect = [pscustomobject]@{ Readable = $Readable; Missing = [string[]]@($Missing); Defaulted = [string[]]@($Defaulted); Kept = [string[]]@($Kept); SameValueAs = $SameValueAs }
            })
    }
    function Build([string]$Mode, [string]$Skip = "", [string]$Empty = "", [hashtable]$Override = @{}) {
        $defaulted = New-Object System.Collections.ArrayList
        $document = New-TestTeamSchemaSample -Fields $fields -Mode $Mode -Skip $Skip -Empty $Empty -Override $Override -Defaulted $defaulted
        return [pscustomobject]@{ Document = $document; Defaulted = [string[]]$defaulted.ToArray() }
    }
    function Get-Outcome([string]$Path, $Spec, $Base) {
        if ($Spec.required) { return [pscustomobject]@{ Readable = $false; Missing = [string[]]@($Path); Defaulted = $Base } }
        return [pscustomobject]@{ Readable = $true; Missing = [string[]]@(); Defaulted = [string[]](@($Base) + $Path) }
    }

    $built = Build -Mode "required"
    Add-Doc -Name "zorunlu-yalniz" -Document $built.Document -Readable $true -Missing @() -Defaulted $built.Defaulted
    $built = Build -Mode "skeleton"
    Add-Doc -Name "iskelet" -Document $built.Document -Readable $true -Missing @() -Defaulted $built.Defaulted
    foreach ($field in @($list | Where-Object { $_.Spec.required })) {
        $built = Build -Mode "skeleton" -Skip $field.Path
        Add-Doc -Name "zorunlu-eksik:$($field.Path)" -Document $built.Document -Readable $false -Missing @($field.Path) -Defaulted $built.Defaulted
    }
    foreach ($field in @($list | Where-Object { $_.Spec.type -eq "array" -and [int](Get-TeamProperty -InputObject $_.Spec -Name "min_items" -Default 0) -gt 0 })) {
        $built = Build -Mode "skeleton" -Empty $field.Path
        $outcome = Get-Outcome -Path $field.Path -Spec $field.Spec -Base $built.Defaulted
        Add-Doc -Name "az-oge:$($field.Path)" -Document $built.Document -Readable $outcome.Readable -Missing $outcome.Missing -Defaulted $outcome.Defaulted
    }
    $full = Build -Mode "full"
    Add-Doc -Name "tam" -Document $full.Document -Readable $true -Missing @() -Defaulted @()
    foreach ($field in @($list | Where-Object { $_.Spec.type -eq "enum" -or $null -ne $_.Spec.PSObject.Properties["values_from"] })) {
        $built = Build -Mode "full" -Override @{ $field.Path = "liste-disi-deger" }
        $outcome = Get-Outcome -Path $field.Path -Spec $field.Spec -Base $built.Defaulted
        Add-Doc -Name "enum-disi:$($field.Path)" -Document $built.Document -Readable $outcome.Readable -Missing $outcome.Missing -Defaulted $outcome.Defaulted
    }
    foreach ($field in @($list | Where-Object { @("int", "bool", "array", "object") -contains $_.Spec.type })) {
        $built = Build -Mode "full" -Override @{ $field.Path = "tip-yanlis" }
        $outcome = Get-Outcome -Path $field.Path -Spec $field.Spec -Base $built.Defaulted
        Add-Doc -Name "yanlis-tip:$($field.Path)" -Document $built.Document -Readable $outcome.Readable -Missing $outcome.Missing -Defaulted $outcome.Defaulted
    }
    $asText = @{}
    foreach ($field in $list) {
        switch ([string]$field.Spec.type) {
            "int" { $asText[$field.Path] = " 7 " }
            "bool" { $asText[$field.Path] = "TRUE" }
            "enum" { $asText[$field.Path] = ([string]@($field.Spec.values)[0]).ToUpperInvariant() }
        }
    }
    $built = Build -Mode "full" -Override $asText
    Add-Doc -Name "metin:sayi-bool-enum" -Document $built.Document -Readable $true -Missing @() -Defaulted $built.Defaulted -SameValueAs "tam"
    $built = Build -Mode "full"
    $kept = New-Object System.Collections.ArrayList
    Add-Member -InputObject $built.Document -NotePropertyName "notes" -NotePropertyValue "testçinin notu"
    [void]$kept.Add("notes")
    foreach ($field in @($list | Where-Object { $_.Spec.type -eq "object" -or $_.Spec.type -eq "array" })) {
        $at = $built.Document
        foreach ($part in $field.Path.Split('.')) {
            $name = $part; $index = -1
            if ($part -match '^(.+)\[(\d+)\]$') { $name = $Matches[1]; $index = [int]$Matches[2] }
            $at = $at.PSObject.Properties[$name].Value
            if ($index -ge 0) { $at = @($at)[$index] }
        }
        if ($field.Spec.type -eq "array") { $at = @($at)[0]; $keptPath = "$($field.Path)[0].notes" } else { $keptPath = "$($field.Path).notes" }
        Add-Member -InputObject $at -NotePropertyName "notes" -NotePropertyValue "iç not"
        [void]$kept.Add($keptPath)
    }
    Add-Doc -Name "fazladan" -Document $built.Document -Readable $true -Missing @() -Defaulted @() -Kept ([string[]]$kept.ToArray())
    Add-Doc -Name "json-degil" -Document $null -Text '{ "state": "passed", "jobs": [ ' -Readable $false -Missing $required -Defaulted @()
    Add-Doc -Name "bos" -Document $null -Text "" -Readable $false -Missing $required -Defaulted @()
    Add-Doc -Name "nesne-degil" -Document $null -Text "[1, 2]" -Readable $false -Missing $required -Defaulted @()
    return @($out.ToArray())
}

function Test-TestTeamRoleNamesSchema {
    <# Whether a role file's text names a schema by its repository path
       (scripts/testteam/schema/plan.json): the model learns the shape from the schema. #>
    param([Parameter(Mandatory = $true)][string]$RoleFile, [Parameter(Mandatory = $true)][string]$SchemaPath)
    if (-not (Test-Path -LiteralPath $RoleFile -PathType Leaf)) { return $false }
    $text = [System.IO.File]::ReadAllText($RoleFile, [System.Text.Encoding]::UTF8)
    return $text.Contains($SchemaPath)
}
