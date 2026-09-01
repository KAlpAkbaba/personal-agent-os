<#
.SYNOPSIS
    One elevated command: bootstrap the owner credential (if needed), enrol the already
    installed Device Service, and start it.

.DESCRIPTION
    The Device Service and the Session Companion are already installed and must not be
    reinstalled — this script touches neither binary. What it does is the part that needs
    owner authority and elevation:

      1. checks the Cloud Core is answering on the port the INSTALLED agent is configured to
         dial, read from the agent's own appsettings.json;
      2. bootstraps the owner credential if none exists. That is the one thing only you can
         do: it is displayed exactly once, is never written to disk by this script, and
         cannot be recovered afterwards;
      3. exchanges it for an owner session, mints a single-use enrolment token, and enrols
         the installed agent with it — all in memory. The token is never printed, never
         logged and never written to disk;
      4. starts the Windows Service and waits for it to be Running.

    The enrolment token is passed to the agent's `enroll` verb as a command-line argument,
    which is briefly visible to anything on this machine that can enumerate process command
    lines. It is single-use and short-lived, and it is consumed within milliseconds — but it
    is a real, if small, exposure and is stated rather than hidden.

.PARAMETER StoreAutomationSession
    Also mint a second, labelled owner session and store it DPAPI-encrypted under your
    Windows account (scripts/secret-store.ps1), so local automation can drive the API without
    you pasting anything. It is a session, not the credential: revocable, TTL-bounded, and
    visible in `GET /v1/identity/sessions`. Defaults to on; pass -StoreAutomationSession:$false
    to skip it and drive the API yourself.

.EXAMPLE
    # From an ELEVATED PowerShell, at the repository root:
    .\scripts\complete-device-enrollment.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$AgentConfig = (Join-Path $env:ProgramFiles "PagentOS\agent\service\appsettings.json"),
    [bool]$StoreAutomationSession = $true,
    [int]$AutomationSessionTtlSeconds = 86400,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Enrolment writes to $env:ProgramData\PagentOS and starts a Windows Service, so this must run elevated. Right-click PowerShell -> Run as administrator."
    }
}

function ConvertFrom-SecureStringPlain {
    <#  Decrypt in memory only; the unmanaged copy is zeroed immediately.  #>
    param([System.Security.SecureString]$Secure)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Assert-Elevated

# --- 1. where the installed agent expects the broker ---------------------------------------

if (-not (Test-Path -LiteralPath $AgentConfig)) {
    throw "no installed agent configuration at $AgentConfig; run scripts\install-device-service.ps1 first"
}
$agent = Get-Content -LiteralPath $AgentConfig -Raw | ConvertFrom-Json
$baseUrl = $agent.BrokerRestUrl.TrimEnd('/')
$dataDir = $agent.DataDir
$serviceExe = Join-Path (Split-Path -Parent $AgentConfig) "PagentOS.DeviceService.exe"

Write-Host "agent configuration: broker=$baseUrl data=$dataDir"
if (-not (Test-Path -LiteralPath $serviceExe)) {
    throw "the installed service executable is missing: $serviceExe"
}

try {
    $health = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 10 -ErrorAction Stop
}
catch {
    throw "the Cloud Core is not answering at $baseUrl. Start it first (non-elevated): .\scripts\dev-broker.ps1"
}
Write-Host "cloud core health: $($health.status)"

# --- 2. owner credential ---------------------------------------------------------------------

$credential = $null
try {
    $bootstrap = $null
    try {
        $bootstrap = Invoke-RestMethod -Uri "$baseUrl/v1/identity/bootstrap" -Method Post -TimeoutSec 30 -ErrorAction Stop
    }
    catch {
        $status = $null
        if ($_.Exception.PSObject.Properties.Name -contains "Response" -and $_.Exception.Response) {
            $status = $_.Exception.Response.StatusCode.value__
        }
        if ($status -ne 409) { throw }
    }

    if ($bootstrap) {
        $credential = $bootstrap.owner_credential
        Write-Host ""
        Write-Host "================= OWNER CREDENTIAL - PRIVATE, SHOWN ONCE =================" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  $credential"
        Write-Host ""
        Write-Host "=========================================================================" -ForegroundColor Cyan
        Write-Host "Put this in your password manager now. The server keeps only its SHA-256" -ForegroundColor Yellow
        Write-Host "hash; nothing here writes it to disk; no API can show it again." -ForegroundColor Yellow
        Write-Host "DO NOT paste this block into a chat. Everything below the separator is" -ForegroundColor Yellow
        Write-Host "safe to share." -ForegroundColor Yellow
        Write-Host ""
    }
    else {
        Write-Host "an owner credential already exists; it is needed to mint an enrolment token"
        $secure = Read-Host -Prompt "Owner credential (input hidden)" -AsSecureString
        $credential = ConvertFrom-SecureStringPlain -Secure $secure
        if (-not $credential) { throw "no credential supplied" }
    }

    Write-Host "------------------------------------------------------------------------"
    Write-Host ""

    # --- 3. session -> enrolment token -> enrol, entirely in memory -------------------------

    $session = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" `
        -Body (@{ owner_credential = $credential; client_kind = "cli"; label = "device-enrolment" } | ConvertTo-Json)
    $headers = @{ Authorization = "Bearer $($session.token)" }
    Write-Host "owner session established (session_id=$($session.session_id))"

    $tokenResponse = Invoke-RestMethod -Uri "$baseUrl/v1/devices/enrollment-tokens" -Method Post `
        -Headers $headers -TimeoutSec 30
    Write-Host "minted a single-use enrolment token (expires $($tokenResponse.expires_at)); it is not printed"

    $enroll = Invoke-NativeProcess -FilePath $serviceExe -Arguments @(
        "enroll",
        "--broker-url", $baseUrl,
        "--token", $tokenResponse.token,
        "--name", $env:COMPUTERNAME
    ) -TimeoutSeconds 120

    # Redact before anything is shown: the command line contains the token.
    $safeOut = ($enroll.StdOut + $enroll.StdErr) -replace [regex]::Escape($tokenResponse.token), "<token redacted>"
    if (-not $enroll.Success) {
        throw "enrolment failed with exit code $($enroll.ExitCode):`n$safeOut"
    }
    Write-Host ($safeOut.Trim())

    $statePath = Join-Path $dataDir "state.json"
    if (-not (Test-Path -LiteralPath $statePath)) {
        throw "enrolment reported success but $statePath does not exist"
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    Write-Host "enrolled: device_id=$($state.device_id) name=$($state.name)"

    # --- 4. optional automation session ------------------------------------------------------

    if ($StoreAutomationSession) {
        $automation = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
            -ContentType "application/json" `
            -Body (@{
                owner_credential = $credential
                client_kind      = "cli"
                label            = "local-automation"
                ttl_s            = $AutomationSessionTtlSeconds
            } | ConvertTo-Json)

        $storeRoot = Join-Path $env:LOCALAPPDATA "PagentOS\secrets"
        New-Item -ItemType Directory -Force -Path $storeRoot | Out-Null
        $secure = ConvertTo-SecureString -String $automation.token -AsPlainText -Force
        $secure | ConvertFrom-SecureString | Set-Content -Path (Join-Path $storeRoot "PAGENTOS_OWNER_SESSION_TOKEN.dpapi") -Encoding ASCII

        Write-Host ""
        Write-Host "stored a labelled 'local-automation' session, DPAPI-encrypted to your Windows account:"
        Write-Host "  $storeRoot\PAGENTOS_OWNER_SESSION_TOKEN.dpapi"
        Write-Host "  session_id=$($automation.session_id) expires=$($automation.expires_at)"
        Write-Host "  It is a session, not the credential: revoke it any time with"
        Write-Host "  POST $baseUrl/v1/identity/sessions/$($automation.session_id)/revoke"
    }
}
finally {
    # The plaintext credential lives no longer than it must.
    $credential = $null
    [System.GC]::Collect()
}

# --- 5. start the service --------------------------------------------------------------------

if (-not $SkipStart) {
    Write-Host ""
    Write-Host "starting $ServiceName ..."
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))

    $service = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'"
    Write-Host "service: state=$($service.State) account=$($service.StartName) start=$($service.StartMode) pid=$($service.ProcessId)"
}

Write-Host ""
Write-Host "Done. Next: .\scripts\verify-device-service.ps1" -ForegroundColor Green
Write-Host "Everything from the separator down is safe to share; the credential block is not."
