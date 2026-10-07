<#
.SYNOPSIS
  The owner's /voice/measure recordings taken from the Cloud Core, for the measurement
  scripts (stt-compare.ps1 -FromCore, speaker-compare.ps1 -FromCore).

.DESCRIPTION
  Moved out of scripts/voice/stt-compare.ps1 unchanged in behaviour (speaker-engine-measure):
  GET /v1/voice/measurement and its manifest under an owner session, every audio downloaded
  into a FRESH folder under the user's temp directory (never inside the repository), each
  file's sha256 checked against the Core's - one mismatch throws before anything is measured -
  and the folder removed afterwards with retries.

  The owner session: the caller's own PAGENTOS_OWNER_SESSION_TOKEN wins and is left alone;
  otherwise one is taken with the DPAPI-stored owner credential. The token is never printed,
  never written to disk and never a command-line argument of a child.

  Dot-source scripts\lib\HttpJson.ps1, SecretStore.ps1 and AgentUpdate.ps1 before calling the
  -FromCore helpers (Invoke-JsonUtf8, ConvertFrom-Utf8Json, Get-ManifestMember,
  Get-OwnerSessionToken). This file defines functions only.
#>

function Get-CoreBytes {
    <#  GET one body as raw bytes (the audio). Non-2xx is an exception naming the status.  #>
    param([Parameter(Mandatory = $true)][string]$Uri, [Parameter(Mandatory = $true)][hashtable]$Headers, [int]$TimeoutSec = 60)
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = "GET"
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    foreach ($key in $Headers.Keys) { $request.Headers.Add([string]$key, [string]$Headers[$key]) }
    $response = $null
    try { $response = $request.GetResponse() }
    catch [System.Net.WebException] {
        $status = $null
        if ($null -ne $_.Exception.Response) {
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
            $_.Exception.Response.Close()
        }
        throw (New-Object System.Exception(("HTTP {0} from {1}" -f $status, $Uri)))
    }
    try {
        $stream = $response.GetResponseStream()
        $buffer = New-Object System.IO.MemoryStream
        try { $stream.CopyTo($buffer); return , $buffer.ToArray() }
        finally { $buffer.Dispose(); $stream.Dispose() }
    }
    finally { $response.Close() }
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace "-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Remove-CoreFolder {
    <#  The downloaded recordings are personal data: removed, and a locked file is RETRIED
        (an antivirus scan or a child that has not let go yet), never silently left.  #>
    param([Parameter(Mandatory = $true)][string]$Path)
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        try { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop }
        catch { Start-Sleep -Milliseconds (200 * $attempt) }
    }
    return (-not (Test-Path -LiteralPath $Path))
}

function Resolve-VoiceCoreUrl {
    <#  -CoreUrl, else the address the installed Windows agent dials (what
        verify-core-device-row reads). Throws when neither exists.  #>
    param([string]$CoreUrl = "", [string]$InstallRoot = "")
    if (-not $CoreUrl -and $InstallRoot) {
        $settings = Join-Path $InstallRoot "service\appsettings.json"
        if (Test-Path -LiteralPath $settings) {
            $config = [System.IO.File]::ReadAllText($settings) | ConvertFrom-Json
            $CoreUrl = [string](Get-ManifestMember $config "BrokerRestUrl")
            if (-not $CoreUrl) { $CoreUrl = [string](Get-ManifestMember (Get-ManifestMember $config "Agent") "BrokerRestUrl") }
        }
    }
    if (-not $CoreUrl) { throw "no Cloud Core URL: pass -CoreUrl, or install the Windows agent first" }
    return $CoreUrl.TrimEnd("/")
}

function Get-VoiceCoreHeaders {
    <#  The Authorization header of an owner session (see the file's description).  #>
    param([Parameter(Mandatory = $true)][string]$Base, [Parameter(Mandatory = $true)][string]$Label)
    $headers = @{}
    if (Test-Path "Env:PAGENTOS_OWNER_SESSION_TOKEN") {
        # the caller's own session wins and is left alone
        $headers["Authorization"] = "Bearer " + $env:PAGENTOS_OWNER_SESSION_TOKEN
    }
    else {
        $session = Get-OwnerSessionToken -BaseUrl $Base -Label $Label
        if (-not $session) { throw "no owner session: store the owner credential (bootstrap-owner-credential.ps1) or set PAGENTOS_OWNER_SESSION_TOKEN" }
        $headers["Authorization"] = "Bearer " + $session
        $session = $null
    }
    return $headers
}

function Get-VoiceMeasurementListing {
    <#  The Core's recording list and its manifest (optionally one place's): Items (the
        manifest's), ByFile (the list's rows by file name) and the manifest's raw bytes.  #>
    param([Parameter(Mandatory = $true)][string]$Base, [Parameter(Mandatory = $true)][hashtable]$Headers, [string]$Place = "")
    $listing = Invoke-JsonUtf8 -Uri "$Base/v1/voice/measurement" -Headers $Headers -TimeoutSec 60
    $query = if ($Place) { "?place=$Place" } else { "" }
    [byte[]]$manifestBytes = Get-CoreBytes -Uri "$Base/v1/voice/measurement/manifest$query" -Headers $Headers
    $manifest = ConvertFrom-Utf8Json -Bytes $manifestBytes
    $byFile = @{}
    foreach ($recording in @($listing.recordings | Where-Object { $null -ne $_ })) { $byFile[[string]$recording.file] = $recording }
    return [pscustomobject]@{
        Items         = @($manifest.items | Where-Object { $null -ne $_ })
        ByFile        = $byFile
        ManifestBytes = $manifestBytes
    }
}

function New-VoiceCoreFolder {
    <#  A fresh folder under the user's temp directory; refused when temp is inside the
        repository (the recordings are personal data).  #>
    param([Parameter(Mandatory = $true)][string]$Prefix, [Parameter(Mandatory = $true)][string]$RepoRoot)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $folder = Join-Path $tempRoot ($Prefix + [guid]::NewGuid().ToString("N"))
    if ($folder.StartsWith($RepoRoot.TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "the temp directory is inside the repository ($tempRoot): the recordings are personal data and are never written there"
    }
    New-Item -ItemType Directory -Path $folder | Out-Null
    return $folder
}

function Save-VoiceMeasurementAudio {
    <#  Download every manifest item into -Folder under its Core file name, each sha256 checked
        against the Core's; the first mismatch throws (-MismatchNote says what was spared).  #>
    param(
        [Parameter(Mandatory = $true)][string]$Base,
        [Parameter(Mandatory = $true)][hashtable]$Headers,
        [Parameter(Mandatory = $true)][object[]]$Items,
        [Parameter(Mandatory = $true)][hashtable]$ByFile,
        [Parameter(Mandatory = $true)][string]$Folder,
        [string]$MismatchNote = "nothing was sent to any engine"
    )
    foreach ($item in $Items) {
        $file = [string]$item.file
        if ($file -cnotmatch '^(ev|ofis)-\d{2}\.wav$' -or -not $ByFile.ContainsKey($file)) {
            throw "the Core's manifest names '$file', which its recording list does not hold"
        }
        $recording = $ByFile[$file]
        [byte[]]$audio = Get-CoreBytes -Uri ("$Base/v1/voice/measurement/recordings/{0}/{1}/audio" -f $recording.place, $recording.index) -Headers $Headers
        $actual = Get-Sha256Hex -Bytes $audio
        if ($actual -ne ([string]$recording.sha256).ToLowerInvariant()) {
            throw "sha256 mismatch for $file (the Core says $($recording.sha256), the download is $actual): $MismatchNote"
        }
        [System.IO.File]::WriteAllBytes((Join-Path $Folder $file), $audio)
    }
}
