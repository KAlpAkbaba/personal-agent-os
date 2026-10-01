<#
.SYNOPSIS
    The OWNER runs this once: it exchanges the Cloud Owner Credential for one owner session
    and writes that session's token to a file only this Windows account can read. The team's
    cycle reads the file through `cycle.ps1 -QueueUrl ... -QueueToken <that file>`.

.DESCRIPTION
    The credential is typed at a hidden prompt, sent to POST /v1/identity/sessions, and
    dropped; it is never written, printed or put on a command line. The token is written to
    the file and never printed. Claude never runs this script: signing in as the owner is
    the owner's own act (CLAUDE.md, "Asking the owner").

.EXAMPLE
    .\scripts\team\write-queue-token.ps1
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$TokenFile = (Join-Path $env:LOCALAPPDATA "PagentOS\team-queue.token")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
if ($secure.Length -eq 0) { throw "empty credential; nothing done" }
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
try {
    $body = @{ owner_credential = $credential; client_kind = "cli"; label = "team-queue" } | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $issued = Invoke-RestMethod -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 30
}
finally { $credential = $null; $body = $null; $bytes = $null }

$token = [string]$issued.token
if (-not $token) { throw "the identity service issued no session token (wrong credential? it never says which)" }

$folder = Split-Path -Parent $TokenFile
if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
[System.IO.File]::WriteAllText($TokenFile, $token, (New-Object System.Text.UTF8Encoding($false)))
$token = $null

# Only this account (and SYSTEM/Administrators by inheritance being removed) may read the file.
$acl = New-Object System.Security.AccessControl.FileSecurity
$acl.SetAccessRuleProtection($true, $false)
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($me, "FullControl", "Allow")))
Set-Acl -LiteralPath $TokenFile -AclObject $acl

Write-Host "the queue token was written to $TokenFile (session $($issued.session_id)); it was not printed."
