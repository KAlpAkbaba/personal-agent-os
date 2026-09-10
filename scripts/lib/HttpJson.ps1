<#
.SYNOPSIS
    JSON over HTTP for Windows PowerShell 5.1 that decodes the body as UTF-8 no matter
    what the response says.

.DESCRIPTION
    5.1's Invoke-RestMethod decodes a response body with the charset from Content-Type
    and falls back to ISO-8859-1 when there is none. A real owner qualification record
    (Turkish transcript summary) came back as "Ã"/"Å" mojibake that way while the API and
    the database held correct UTF-8. Every JSON body in this project is UTF-8 by contract,
    so these helpers work on raw bytes via HttpWebRequest (no cmdlet pre-decodes or
    pre-reads anything, so a non-2xx body is still readable) and decode UTF-8, always.

    Dot-source; StrictMode-safe; no dependency on the response charset.
#>

Set-StrictMode -Version Latest

function ConvertFrom-Utf8Json {
    <#
    .SYNOPSIS
        Decode UTF-8 bytes (BOM tolerated) and parse them as JSON.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes)
    $text = (New-Object System.Text.UTF8Encoding($false)).GetString($Bytes)
    if ($text.Length -gt 0 -and [int][char]$text[0] -eq 0xFEFF) { $text = $text.Substring(1) }
    return ($text | ConvertFrom-Json)
}

function Read-AllBytes {
    param([Parameter(Mandatory = $true)]$Stream)
    $ms = New-Object System.IO.MemoryStream
    try { $Stream.CopyTo($ms); return $ms.ToArray() } finally { $ms.Dispose() }
}

function Invoke-JsonUtf8 {
    <#
    .SYNOPSIS
        GET/POST a JSON endpoint and return the parsed body decoded as UTF-8.
        Non-2xx responses surface as an exception whose message carries the status and
        the UTF-8-decoded body (bounded) and whose StatusCode property is the number;
        never a Latin-1 rendering.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [ValidateSet("GET", "POST", "PUT", "PATCH")][string]$Method = "GET",
        [hashtable]$Headers = @{},
        [AllowNull()][AllowEmptyString()][string]$Body = $null,
        [int]$TimeoutSec = 20
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = $Method
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    $request.Accept = "application/json"
    foreach ($key in $Headers.Keys) { $request.Headers.Add([string]$key, [string]$Headers[$key]) }
    if ($Method -in @("POST", "PUT", "PATCH")) {
        $bytes = $utf8.GetBytes([string]$(if ($null -eq $Body) { "" } else { $Body }))
        $request.ContentType = "application/json; charset=utf-8"
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    }
    $response = $null
    try {
        $response = $request.GetResponse()
    }
    catch [System.Net.WebException] {
        $status = $null; $detail = ""
        $errorResponse = $_.Exception.Response
        if ($null -ne $errorResponse) {
            try { $status = [int]$errorResponse.StatusCode } catch { }
            try {
                $errorStream = $errorResponse.GetResponseStream()
                try { $detail = $utf8.GetString((Read-AllBytes -Stream $errorStream)) } finally { $errorStream.Dispose() }
                if ($detail.Length -gt 600) { $detail = $detail.Substring(0, 600) }
            } catch { }
            $errorResponse.Close()
        }
        $err = New-Object System.Exception(("HTTP {0} from {1}: {2}" -f $status, $Uri, $detail))
        $err | Add-Member -NotePropertyName StatusCode -NotePropertyValue $status -Force
        throw $err
    }
    try {
        $bodyStream = $response.GetResponseStream()
        try { $bytes = Read-AllBytes -Stream $bodyStream } finally { $bodyStream.Dispose() }
    }
    finally { $response.Close() }
    return (ConvertFrom-Utf8Json -Bytes $bytes)
}
