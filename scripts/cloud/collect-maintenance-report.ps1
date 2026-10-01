<#
.SYNOPSIS
    The before/after report of a Cloud Core maintenance window (ADR-0223 step 13), read from
    the host and written under team/reports/. Reads only; changes nothing on the host.

.DESCRIPTION
    The window itself is the HOST's own systemd timer (ADR-0223 addendum, 2026-10-01): it
    writes /opt/pagentos/maintenance/before-<date>.txt, after-<date>.txt, window-<date>.log
    and /opt/pagentos/LAST_MAINTENANCE.json. This script fetches those over Tailscale SSH and
    writes team/reports/maintenance-<date>.md. It is started by a Windows scheduled task, so
    the report does not depend on a Claude session being open; when SSH cannot be reached
    (Tailscale's browser check) it says so in the report file and exits 4 - the facts stay on
    the host and the next run, or the lead, collects them.

.EXAMPLE
    .\scripts\cloud\collect-maintenance-report.ps1 -Date 2026-10-01
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Date,
    [string]$CloudHost = "root@100.90.158.26",
    [string]$ReportsRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') { throw "-Date is yyyy-MM-dd" }
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $ReportsRoot) { $ReportsRoot = Join-Path $repoRoot "team\reports" }
$report = Join-Path $ReportsRoot "maintenance-$Date.md"
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-HostText {
    # One read-only command on the host; stdout only (stderr is not merged: see the release
    # scripts' rule), bounded, never interactive.
    param([string]$Command)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "ssh.exe"
    $psi.Arguments = "-o BatchMode=yes -o ConnectTimeout=20 $CloudHost `"$Command`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $process = [System.Diagnostics.Process]::Start($psi)
    $out = $process.StandardOutput.ReadToEndAsync()
    $err = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(60000)) { try { $process.Kill() } catch { }; return [pscustomobject]@{ Ok = $false; Text = "ssh did not answer in 60 s" } }
    [void]$out.Wait(5000); [void]$err.Wait(5000)
    return [pscustomobject]@{ Ok = ($process.ExitCode -eq 0); Text = ([string]$out.Result).TrimEnd() }
}

$dir = "/opt/pagentos/maintenance"
$before = Get-HostText -Command "cat $dir/before-$Date.txt"
$lines = New-Object System.Collections.ArrayList
[void]$lines.Add("# Bakım penceresi raporu — $Date (ADR-0223)")
[void]$lines.Add("")
[void]$lines.Add("Toplandı: $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) · makine $env:COMPUTERNAME · kaynak $CloudHost (salt okuma)")
[void]$lines.Add("")
if (-not $before.Ok) {
    [void]$lines.Add("**Sunucuya ulaşılamadı ya da pencere dosyası yok** ($($before.Text)). Gerçekler sunucuda duruyor:")
    [void]$lines.Add("``$dir/before-$Date.txt``, ``after-$Date.txt``, ``window-$Date.log``, ``/opt/pagentos/LAST_MAINTENANCE.json``.")
    [void]$lines.Add("Tailscale SSH tarayıcı onayı istiyor olabilir; onaydan sonra bu betik yeniden çalıştırılır.")
    [System.IO.File]::WriteAllText($report, (($lines.ToArray()) -join "`n") + "`n", $utf8)
    Write-Host "the host was not read; said so in $report"
    exit 4
}
$after = Get-HostText -Command "cat $dir/after-$Date.txt"
$record = Get-HostText -Command "cat /opt/pagentos/LAST_MAINTENANCE.json"
$log = Get-HostText -Command "tail -n 60 $dir/window-$Date.log"
$marker = Get-HostText -Command "test -e /opt/pagentos/MAINTENANCE_MARKER && echo present || echo gone"
$now = Get-HostText -Command "uname -r; uptime -s; curl -fsS --max-time 10 http://127.0.0.1:8001/v1/system/health | head -c 200"

$verdict = "BELİRSİZ"
if ($record.Ok -and $record.Text -match '"window_start":"' + [regex]::Escape($Date)) { $verdict = "TAMAMLANDI VE DOĞRULANDI" }
elseif ($marker.Text -eq "present") { $verdict = "YENİDEN BAŞLATILDI AMA DOĞRULAMA GEÇMEDİ (işaret dosyası duruyor; ADR-0223 adım 12)" }
elseif (-not $after.Ok) { $verdict = "PENCERE YENİDEN BAŞLATMAYA VARMADI (ön kontrol ya da bir adım durdurdu; günlüğe bakın)" }
[void]$lines.Add("## Sonuç: $verdict")
[void]$lines.Add("")
[void]$lines.Add("## Kayıt (LAST_MAINTENANCE.json)")
[void]$lines.Add("")
[void]$lines.Add('```')
[void]$lines.Add($(if ($record.Ok) { $record.Text } else { "(yok)" }))
[void]$lines.Add('```')
foreach ($part in @(@("Öncesi", $before), @("Sonrası", $after), @("Şu an (rapor toplanırken)", $now), @("Pencere günlüğü (son 60 satır)", $log))) {
    [void]$lines.Add("")
    [void]$lines.Add("## $($part[0])")
    [void]$lines.Add("")
    [void]$lines.Add('```')
    [void]$lines.Add($(if ($part[1].Ok -and $part[1].Text) { $part[1].Text } else { "(okunamadı ya da yok)" }))
    [void]$lines.Add('```')
}
[System.IO.File]::WriteAllText($report, (($lines.ToArray()) -join "`n") + "`n", $utf8)
Write-Host "report: $report ($verdict)"
exit 0
