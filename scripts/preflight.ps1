param(
  [string]$Output = "docs/LOCAL_ENV_REPORT.generated.json"
)
$ErrorActionPreference = "SilentlyContinue"

function CmdVersion($cmd, $args) {
  $c = Get-Command $cmd -ErrorAction SilentlyContinue
  if (-not $c) { return $null }
  try { return ((& $cmd @args 2>&1) | Out-String).Trim() } catch { return "ERROR: $($_.Exception.Message)" }
}

$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpus = Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion
$drives = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, Size, FreeSpace

$result = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  os = [ordered]@{ caption=$os.Caption; version=$os.Version; build=$os.BuildNumber }
  cpu = [ordered]@{ name=$cpu.Name; cores=$cpu.NumberOfCores; logical=$cpu.NumberOfLogicalProcessors }
  memory_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
  gpu = $gpus
  drives = $drives
  tools = [ordered]@{
    git = CmdVersion "git" @("--version")
    pwsh = CmdVersion "pwsh" @("--version")
    wsl = CmdVersion "wsl" @("--version")
    docker = CmdVersion "docker" @("--version")
    python = CmdVersion "python" @("--version")
    uv = CmdVersion "uv" @("--version")
    node = CmdVersion "node" @("--version")
    pnpm = CmdVersion "pnpm" @("--version")
    dotnet = CmdVersion "dotnet" @("--version")
    gh = CmdVersion "gh" @("--version")
    tailscale = CmdVersion "tailscale" @("version")
    nvidia_smi = CmdVersion "nvidia-smi" @()
  }
}

$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $Output
Write-Host "Preflight written to $Output"
