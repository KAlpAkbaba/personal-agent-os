# Preflight: non-destructive local environment inventory.
# Writes machine-readable JSON (gitignored) and prints a short summary.
# Windows PowerShell 5.1 compatible. Every external command runs with a
# hard timeout so a wedged tool cannot hang the preflight.

param(
  [string]$Output = "docs/LOCAL_ENV_REPORT.generated.json",
  [int]$CommandTimeoutSec = 15
)

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Invoke-WithTimeout {
  param([string]$Exe, [string[]]$Arguments, [int]$TimeoutSec)
  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($Arguments -join ' ')
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
      try { $p.Kill() } catch {}
      return "TIMEOUT after ${TimeoutSec}s"
    }
    $out = ($p.StandardOutput.ReadToEnd() + $p.StandardError.ReadToEnd())
    # wsl.exe emits UTF-16; strip embedded NULs so JSON stays clean.
    return ($out -replace "`0", '').Trim()
  } catch {
    return "ERROR: $($_.Exception.Message)"
  }
}

function Resolve-Tool {
  param([string]$Name, [string[]]$Fallbacks)
  $c = Get-Command $Name -ErrorAction SilentlyContinue
  if ($c -and $c.Source) { return $c.Source }
  foreach ($f in $Fallbacks) {
    $expanded = [Environment]::ExpandEnvironmentVariables($f)
    if (Test-Path $expanded) { return $expanded }
  }
  return $null
}

function Probe-Tool {
  param([string]$Name, [string[]]$VersionArgs, [string[]]$Fallbacks)
  $path = Resolve-Tool -Name $Name -Fallbacks $Fallbacks
  if (-not $path) { return [ordered]@{ present = $false } }
  $version = Invoke-WithTimeout -Exe $path -Arguments $VersionArgs -TimeoutSec $CommandTimeoutSec
  # keep only the first line of noisy version banners
  $firstLine = ($version -split "\r?\n" | Where-Object { $_ -ne '' } | Select-Object -First 1)
  return [ordered]@{ present = $true; path = $path; version = $firstLine }
}

Write-Host "Collecting hardware inventory..."
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpus = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion)
$drives = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  [ordered]@{
    device = $_.DeviceID
    size_gb = [math]::Round($_.Size / 1GB)
    free_gb = [math]::Round($_.FreeSpace / 1GB)
  }
})

Write-Host "Probing toolchain..."
$tools = [ordered]@{
  git       = Probe-Tool "git"       @("--version") @("C:\Program Files\Git\cmd\git.exe")
  powershell5 = Probe-Tool "powershell" @("-NoProfile","-Command","`$PSVersionTable.PSVersion.ToString()") @("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
  pwsh      = Probe-Tool "pwsh"      @("--version") @("C:\Program Files\PowerShell\7\pwsh.exe", "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe")
  wsl       = Probe-Tool "wsl"       @("--status")  @("C:\Windows\System32\wsl.exe")
  docker    = Probe-Tool "docker"    @("--version") @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
  python    = Probe-Tool "python"    @("--version") @()
  uv        = Probe-Tool "uv"        @("--version") @("%USERPROFILE%\.local\bin\uv.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")
  node      = Probe-Tool "node"      @("--version") @("C:\Program Files\nodejs\node.exe")
  pnpm      = Probe-Tool "pnpm"      @("--version") @("%LOCALAPPDATA%\pnpm\pnpm.exe", "%APPDATA%\npm\pnpm.cmd")
  dotnet    = Probe-Tool "dotnet"    @("--list-sdks") @("C:\Program Files\dotnet\dotnet.exe", "%LOCALAPPDATA%\Microsoft\dotnet\dotnet.exe")
  gh        = Probe-Tool "gh"        @("--version") @("C:\Program Files\GitHub CLI\gh.exe", "%LOCALAPPDATA%\Programs\GitHub CLI\gh.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe")
  tailscale = Probe-Tool "tailscale" @("version")   @("C:\Program Files\Tailscale\tailscale.exe")
  winget    = Probe-Tool "winget"    @("--version") @("%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe")
  nvidia_smi = Probe-Tool "nvidia-smi" @("--query-gpu=name,memory.total,driver_version","--format=csv,noheader") @("C:\Windows\System32\nvidia-smi.exe")
}

Write-Host "Checking Docker engine reachability..."
$dockerEngine = "NOT_INSTALLED"
if ($tools.docker.present) {
  $engineInfo = Invoke-WithTimeout -Exe $tools.docker.path -Arguments @("info","--format","{{.ServerVersion}}") -TimeoutSec 20
  if ($engineInfo -match "error|TIMEOUT|ERROR") { $dockerEngine = "INSTALLED_NOT_RUNNING" }
  elseif ($engineInfo) { $dockerEngine = "RUNNING server=$engineInfo" }
}

$result = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  os = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber }
  cpu = [ordered]@{ name = $cpu.Name; cores = $cpu.NumberOfCores; logical = $cpu.NumberOfLogicalProcessors }
  memory_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
  gpu = $gpus
  drives = $drives
  docker_engine = $dockerEngine
  tools = $tools
}

$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $Output
Write-Host "Preflight written to $Output"
Write-Host ""
Write-Host "Summary:"
Write-Host "  OS: $($os.Caption) build $($os.BuildNumber)"
Write-Host "  CPU: $($cpu.Name)"
Write-Host "  RAM: $([math]::Round($os.TotalVisibleMemorySize / 1MB, 1)) GB"
Write-Host "  Docker engine: $dockerEngine"
foreach ($k in $tools.Keys) {
  $t = $tools[$k]
  if ($t.present) { Write-Host ("  {0}: {1}" -f $k, $t.version) }
  else { Write-Host ("  {0}: MISSING" -f $k) }
}
exit 0
