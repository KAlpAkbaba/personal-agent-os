<#
.SYNOPSIS
  Collects NEW real STT renderings from a production dump into a proposals file for the STT
  corpus (ADR-0224). A tool a person runs, not a scheduled job - and it never writes the
  corpus: services/api/tests/voice_corpus/stt_corpus.py is edited by hand, with the meaning
  the owner confirms.

.DESCRIPTION
  Input: a JSON-lines dump the lead takes READ-ONLY from production (run with -ShowQuery to
  print the one SELECT). Two kinds of line:

    {"kind":"session","session_id":..,"provider":..,"updated_at":..,"last_utterance":{..}}
    {"kind":"audit","at":..,"metadata":{..}}

  What production keeps, and therefore what can be collected (measured on the code, 2026-10-01):

  * The owner's SENTENCE is kept in exactly one place: last_utterance.chat_question, for a
    LOCAL-mode session whose sentence the router understood nothing of (intent none), for
    that one turn. That is the sentence worth collecting - the one the tables did not read.
  * A paid realtime session keeps NO sentence, and the voice_intent_resolved audit row holds
    names and numbers only (KVKK). Those lines are counted (band, layer) and never guessed at.

  Output: a proposals JSON. Every proposal is origin "real", carries the day it was heard and
  what the router made of it, and has status "needs_owner_meaning": the intent, the entities
  and the device the owner MEANT are in no dump, so they are left null for a person to fill.
  A rendering already in the corpus is skipped; the same sentence heard twice is one proposal.

.PARAMETER DumpPath
  The JSON-lines dump file (UTF-8).

.PARAMETER OutPath
  Where to write the proposals. Default: state/reports/stt-corpus-proposals-<date>.json.
  A .py path, or the corpus file itself, is refused.

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
    $sql = @"
select json_build_object('kind','session','session_id',id,'provider',provider,'updated_at',updated_at,'last_utterance',context_json->'last_utterance') from realtime_sessions where context_json->'last_utterance' is not null and updated_at >= now() - interval '$Days days'
union all
select json_build_object('kind','audit','at',created_at,'metadata',metadata_json) from audit_events where action = 'voice_intent_resolved' and created_at >= now() - interval '$Days days';
"@
    Write-Output "-- READ-ONLY. Run on the Cloud Core's database with: psql -At -f <this file>  > stt-dump.jsonl"
    Write-Output "-- Then: collect-stt-corpus.ps1 -DumpPath stt-dump.jsonl"
    Write-Output $sql
    exit 0
}

if (-not $DumpPath) { throw "collect-stt-corpus: -DumpPath is required (or -ShowQuery)" }
if (-not (Test-Path -LiteralPath $DumpPath)) { throw "collect-stt-corpus: no dump at $DumpPath" }
if (-not $CorpusPath) { $CorpusPath = Join-Path $root "services\api\tests\voice_corpus\stt_corpus.py" }
if (-not (Test-Path -LiteralPath $CorpusPath)) { throw "collect-stt-corpus: no corpus at $CorpusPath" }
if (-not $OutPath) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd")
    $OutPath = Join-Path $root "state\reports\stt-corpus-proposals-$stamp.json"
}

# The one thing this tool must never do: write the corpus. Decided before anything is read.
$outFull = [System.IO.Path]::GetFullPath($OutPath)
$corpusFull = [System.IO.Path]::GetFullPath($CorpusPath)
if ($outFull -ieq $corpusFull) { throw "collect-stt-corpus: refusing to write the corpus itself ($outFull)" }
if ([System.IO.Path]::GetExtension($outFull) -ieq ".py") {
    throw "collect-stt-corpus: refusing to write a .py file ($outFull); proposals are JSON, the corpus is edited by hand"
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
$corpusText = [System.IO.File]::ReadAllText($corpusFull, $utf8)

function Get-Day([object]$Value) {
    $text = [string]$Value
    if ($text -match '^\d{4}-\d{2}-\d{2}') { return $text.Substring(0, 10) }
    return $null
}

$proposals = [ordered]@{}
$skipped = [ordered]@{ already_in_corpus = 0; no_sentence_kept = 0; unreadable_lines = 0 }
$auditTurns = 0
$byBand = [ordered]@{}
$byLayer = [ordered]@{}

foreach ($line in [System.IO.File]::ReadAllLines($DumpPath, $utf8)) {
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
    if ($row.kind -ne "session") { $skipped.unreadable_lines += 1; continue }

    $turn = $row.last_utterance
    $sentence = if ($turn -and $turn.chat_question) { ([string]$turn.chat_question).Trim() } else { "" }
    if (-not $sentence) { $skipped.no_sentence_kept += 1; continue }
    if ($corpusText.Contains($sentence)) { $skipped.already_in_corpus += 1; continue }

    $day = Get-Day $turn.at
    if (-not $day) { $day = Get-Day $row.updated_at }
    if ($proposals.Contains($sentence)) {
        $known = $proposals[$sentence]
        $known.times_heard += 1
        if ($day -and (-not $known.heard_at -or $day -lt $known.heard_at)) { $known.heard_at = $day }
        continue
    }
    $proposals[$sentence] = [ordered]@{
        rendering       = $sentence
        origin          = "real"
        heard_at        = $day
        times_heard     = 1
        provider        = [string]$row.provider
        resolved_intent = $turn.intent
        band            = $turn.band
        confidence      = $turn.confidence
        candidates      = if ($turn.candidates) { @($turn.candidates) } else { @() }
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
    dump         = [System.IO.Path]::GetFileName($DumpPath)
    note         = "PROPOSALS ONLY. Nothing here is in the corpus until a person adds it to stt_corpus.py with the meaning the owner confirms."
    proposals    = @($proposals.Values)
    skipped      = $skipped
    audit        = [ordered]@{ turns = $auditTurns; by_band = $byBand; by_layer = $byLayer }
}

$outDir = Split-Path -Parent $outFull
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }
[System.IO.File]::WriteAllText($outFull, ($report | ConvertTo-Json -Depth 8), $utf8)

Write-Output ("collect-stt-corpus: {0} proposal(s) -> {1}" -f $proposals.Count, $outFull)
Write-Output ("   skipped: {0} already in the corpus, {1} with no sentence kept, {2} unreadable line(s); {3} audit turn(s) counted" -f `
    $skipped.already_in_corpus, $skipped.no_sentence_kept, $skipped.unreadable_lines, $auditTurns)
exit 0
