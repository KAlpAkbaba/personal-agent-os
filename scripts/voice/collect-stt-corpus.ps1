<#
.SYNOPSIS
  Collects NEW real STT renderings from a production dump into a proposals file for the STT
  corpus (ADR-0224). A tool a person runs, not a scheduled job - and it never writes the
  corpus: services/api/tests/voice_corpus/stt_corpus.py is edited by hand, with the meaning
  the owner confirms.

.DESCRIPTION
  Input: a JSON-lines dump the lead takes READ-ONLY from production (run with -ShowQuery to
  print the three SELECTs). Three kinds of line:

    {"kind":"session","session_id":..,"provider":..,"updated_at":..,"last_utterance":{..}}
    {"kind":"audit","at":..,"metadata":{..}}
    {"kind":"misheard","id":..,"heard_at":..,"sentence":..,"mode":..,"meant":.., ...}

  A 'misheard' line is one row of the misheard notebook (ADR-0254, misheard_utterances): the
  sentence the recogniser WROTE, in a paid session as well as a local one, and - once the
  owner has answered - what he meant. It is proposed exactly as a 'session' line is, and
  carries mode, engine, device_id, reason, resolved_intent, band and confidence; its status is
  "owner_answered" with `meant` = his words when the row has an answer. The same sentence in
  lines of either kind is ONE proposal (times_heard summed, the earliest day kept); an answer
  is never lost to a line without one, and two different answers are both kept (`meant`, then
  `meant_also`), never merged. intent / tool / application / device stay null even then: a
  person maps the owner's words to them.

  What production keeps, and therefore what can be collected (measured on the code, 2026-10-01):

  * The owner's SENTENCE is kept in exactly one place: last_utterance.chat_question, for a
    LOCAL-mode session whose sentence the router understood nothing of (intent none), for
    that one turn. That is the sentence worth collecting - the one the tables did not read.
  * A paid realtime session keeps NO sentence, and the voice_intent_resolved audit row holds
    names and numbers only (KVKK). Those lines are counted (band, layer) and never guessed at.

  Output: a proposals JSON. Every proposal is origin "real", carries the day it was heard and
  what the router made of it, and has status "needs_owner_meaning": the intent, the entities
  and the device the owner MEANT are in no dump, so they are left null for a person to fill.

  "Already in the corpus" is decided on WHOLE renderings, letter for letter - never on a part
  of the corpus file's text. A sentence equal to a REAL rendering is skipped. A sentence equal
  to a DERIVED rendering is proposed, and names the case it confirms (confirms_derived_case):
  a derived case production really heard is the best proposal there is. The same sentence
  heard twice is one proposal; two that differ by a capital letter are two.

.PARAMETER DumpPath
  The JSON-lines dump file (UTF-8).

.PARAMETER OutPath
  Where to write the proposals. Default: state/reports/stt-corpus-proposals-<date>.json.
  Refused: a .py path, the corpus file, the dump file (the tool never writes over what it
  reads), and any path inside the repository that is not under state/reports - proposals hold
  the owner's raw sentences (KVKK) and state/reports is what git ignores.

.PARAMETER CorpusPath
  The corpus file, READ to skip renderings it already holds. Never written.

.PARAMETER ShowQuery
  Print the read-only query that produces the dump, and exit.
#>
[CmdletBinding()]
param(
    [string]$DumpPath = "",
    [string]$OutPath = "",
    [string]$CorpusPath = "",
    [int]$Days = 14,
    [switch]$ShowQuery
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($ShowQuery) {
    # Three statements, each one read-only SELECT. The third reads the misheard notebook
    # (ADR-0254) with every column of its CONTRACT, named as the table names them.
    $sql = @"
select json_build_object('kind','session','session_id',id,'provider',provider,'updated_at',updated_at,'last_utterance',context_json->'last_utterance') from realtime_sessions where context_json->'last_utterance' is not null and updated_at >= now() - interval '$Days days';
select json_build_object('kind','audit','at',created_at,'metadata',metadata_json) from audit_events where action = 'voice_intent_resolved' and created_at >= now() - interval '$Days days';
select json_build_object('kind','misheard','id',id,'heard_at',heard_at,'sentence',sentence,'mode',mode,'engine',engine,'device_id',device_id,'band',band,'confidence',confidence,'reason',reason,'resolved_intent',resolved_intent,'tool',tool,'session_id',session_id,'meant',meant,'answered_at',answered_at,'expires_at',expires_at) from misheard_utterances where heard_at >= now() - interval '$Days days' order by heard_at;
"@
    Write-Output "-- READ-ONLY. Run on the Cloud Core's database with: psql -At -f <this file>  > stt-dump.jsonl"
    Write-Output "-- Then: collect-stt-corpus.ps1 -DumpPath stt-dump.jsonl"
    Write-Output $sql
    exit 0
}

if (-not $DumpPath) { throw "collect-stt-corpus: -DumpPath is required (or -ShowQuery)" }
if (-not $CorpusPath) { $CorpusPath = Join-Path $root "services\api\tests\voice_corpus\stt_corpus.py" }
if (-not $OutPath) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd")
    $OutPath = Join-Path $root "state\reports\stt-corpus-proposals-$stamp.json"
}

# A relative path means the shell's location, typed or run with -File alike - not the process's
# working directory, which the two disagree about.
function Get-FullPath([string]$Path) {
    $provider = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    return [System.IO.Path]::GetFullPath($provider)
}

function Test-Under([string]$Path, [string]$Directory) {
    $prefix = $Directory.TrimEnd('\') + '\'
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

$dumpFull = Get-FullPath $DumpPath
$corpusFull = Get-FullPath $CorpusPath
$outFull = Get-FullPath $OutPath
if (-not (Test-Path -LiteralPath $dumpFull)) { throw "collect-stt-corpus: no dump at $dumpFull" }
if (-not (Test-Path -LiteralPath $corpusFull)) { throw "collect-stt-corpus: no corpus at $corpusFull" }

# What this tool must never do: write the corpus, or write over what it reads. Decided before
# anything is read.
if ($outFull -ieq $corpusFull) { throw "collect-stt-corpus: refusing to write the corpus itself ($outFull)" }
if ($outFull -ieq $dumpFull) { throw "collect-stt-corpus: refusing to write over the dump it reads ($outFull)" }
if ([System.IO.Path]::GetExtension($outFull) -ieq ".py") {
    throw "collect-stt-corpus: refusing to write a .py file ($outFull); proposals are JSON, the corpus is edited by hand"
}
# Proposals hold the owner's raw sentences (KVKK): inside the repository, only where git ignores.
$reportsDir = Join-Path $root "state\reports"
if ((Test-Under $outFull $root) -and -not (Test-Under $outFull $reportsDir)) {
    throw "collect-stt-corpus: refusing to write raw sentences into the tracked tree ($outFull); inside the repository proposals go under state\reports"
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
$ordinal = [System.StringComparer]::Ordinal

# The corpus, read as its own file writes it: the WHOLE renderings, real and derived apart.
# Every count is checked against the lines that should have produced it, so a corpus written in
# a shape this does not read stops the tool instead of quietly matching less.
function Read-Corpus([string]$Text) {
    $real = New-Object 'System.Collections.Generic.HashSet[string]' -ArgumentList $ordinal
    foreach ($m in [regex]::Matches($Text, '(?m)^\s*rendering="([^"\\]*)",\s*$')) { [void]$real.Add($m.Groups[1].Value) }
    $realLines = [regex]::Matches($Text, '(?m)^\s*rendering=["''(]').Count
    if ($real.Count -eq 0 -or $real.Count -ne $realLines) {
        throw "collect-stt-corpus: read $($real.Count) real rendering(s) from $realLines 'rendering=' line(s); the corpus is not in the shape this tool reads"
    }

    $names = @{}
    foreach ($m in [regex]::Matches($Text, '(?m)^(DISTORTION_\w+): Final = "(\w+)"\s*$')) { $names[$m.Groups[1].Value] = $m.Groups[2].Value }
    $block = [regex]::Match($Text, '(?ms)^_DERIVED: Final[^\n]* = \{\r?\n(.*?)^\}')
    if (-not $block.Success) { throw "collect-stt-corpus: no _DERIVED table in the corpus" }
    $body = $block.Groups[1].Value

    $derived = New-Object 'System.Collections.Generic.Dictionary[string,string]' -ArgumentList $ordinal
    $base = $null
    $token = '(?m)^ {4}"([^"\\]+)": \{|^ {8}(DISTORTION_\w+): (?:"([^"\\]*)"|\(\s*((?:"[^"\\]*"\s*)+)\)),'
    foreach ($m in [regex]::Matches($body, $token)) {
        if ($m.Groups[1].Success) { $base = $m.Groups[1].Value; continue }
        $name = $m.Groups[2].Value
        if (-not $base -or -not $names.ContainsKey($name)) { throw "collect-stt-corpus: cannot place the corpus entry '$($m.Value)'" }
        $rendering = $m.Groups[3].Value
        if ($m.Groups[4].Success) {
            # Python joins neighbouring literals inside the parentheses into one sentence.
            $rendering = -join ([regex]::Matches($m.Groups[4].Value, '"([^"\\]*)"') | ForEach-Object { $_.Groups[1].Value })
        }
        $derived[$rendering] = "stt.derived.$base.$($names[$name])"
    }
    $derivedLines = [regex]::Matches($body, '(?m)^ {8}DISTORTION_\w+:').Count
    if ($derived.Count -eq 0 -or $derived.Count -ne $derivedLines) {
        throw "collect-stt-corpus: read $($derived.Count) derived rendering(s) from $derivedLines entry line(s); the corpus is not in the shape this tool reads"
    }
    return @{ real = $real; derived = $derived }
}

$corpus = Read-Corpus ([System.IO.File]::ReadAllText($corpusFull, $utf8))

function Get-Day([object]$Value) {
    $text = [string]$Value
    if ($text -match '^\d{4}-\d{2}-\d{2}') { return $text.Substring(0, 10) }
    return $null
}

# Keyed letter for letter: [ordered]@{} would fold "Ac" and "ac" into one rendering.
$proposals = New-Object System.Collections.Specialized.OrderedDictionary -ArgumentList $ordinal
$confirmed = 0
$skipped = [ordered]@{ already_in_corpus = 0; no_sentence_kept = 0; unreadable_lines = 0 }
$auditTurns = 0
$byBand = [ordered]@{}
$byLayer = [ordered]@{}
$misheardRows = 0
$misheardAnswered = 0
$byReason = [ordered]@{}
$byMode = [ordered]@{}

# A notebook row's day is its UTC day, whatever offset the database's session wrote it in.
function Get-UtcDay([object]$Value) {
    $moment = [DateTimeOffset]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal
    if ([DateTimeOffset]::TryParse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$moment)) {
        return $moment.ToUniversalTime().ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    return Get-Day $Value
}

# What a notebook line adds to a proposal. A 'session' proposal it joins gains what the notebook
# knows (the first line's, kept). An answer is never lost to a line without one, and two
# different answers are both kept - the first in `meant`, the rest in `meant_also` - never merged.
function Add-Notebook($Proposal, $Row, [string]$Meant) {
    if (-not $Proposal.Contains("mode")) {
        foreach ($column in "mode", "engine", "device_id", "reason") { $Proposal[$column] = $Row.$column }
        $Proposal["meant_also"] = @()
    }
    if (-not $Meant) { return }
    if (-not $Proposal.meant) {
        $Proposal.meant = $Meant
        $Proposal.status = "owner_answered"
    } elseif ($Proposal.meant -cne $Meant -and -not ($Proposal.meant_also -ccontains $Meant)) {
        $Proposal.meant_also = @($Proposal.meant_also) + $Meant
    }
}

foreach ($line in [System.IO.File]::ReadAllLines($dumpFull, $utf8)) {
    if (-not $line.Trim()) { continue }
    $row = $null
    try { $row = $line | ConvertFrom-Json } catch { $row = $null }
    if ($null -eq $row -or -not $row.kind) { $skipped.unreadable_lines += 1; continue }

    if ($row.kind -eq "audit") {
        $auditTurns += 1
        $block = $row.metadata.understanding
        $band = if ($block -and $block.band) { [string]$block.band } else { "unrecorded" }
        $layer = if ($block -and $block.layer) { [string]$block.layer } else { "unrecorded" }
        if ($byBand.Contains($band)) { $byBand[$band] += 1 } else { $byBand[$band] = 1 }
        if ($byLayer.Contains($layer)) { $byLayer[$layer] += 1 } else { $byLayer[$layer] = 1 }
        continue
    }
    $notebook = $row.kind -eq "misheard"
    if ($notebook) {
        # A notebook row: the sentence the recogniser wrote, in either mode, and - once the owner
        # has answered - what he meant. Counted whether or not it becomes a proposal.
        $misheardRows += 1
        $reason = if ($row.reason) { [string]$row.reason } else { "unrecorded" }
        $mode = if ($row.mode) { [string]$row.mode } else { "unrecorded" }
        if ($byReason.Contains($reason)) { $byReason[$reason] += 1 } else { $byReason[$reason] = 1 }
        if ($byMode.Contains($mode)) { $byMode[$mode] += 1 } else { $byMode[$mode] = 1 }
        # The owner's words letter for letter; only a blank answer is no answer.
        $meant = $null
        if ($null -ne $row.meant -and ([string]$row.meant).Trim()) { $meant = [string]$row.meant; $misheardAnswered += 1 }
        $sentence = if ($row.sentence) { ([string]$row.sentence).Trim() } else { "" }
    } elseif ($row.kind -eq "session") {
        $turn = $row.last_utterance
        $sentence = if ($turn -and $turn.chat_question) { ([string]$turn.chat_question).Trim() } else { "" }
    } else { $skipped.unreadable_lines += 1; continue }

    if (-not $sentence) { $skipped.no_sentence_kept += 1; continue }
    if ($corpus.real.Contains($sentence)) { $skipped.already_in_corpus += 1; continue }

    if ($notebook) {
        $day = Get-UtcDay $row.heard_at
    } else {
        $day = Get-Day $turn.at
        if (-not $day) { $day = Get-Day $row.updated_at }
    }
    if ($proposals.Contains($sentence)) {
        # One sentence is one proposal, whatever kind of line names it.
        $known = $proposals[$sentence]
        $known.times_heard += 1
        if ($day -and (-not $known.heard_at -or $day -lt $known.heard_at)) { $known.heard_at = $day }
        if ($notebook) { Add-Notebook $known $row $meant }
        continue
    }
    # A derived rendering production really heard is NOT "already there": it is confirmed.
    $confirms = $null
    if ($corpus.derived.ContainsKey($sentence)) { $confirms = $corpus.derived[$sentence]; $confirmed += 1 }
    if ($notebook) {
        $proposals[$sentence] = [ordered]@{
            rendering       = $sentence
            origin          = "real"
            heard_at        = $day
            times_heard     = 1
            provider        = $null
            mode            = $row.mode
            engine          = $row.engine
            device_id       = $row.device_id
            reason          = $row.reason
            resolved_intent = $row.resolved_intent
            band            = $row.band
            confidence      = $row.confidence
            candidates      = @()
            confirms_derived_case = $confirms
            meant           = $null
            meant_also      = @()
            # What the corpus case needs, a person maps from the owner's words.
            intent          = $null
            tool            = $null
            application     = $null
            device          = $null
            status          = "needs_owner_meaning"
        }
        Add-Notebook $proposals[$sentence] $row $meant
        continue
    }
    # Assigned as a statement: an empty array out of an if-EXPRESSION is written as {}.
    $candidates = @()
    if ($turn.candidates) { $candidates = @($turn.candidates) }
    $proposals[$sentence] = [ordered]@{
        rendering       = $sentence
        origin          = "real"
        heard_at        = $day
        times_heard     = 1
        provider        = [string]$row.provider
        resolved_intent = $turn.intent
        band            = $turn.band
        confidence      = $turn.confidence
        candidates      = $candidates
        # The derived case this real sentence is, letter for letter; null for a new one.
        confirms_derived_case = $confirms
        # What the owner MEANT is in no dump. A person fills these in; the tool never guesses.
        meant           = $null
        intent          = $null
        tool            = $null
        application     = $null
        device          = $null
        status          = "needs_owner_meaning"
    }
}

$report = [ordered]@{
    tool         = "collect-stt-corpus"
    generated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    dump         = [System.IO.Path]::GetFileName($dumpFull)
    corpus_seen  = [ordered]@{ real_renderings = $corpus.real.Count; derived_renderings = $corpus.derived.Count }
    note         = "PROPOSALS ONLY. Nothing here is in the corpus until a person adds it to stt_corpus.py with the meaning the owner confirms."
    proposals    = @($proposals.Values)
    skipped      = $skipped
    audit        = [ordered]@{ turns = $auditTurns; by_band = $byBand; by_layer = $byLayer }
    misheard     = [ordered]@{ rows = $misheardRows; by_reason = $byReason; by_mode = $byMode; answered = $misheardAnswered }
}

$outDir = Split-Path -Parent $outFull
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }
[System.IO.File]::WriteAllText($outFull, ($report | ConvertTo-Json -Depth 8), $utf8)

Write-Output ("collect-stt-corpus: {0} proposal(s), {1} of them a derived case confirmed -> {2}" -f $proposals.Count, $confirmed, $outFull)
Write-Output ("   skipped: {0} already in the corpus, {1} with no sentence kept, {2} unreadable line(s); {3} audit turn(s) counted" -f `
    $skipped.already_in_corpus, $skipped.no_sentence_kept, $skipped.unreadable_lines, $auditTurns)
Write-Output ("   notebook: {0} misheard row(s), {1} answered by the owner" -f $misheardRows, $misheardAnswered)
exit 0
