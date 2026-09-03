<#
.SYNOPSIS
    Windows PowerShell 5.1 tests for scripts/lib/HttpJson.ps1: JSON bodies are decoded as
    UTF-8 regardless of the response charset, and Turkish text survives to disk.

.DESCRIPTION
    The defect: a real owner qualification record showed "Ã"/"Å" for Turkish letters. The
    database held correct UTF-8 (proven on the host); 5.1's Invoke-RestMethod decoded a JSON
    body without charset as ISO-8859-1. These tests pin (1) that the exact bytes reproduce
    the defect under the old decoding, (2) that the helper decodes them correctly, BOM or
    not, (3) that a non-2xx body is surfaced UTF-8-decoded, and (4) that the file written by
    fetch-benchmark.ps1's writer is byte-exact UTF-8.

    Run: powershell -NoProfile -File scripts\tests\utf8-json.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

$script:Failures = 0
$script:Passes = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

# Built from code points on purpose: 5.1 reads a BOM-less .ps1 in the ANSI code page, so a
# Turkish literal in this file would itself be mangled before the test ran.
# Distinct names for upper/lower case: 5.1 variable names are case-INsensitive, so $S and
# $s would be ONE variable (the provisioning-incident class, ADR-0032).
$Scap = [string][char]0x015E; $slow = [string][char]0x015F; $gbreve = [string][char]0x011F; $dotless = [string][char]0x0131
$Icap = [string][char]0x0130; $udia = [string][char]0x00FC; $odia = [string][char]0x00F6; $ccedil = [string][char]0x00E7
$mojibakeA = [string][char]0x00C3   # "A with tilde": the first byte of every UTF-8 Turkish letter read as Latin-1
$turkish = "${Scap}ey... yani ${Icap}stanbul'da ${dotless}${gbreve}${udia}${slow}${odia}${ccedil} harfleri: I${slow}${dotless}k ve G${odia}r${udia}${slow}"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$jsonText = '{"transcript_summary":"' + $turkish + '","n":3}'
$bytes = $utf8.GetBytes($jsonText)

Write-Host "the defect, reproduced on the exact bytes"
$latin1 = [System.Text.Encoding]::GetEncoding("ISO-8859-1").GetString($bytes)
Assert-True ($latin1.Contains($mojibakeA) -and -not $latin1.Contains($slow)) "decoding the UTF-8 body as ISO-8859-1 yields the mojibake the owner saw"

Write-Host "the helper"
$doc = ConvertFrom-Utf8Json -Bytes $bytes
Assert-True ($doc.transcript_summary -ceq $turkish) "UTF-8 bytes decode to the exact Turkish text"
$withBom = [byte[]](@(0xEF, 0xBB, 0xBF) + $bytes)
Assert-True ((ConvertFrom-Utf8Json -Bytes $withBom).transcript_summary -ceq $turkish) "a BOM is tolerated"
Assert-True ((ConvertFrom-Utf8Json -Bytes $utf8.GetBytes('{"a":[1,2]}')).a.Count -eq 2) "plain ASCII JSON still parses"

Write-Host "Invoke-JsonUtf8 against a local UTF-8 server without charset"
$listener = New-Object System.Net.HttpListener
$prefix = "http://127.0.0.1:18099/"
$listener.Prefixes.Add($prefix)
$listener.Start()
$serve = {
    param($listener, $bytes)
    for ($i = 0; $i -lt 2; $i++) {
        $ctx = $listener.GetContext()
        if ($ctx.Request.Url.AbsolutePath -eq "/missing") {
            $ctx.Response.StatusCode = 404
            $payload = (New-Object System.Text.UTF8Encoding($false)).GetBytes('{"detail":"bilinmeyen oturum: ' + [string][char]0x015E + 'ey"}')
        }
        else {
            $ctx.Response.StatusCode = 200
            $payload = $bytes
        }
        $ctx.Response.ContentType = "application/json"   # deliberately NO charset
        $ctx.Response.OutputStream.Write($payload, 0, $payload.Length)
        $ctx.Response.Close()
    }
}
# Serve from a background runspace in THIS process (an HttpListener cannot cross a job).
$rs = [runspacefactory]::CreateRunspace(); $rs.Open()
$ps = [powershell]::Create(); $ps.Runspace = $rs
[void]$ps.AddScript($serve).AddArgument($listener).AddArgument($bytes)
$handle = $ps.BeginInvoke()
try {
    $got = Invoke-JsonUtf8 -Uri ($prefix + "ok")
    Assert-True ($got.transcript_summary -ceq $turkish) "a body served WITHOUT charset is still decoded as UTF-8"
    $threw = $false; $msg = ""
    try { Invoke-JsonUtf8 -Uri ($prefix + "missing") | Out-Null } catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -match "HTTP 404" -and $msg.Contains($Scap + "ey") -and -not $msg.Contains($mojibakeA)) "a non-2xx body is surfaced with status and UTF-8 text (threw=$threw; got: $msg)"
}
finally {
    $listener.Stop(); $listener.Close()
    try { $ps.EndInvoke($handle) | Out-Null } catch { }
    $ps.Dispose(); $rs.Close()
}

Write-Host "the file writer"
$tmp = Join-Path $env:TEMP ("pagentos-utf8-test-" + [guid]::NewGuid().ToString("N") + ".json")
try {
    $json = ([ordered]@{ session = $doc } | ConvertTo-Json -Depth 5)
    [IO.File]::WriteAllText($tmp, $json + "`n", $utf8)
    $back = $utf8.GetString([IO.File]::ReadAllBytes($tmp))
    Assert-True ($back.Contains("${dotless}${gbreve}${udia}${slow}${odia}${ccedil}")) "the written file holds the Turkish letters as UTF-8"
    Assert-True (([IO.File]::ReadAllBytes($tmp))[0] -ne 0xEF) "no BOM is written"
}
finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "utf8-json tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
