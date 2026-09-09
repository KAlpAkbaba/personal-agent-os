<#
.SYNOPSIS
    Drive the LIVE Cloud Core to type into a window the caller names by TITLE — the exact
    shape that failed on the owner's device on 2026-09-09 — and report what the device rows
    say afterwards.

.DESCRIPTION
    On 2026-09-09 the model called ``operator.type`` with ``target: "Not Defteri"``. The
    server forwarded that string to the device as a window id, and the Windows companion
    refused it eight times with

        'Not Defteri' is not a window id (expected w-<hwnd>-<tick>)

    while the correct id for that very window sat in the durable focus stack. Nothing was
    typed. ADR-0100 is the fix.

    This probe reproduces the caller's half exactly — a real realtime session, the real
    ``operator.app_open`` and ``operator.type`` tools, the real device — and then reads the
    ``device_commands`` the run produced. It asserts three things a green unit suite cannot:

      1. every ``window_id`` that reached the device has the device's own shape;
      2. ``keyboard.type`` actually ran and succeeded;
      3. the device read the text back (the plan's own ``ui.inspect`` postcondition).

    The window it opens is closed again, so the probe leaves nothing running.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\probe-window-by-name.ps1
    Exit 0 when typing by name worked, 1 when it did not, 2 when it could not be asked.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$Text = "ADR-0100 dogrulama",
    [string]$CoreHost = "root@100.90.158.26",
    [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

#: scripts\core -> scripts -> repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

function Assert-Ok {
    param([bool]$Condition, [string]$Claim)
    if ($Condition) {
        Write-Host "  PASS  $Claim" -ForegroundColor Green
    }
    else {
        Write-Host "  FAIL  $Claim" -ForegroundColor Red
        $script:failed++
    }
}

$script:failed = 0

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$body = @{ owner_credential = $credential; client_kind = "cli"; label = "probe-window-by-name" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
$credential = $null
$headers = @{ Authorization = "Bearer $($issued.token)" }

$listing = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices" -Headers $headers -TimeoutSec 30
$row = @($listing.devices) | Where-Object { $_.presence -eq "online" } | Select-Object -First 1
if (-not $row) { Write-Host "no online device"; exit 2 }
Write-Host "device   : $($row.name) ($($row.device_id)) $($row.software_version)"

$session = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions" -Headers $headers -Body "{}" -TimeoutSec 60
$sid = $session.session_id
Write-Host "session  : $sid"
Write-Host ""

function Invoke-Tool {
    param([string]$Name, [hashtable]$Arguments)
    $payload = @{ call_id = "probe-$([guid]::NewGuid())"; name = $Name; arguments = $Arguments } |
        ConvertTo-Json -Depth 6 -Compress
    return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/tool-calls" `
        -Headers $headers -Body $payload -TimeoutSec $TimeoutSec
}

Write-Host "1. operator.app_open { application = 'Not Defteri' }"
$opened = Invoke-Tool -Name "operator.app_open" -Arguments @{ application = "Not Defteri" }
Write-Host "   status : $($opened.status)"
Write-Host "   speech : $($opened.result.speech)"
Assert-Ok ([string]$opened.status -eq "succeeded") "Not Defteri opened through the real tool"

Write-Host ""
Write-Host "2. operator.type { content = '$Text'; target = 'Not Defteri' }   <- the failing shape"
$typed = Invoke-Tool -Name "operator.type" -Arguments @{ content = $Text; target = "Not Defteri" }
Write-Host "   status : $($typed.status)"
Write-Host "   speech : $($typed.result.speech)"
Assert-Ok ([string]$typed.status -eq "succeeded") "typing into a window named by TITLE succeeded"
#: On the receipt's own structured fields rather than its Turkish prose: a console encoding
#: must never be able to decide whether a qualification passed.
$fields = @($typed.result.PSObject.Properties.Name)
Assert-Ok ($fields -contains "execution_status" -and [string]$typed.result.execution_status -eq "executed") `
    "the receipt says the action EXECUTED"
Assert-Ok ($fields -contains "terminal_status" -and [string]$typed.result.terminal_status -eq "verified") `
    "the receipt says the end state was VERIFIED"

Write-Host ""
Write-Host "3. what the DEVICE read back (the receipt's own observation, not the plan's hope)"
$readBack = ""
if ($fields -contains "observed_after" -and $null -ne $typed.result.observed_after) {
    $localNames = @($typed.result.observed_after.PSObject.Properties.Name)
    if ($localNames -contains "local" -and $null -ne $typed.result.observed_after.local) {
        $observed = $typed.result.observed_after.local
        $names = @($observed.PSObject.Properties.Name)
        if ($names -contains "root" -and $null -ne $observed.root) {
            $rootKeys = @($observed.root.PSObject.Properties.Name)
            if ($rootKeys -contains "value") { $readBack = [string]$observed.root.value }
        }
    }
}
Write-Host "   value  : '$readBack'"
Assert-Ok ($readBack.EndsWith($Text)) "the device read the typed text back from the control"

Write-Host ""
Write-Host "4. what the SERVER actually asked the device for (Cloud Core rows)"
#: The decisive claim, and the only one the API cannot show: which window_id left the
#: server. Read from the device_commands the run just wrote. A check that cannot be made
#: is reported as SKIPPED, never as a pass.
$shape = [regex]"^w-[0-9]+-[0-9]+$"
$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
if (-not (Test-Path -LiteralPath $ssh)) {
    Write-Host "  SKIP  no ssh on this machine; cannot read the device rows" -ForegroundColor Yellow
    $script:failed++
}
else {
    $sql = "select capability || ' ' || status || ' ' || coalesce(payload_json->>'window_id','-') " +
           "from device_commands where created_at > now() - interval '5 minutes' " +
           "and payload_json ? 'window_id' order by created_at;"
    $rows = @($sql | & $ssh -o BatchMode=yes -o ConnectTimeout=20 $CoreHost `
        "docker exec -i pagentos-prod-postgres psql -U pagentos -d pagentos_prod -At -f -" 2>$null)
    $sentIds = @()
    foreach ($line in $rows) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        Write-Host "   $line"
        $parts = $line.Trim() -split "\s+"
        if ($parts.Count -ge 3 -and $parts[2] -ne "-") { $sentIds += $parts[2] }
    }
    Assert-Ok ($sentIds.Count -gt 0) "the run reached the device with a window_id at all"
    $badIds = @($sentIds | Where-Object { -not $shape.IsMatch($_) })
    Assert-Ok ($badIds.Count -eq 0) "every window_id sent has the device's shape (offenders: $($badIds -join ', '))"
    $typeLine = @($rows) | Where-Object { $_ -like "keyboard.type *" } | Select-Object -First 1
    Assert-Ok ($null -ne $typeLine) "keyboard.type actually ran on the device"
    Assert-Ok ($null -ne $typeLine -and $typeLine -like "keyboard.type succeeded*") `
        "keyboard.type succeeded on the real device"
}

Write-Host ""
Write-Host "5. closing the window the probe opened"
[void](Invoke-Tool -Name "operator.window_control" -Arguments @{ action = "close" })

Write-Host ""
if ($script:failed -eq 0) {
    Write-Host "TYPING BY WINDOW NAME WORKS ON THE REAL DEVICE (ADR-0100)." -ForegroundColor Green
    exit 0
}
Write-Host "$($script:failed) check(s) failed." -ForegroundColor Red
exit 1
